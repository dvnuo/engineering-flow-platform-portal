import markupsafe
import app.logger  # Ensure logging is configured (intentional side-effect import)  # noqa: F401
import json
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request, Response, status, Query
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.db import SessionLocal
from app.repositories.agent_repo import AgentRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.repositories.user_repo import UserRepository
from app.schemas.requirement_bundle import BundleRef, RequirementBundleCreateForm
from app.services.requirement_bundle_github_service import (
    RequirementBundleGithubService,
    RequirementBundleGithubServiceError,
)
from app.services.auth_service import parse_session_token
from app.services.proxy_service import ProxyService, build_portal_execution_headers, build_portal_identity_headers
from app.services.runtime_execution_context_service import RuntimeExecutionContextService
from app.services.task_dispatcher import TaskDispatcherService

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory="app/templates")

def escape_data_attr(s):
    """Escape string for safe embedding in HTML data-* attributes using markupsafe."""
    if s is None:
        return ''
    return markupsafe.escape(str(s))

templates.env.filters['data_attr'] = escape_data_attr
settings = get_settings()
proxy_service = ProxyService()
runtime_execution_context_service = RuntimeExecutionContextService()
task_dispatcher_service = TaskDispatcherService()
requirement_bundle_service = RequirementBundleGithubService()
base_uri = settings.base_uri


def _current_user_from_cookie(request: Request):
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None
    user_id = parse_session_token(token)
    if not user_id:
        return None

    db = SessionLocal()
    try:
        user = UserRepository(db).get_by_id(user_id)
        if not user or not user.is_active:
            return None
        return user
    finally:
        db.close()


def _can_access(agent, user) -> bool:
    return user.role == "admin" or agent.owner_user_id == user.id or agent.visibility == "public"


def _can_write(agent, user) -> bool:
    return user.role == "admin" or agent.owner_user_id == user.id


def _portal_extra_headers(user) -> dict[str, str]:
    return build_portal_identity_headers(user)


def _list_writable_agents(db, user) -> list:
    agents = AgentRepository(db).list_all()
    return [agent for agent in agents if _can_write(agent, user)]


def _parse_multivalue_text_field(raw: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    normalized = (raw or "").replace(",", "\n")
    for item in normalized.splitlines():
        cleaned = item.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        values.append(cleaned)
    return values


def _has_supported_collect_sources(sources: dict) -> bool:
    supported_source_keys = ("jira", "confluence", "github_docs")
    return any(sources.get(source_key) for source_key in supported_source_keys)


async def _forward_runtime(
    *,
    user,
    agent,
    method: str,
    subpath: str,
    query_items,
    body,
    headers: Optional[dict[str, str]] = None,
):
    return await proxy_service.forward(
        agent=agent,
        method=method,
        subpath=subpath,
        query_items=query_items,
        body=body,
        headers=headers or {},
        extra_headers=_portal_extra_headers(user),
    )


async def _forward_runtime_multipart(
    *,
    user,
    agent,
    method: str,
    subpath: str,
    query_items,
    files,
    data=None,
    headers: Optional[dict[str, str]] = None,
):
    return await proxy_service.forward_multipart(
        agent=agent,
        method=method,
        subpath=subpath,
        query_items=query_items,
        files=files,
        data=data,
        headers=headers or {},
        extra_headers=_portal_extra_headers(user),
    )


def _settings_view_payload(config_data: dict) -> dict:
    llm = config_data.get("llm") if isinstance(config_data.get("llm"), dict) else {}
    jira = config_data.get("jira") if isinstance(config_data.get("jira"), dict) else {}
    confluence = config_data.get("confluence") if isinstance(config_data.get("confluence"), dict) else {}
    jira_instances = jira.get("instances") if isinstance(jira.get("instances"), list) else []
    confluence_instances = confluence.get("instances") if isinstance(confluence.get("instances"), list) else []

    return {
        "config": config_data,
        "llm": llm,
        "jira": jira,
        "jira_instances": jira_instances,
        "confluence": confluence,
        "confluence_instances": confluence_instances,
        "github": config_data.get("github") if isinstance(config_data.get("github"), dict) else {},
        "git": config_data.get("git") if isinstance(config_data.get("git"), dict) else {},
        "ssh": config_data.get("ssh") if isinstance(config_data.get("ssh"), dict) else {},
        "proxy": config_data.get("proxy") if isinstance(config_data.get("proxy"), dict) else {},
        "debug": config_data.get("debug") if isinstance(config_data.get("debug"), dict) else {},
    }


def _settings_error_response(request: Request, agent_id: str, config_payload: dict, message: str):
    view_data = _settings_view_payload(config_payload if isinstance(config_payload, dict) else {})
    return templates.TemplateResponse(
        "partials/settings_panel.html",
        {
            "request": request,
            "agent_id": agent_id,
            "status_type": "error",
            "status_message": message,
            **view_data,
        },
    )


def _settings_parse_instances(
    form,
    prefix: str,
    fields: list[str],
    existing_instances: Optional[list] = None,
    preserve_blank_fields: Optional[set[str]] = None,
) -> list[dict]:
    count_text = (form.get(f"{prefix}_instance_count") or "0").strip()
    try:
        count = max(0, int(count_text))
    except ValueError:
        count = 0

    instances = []
    existing_instances = existing_instances if isinstance(existing_instances, list) else []
    preserve_blank_fields = preserve_blank_fields or set()
    for i in range(count):
        item = {}
        existing_item = existing_instances[i] if i < len(existing_instances) and isinstance(existing_instances[i], dict) else {}
        for field in fields:
            value = (form.get(f"{prefix}_instances_{i}_{field}") or "").strip()
            if not value and field in preserve_blank_fields:
                value = existing_item.get(field) or ""
            item[field] = value
        if item.get("name") or item.get("url"):
            instances.append(item)
    return instances


def _settings_merge_payload(config_payload: dict, form) -> tuple[dict, Optional[str]]:
    def as_bool(value) -> bool:
        return str(value or "").lower() in {"1", "true", "on", "yes"}

    config_payload = config_payload if isinstance(config_payload, dict) else {}

    existing_proxy_password = None
    if "proxy" in config_payload and isinstance(config_payload["proxy"], dict):
        existing_proxy_password = config_payload["proxy"].get("password")

    jira_config = config_payload.get("jira")
    if isinstance(jira_config, dict):
        jira_instances = jira_config.get("instances", [])
        existing_jira_instances = jira_instances if isinstance(jira_instances, list) else []
    else:
        existing_jira_instances = []

    confluence_config = config_payload.get("confluence")
    if isinstance(confluence_config, dict):
        confluence_instances = confluence_config.get("instances", [])
        existing_confluence_instances = confluence_instances if isinstance(confluence_instances, list) else []
    else:
        existing_confluence_instances = []

    llm = (config_payload.get("llm") if isinstance(config_payload.get("llm"), dict) else {}).copy()
    provider_value = (form.get("llm_provider") or "").strip()
    model_value = (form.get("llm_model") or "").strip()
    api_key_value = (form.get("llm_api_key") or "").strip()
    if provider_value:
        llm["provider"] = provider_value
    if model_value:
        llm["model"] = model_value
    if api_key_value:
        llm["api_key"] = api_key_value

    temperature_text = (form.get("llm_temperature") or "").strip()
    if temperature_text:
        try:
            llm["temperature"] = float(temperature_text)
        except ValueError:
            return config_payload, "Temperature must be a number."

    max_tokens_text = (form.get("llm_max_tokens") or "").strip()
    if max_tokens_text:
        try:
            llm["max_tokens"] = int(max_tokens_text)
        except ValueError:
            return config_payload, "Max tokens must be an integer."

    jira = (config_payload.get("jira") if isinstance(config_payload.get("jira"), dict) else {}).copy()
    jira["enabled"] = as_bool(form.get("jira_enabled"))
    if "jira_instance_count" in form:
        jira["instances"] = _settings_parse_instances(
            form,
            "jira",
            ["name", "url", "username", "password", "token", "project"],
            existing_instances=existing_jira_instances,
            preserve_blank_fields={"password", "token"},
        )

    confluence = (config_payload.get("confluence") if isinstance(config_payload.get("confluence"), dict) else {}).copy()
    confluence["enabled"] = as_bool(form.get("confluence_enabled"))
    if "confluence_instance_count" in form:
        confluence["instances"] = _settings_parse_instances(
            form,
            "confluence",
            ["name", "url", "username", "password", "token", "space"],
            existing_instances=existing_confluence_instances,
            preserve_blank_fields={"password", "token"},
        )

    github_cfg = (config_payload.get("github") if isinstance(config_payload.get("github"), dict) else {}).copy()
    github_cfg["enabled"] = as_bool(form.get("github_enabled"))
    github_token_value = (form.get("github_api_token") or "").strip()
    github_base_url_value = (form.get("github_base_url") or "").strip()
    if github_token_value:
        github_cfg["api_token"] = github_token_value
    if github_base_url_value:
        github_cfg["base_url"] = github_base_url_value

    git_cfg = (config_payload.get("git") if isinstance(config_payload.get("git"), dict) else {}).copy()
    git_user = (git_cfg.get("user") if isinstance(git_cfg.get("user"), dict) else {}).copy()
    git_name_value = (form.get("git_user_name") or "").strip()
    git_email_value = (form.get("git_user_email") or "").strip()
    if git_name_value:
        git_user["name"] = git_name_value
    if git_email_value:
        git_user["email"] = git_email_value
    git_cfg["user"] = git_user

    ssh_cfg = (config_payload.get("ssh") if isinstance(config_payload.get("ssh"), dict) else {}).copy()
    ssh_cfg["enabled"] = as_bool(form.get("ssh_enabled"))
    ssh_key_path_value = (form.get("ssh_private_key_path") or "").strip()
    if ssh_key_path_value:
        ssh_cfg["private_key_path"] = ssh_key_path_value

    proxy_cfg = (config_payload.get("proxy") if isinstance(config_payload.get("proxy"), dict) else {}).copy()
    proxy_cfg["enabled"] = as_bool(form.get("proxy_enabled"))
    proxy_url_value = (form.get("proxy_url") or "").strip()
    proxy_username_value = (form.get("proxy_username") or "").strip()
    if "proxy_url" in form:
        proxy_cfg["url"] = proxy_url_value
    if "proxy_username" in form:
        proxy_cfg["username"] = proxy_username_value
    if "proxy_password" in form:
        new_password = (form.get("proxy_password") or "").strip()
        if new_password:
            proxy_cfg["password"] = new_password
        else:
            proxy_cfg.pop("password", None)
    elif existing_proxy_password:
        proxy_cfg["password"] = existing_proxy_password

    debug_cfg = (config_payload.get("debug") if isinstance(config_payload.get("debug"), dict) else {}).copy()
    debug_cfg["enabled"] = as_bool(form.get("debug_enabled"))
    valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR"]
    log_level = (form.get("debug_log_level") or "").strip()
    if log_level in valid_log_levels:
        debug_cfg["log_level"] = log_level

    config_payload["llm"] = llm
    config_payload["jira"] = jira
    config_payload["confluence"] = confluence
    config_payload["github"] = github_cfg
    config_payload["git"] = git_cfg
    config_payload["ssh"] = ssh_cfg
    config_payload["proxy"] = proxy_cfg
    config_payload["debug"] = debug_cfg
    return config_payload, None


@router.get("/")
def index(request: Request) -> RedirectResponse:
    user = _current_user_from_cookie(request)
    return RedirectResponse(url="/app" if user else "/login", status_code=302)


@router.get("/login")
def login_page(request: Request):
    if _current_user_from_cookie(request):
        return RedirectResponse(url="/app", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "title": "Portal Login"})


@router.get("/register")
def register_page(request: Request):
    if _current_user_from_cookie(request):
        return RedirectResponse(url="/app", status_code=302)
    return templates.TemplateResponse("register.html", {"request": request, "title": "Create Account"})


@router.get("/app")
def app_page(request: Request):
    user = _current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    return templates.TemplateResponse(
        "app.html",
        {
            "request": request,
            "title": "Engineering Portal",
            "username": user.username,
            "nickname": user.nickname or user.username,
            "user_id": user.id,
            "role": user.role,
        },
    )


@router.get("/app/requirement-bundles")
def requirement_bundles_page(request: Request):
    user = _current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    db = SessionLocal()
    try:
        agents = _list_writable_agents(db, user)
        return templates.TemplateResponse(
            "requirement_bundles.html",
            {
                "request": request,
                "title": "Requirement Bundles",
                "username": user.username,
                "nickname": user.nickname or user.username,
                "bundle_defaults": {
                    "repo": settings.assets_repo_full_name,
                    "base_branch": settings.assets_default_base_branch,
                    "root_dir": settings.assets_bundle_root_dir,
                },
                "agents": agents,
                "bundle_result": None,
                "bundle_detail": None,
                "status_type": "",
                "status_message": "",
            },
        )
    finally:
        db.close()


@router.post("/app/requirement-bundles/create")
async def requirement_bundle_create(request: Request):
    user = _current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    db = SessionLocal()
    try:
        agents = _list_writable_agents(db, user)
        form_data = await request.form()
        create_form = RequirementBundleCreateForm(
            title=str(form_data.get("title") or ""),
            domain=str(form_data.get("domain") or ""),
            slug=(str(form_data.get("slug") or "").strip() or None),
            base_branch=str(form_data.get("base_branch") or settings.assets_default_base_branch),
        )
        bundle_ref = requirement_bundle_service.create_bundle(create_form)
        bundle_detail = requirement_bundle_service.inspect_bundle(bundle_ref)
        return templates.TemplateResponse(
            "requirement_bundles.html",
            {
                "request": request,
                "title": "Requirement Bundles",
                "username": user.username,
                "nickname": user.nickname or user.username,
                "bundle_defaults": {
                    "repo": settings.assets_repo_full_name,
                    "base_branch": settings.assets_default_base_branch,
                    "root_dir": settings.assets_bundle_root_dir,
                },
                "agents": agents,
                "bundle_result": bundle_ref,
                "bundle_detail": bundle_detail,
                "status_type": "success",
                "status_message": "Bundle created successfully.",
            },
        )
    except RequirementBundleGithubServiceError as exc:
        return templates.TemplateResponse(
            "requirement_bundles.html",
            {
                "request": request,
                "title": "Requirement Bundles",
                "username": user.username,
                "nickname": user.nickname or user.username,
                "bundle_defaults": {
                    "repo": settings.assets_repo_full_name,
                    "base_branch": settings.assets_default_base_branch,
                    "root_dir": settings.assets_bundle_root_dir,
                },
                "agents": _list_writable_agents(db, user),
                "bundle_result": None,
                "bundle_detail": None,
                "status_type": "error",
                "status_message": str(exc),
            },
        )
    finally:
        db.close()


@router.get("/app/requirement-bundles/open")
def requirement_bundle_open(request: Request, repo: str = Query(""), path: str = Query(""), branch: str = Query("")):
    user = _current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    target_repo = (repo or settings.assets_repo_full_name).strip()
    target_path = (path or "").strip()
    target_branch = (branch or settings.assets_default_base_branch).strip()

    db = SessionLocal()
    try:
        detail = requirement_bundle_service.inspect_bundle(
            BundleRef(repo=target_repo, path=target_path, branch=target_branch)
        )
        return templates.TemplateResponse(
            "requirement_bundles.html",
            {
                "request": request,
                "title": "Requirement Bundles",
                "username": user.username,
                "nickname": user.nickname or user.username,
                "bundle_defaults": {
                    "repo": settings.assets_repo_full_name,
                    "base_branch": settings.assets_default_base_branch,
                    "root_dir": settings.assets_bundle_root_dir,
                },
                "agents": _list_writable_agents(db, user),
                "bundle_result": None,
                "bundle_detail": detail,
                "status_type": "success",
                "status_message": "Bundle opened successfully.",
            },
        )
    except RequirementBundleGithubServiceError as exc:
        return templates.TemplateResponse(
            "requirement_bundles.html",
            {
                "request": request,
                "title": "Requirement Bundles",
                "username": user.username,
                "nickname": user.nickname or user.username,
                "bundle_defaults": {
                    "repo": settings.assets_repo_full_name,
                    "base_branch": settings.assets_default_base_branch,
                    "root_dir": settings.assets_bundle_root_dir,
                },
                "agents": _list_writable_agents(db, user),
                "bundle_result": None,
                "bundle_detail": None,
                "status_type": "error",
                "status_message": str(exc),
            },
        )
    finally:
        db.close()


def _create_bundle_task_payload(task_type: str, bundle_ref: BundleRef, sources: dict | None = None) -> dict:
    payload = {
        "bundle_ref": {
            "repo": bundle_ref.repo,
            "path": bundle_ref.path,
            "branch": bundle_ref.branch,
        }
    }
    if task_type == "requirement_bundle_collect_task":
        payload["sources"] = sources or {"jira": [], "confluence": [], "github_docs": [], "figma": []}
    return payload


async def _create_and_dispatch_bundle_task(
    request: Request,
    *,
    task_type: str,
    assignee_agent_id: str,
    bundle_ref: BundleRef,
    sources: dict | None = None,
):
    user = _current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    db = SessionLocal()
    try:
        assignee = AgentRepository(db).get_by_id(assignee_agent_id)
        if not assignee:
            raise HTTPException(status_code=404, detail="Assignee agent not found")
        if not _can_write(assignee, user):
            raise HTTPException(status_code=403, detail="Forbidden")

        task_payload = _create_bundle_task_payload(task_type, bundle_ref, sources=sources)
        task = AgentTaskRepository(db).create(
            assignee_agent_id=assignee_agent_id,
            source="portal",
            task_type=task_type,
            input_payload_json=json.dumps(task_payload),
            status="queued",
        )

        dispatch_result = await task_dispatcher_service.dispatch_task(task.id, db, user=user)
        bundle_detail = requirement_bundle_service.inspect_bundle(bundle_ref)
        return templates.TemplateResponse(
            "requirement_bundles.html",
            {
                "request": request,
                "title": "Requirement Bundles",
                "username": user.username,
                "nickname": user.nickname or user.username,
                "bundle_defaults": {
                    "repo": settings.assets_repo_full_name,
                    "base_branch": settings.assets_default_base_branch,
                    "root_dir": settings.assets_bundle_root_dir,
                },
                "agents": _list_writable_agents(db, user),
                "bundle_result": None,
                "bundle_detail": bundle_detail,
                "status_type": "success" if dispatch_result.dispatched else "error",
                "status_message": (
                    f"Created task {task.id}. Dispatch status: {dispatch_result.task_status}."
                    if dispatch_result.dispatched
                    else f"Created task {task.id}, but dispatch failed: {dispatch_result.message}"
                ),
                "task_result": {
                    "task_id": task.id,
                    "dispatch_status": dispatch_result.task_status,
                    "dispatch_message": dispatch_result.message,
                },
            },
        )
    except RequirementBundleGithubServiceError as exc:
        return templates.TemplateResponse(
            "requirement_bundles.html",
            {
                "request": request,
                "title": "Requirement Bundles",
                "username": user.username,
                "nickname": user.nickname or user.username,
                "bundle_defaults": {
                    "repo": settings.assets_repo_full_name,
                    "base_branch": settings.assets_default_base_branch,
                    "root_dir": settings.assets_bundle_root_dir,
                },
                "agents": _list_writable_agents(db, user),
                "bundle_result": None,
                "bundle_detail": None,
                "status_type": "error",
                "status_message": str(exc),
            },
        )
    finally:
        db.close()


@router.post("/app/requirement-bundles/collect")
async def requirement_bundle_collect(request: Request):
    user = _current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    form = await request.form()
    assignee_agent_id = str(form.get("collect_agent_id") or "").strip()
    sources = {
        "jira": _parse_multivalue_text_field(str(form.get("jira_sources") or "")),
        "confluence": _parse_multivalue_text_field(str(form.get("confluence_sources") or "")),
        "github_docs": _parse_multivalue_text_field(str(form.get("github_doc_sources") or "")),
        "figma": _parse_multivalue_text_field(str(form.get("figma_sources") or "")),
    }
    bundle_ref = BundleRef(
        repo=str(form.get("bundle_repo") or "").strip(),
        path=str(form.get("bundle_path") or "").strip(),
        branch=str(form.get("bundle_branch") or "").strip(),
    )
    if not assignee_agent_id:
        raise HTTPException(status_code=400, detail="collect_agent_id is required")
    if not _has_supported_collect_sources(sources):
        bundle_detail = requirement_bundle_service.inspect_bundle(bundle_ref)
        status_message = (
            "Figma-only collection is not supported in MVP"
            if sources.get("figma")
            else "At least one Jira, Confluence, or GitHub Docs source is required."
        )
        db = SessionLocal()
        try:
            return templates.TemplateResponse(
                "requirement_bundles.html",
                {
                    "request": request,
                    "title": "Requirement Bundles",
                    "username": user.username,
                    "nickname": user.nickname or user.username,
                    "bundle_defaults": {
                        "repo": settings.assets_repo_full_name,
                        "base_branch": settings.assets_default_base_branch,
                        "root_dir": settings.assets_bundle_root_dir,
                    },
                    "agents": _list_writable_agents(db, user),
                    "bundle_result": None,
                    "bundle_detail": bundle_detail,
                    "status_type": "error",
                    "status_message": status_message,
                },
            )
        finally:
            db.close()
    return await _create_and_dispatch_bundle_task(
        request,
        task_type="requirement_bundle_collect_task",
        assignee_agent_id=assignee_agent_id,
        bundle_ref=bundle_ref,
        sources=sources,
    )


@router.post("/app/requirement-bundles/design-test-cases")
async def requirement_bundle_design_test_cases(request: Request):
    user = _current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    form = await request.form()
    assignee_agent_id = str(form.get("design_agent_id") or "").strip()
    bundle_ref = BundleRef(
        repo=str(form.get("bundle_repo") or "").strip(),
        path=str(form.get("bundle_path") or "").strip(),
        branch=str(form.get("bundle_branch") or "").strip(),
    )
    if not assignee_agent_id:
        raise HTTPException(status_code=400, detail="design_agent_id is required")
    bundle_detail = requirement_bundle_service.inspect_bundle(bundle_ref)
    if not bundle_detail.requirements_exists:
        db = SessionLocal()
        try:
            return templates.TemplateResponse(
                "requirement_bundles.html",
                {
                    "request": request,
                    "title": "Requirement Bundles",
                    "username": user.username,
                    "nickname": user.nickname or user.username,
                    "bundle_defaults": {
                        "repo": settings.assets_repo_full_name,
                        "base_branch": settings.assets_default_base_branch,
                        "root_dir": settings.assets_bundle_root_dir,
                    },
                    "agents": _list_writable_agents(db, user),
                    "bundle_result": None,
                    "bundle_detail": bundle_detail,
                    "status_type": "error",
                    "status_message": "requirements.yaml is missing; collect requirements first",
                },
            )
        finally:
            db.close()
    return await _create_and_dispatch_bundle_task(
        request,
        task_type="requirement_bundle_design_test_cases_task",
        assignee_agent_id=assignee_agent_id,
        bundle_ref=bundle_ref,
    )


@router.get("/app/users/panel")
async def app_users_panel(request: Request):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")

    db = SessionLocal()
    try:
        users = UserRepository(db).list_all()[:100]  # Limit to 100 users
        return templates.TemplateResponse(
            "partials/users_panel.html",
            {
                "request": request,
                "users": [{"id": u.id, "username": u.username, "role": u.role, "is_active": u.is_active, "created_at": u.created_at} for u in users],
            },
        )
    finally:
        db.close()


@router.get("/app/agents/{agent_id}/sessions/panel")
async def app_agent_sessions_panel(request: Request, agent_id: str):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    current_session_id = (request.query_params.get("current_session_id") or "").strip()
    limit = (request.query_params.get("limit") or "10").strip()

    db = SessionLocal()
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_access(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")

        # When K8s is disabled, return empty sessions
        if not settings.k8s_enabled:
            return templates.TemplateResponse(
                "partials/sessions_panel.html",
                {"request": request, "agent_id": agent_id, "sessions": [], "current_session_id": current_session_id},
            )

        status_code, content, _ = await _forward_runtime(
            user=user,
            agent=agent,
            method="GET",
            subpath="api/sessions",
            query_items=[("limit", limit)],
            body=None,
        )

        if status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Runtime error: {content.decode('utf-8', errors='ignore')}")

        payload = json.loads(content.decode("utf-8"))
        return templates.TemplateResponse(
            "partials/sessions_panel.html",
            {
                "request": request,
                "sessions": payload.get("sessions") or [],
                "current_session_id": current_session_id,
            },
        )
    finally:
        db.close()




@router.get("/app/agents/{agent_id}/skills/panel")
async def app_agent_skills_panel(request: Request, agent_id: str):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    db = SessionLocal()
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_access(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")

        # When K8s is disabled, return empty skills
        if not settings.k8s_enabled:
            return templates.TemplateResponse(
                "partials/skills_panel.html",
                {"request": request, "agent_id": agent_id, "skills": []},
            )

        status_code, content, _ = await _forward_runtime(
            user=user,
            agent=agent,
            method="GET",
            subpath="api/skills",
            query_items=[],
            body=None,
        )

        if status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Runtime error: {content.decode('utf-8', errors='ignore')}")

        payload = json.loads(content.decode("utf-8"))
        return templates.TemplateResponse(
            "partials/skills_panel.html",
            {
                "request": request,
                "skills": payload.get("skills") or [],
            },
        )
    finally:
        db.close()


@router.get("/app/agents/{agent_id}/usage/panel")
async def app_agent_usage_panel(request: Request, agent_id: str):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    days = (request.query_params.get("days") or "30").strip()

    db = SessionLocal()
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_access(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")

        # When K8s is disabled, return empty usage
        if not settings.k8s_enabled:
            return templates.TemplateResponse(
                "partials/usage_panel.html",
                {"request": request, "agent_id": agent_id, "usage": [], "total_messages": 0, "total_cost": 0},
            )

        status_code, content, _ = await _forward_runtime(
            user=user,
            agent=agent,
            method="GET",
            subpath="api/usage",
            query_items=[("days", days)],
            body=None,
        )

        if status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Runtime error: {content.decode('utf-8', errors='ignore')}")

        payload = json.loads(content.decode("utf-8"))
        return templates.TemplateResponse(
            "partials/usage_panel.html",
            {
                "request": request,
                "usage": payload if isinstance(payload, dict) else {},
            },
        )
    finally:
        db.close()




@router.get("/app/agents/{agent_id}/files/panel")
async def app_agent_files_panel(request: Request, agent_id: str):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    db = SessionLocal()
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_access(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")

        # When K8s is disabled, return empty files
        if not settings.k8s_enabled:
            return templates.TemplateResponse(
                "partials/files_panel.html",
                {"request": request, "agent_id": agent_id, "files": [], "path": "/"},
            )

        status_code, content, _ = await _forward_runtime(
            user=user,
            agent=agent,
            method="GET",
            subpath="api/files/list",
            query_items=[],
            body=None,
        )

        if status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Runtime error: {content.decode('utf-8', errors='ignore')}")

        payload = json.loads(content.decode("utf-8"))
        return templates.TemplateResponse(
            "partials/files_panel.html",
            {
                "request": request,
                "files": payload.get("files") or [],
            },
        )
    finally:
        db.close()


@router.post("/a/{agent_id}/api/files/upload")
async def agent_files_upload(agent_id: str, request: Request):
    """Proxy file upload to EFP agent"""
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    db = SessionLocal()
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_access(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")

        # Read the multipart form data
        form = await request.form()
        file_field = form.get("file")
        if not file_field:
            raise HTTPException(status_code=400, detail="No file provided")

        # Read file content
        content = await file_field.read()
        
        # Limit file size to 10MB to prevent memory issues
        MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail=f"File too large. Maximum size is 10MB.")
        
        # Prepare files for upload
        files = {"file": (file_field.filename, content, file_field.content_type)}
        
        status_code, content, content_type = await _forward_runtime_multipart(
            user=user,
            agent=agent,
            method="POST",
            subpath="api/files/upload",
            query_items=[],
            files=files,
        )

        if status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Upload failed: {content.decode('utf-8', errors='ignore')}")

        return Response(content=content, media_type=content_type, status_code=status_code)
    finally:
        db.close()


@router.get("/a/{agent_id}/api/files/{file_id}/preview")
async def agent_files_preview(request: Request, agent_id: str, file_id: str, max_chars: int = 5000):
    """Proxy file preview to EFP agent"""
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    
    # Validate and cap max_chars
    max_chars = max(0, min(max_chars, 20000))  # Clamp between 0 and 20000

    db = SessionLocal()
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_access(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")

        # Use proxy_service.forward for consistent proxy behavior
        status_code, content, content_type = await _forward_runtime(
            user=user,
            agent=agent,
            method="GET",
            subpath=f"api/files/{file_id}/preview",
            query_items=[("max_chars", str(max_chars))],
            body=None,
        )
        
        if status_code >= 400:
            raise HTTPException(status_code=502, detail="Preview failed")
        
        return Response(content=content, media_type=content_type, status_code=status_code)
    finally:
        db.close()


@router.get("/a/{agent_id}/api/files/download")
async def agent_files_download(agent_id: str, request: Request, path: str = "", paths: Optional[List[str]] = Query(default=None)):
    """Proxy download file request to agent."""
    # Support both 'path' and 'paths' parameter (frontend uses 'paths')
    # paths can be a list for multiple files
    if paths is None:
        # Fallback to single 'path' param
        file_paths = [path] if path else []
    else:
        file_paths = paths
    
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    db = SessionLocal()
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_access(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")

        # Use proxy_service.forward for consistent proxy behavior (frontend uses 'paths')
        query_items = [("paths", p) for p in file_paths]
        status_code, content, content_type = await _forward_runtime(
            user=user,
            agent=agent,
            method="GET",
            subpath="api/files/download",
            query_items=query_items,
            body=None,
        )
        
        if status_code >= 400:
            raise HTTPException(status_code=502, detail="Download failed")
        
        # Extract filename from path (use first for single, zip for multiple)
        if len(file_paths) > 1:
            filename = "files.zip"
        elif content_type == 'application/zip':
            # Agent already determined it's a ZIP (e.g., folder download)
            filename = file_paths[0].split("/")[-1] + ".zip" if file_paths else "download.zip"
        else:
            filename = file_paths[0].split("/")[-1] if file_paths else "download"
        
        return Response(
            content=content, 
            media_type=content_type, 
            status_code=status_code,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    finally:
        db.close()


@router.get("/app/agents/{agent_id}/thinking/panel")
async def app_agent_thinking_panel(request: Request, agent_id: str, session_id: str = ""):
    """Backend-rendered thinking process panel"""
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    if not session_id:
        return templates.TemplateResponse(
            "partials/thinking_process_panel.html",
            {"request": request, "agent_id": agent_id, "session_id": "", "chatlog": None, "error": "No session selected"},
        )

    db = SessionLocal()
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_access(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")

        if not settings.k8s_enabled:
            return templates.TemplateResponse(
                "partials/thinking_process_panel.html",
                {"request": request, "agent_id": agent_id, "session_id": session_id, "chatlog": None, "error": "Agent not running"},
            )

        status_code, content, _ = await _forward_runtime(
            user=user,
            agent=agent,
            method="GET",
            subpath=f"api/sessions/{session_id}/chatlog",
            query_items=[],
            body=None,
        )

        if status_code >= 400:
            return templates.TemplateResponse(
                "partials/thinking_process_panel.html",
                {"request": request, "agent_id": agent_id, "session_id": session_id, "chatlog": None, "error": f"Error: {status_code}"},
            )

        chatlog = json.loads(content.decode("utf-8"))
        return templates.TemplateResponse(
            "partials/thinking_process_panel.html",
            {"request": request, "agent_id": agent_id, "session_id": session_id, "chatlog": chatlog, "error": None},
        )
    finally:
        db.close()


@router.get("/app/agents/{agent_id}/settings/panel")
async def app_agent_settings_panel(request: Request, agent_id: str):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    db = SessionLocal()
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_access(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")

        # When K8s is disabled, return empty config
        if not settings.k8s_enabled:
            # Return empty config for agents without runtime
            view_data = _settings_view_payload({})
            return templates.TemplateResponse(
                "partials/settings_panel.html",
                {
                    "request": request,
                    "agent_id": agent_id,
                    "status_type": "",
                    "status_message": "",
                    **view_data,
                },
            )

        status_code, content, _ = await _forward_runtime(
            user=user,
            agent=agent,
            method="GET",
            subpath="api/config",
            query_items=[],
            body=None,
        )

        if status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Runtime error: {content.decode('utf-8', errors='ignore')}")

        payload = json.loads(content.decode("utf-8"))
        config_data = payload.get("config") or {}
        view_data = _settings_view_payload(config_data)
        return templates.TemplateResponse(
            "partials/settings_panel.html",
            {
                "request": request,
                "agent_id": agent_id,
                "status_type": "",
                "status_message": "",
                **view_data,
            },
        )
    finally:
        db.close()


@router.post("/app/agents/{agent_id}/settings/save")
async def app_agent_settings_save(request: Request, agent_id: str):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    
    # Check if K8s is enabled before saving settings
    if not settings.k8s_enabled:
        raise HTTPException(status_code=400, detail="Settings cannot be saved: Kubernetes integration is disabled")

    form = await request.form()

    original_config_json = (form.get("original_config_json") or "").strip()
    try:
        config_payload = json.loads(original_config_json) if original_config_json else {}
    except Exception:
        config_payload = {}
    if not isinstance(config_payload, dict):
        config_payload = {}

    config_payload, merge_error = _settings_merge_payload(config_payload, form)
    if merge_error:
        return _settings_error_response(request, agent_id, config_payload, merge_error)

    db = SessionLocal()
    status_type = "success"
    status_message = "Settings saved. Runtime configuration reloaded."
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_write(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")

        status_code, content, _ = await _forward_runtime(
            user=user,
            agent=agent,
            method="POST",
            subpath="api/config/save",
            query_items=[],
            body=json.dumps(config_payload).encode("utf-8"),
            headers={"content-type": "application/json"},
        )

        if status_code >= 400:
            status_type = "error"
            status_message = f"Save failed: {content.decode('utf-8', errors='ignore')}"

        read_status, read_content, _ = await _forward_runtime(
            user=user,
            agent=agent,
            method="GET",
            subpath="api/config",
            query_items=[],
            body=None,
        )

        config_data = config_payload
        if read_status < 400:
            payload = json.loads(read_content.decode("utf-8"))
            config_data = payload.get("config") or config_payload
        elif status_type != "error":
            status_type = "error"
            status_message = f"Saved but failed to reload panel: {read_content.decode('utf-8', errors='ignore')}"

        view_data = _settings_view_payload(config_data)
        return templates.TemplateResponse(
            "partials/settings_panel.html",
            {
                "request": request,
                "agent_id": agent_id,
                "status_type": status_type,
                "status_message": status_message,
                **view_data,
            },
        )
    finally:
        db.close()


@router.post("/app/chat/send")
async def app_chat_send(request: Request):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    form = await request.form()
    agent_id = (form.get("agent_id") or "").strip()
    message = (form.get("message") or "").strip()
    session_id = (form.get("session_id") or "").strip() or None
    attachments_str = (form.get("attachments") or "").strip()

    if not agent_id:
        raise HTTPException(status_code=400, detail="Agent not selected")
    if not message:
        raise HTTPException(status_code=400, detail="Message required")

    # Parse attachments from JSON
    attachments = []
    if attachments_str:
        try:
            parsed = json.loads(attachments_str)
            if isinstance(parsed, list):
                attachments = parsed
        except json.JSONDecodeError:
            pass  # Invalid JSON, ignore attachments

    db = SessionLocal()
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_access(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")
        if agent.status != "running":
            raise HTTPException(status_code=409, detail="Agent not running")

        metadata = runtime_execution_context_service.build_runtime_metadata(db, agent)
        payload = {
            "message": message,
            "metadata": metadata,
        }
        if session_id:
            payload["session_id"] = session_id
        if attachments:
            payload["attachments"] = attachments

        try:
            extra_headers = build_portal_execution_headers(user)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="PORTAL_INTERNAL_API_KEY is not configured",
            ) from exc

        status_code, content, _ = await proxy_service.forward(
            agent=agent,
            method="POST",
            subpath="api/chat",
            query_items=[],
            body=json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json"},
            extra_headers=extra_headers,
        )

        if status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Runtime error: {content.decode('utf-8', errors='ignore')}")

        data = json.loads(content.decode("utf-8"))
        
        # Forward events to frontend for Thinking Process
        events = data.get("events", [])
        
        return templates.TemplateResponse(
            "partials/chat_response.html",
            {
                "request": request,
                "user_message": message,
                "assistant_message": data.get("response") or "(empty response)",
                "session_id": data.get("session_id") or session_id or "",
                "agent_name": agent.name if agent else "Assistant",
                "user_message_id": data.get("user_message_id") or "",
                "events": events,
            },
        )
    finally:
        db.close()


@router.get("/app/agent-groups/{group_id}/task-board/panel")
async def app_group_task_board_panel(request: Request, group_id: str):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    db = SessionLocal()
    try:
        from app.services.agent_group_service import AgentGroupService, AgentGroupServiceError

        service = AgentGroupService(db)
        try:
            board = service.get_group_task_board(group_id, user=user, apply_visibility=True)
        except AgentGroupServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

        return templates.TemplateResponse(
            "partials/group_task_board.html",
            {
                "request": request,
                "group_id": board["group_id"],
                "leader_agent_id": board["leader_agent_id"],
                "summary": board["summary"],
                "items": board["items"],
            },
        )
    finally:
        db.close()


@router.get("/app/agent-groups/{group_id}/shared-contexts/panel")
async def app_group_shared_context_list_panel(request: Request, group_id: str):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    db = SessionLocal()
    try:
        from app.services.agent_group_service import AgentGroupService, AgentGroupServiceError

        service = AgentGroupService(db)
        try:
            snapshots = service.list_group_shared_context_snapshots(group_id, user=user)
        except AgentGroupServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

        return templates.TemplateResponse(
            "partials/group_shared_context_list.html",
            {
                "request": request,
                "group_id": group_id,
                "items": snapshots,
            },
        )
    finally:
        db.close()


@router.get("/app/agent-groups/{group_id}/shared-contexts/{context_ref}/panel")
async def app_group_shared_context_detail_panel(request: Request, group_id: str, context_ref: str):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    db = SessionLocal()
    try:
        from app.services.agent_group_service import AgentGroupService, AgentGroupServiceError

        service = AgentGroupService(db)
        try:
            snapshot = service.get_group_shared_context_snapshot(group_id, context_ref, user=user)
        except AgentGroupServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

        return templates.TemplateResponse(
            "partials/group_shared_context_detail.html",
            {
                "request": request,
                "group_id": group_id,
                "item": snapshot,
            },
        )
    finally:
        db.close()
