import markupsafe
import app.logger  # Ensure logging is configured (intentional side-effect import)  # noqa: F401
import json
import logging
from datetime import datetime
from urllib.parse import urlencode
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request, Response, status, Query
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.db import SessionLocal
from app.repositories.agent_repo import AgentRepository
from app.repositories.agent_group_repo import AgentGroupRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.repositories.agent_identity_binding_repo import AgentIdentityBindingRepository
from app.repositories.agent_session_metadata_repo import AgentSessionMetadataRepository
from app.repositories.user_repo import UserRepository
from app.repositories.runtime_profile_repo import RuntimeProfileRepository
from app.schemas.requirement_bundle import BundleRef, RequirementBundleCreateForm
from app.schemas.runtime_profile import (
    dump_runtime_profile_config_json,
    normalize_runtime_profile_llm_tools,
    parse_runtime_profile_config_json,
    sanitize_runtime_profile_config_dict,
)
from app.services.bundle_template_registry import list_bundle_templates, require_bundle_template
from app.services.requirement_bundle_github_service import (
    RequirementBundleGithubService,
    RequirementBundleGithubServiceError,
)
from app.services.auth_service import parse_session_token
from app.services.proxy_service import ProxyService, build_portal_agent_identity_headers
from app.services.runtime_execution_context_service import RuntimeExecutionContextService
from app.services.task_dispatcher import TaskDispatcherService
from app.services.agent_group_service import AgentGroupService, AgentGroupServiceError
from app.services.runtime_profile_sync_service import RuntimeProfileSyncService
from app.services.runtime_profile_service import RuntimeProfileService
from app.services.runtime_profile_test_service import RuntimeProfileTestService
from app.services.session_context_preview import merge_runtime_sessions_with_metadata
from app.services.thinking_process_view import build_thinking_process_view
from app.utils.runtime_proxy_query import _filter_runtime_file_upload_query_items
from app.log_context import bind_log_context, get_log_context, reset_log_context
from app.chat_payloads import normalize_assistant_chat_payload

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)

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
runtime_profile_sync_service = RuntimeProfileSyncService(proxy_service=proxy_service)
runtime_profile_test_service = RuntimeProfileTestService()
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


def _portal_extra_headers(user, agent) -> dict[str, str]:
    return build_portal_agent_identity_headers(user, agent)


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


def _status_tone_from_value(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {"done", "completed", "ready", "success"}:
        return "success"
    if normalized in {"queued", "running", "draft", "in_progress"}:
        return "warning"
    if normalized in {"failed", "blocked", "missing", "error"}:
        return "error"
    if normalized in {"", "unknown", "none", "null"}:
        return "neutral"
    return "info"


def _has_thinking_view_data(view: dict) -> bool:
    if not isinstance(view, dict):
        return False
    if "has_data" in view:
        return bool(view.get("has_data"))
    context = view.get("context") if isinstance(view.get("context"), dict) else {}
    budget = view.get("budget") if isinstance(view.get("budget"), dict) else {}
    active_skill = view.get("active_skill") if isinstance(view.get("active_skill"), dict) else {}
    fallback = view.get("fallback") if isinstance(view.get("fallback"), dict) else {}
    return bool(
        view.get("events")
        or budget
        or active_skill.get("name")
        or any(context.get(key) for key in ("objective", "summary", "current_state", "next_step"))
        or fallback.get("latest_event_type")
        or fallback.get("latest_event_state")
        or fallback.get("last_execution_id")
    )


def _safe_json_object(raw: str | None) -> dict | list | None:
    if raw is None or not str(raw).strip():
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def _pretty_json_text(raw: str | None) -> str:
    if raw is None or not str(raw).strip():
        return "{}"
    parsed = _safe_json_object(raw)
    if parsed is None:
        return raw
    return json.dumps(parsed, indent=2, ensure_ascii=False)


def _parse_json_textarea(raw: str | None, *, field_name: str) -> tuple[str | None, str | None]:
    cleaned = (raw or "").strip()
    if not cleaned:
        return None, None
    try:
        parsed = json.loads(cleaned)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, f"{field_name} must be valid JSON"
    if not isinstance(parsed, dict):
        return None, f"{field_name} must be a JSON object"
    return json.dumps(parsed, ensure_ascii=False), None


def _parse_form_bool(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "on", "yes"}

def _append_error_code(parts: list[str], code, error_type) -> None:
    code_text = str(code or "").strip()
    if code_text:
        parts.append(f"code={code_text}")
    error_type_text = str(error_type or "").strip()
    if error_type_text:
        parts.append(f"error_type={error_type_text}")


def _append_allowlisted_error_details(parts: list[str], details) -> None:
    if not isinstance(details, dict):
        return
    allowlist = (
        "legacy_max_chat_output_chars_ignored",
        "configured_max_chat_output_chars",
        "legacy_max_tokens_ignored",
        "configured_max_tokens",
        "effective_max_tokens",
        "budget_max_chat_output_chars_ignored",
        "configured_budget_max_chat_output_chars",
        "arg_max_chat_output_chars_ignored",
        "configured_arg_max_chat_output_chars",
        "file_context_budget_status",
        "file_context_estimated_tokens",
        "file_context_threshold_source",
        "output_boundary_source",
        "max_context_window_tokens",
        "max_prompt_tokens",
        "max_output_tokens",
        "max_chat_output_tokens",
        "max_chat_output_chars",
        "chars_per_token_estimate",
    )
    for key in allowlist:
        if key not in details:
            continue
        value = details.get(key)
        if value is None and key != "max_chat_output_chars":
            continue
        parts.append(f"{key}={value}")


def _merge_error_details(top_level_details, nested_details) -> dict:
    merged: dict = {}
    if isinstance(top_level_details, dict):
        merged.update(top_level_details)
    if isinstance(nested_details, dict):
        merged.update(nested_details)
    return merged


def _normalize_runtime_error_detail(content: bytes) -> str:
    decoded = content.decode("utf-8", errors="replace")
    preview = decoded[:1000]
    try:
        parsed = json.loads(decoded)
    except (TypeError, ValueError, json.JSONDecodeError):
        return f"Runtime error: {preview}"

    if not isinstance(parsed, dict):
        return f"Runtime error: {preview}"

    error = parsed.get("error")
    parts: list[str] = []

    if isinstance(error, str):
        message = error.strip()
        if message:
            parts.append(message)
        _append_error_code(parts, parsed.get("code"), parsed.get("error_type"))
        _append_allowlisted_error_details(parts, parsed.get("details"))
    elif isinstance(error, dict):
        message = str(error.get("message") or "").strip()
        if message:
            parts.append(message)
        _append_error_code(parts, error.get("code") or parsed.get("code"), error.get("error_type") or parsed.get("error_type"))
        details = _merge_error_details(parsed.get("details"), error.get("details"))
        _append_allowlisted_error_details(parts, details)
    else:
        message = str(parsed.get("message") or "").strip()
        if message:
            parts.append(message)
        _append_error_code(parts, parsed.get("code"), parsed.get("error_type"))
        _append_allowlisted_error_details(parts, parsed.get("details"))

    if not parts:
        parts.append(preview)
    return f"Runtime error: {' '.join(parts)}"


def _build_settings_panel_context(*, request: Request, agent_id: str, base_context: dict, db, triggered_work_state: dict | None = None) -> dict:
    bindings = AgentIdentityBindingRepository(db).list_by_agent(agent_id)
    triggered_state = triggered_work_state or {}
    context = {
        **base_context,
        "request": request,
        "agent_id": agent_id,
        "identity_bindings": bindings,
        "triggered_work_error": triggered_state.get("error", ""),
        "triggered_work_success": triggered_state.get("success", ""),
        "binding_form": triggered_state.get("binding_form", {}),
    }
    return context


def _short_sha(value: str | None) -> str:
    cleaned = (value or "").strip()
    return cleaned[:7] if cleaned else "-"


def _format_duration_label(started_at, finished_at) -> str:
    if not started_at:
        return "-"
    end_time = finished_at or datetime.utcnow()
    total_seconds = max(0, int((end_time - started_at).total_seconds()))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def _bundle_action_output_artifact_key(template_id: str, action_id: str) -> str | None:
    mapping = {
        ("requirement.v1", "collect_requirements"): "requirements",
        ("requirement.v1", "design_test_cases"): "test_cases",
        ("research.v1", "collect_research_notes"): "research_notes",
        ("development.v1", "generate_implementation_plan"): "implementation_plan",
        ("operations.v1", "generate_runbook"): "runbook",
    }
    return mapping.get((template_id, action_id))


def _humanize_artifact_label(value: str | None) -> str:
    cleaned = (value or "").strip()
    return cleaned.replace("_", " ").title() if cleaned else "Artifact"


def _build_bundle_detail_view_model(bundle_detail, bundle_templates, agents, *, form_state=None) -> dict:
    _ = agents
    manifest = bundle_detail.manifest if isinstance(bundle_detail.manifest, dict) else {}
    scope = manifest.get("scope") if isinstance(manifest.get("scope"), dict) else {}
    bundle_path = (bundle_detail.bundle_ref.path or "").strip()
    fallback_title = bundle_path.split("/")[-1] if bundle_path else "Bundle"
    bundle_id = manifest.get("bundle_id") or fallback_title or "-"
    title = manifest.get("title") or bundle_id or fallback_title or "Bundle"
    status_label = manifest.get("status") or "unknown"
    template_id = bundle_detail.template_id
    repo = bundle_detail.bundle_ref.repo or "-"
    branch = bundle_detail.bundle_ref.branch or "-"
    github_url = f"https://github.com/{repo}/tree/{branch}/{bundle_detail.bundle_ref.path}"

    artifacts = []
    artifact_exists_map: dict[str, bool] = {}
    for artifact in (bundle_detail.artifacts or []):
        exists = bool(artifact.exists)
        artifact_exists_map[artifact.artifact_key] = exists
        artifacts.append(
            {
                "artifact_key": artifact.artifact_key,
                "label": _humanize_artifact_label(artifact.artifact_key),
                "file_path": artifact.file_path or "-",
                "exists": exists,
                "status_label": "Ready" if exists else "Missing",
                "status_tone": "success" if exists else "error",
                "github_url": f"https://github.com/{repo}/blob/{branch}/{bundle_detail.bundle_ref.path}/{artifact.file_path}"
                if artifact.file_path
                else None,
            }
        )

    form_state = form_state or {}
    template = next((item for item in bundle_templates if item.template_id == template_id), None)
    actions = []
    if template:
        for action in template.actions:
            missing_required = [key for key in action.required_artifacts if not artifact_exists_map.get(key)]
            is_blocked = bool(missing_required)
            output_artifact = _bundle_action_output_artifact_key(template_id, action.action_id)
            is_complete = bool(output_artifact and artifact_exists_map.get(output_artifact))
            actions.append(
                {
                    "action_id": action.action_id,
                    "label": action.label,
                    "description": action.description,
                    "requires_sources": bool(action.requires_sources),
                    "required_artifacts": list(action.required_artifacts),
                    "missing_required": missing_required,
                    "is_blocked": is_blocked,
                    "is_complete": is_complete,
                    "is_recommended": False,
                    "status_label": "Completed" if is_complete else ("Blocked" if is_blocked else "Ready"),
                    "status_tone": "success" if is_complete else ("error" if is_blocked else "info"),
                    "status_reason": (action.missing_artifact_message or f"Required artifacts missing: {', '.join(missing_required)}")
                    if is_blocked
                    else "",
                    "expanded": False,
                    "selected_agent_id": form_state.get("action_agent_id") or "",
                    "jira_sources": form_state.get("jira_sources") or "",
                    "confluence_sources": form_state.get("confluence_sources") or "",
                    "github_doc_sources": form_state.get("github_doc_sources") or "",
                    "figma_sources": form_state.get("figma_sources") or "",
                }
            )

    recommended_action_id = None
    for action in actions:
        if not action["is_complete"] and not action["is_blocked"]:
            recommended_action_id = action["action_id"]
            break
    expanded_action_id = form_state.get("action_id") or ""

    for action in actions:
        action["is_recommended"] = action["action_id"] == recommended_action_id
        action["expanded"] = action["is_recommended"] or action["action_id"] == expanded_action_id

    recommended_action = next((item for item in actions if item["is_recommended"]), None)
    other_actions = [item for item in actions if not item["is_recommended"]]

    return {
        "title": title,
        "subtitle": f"{bundle_id} · {bundle_path or '-'}",
        "bundle_id": bundle_id,
        "status_label": status_label,
        "status_tone": _status_tone_from_value(status_label),
        "domain": scope.get("domain") or "-",
        "template_label": bundle_detail.template_label,
        "template_id": template_id,
        "repo": repo,
        "branch": branch,
        "path": bundle_path or "-",
        "github_url": github_url,
        "last_commit_short": _short_sha(bundle_detail.last_commit_sha),
        "last_commit_full": bundle_detail.last_commit_sha or "-",
        "artifact_ready_count": sum(1 for item in artifacts if item["exists"]),
        "artifact_total_count": len(artifacts),
        "artifacts": artifacts,
        "actions": actions,
        "recommended_action": recommended_action,
        "other_actions": other_actions,
    }


def _build_task_detail_view_model(task) -> dict:
    input_payload = _safe_json_object(getattr(task, "input_payload_json", None))
    input_obj = input_payload if isinstance(input_payload, dict) else {}
    bundle_ref = input_obj.get("bundle_ref") if isinstance(input_obj.get("bundle_ref"), dict) else {}
    manifest_ref = input_obj.get("manifest_ref") if isinstance(input_obj.get("manifest_ref"), dict) else {}
    sources = input_obj.get("sources") if isinstance(input_obj.get("sources"), dict) else {}
    template_id = input_obj.get("template_id") if isinstance(input_obj.get("template_id"), str) else ""
    action_id = input_obj.get("action_id") if isinstance(input_obj.get("action_id"), str) else ""

    action_label = ""
    template_label = template_id or "-"
    template = None
    if getattr(task, "task_type", "") == "bundle_action_task" and template_id:
        try:
            template = require_bundle_template(template_id)
        except ValueError:
            template = None
        if template is not None:
            template_label = template.display_name or template_id
            action = next((item for item in template.actions if item.action_id == action_id), None)
            if action is not None:
                action_label = action.label
    if not action_label and action_id:
        action_label = action_id.replace("_", " ").title()

    bundle_path = str(bundle_ref.get("path") or manifest_ref.get("path") or "").strip()
    status_label = getattr(task, "status", None) or "unknown"
    is_active = status_label in {"queued", "running"}
    source_counts = {
        "jira": len(sources.get("jira") or []),
        "confluence": len(sources.get("confluence") or []),
        "github_docs": len(sources.get("github_docs") or []),
        "figma": len(sources.get("figma") or []),
    }

    bundle_open_url = None
    if bundle_ref.get("repo") and bundle_ref.get("path") and bundle_ref.get("branch"):
        bundle_open_url = f"/app/requirement-bundles/open?{urlencode({'repo': bundle_ref.get('repo'), 'path': bundle_ref.get('path'), 'branch': bundle_ref.get('branch')})}"

    context_items = [
        ("Task Type", getattr(task, "task_type", None) or "-"),
        ("Task Family", getattr(task, "task_family", None) or "-"),
        ("Provider", getattr(task, "provider", None) or "-"),
        ("Trigger", getattr(task, "trigger", None) or "-"),
        ("Template", template_label),
        ("Action", action_label or (action_id or "-")),
        ("Bundle Path", bundle_path or "-"),
        ("Repo", bundle_ref.get("repo") or "-"),
        ("Branch", bundle_ref.get("branch") or "-"),
        ("Jira Sources", str(source_counts["jira"])),
        ("Confluence Sources", str(source_counts["confluence"])),
        ("GitHub Docs Sources", str(source_counts["github_docs"])),
        ("Figma Sources", str(source_counts["figma"])),
    ]
    metadata_items = [
        ("Task ID", getattr(task, "id", "-")),
        ("Bundle ID", getattr(task, "bundle_id", None) or "-"),
        ("Version Key", getattr(task, "version_key", None) or "-"),
        ("Dedupe Key", getattr(task, "dedupe_key", None) or "-"),
        ("Runtime Request ID", getattr(task, "runtime_request_id", None) or "-"),
        ("Group ID", getattr(task, "group_id", None) or "-"),
        ("Owner User ID", getattr(task, "owner_user_id", None) or "-"),
        ("Created By User ID", getattr(task, "created_by_user_id", None) or "-"),
        ("Updated At", getattr(task, "updated_at", None) or "-"),
    ]

    return {
        "display_title": action_label or (getattr(task, "task_type", None) or "Task Detail").replace("_", " ").title(),
        "display_subtitle": bundle_path.split("/")[-1] if bundle_path else (getattr(task, "task_type", None) or "Task"),
        "status_label": status_label,
        "status_tone": _status_tone_from_value(status_label),
        "is_active": is_active,
        "summary_text": getattr(task, "summary", None) or "",
        "error_text": getattr(task, "error_message", None) or "",
        "duration_label": _format_duration_label(getattr(task, "started_at", None), getattr(task, "finished_at", None)),
        "assignee_agent_id": getattr(task, "assignee_agent_id", None) or "-",
        "group_id": getattr(task, "group_id", None) or "-",
        "owner_user_id": getattr(task, "owner_user_id", None) or "-",
        "created_by_user_id": getattr(task, "created_by_user_id", None) or "-",
        "runtime_request_id": getattr(task, "runtime_request_id", None) or "-",
        "task_type": getattr(task, "task_type", None) or "-",
        "task_family": getattr(task, "task_family", None) or "-",
        "provider": getattr(task, "provider", None) or "-",
        "trigger": getattr(task, "trigger", None) or "-",
        "bundle_id": getattr(task, "bundle_id", None) or "-",
        "version_key": getattr(task, "version_key", None) or "-",
        "dedupe_key": getattr(task, "dedupe_key", None) or "-",
        "source": getattr(task, "source", None) or "-",
        "created_at": getattr(task, "created_at", None) or "-",
        "started_at": getattr(task, "started_at", None) or "-",
        "finished_at": getattr(task, "finished_at", None) or "-",
        "updated_at": getattr(task, "updated_at", None) or "-",
        "retry_count": getattr(task, "retry_count", 0) or 0,
        "context_items": context_items,
        "metadata_items": metadata_items,
        "input_payload_pretty": _pretty_json_text(getattr(task, "input_payload_json", None)),
        "result_payload_pretty": _pretty_json_text(getattr(task, "result_payload_json", None)),
        "bundle_open_url": bundle_open_url,
    }


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
        extra_headers=_portal_extra_headers(user, agent),
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
        extra_headers=_portal_extra_headers(user, agent),
    )


def _settings_view_payload(raw_config_data: dict, effective_config_data: dict | None = None) -> dict:
    raw_config = dict(raw_config_data or {})
    raw_config.pop("ssh", None)
    effective_config = dict(effective_config_data or RuntimeProfileService.merge_with_managed_defaults(raw_config))
    effective_config.pop("ssh", None)

    llm = effective_config.get("llm") if isinstance(effective_config.get("llm"), dict) else {}
    raw_llm = raw_config.get("llm") if isinstance(raw_config.get("llm"), dict) else {}
    jira = effective_config.get("jira") if isinstance(effective_config.get("jira"), dict) else {}
    confluence = effective_config.get("confluence") if isinstance(effective_config.get("confluence"), dict) else {}
    jira_instances = jira.get("instances") if isinstance(jira.get("instances"), list) else []
    confluence_instances = confluence.get("instances") if isinstance(confluence.get("instances"), list) else []
    llm_tools_mode, llm_tools_patterns = _settings_llm_tools_view(llm)
    raw_llm_tools_mode, raw_llm_tools_patterns = _settings_llm_tools_view(raw_llm)
    raw_github = raw_config.get("github") if isinstance(raw_config.get("github"), dict) else {}
    raw_git = raw_config.get("git") if isinstance(raw_config.get("git"), dict) else {}
    raw_proxy = raw_config.get("proxy") if isinstance(raw_config.get("proxy"), dict) else {}

    return {
        "config": effective_config,
        "raw_config": raw_config,
        "llm": llm,
        "raw_llm": raw_llm,
        "effective_llm": llm,
        "llm_tools_mode": llm_tools_mode,
        "llm_tools_patterns": llm_tools_patterns,
        "raw_llm_tools_mode": raw_llm_tools_mode,
        "raw_llm_tools_patterns": raw_llm_tools_patterns,
        "jira": jira,
        "jira_instances": jira_instances,
        "confluence": confluence,
        "confluence_instances": confluence_instances,
        "github": effective_config.get("github") if isinstance(effective_config.get("github"), dict) else {},
        "raw_github": raw_github,
        "git": effective_config.get("git") if isinstance(effective_config.get("git"), dict) else {},
        "raw_git": raw_git,
        "proxy": effective_config.get("proxy") if isinstance(effective_config.get("proxy"), dict) else {},
        "raw_proxy": raw_proxy,
        "debug": effective_config.get("debug") if isinstance(effective_config.get("debug"), dict) else {},
    }


def _settings_llm_tools_view(llm: dict) -> tuple[str, list[str]]:
    if not isinstance(llm, dict) or "tools" not in llm:
        return "inherit", []
    normalized = normalize_runtime_profile_llm_tools(llm.get("tools"))
    if normalized == ["*"]:
        return "all", []
    if not normalized:
        return "none", []
    return "custom", normalized


def _settings_error_response(
    request: Request,
    db,
    agent_id: str,
    config_payload: dict,
    message: str,
    *,
    profile_name: str | None = None,
    profile_revision: int | None = None,
    profile_bound_agent_count: int = 0,
    read_only: bool = False,
):
    base = config_payload if isinstance(config_payload, dict) else {}
    view_data = _settings_view_payload(base, RuntimeProfileService.merge_with_managed_defaults(base))
    summary = _settings_settings_panel_summary(db, agent_id)
    return templates.TemplateResponse(
        "partials/settings_panel.html",
        {
            "request": request,
            "agent_id": agent_id,
            "status_type": "error",
            "status_message": message,
            "profile_missing_message": "",
            "profile_name": profile_name,
            "profile_revision": profile_revision,
            "profile_bound_agent_count": profile_bound_agent_count,
            "read_only": read_only,
            **summary,
            **view_data,
        },
    )


def _settings_external_identity_summary(db, agent_id: str) -> dict[str, int]:
    bindings = AgentIdentityBindingRepository(db).list_by_agent(agent_id)
    return {
        "binding_total_count": len(bindings),
        "binding_enabled_count": sum(1 for item in bindings if item.enabled),
    }


def _format_utc_timestamp(value) -> str | None:
    if not value:
        return None
    return f"{value.strftime('%Y-%m-%d %H:%M')} UTC"


def _is_external_trigger_task(task) -> bool:
    source = (task.source or "").strip().lower()
    task_type = (task.task_type or "").strip().lower()
    external_sources = {"github", "jira", "confluence", "cron", "internal", "external_event"}
    external_task_types = {"github_review_task", "jira_workflow_review_task"}
    return source in external_sources or task_type in external_task_types


def _task_activity_time(task):
    return task.finished_at or task.started_at or task.updated_at or task.created_at


def _settings_automation_activity_summary(db, agent_id: str) -> dict[str, str]:
    tasks = [task for task in AgentTaskRepository(db).list_by_agent(agent_id) if _is_external_trigger_task(task)]
    if not tasks:
        return {
            "last_triggered_task_at_text": "No automation activity yet.",
            "last_external_event_task_accepted_at_text": "No automation activity yet.",
            "recent_failed_trigger_summary": "No recent failed triggers.",
        }

    latest_task = max(tasks, key=lambda task: _task_activity_time(task) or datetime.min)
    latest_accepted_task = max(tasks, key=lambda task: task.created_at or datetime.min)

    last_triggered_text = _format_utc_timestamp(_task_activity_time(latest_task)) or "No automation activity yet."
    last_accepted_text = _format_utc_timestamp(latest_accepted_task.created_at) or "No automation activity yet."

    failed_tasks = [task for task in tasks if (task.status or "").strip().lower() == "failed" or bool((task.error_message or "").strip())]
    recent_failed_trigger_summary = "No recent failed triggers."
    if failed_tasks:
        latest_failed = max(failed_tasks, key=lambda task: _task_activity_time(task) or datetime.min)
        summary_text = (
            (latest_failed.error_message or "").strip()
            or (latest_failed.summary or "").strip()
            or f"{latest_failed.task_type} ({latest_failed.status})"
        )
        recent_failed_trigger_summary = summary_text

    return {
        "last_triggered_task_at_text": last_triggered_text,
        "last_external_event_task_accepted_at_text": last_accepted_text,
        "recent_failed_trigger_summary": recent_failed_trigger_summary,
    }


def _settings_settings_panel_summary(db, agent_id: str) -> dict:
    return {
        **_settings_external_identity_summary(db, agent_id),
        **_settings_automation_activity_summary(db, agent_id),
    }


def _runtime_profile_panel_context(
    request: Request,
    profile,
    profile_repo: RuntimeProfileRepository,
    *,
    status_type: str = "",
    status_message: str = "",
) -> dict:
    bound_count = profile_repo.count_bound_agents(profile.id)
    raw_config_data = parse_runtime_profile_config_json(profile.config_json, fallback_to_empty=True)
    config_data = RuntimeProfileService.merge_with_managed_defaults(raw_config_data)
    view_data = _settings_view_payload(raw_config_data, config_data)
    return {
        "request": request,
        "profile_id": profile.id,
        "status_type": status_type,
        "status_message": status_message,
        "profile_name": profile.name,
        "profile_description": profile.description or "",
        "profile_revision": profile.revision,
        "profile_is_default": bool(profile.is_default),
        "profile_bound_agent_count": bound_count,
        **view_data,
    }


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




def _parse_multiline_csv_list(raw: str | None) -> list[str]:
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


def _settings_parse_llm_tools_patterns(form) -> list[str]:
    count_text = (form.get("llm_tools_count") or "0").strip()
    try:
        count = max(0, int(count_text))
    except ValueError:
        count = 0

    patterns: list[str] = []
    seen_lower: set[str] = set()
    for i in range(count):
        value = (form.get(f"llm_tools_{i}_pattern") or "").strip()
        if not value:
            continue
        dedupe_key = value.lower()
        if dedupe_key in seen_lower:
            continue
        seen_lower.add(dedupe_key)
        patterns.append(value)
    return patterns


def _settings_parse_response_flow_select(form, field_name: str, allowed: set[str]) -> str | None:
    value = (form.get(field_name) or "").strip()
    if not value:
        return None
    return value if value in allowed else None


def _settings_parse_response_flow_ratio(form, field_name: str) -> tuple[float | None, str | None]:
    value = (form.get(field_name) or "").strip()
    if not value:
        return None, None
    try:
        parsed = float(value)
    except ValueError:
        return None, "Response flow complexity ratio must be a number between 0 and 1."
    if not (0 < parsed <= 1):
        return None, "Response flow complexity ratio must be a number between 0 and 1."
    return parsed, None


def _settings_parse_response_flow_min_tokens(form, field_name: str) -> tuple[int | None, str | None]:
    value = (form.get(field_name) or "").strip()
    if not value:
        return None, None
    try:
        parsed = int(value)
    except ValueError:
        return None, "Response flow complexity minimum tokens must be a positive integer."
    if parsed <= 0:
        return None, "Response flow complexity minimum tokens must be a positive integer."
    return parsed, None

def _settings_merge_payload(config_payload: dict, form) -> tuple[dict, Optional[str]]:
    def as_bool(value) -> bool:
        return str(value or "").lower() in {"1", "true", "on", "yes"}

    def is_section_touched(section: str) -> bool:
        return str(form.get(f"__touch_{section}") or "0").strip() == "1"

    config_payload = config_payload if isinstance(config_payload, dict) else {}
    config_payload.pop("ssh", None)

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
    if is_section_touched("llm"):
        provider_value = (form.get("llm_provider") or "").strip()
        model_value = (form.get("llm_model") or "").strip()
        api_key_value = (form.get("llm_api_key") or "").strip()
        if provider_value:
            llm["provider"] = provider_value
        else:
            llm.pop("provider", None)
        if model_value:
            llm["model"] = model_value
        else:
            llm.pop("model", None)
        if api_key_value:
            llm["api_key"] = api_key_value
        else:
            llm.pop("api_key", None)

        temperature_text = (form.get("llm_temperature") or "").strip()
        if "llm_temperature" in form:
            if not temperature_text:
                llm.pop("temperature", None)
            else:
                try:
                    parsed_temperature = float(temperature_text)
                except ValueError:
                    return config_payload, "Temperature must be a number between 0 and 2."
                if parsed_temperature < 0 or parsed_temperature > 2:
                    return config_payload, "Temperature must be a number between 0 and 2."
                llm["temperature"] = parsed_temperature
        
        max_tokens_text = (form.get("llm_max_tokens") or "").strip()
        if "llm_max_tokens" in form:
            if not max_tokens_text:
                llm.pop("max_tokens", None)
            else:
                try:
                    llm["max_tokens"] = int(max_tokens_text)
                except ValueError:
                    return config_payload, "Max tokens must be an integer."

        llm_tools_mode = (form.get("llm_tools_mode") or "").strip().lower()
        if llm_tools_mode == "inherit":
            llm.pop("tools", None)
        elif llm_tools_mode == "all":
            llm["tools"] = ["*"]
        elif llm_tools_mode == "none":
            llm["tools"] = []
        elif llm_tools_mode == "custom":
            llm["tools"] = _settings_parse_llm_tools_patterns(form)

        existing_response_flow = llm.get("response_flow") if isinstance(llm.get("response_flow"), dict) else {}
        response_flow = existing_response_flow.copy() if isinstance(existing_response_flow, dict) else {}

        plan_policy = _settings_parse_response_flow_select(
            form,
            "llm_response_flow_plan_policy",
            {"explicit_or_complex", "always", "never"},
        )
        staging_policy = _settings_parse_response_flow_select(
            form,
            "llm_response_flow_staging_policy",
            {"explicit_or_complex", "always", "never"},
        )
        default_skill_execution_style = _settings_parse_response_flow_select(
            form,
            "llm_response_flow_default_skill_execution_style",
            {"direct", "stepwise"},
        )
        ask_user_policy = _settings_parse_response_flow_select(
            form,
            "llm_response_flow_ask_user_policy",
            {"blocked_only", "permissive"},
        )
        active_skill_conflict_policy = _settings_parse_response_flow_select(
            form,
            "llm_response_flow_active_skill_conflict_policy",
            {"auto_switch_direct", "always_ask"},
        )

        if plan_policy is not None:
            response_flow["plan_policy"] = plan_policy
        else:
            response_flow.pop("plan_policy", None)

        if staging_policy is not None:
            response_flow["staging_policy"] = staging_policy
        else:
            response_flow.pop("staging_policy", None)

        if default_skill_execution_style is not None:
            response_flow["default_skill_execution_style"] = default_skill_execution_style
        else:
            response_flow.pop("default_skill_execution_style", None)

        if ask_user_policy is not None:
            response_flow["ask_user_policy"] = ask_user_policy
        else:
            response_flow.pop("ask_user_policy", None)

        if active_skill_conflict_policy is not None:
            response_flow["active_skill_conflict_policy"] = active_skill_conflict_policy
        else:
            response_flow.pop("active_skill_conflict_policy", None)

        complexity_prompt_budget_ratio, ratio_error = _settings_parse_response_flow_ratio(
            form, "llm_response_flow_complexity_prompt_budget_ratio"
        )
        if ratio_error:
            return config_payload, ratio_error
        if complexity_prompt_budget_ratio is not None:
            response_flow["complexity_prompt_budget_ratio"] = complexity_prompt_budget_ratio
        else:
            response_flow.pop("complexity_prompt_budget_ratio", None)

        complexity_min_request_tokens, min_tokens_error = _settings_parse_response_flow_min_tokens(
            form, "llm_response_flow_complexity_min_request_tokens"
        )
        if min_tokens_error:
            return config_payload, min_tokens_error
        if complexity_min_request_tokens is not None:
            response_flow["complexity_min_request_tokens"] = complexity_min_request_tokens
        else:
            response_flow.pop("complexity_min_request_tokens", None)

        if response_flow:
            llm["response_flow"] = response_flow
        else:
            llm.pop("response_flow", None)

        if llm:
            config_payload["llm"] = llm
        else:
            config_payload.pop("llm", None)

    if is_section_touched("jira"):
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
        jira.pop("automation", None)
        config_payload["jira"] = jira

    if is_section_touched("confluence"):
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
        confluence.pop("automation", None)
        config_payload["confluence"] = confluence

    if is_section_touched("github"):
        github_cfg = (config_payload.get("github") if isinstance(config_payload.get("github"), dict) else {}).copy()
        github_cfg["enabled"] = as_bool(form.get("github_enabled"))
        github_token_value = (form.get("github_api_token") or "").strip()
        github_base_url_value = (form.get("github_base_url") or "").strip()
        if "github_api_token" in form:
            if github_token_value:
                github_cfg["api_token"] = github_token_value
            else:
                github_cfg.pop("api_token", None)
        if "github_base_url" in form:
            if github_base_url_value:
                github_cfg["base_url"] = github_base_url_value
            else:
                github_cfg.pop("base_url", None)
        github_cfg.pop("automation", None)
        config_payload["github"] = github_cfg

    if is_section_touched("git"):
        git_cfg = (config_payload.get("git") if isinstance(config_payload.get("git"), dict) else {}).copy()
        git_user = (git_cfg.get("user") if isinstance(git_cfg.get("user"), dict) else {}).copy()
        git_name_value = (form.get("git_user_name") or "").strip()
        git_email_value = (form.get("git_user_email") or "").strip()
        if "git_user_name" in form:
            if git_name_value:
                git_user["name"] = git_name_value
            else:
                git_user.pop("name", None)
        if "git_user_email" in form:
            if git_email_value:
                git_user["email"] = git_email_value
            else:
                git_user.pop("email", None)
        if git_user:
            git_cfg["user"] = git_user
        else:
            git_cfg.pop("user", None)
        if git_cfg:
            config_payload["git"] = git_cfg
        else:
            config_payload.pop("git", None)

    if is_section_touched("proxy"):
        proxy_cfg = (config_payload.get("proxy") if isinstance(config_payload.get("proxy"), dict) else {}).copy()
        proxy_cfg["enabled"] = as_bool(form.get("proxy_enabled"))
        proxy_url_value = (form.get("proxy_url") or "").strip()
        proxy_username_value = (form.get("proxy_username") or "").strip()
        if "proxy_url" in form:
            if proxy_url_value:
                proxy_cfg["url"] = proxy_url_value
            else:
                proxy_cfg.pop("url", None)
        if "proxy_username" in form:
            if proxy_username_value:
                proxy_cfg["username"] = proxy_username_value
            else:
                proxy_cfg.pop("username", None)
        if "proxy_password" in form:
            new_password = (form.get("proxy_password") or "").strip()
            if new_password:
                proxy_cfg["password"] = new_password
            else:
                proxy_cfg.pop("password", None)
        elif existing_proxy_password:
            proxy_cfg["password"] = existing_proxy_password
        config_payload["proxy"] = proxy_cfg

    if is_section_touched("debug"):
        debug_cfg = (config_payload.get("debug") if isinstance(config_payload.get("debug"), dict) else {}).copy()
        debug_cfg["enabled"] = as_bool(form.get("debug_enabled"))
        valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR"]
        log_level = (form.get("debug_log_level") or "").strip()
        if log_level in valid_log_levels:
            debug_cfg["log_level"] = log_level
        config_payload["debug"] = debug_cfg

    config_payload.pop("ssh", None)
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
            "bundle_base_branch": settings.assets_default_base_branch,
        },
    )


@router.get("/app/requirement-bundles")
def requirement_bundles_page(request: Request):
    user = _current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    db = SessionLocal()
    try:
        return _render_requirement_bundles_view(request, user, db)
    finally:
        db.close()


@router.get("/app/requirement-bundles/panel")
def requirement_bundles_panel(request: Request):
    user = _current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    db = SessionLocal()
    try:
        return _render_requirement_bundles_view(request, user, db, panel_mode=True)
    finally:
        db.close()




def _content_target_from_request(request: Request, default: str = "#tool-panel-body") -> str:
    hx_target = (request.headers.get("HX-Target") or "").strip()
    if hx_target:
        return hx_target if hx_target.startswith("#") else f"#{hx_target}"
    return default

def _is_htmx_request(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def _requirement_bundles_context(request: Request, user, db, **kwargs) -> dict:
    context = {
        "request": request,
        "title": "Bundles",
        "username": user.username,
        "nickname": user.nickname or user.username,
        "bundle_defaults": {
            "repo": settings.assets_repo_full_name,
            "base_branch": settings.assets_default_base_branch,
            "root_dir": settings.assets_bundle_root_dir,
        },
        "bundle_templates": list_bundle_templates(),
        "agents": _list_writable_agents(db, user),
        "bundle_result": None,
        "bundle_detail": None,
        "status_type": "",
        "status_message": "",
        "task_result": None,
        "bundle_action_form_state": {},
        "bundle_view_model": None,
    }
    context.update(kwargs)
    return context


def _render_requirement_bundles_view(request: Request, user, db, *, panel_mode: bool = False, **kwargs):
    context = _requirement_bundles_context(request, user, db, **kwargs)
    if context.get("bundle_detail"):
        context["bundle_view_model"] = _build_bundle_detail_view_model(
            context["bundle_detail"],
            context.get("bundle_templates") or [],
            context.get("agents") or [],
            form_state=context.get("bundle_action_form_state") or {},
        )
    context["content_target"] = _content_target_from_request(
        request,
        default="#tool-panel-body" if panel_mode else "#requirement-bundles-page-content",
    )
    template_name = "partials/requirement_bundles_panel.html" if panel_mode else "requirement_bundles.html"
    return templates.TemplateResponse(template_name, context)


def _visible_group_ids_for_user(db, user) -> list[str]:
    group_service = AgentGroupService(db)
    groups = AgentGroupRepository(db).list_all()
    return [group.id for group in groups if group_service.can_view_group(group, user)]


@router.get("/app/tasks/panel")
def my_tasks_panel(request: Request):
    user = _current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    db = SessionLocal()
    try:
        group_ids = _visible_group_ids_for_user(db, user)
        tasks = AgentTaskRepository(db).list_visible_to_user(user_id=user.id, visible_group_ids=group_ids)
        summary = {"queued": 0, "running": 0, "done": 0, "failed": 0}
        for task in tasks:
            if task.status in summary:
                summary[task.status] += 1
        return templates.TemplateResponse(
            "partials/my_tasks_panel.html",
            {"request": request, "tasks": tasks, "summary": summary, "content_target": _content_target_from_request(request)},
        )
    finally:
        db.close()


@router.get("/app/tasks/{task_id}/panel")
def task_detail_panel(request: Request, task_id: str):
    user = _current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    db = SessionLocal()
    try:
        task = AgentTaskRepository(db).get_by_id(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if user.role != "admin":
            group_ids = _visible_group_ids_for_user(db, user)
            is_visible = task.owner_user_id == user.id or task.created_by_user_id == user.id or (
                task.group_id and task.group_id in group_ids
            )
            if not is_visible:
                raise HTTPException(status_code=404, detail="Task not found")
        return templates.TemplateResponse(
            "partials/task_detail_panel.html",
            {
                "request": request,
                "task": task,
                "task_view_model": _build_task_detail_view_model(task),
                "content_target": _content_target_from_request(request),
            },
        )
    finally:
        db.close()


@router.post("/app/requirement-bundles/create")
async def requirement_bundle_create(request: Request):
    user = _current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    panel_mode = _is_htmx_request(request)
    db = SessionLocal()
    try:
        form_data = await request.form()
        create_form = RequirementBundleCreateForm(
            template_id=str(form_data.get("template_id") or "requirement.v1"),
            title=str(form_data.get("title") or ""),
            domain=str(form_data.get("domain") or ""),
            slug=(str(form_data.get("slug") or "").strip() or None),
            base_branch=str(form_data.get("base_branch") or settings.assets_default_base_branch),
        )
        bundle_ref = requirement_bundle_service.create_bundle(create_form)
        bundle_detail = requirement_bundle_service.inspect_bundle(bundle_ref)
        return _render_requirement_bundles_view(
            request,
            user,
            db,
            panel_mode=panel_mode,
            bundle_result=bundle_ref,
            bundle_detail=bundle_detail,
            status_type="success",
            status_message="Bundle created successfully.",
        )
    except RequirementBundleGithubServiceError as exc:
        return _render_requirement_bundles_view(
            request,
            user,
            db,
            panel_mode=panel_mode,
            status_type="error",
            status_message=str(exc),
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
    panel_mode = _is_htmx_request(request)

    db = SessionLocal()
    try:
        detail = requirement_bundle_service.inspect_bundle(
            BundleRef(repo=target_repo, path=target_path, branch=target_branch)
        )
        return _render_requirement_bundles_view(
            request,
            user,
            db,
            panel_mode=panel_mode,
            bundle_detail=detail,
        )
    except RequirementBundleGithubServiceError as exc:
        return _render_requirement_bundles_view(
            request,
            user,
            db,
            panel_mode=panel_mode,
            status_type="error",
            status_message=str(exc),
        )
    finally:
        db.close()


def _create_bundle_task_payload(
    task_type: str,
    template_id: str,
    action_id: str,
    bundle_ref: BundleRef,
    manifest_ref: BundleRef,
    sources: dict | None = None,
) -> dict:
    _ = task_type
    payload = {
        "template_id": template_id,
        "action_id": action_id,
        "bundle_ref": {
            "repo": bundle_ref.repo,
            "path": bundle_ref.path,
            "branch": bundle_ref.branch,
        },
        "manifest_ref": {
            "repo": manifest_ref.repo,
            "path": manifest_ref.path,
            "branch": manifest_ref.branch,
        },
    }
    if sources is not None:
        payload["sources"] = sources
    return payload


def _render_bundle_action_error_response(
    request: Request,
    *,
    user,
    db,
    panel_mode: bool,
    bundle_ref: BundleRef,
    manifest_ref: BundleRef,
    status_message: str,
    form_state: dict | None = None,
):
    inspect_ref = manifest_ref if manifest_ref.repo and manifest_ref.path and manifest_ref.branch else bundle_ref
    bundle_detail = None
    try:
        if inspect_ref.repo and inspect_ref.path and inspect_ref.branch:
            bundle_detail = requirement_bundle_service.inspect_bundle(inspect_ref)
    except RequirementBundleGithubServiceError:
        bundle_detail = None
    return _render_requirement_bundles_view(
        request,
        user,
        db,
        panel_mode=panel_mode,
        bundle_detail=bundle_detail,
        status_type="error",
        status_message=status_message,
        bundle_action_form_state=form_state or {},
    )


async def _create_and_dispatch_bundle_task(
    request: Request,
    *,
    task_type: str,
    template_id: str,
    action_id: str,
    assignee_agent_id: str,
    manifest_ref: BundleRef,
    bundle_ref: BundleRef,
    sources: dict | None = None,
    form_state: dict | None = None,
):
    user = _current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    panel_mode = _is_htmx_request(request)
    db = SessionLocal()
    dispatch_context_token = None
    try:
        template = require_bundle_template(template_id)
        action_def = next((action for action in template.actions if action.action_id == action_id), None)
        if action_def is None:
            raise HTTPException(status_code=400, detail=f"Unsupported action_id '{action_id}' for template '{template_id}'")

        assignee = AgentRepository(db).get_by_id(assignee_agent_id)
        if not assignee:
            raise HTTPException(status_code=404, detail="Assignee agent not found")
        if not _can_write(assignee, user):
            raise HTTPException(status_code=403, detail="Forbidden")

        inspect_ref = manifest_ref if manifest_ref.repo and manifest_ref.path and manifest_ref.branch else bundle_ref
        bundle_detail = requirement_bundle_service.inspect_bundle(inspect_ref)
        effective_bundle_ref = bundle_detail.bundle_ref
        effective_manifest_ref = bundle_detail.manifest_ref

        if action_def.requires_sources:
            normalized_sources = sources or {"jira": [], "confluence": [], "github_docs": [], "figma": []}
            if not _has_supported_collect_sources(normalized_sources):
                status_message = (
                    "Figma-only collection is not supported in MVP"
                    if normalized_sources.get("figma")
                    else "At least one Jira, Confluence, or GitHub Docs source is required."
                )
                return _render_requirement_bundles_view(
                    request,
                    user,
                    db,
                    panel_mode=panel_mode,
                    bundle_detail=bundle_detail,
                    status_type="error",
                    status_message=status_message,
                    bundle_action_form_state=form_state or {},
                )
            sources = normalized_sources
        else:
            sources = None

        artifact_exists = {item.artifact_key: item.exists for item in (bundle_detail.artifacts or [])}
        missing_required = [key for key in action_def.required_artifacts if not artifact_exists.get(key)]
        if missing_required:
            hint = action_def.missing_artifact_message or f"Required artifacts missing: {', '.join(missing_required)}"
            return _render_requirement_bundles_view(
                request,
                user,
                db,
                panel_mode=panel_mode,
                bundle_detail=bundle_detail,
                status_type="error",
                status_message=hint,
                bundle_action_form_state=form_state or {},
            )

        task_payload = _create_bundle_task_payload(
            task_type,
            template_id,
            action_id,
            effective_bundle_ref,
            effective_manifest_ref,
            sources=sources,
        )
        source_counts = sources or {}
        logger.info(
            "action=create_dispatch_bundle_task task_type=%s template_id=%s action_id=%s selected_agent_id=%s bundle_ref=%s/%s@%s manifest_ref=%s/%s@%s jira_count=%s confluence_count=%s github_docs_count=%s figma_count=%s trace_id=%s",
            task_type,
            template_id,
            action_id,
            assignee_agent_id,
            effective_bundle_ref.repo,
            effective_bundle_ref.path,
            effective_bundle_ref.branch,
            effective_manifest_ref.repo,
            effective_manifest_ref.path,
            effective_manifest_ref.branch,
            len(source_counts.get("jira") or []),
            len(source_counts.get("confluence") or []),
            len(source_counts.get("github_docs") or []),
            len(source_counts.get("figma") or []),
            get_log_context().get("trace_id"),
        )
        task = AgentTaskRepository(db).create(
            assignee_agent_id=assignee_agent_id,
            owner_user_id=assignee.owner_user_id,
            created_by_user_id=user.id,
            source="portal",
            task_type=task_type,
            input_payload_json=json.dumps(task_payload),
            status="queued",
        )
        dispatch_context_token = bind_log_context(portal_task_id=task.id, agent_id=assignee_agent_id)
        logger.info(
            "Created bundle action task task_id=%s task_type=%s template_id=%s action_id=%s selected_agent_id=%s",
            task.id,
            task_type,
            template_id,
            action_id,
            assignee_agent_id,
        )
        logger.debug("Requirement bundle background dispatch scheduled task_id=%s", task.id)
        task_dispatcher_service.dispatch_task_in_background(task.id)
        return _render_requirement_bundles_view(
            request,
            user,
            db,
            panel_mode=panel_mode,
            bundle_detail=bundle_detail,
            status_type="success",
            status_message=f"Created task {task.id} and scheduled background dispatch. Open My Tasks to follow progress.",
            task_result={
                "task_id": task.id,
                "dispatch_status": "scheduled",
                "dispatch_message": "Task scheduled for background dispatch",
            },
        )
    except RequirementBundleGithubServiceError as exc:
        return _render_requirement_bundles_view(
            request,
            user,
            db,
            panel_mode=panel_mode,
            status_type="error",
            status_message=str(exc),
            bundle_action_form_state=form_state or {},
        )
    finally:
        if dispatch_context_token is not None:
            reset_log_context(dispatch_context_token)
        db.close()


@router.post("/app/requirement-bundles/actions/run")
async def requirement_bundle_action_run(request: Request):
    user = _current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    form = await request.form()
    assignee_agent_id = str(form.get("action_agent_id") or "").strip()
    template_id = str(form.get("template_id") or "").strip()
    action_id = str(form.get("action_id") or "").strip()
    form_state = {
        "action_id": action_id,
        "action_agent_id": assignee_agent_id,
        "jira_sources": str(form.get("jira_sources") or ""),
        "confluence_sources": str(form.get("confluence_sources") or ""),
        "github_doc_sources": str(form.get("github_doc_sources") or ""),
        "figma_sources": str(form.get("figma_sources") or ""),
    }

    sources = {
        "jira": _parse_multivalue_text_field(form_state["jira_sources"]),
        "confluence": _parse_multivalue_text_field(form_state["confluence_sources"]),
        "github_docs": _parse_multivalue_text_field(form_state["github_doc_sources"]),
        "figma": _parse_multivalue_text_field(form_state["figma_sources"]),
    }
    bundle_ref = BundleRef(
        repo=str(form.get("bundle_repo") or "").strip(),
        path=str(form.get("bundle_path") or "").strip(),
        branch=str(form.get("bundle_branch") or "").strip(),
    )
    manifest_ref = BundleRef(
        repo=str(form.get("manifest_repo") or form.get("bundle_repo") or "").strip(),
        path=str(form.get("manifest_path") or form.get("bundle_path") or "").strip(),
        branch=str(form.get("manifest_branch") or form.get("bundle_branch") or "").strip(),
    )
    panel_mode = _is_htmx_request(request)
    db = SessionLocal()
    try:
        if not assignee_agent_id:
            return _render_bundle_action_error_response(
                request,
                user=user,
                db=db,
                panel_mode=panel_mode,
                bundle_ref=bundle_ref,
                manifest_ref=manifest_ref,
                status_message="Action agent is required.",
                form_state=form_state,
            )
        if not template_id:
            return _render_bundle_action_error_response(
                request,
                user=user,
                db=db,
                panel_mode=panel_mode,
                bundle_ref=bundle_ref,
                manifest_ref=manifest_ref,
                status_message="template_id is required",
                form_state=form_state,
            )
        if not action_id:
            return _render_bundle_action_error_response(
                request,
                user=user,
                db=db,
                panel_mode=panel_mode,
                bundle_ref=bundle_ref,
                manifest_ref=manifest_ref,
                status_message="action_id is required",
                form_state=form_state,
            )
    finally:
        db.close()

    return await _create_and_dispatch_bundle_task(
        request,
        task_type="bundle_action_task",
        template_id=template_id,
        action_id=action_id,
        assignee_agent_id=assignee_agent_id,
        manifest_ref=manifest_ref,
        bundle_ref=bundle_ref,
        sources=sources,
        form_state=form_state,
    )


@router.post("/app/requirement-bundles/collect")
async def requirement_bundle_collect(request: Request):
    form = await request.form()
    remapped_form = {
        "template_id": "requirement.v1",
        "action_id": "collect_requirements",
        "action_agent_id": str(form.get("collect_agent_id") or "").strip(),
        "bundle_repo": form.get("bundle_repo"),
        "bundle_path": form.get("bundle_path"),
        "bundle_branch": form.get("bundle_branch"),
        "manifest_repo": form.get("manifest_repo"),
        "manifest_path": form.get("manifest_path"),
        "manifest_branch": form.get("manifest_branch"),
        "jira_sources": form.get("jira_sources"),
        "confluence_sources": form.get("confluence_sources"),
        "github_doc_sources": form.get("github_doc_sources"),
        "figma_sources": form.get("figma_sources"),
    }

    class _LegacyForm:
        def __init__(self, payload):
            self._payload = payload

        def get(self, key):
            return self._payload.get(key)

    request._form = _LegacyForm(remapped_form)
    return await requirement_bundle_action_run(request)


@router.post("/app/requirement-bundles/design-test-cases")
async def requirement_bundle_design_test_cases(request: Request):
    form = await request.form()
    remapped_form = {
        "template_id": "requirement.v1",
        "action_id": "design_test_cases",
        "action_agent_id": str(form.get("design_agent_id") or "").strip(),
        "bundle_repo": form.get("bundle_repo"),
        "bundle_path": form.get("bundle_path"),
        "bundle_branch": form.get("bundle_branch"),
        "manifest_repo": form.get("manifest_repo"),
        "manifest_path": form.get("manifest_path"),
        "manifest_branch": form.get("manifest_branch"),
    }

    class _LegacyForm:
        def __init__(self, payload):
            self._payload = payload

        def get(self, key):
            return self._payload.get(key)

    request._form = _LegacyForm(remapped_form)
    return await requirement_bundle_action_run(request)


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
        can_manage_sessions = _can_write(agent, user)

        # When K8s is disabled, return empty sessions
        if not settings.k8s_enabled:
            metadata_repo = AgentSessionMetadataRepository(db)
            try:
                metadata_fallback_limit = max(1, int(limit))
            except (TypeError, ValueError):
                metadata_fallback_limit = 10
            recent_metadata_records = metadata_repo.list_by_agent(agent_id)[:metadata_fallback_limit]
            sessions = merge_runtime_sessions_with_metadata(
                [],
                recent_metadata_records,
                include_metadata_only=True,
            )
            return templates.TemplateResponse(
                "partials/sessions_panel.html",
                {
                    "request": request,
                    "agent_id": agent_id,
                    "sessions": sessions,
                    "current_session_id": current_session_id,
                    "can_manage_sessions": can_manage_sessions,
                },
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
            raise HTTPException(status_code=502, detail=_normalize_runtime_error_detail(content))

        payload = json.loads(content.decode("utf-8"))
        runtime_sessions = payload.get("sessions") or []
        session_ids = [session.get("session_id") for session in runtime_sessions if session.get("session_id")]
        metadata_repo = AgentSessionMetadataRepository(db)
        metadata_records = metadata_repo.list_by_agent_and_session_ids(
            agent_id=agent_id,
            session_ids=session_ids,
        )
        try:
            metadata_fallback_limit = max(1, int(limit))
        except (TypeError, ValueError):
            metadata_fallback_limit = 10
        recent_metadata_records = metadata_repo.list_by_agent(agent_id)[:metadata_fallback_limit]
        all_metadata_records: list = []
        seen_session_ids: set[str] = set()
        for record in [*metadata_records, *recent_metadata_records]:
            record_session_id = getattr(record, "session_id", None)
            if not record_session_id or record_session_id in seen_session_ids:
                continue
            seen_session_ids.add(record_session_id)
            all_metadata_records.append(record)
        sessions = merge_runtime_sessions_with_metadata(
            runtime_sessions,
            all_metadata_records,
            include_metadata_only=True,
        )
        return templates.TemplateResponse(
            "partials/sessions_panel.html",
            {
                "request": request,
                "sessions": sessions,
                "current_session_id": current_session_id,
                "can_manage_sessions": can_manage_sessions,
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
            raise HTTPException(status_code=502, detail=_normalize_runtime_error_detail(content))

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
            raise HTTPException(status_code=502, detail=_normalize_runtime_error_detail(content))

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
            raise HTTPException(status_code=502, detail=_normalize_runtime_error_detail(content))

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
        
        query_items = _filter_runtime_file_upload_query_items(request)

        status_code, content, content_type = await _forward_runtime_multipart(
            user=user,
            agent=agent,
            method="POST",
            subpath="api/files/upload",
            query_items=query_items,
            files=files,
        )

        if status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Upload failed: {content.decode('utf-8', errors='ignore')}")

        return Response(content=content, media_type=content_type, status_code=status_code)
    finally:
        db.close()


@router.post("/a/{agent_id}/api/server-files/upload")
async def agent_server_files_upload(agent_id: str, request: Request):
    """Proxy server files upload to EFP agent."""
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    db = SessionLocal()
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_write(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")

        form = await request.form()
        file_field = form.get("file")
        target_path = form.get("path") or ""
        if not file_field:
            raise HTTPException(status_code=400, detail="No file provided")

        content = await file_field.read()
        files = {"file": (file_field.filename, content, file_field.content_type)}

        status_code, content_bytes, content_type = await _forward_runtime_multipart(
            user=user,
            agent=agent,
            method="POST",
            subpath="api/server-files/upload",
            query_items=[],
            files=files,
            data={"path": target_path},
        )

        return Response(content=content_bytes, media_type=content_type, status_code=status_code)
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
            {"request": request, "agent_id": agent_id, "session_id": "", "chatlog": None, "view": {}, "error": "No session selected"},
        )

    db = SessionLocal()
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_access(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")
        metadata_record = AgentSessionMetadataRepository(db).get_by_agent_and_session(agent_id, session_id)

        if not settings.k8s_enabled:
            view = build_thinking_process_view(None, metadata_record)
            return templates.TemplateResponse(
                "partials/thinking_process_panel.html",
                {
                    "request": request,
                    "agent_id": agent_id,
                    "session_id": session_id,
                    "chatlog": None,
                    "view": view,
                    "error": None if _has_thinking_view_data(view) else "Agent not running",
                },
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
            if metadata_record:
                view = build_thinking_process_view(None, metadata_record)
                view["warning"] = f"Runtime unavailable ({status_code}), showing last metadata snapshot."
                return templates.TemplateResponse(
                    "partials/thinking_process_panel.html",
                    {"request": request, "agent_id": agent_id, "session_id": session_id, "chatlog": None, "view": view, "error": None},
                )
            return templates.TemplateResponse(
                "partials/thinking_process_panel.html",
                {"request": request, "agent_id": agent_id, "session_id": session_id, "chatlog": None, "view": {}, "error": f"Error: {status_code}"},
            )

        chatlog = json.loads(content.decode("utf-8"))
        view = build_thinking_process_view(chatlog, metadata_record)
        return templates.TemplateResponse(
            "partials/thinking_process_panel.html",
            {"request": request, "agent_id": agent_id, "session_id": session_id, "chatlog": chatlog, "view": view, "error": None},
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
        read_only = not _can_write(agent, user)
        summary = _settings_settings_panel_summary(db, agent_id)

        runtime_profile = None
        bound_agent_count = 0
        if agent.runtime_profile_id:
            profile_repo = RuntimeProfileRepository(db)
            runtime_profile = profile_repo.get_by_id(agent.runtime_profile_id)
            if runtime_profile and runtime_profile.owner_user_id in {user.id, agent.owner_user_id}:
                bound_agent_count = profile_repo.count_bound_agents(runtime_profile.id)
            else:
                runtime_profile = None

        if not runtime_profile:
            return templates.TemplateResponse(
                "partials/settings_panel.html",
                {
                    "request": request,
                    "agent_id": agent_id,
                    "status_type": "",
                    "status_message": "",
                    "profile_missing_message": "This agent has no runtime profile. Runtime settings are unavailable until one is assigned. External identities can still be configured below.",
                    "profile_name": None,
                    "profile_revision": None,
                    "profile_bound_agent_count": 0,
                    "config": {},
                    "read_only": read_only,
                    **summary,
                },
            )

        raw_config_data = parse_runtime_profile_config_json(runtime_profile.config_json, fallback_to_empty=True)
        config_data = RuntimeProfileService.merge_with_managed_defaults(raw_config_data)
        view_data = _settings_view_payload(raw_config_data, config_data)
        return templates.TemplateResponse(
            "partials/settings_panel.html",
            {
                "request": request,
                "agent_id": agent_id,
                "status_type": "",
                "status_message": "",
                "profile_missing_message": "",
                "profile_name": runtime_profile.name,
                "profile_revision": runtime_profile.revision,
                "profile_bound_agent_count": bound_agent_count,
                "read_only": read_only,
                **summary,
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

    form = await request.form()

    db = SessionLocal()
    status_type = "success"
    status_message = "Runtime profile updated. Changes are shared across bound agents."
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_write(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")
        summary = _settings_settings_panel_summary(db, agent_id)

        if not agent.runtime_profile_id:
            return templates.TemplateResponse(
                "partials/settings_panel.html",
                {
                    "request": request,
                    "agent_id": agent_id,
                    "status_type": "error",
                    "status_message": "This agent has no runtime profile. Runtime settings are unavailable until one is assigned. External identities can still be configured below.",
                    "profile_missing_message": "This agent has no runtime profile. Runtime settings are unavailable until one is assigned. External identities can still be configured below.",
                    "profile_name": None,
                    "profile_revision": None,
                    "profile_bound_agent_count": 0,
                    "config": {},
                    "read_only": False,
                    **summary,
                },
            )

        profile_repo = RuntimeProfileRepository(db)
        runtime_profile = profile_repo.get_by_id(agent.runtime_profile_id)
        if not runtime_profile or runtime_profile.owner_user_id not in {user.id, agent.owner_user_id}:
            return templates.TemplateResponse(
                "partials/settings_panel.html",
                {
                    "request": request,
                    "agent_id": agent_id,
                    "status_type": "error",
                    "status_message": "Assigned runtime profile was not found.",
                    "profile_missing_message": "This agent has no runtime profile. Runtime settings are unavailable until one is assigned. External identities can still be configured below.",
                    "profile_name": None,
                    "profile_revision": None,
                    "profile_bound_agent_count": 0,
                    "config": {},
                    "read_only": False,
                    **summary,
                },
            )

        profile_bound_agent_count = profile_repo.count_bound_agents(runtime_profile.id)
        config_base = parse_runtime_profile_config_json(runtime_profile.config_json, fallback_to_empty=True)
        config_payload, merge_error = _settings_merge_payload(config_base, form)
        if merge_error:
            return _settings_error_response(
                request,
                db,
                agent_id,
                config_payload,
                merge_error,
                profile_name=runtime_profile.name,
                profile_revision=runtime_profile.revision,
                profile_bound_agent_count=profile_bound_agent_count,
                read_only=False,
            )

        sanitized_config = sanitize_runtime_profile_config_dict(config_payload)
        runtime_profile.config_json = dump_runtime_profile_config_json(sanitized_config)
        runtime_profile.revision = (runtime_profile.revision or 0) + 1
        runtime_profile = profile_repo.save(runtime_profile)

        try:
            sync_result = await runtime_profile_sync_service.sync_profile_to_bound_agents(db, runtime_profile)
            if sync_result.get("failed_agent_ids"):
                status_type = "error"
                status_message = (
                    "Runtime profile saved, but some running agents failed to sync: "
                    + ", ".join(sync_result["failed_agent_ids"])
                )
            else:
                status_message = (
                    "Runtime profile updated. "
                    f"Updated running agents: {sync_result['updated_running_count']}, "
                    f"skipped (not running): {sync_result['skipped_not_running_count']}."
                )
        except Exception:
            logger.exception("runtime profile fan-out sync failed after settings save profile_id=%s", runtime_profile.id)
            status_type = "error"
            status_message = (
                "Runtime profile was saved, but sync fan-out failed this time. "
                "Running agents may need retry; newly started agents will still pull from Portal on startup."
            )

        view_data = _settings_view_payload(sanitized_config, RuntimeProfileService.merge_with_managed_defaults(sanitized_config))
        return templates.TemplateResponse(
            "partials/settings_panel.html",
            {
                "request": request,
                "agent_id": agent_id,
                "status_type": status_type,
                "status_message": status_message,
                "profile_missing_message": "",
                "profile_name": runtime_profile.name,
                "profile_revision": runtime_profile.revision,
                "profile_bound_agent_count": profile_bound_agent_count,
                "read_only": False,
                **summary,
                **view_data,
            },
        )
    finally:
        db.close()


_MANAGED_TEST_TARGETS = {"proxy", "llm", "jira", "confluence", "github"}


def _validate_managed_test_target(target: str) -> str:
    clean_target = (target or "").strip().lower()
    if clean_target not in _MANAGED_TEST_TARGETS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown test target")
    return clean_target


@router.post("/app/agents/{agent_id}/settings/test/{target}")
async def app_agent_settings_test(request: Request, agent_id: str, target: str):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    target = _validate_managed_test_target(target)
    form = await request.form()
    db = SessionLocal()
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_write(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")
        if not agent.runtime_profile_id:
            raise HTTPException(status_code=404, detail="RuntimeProfile not found")

        runtime_profile = RuntimeProfileRepository(db).get_by_id(agent.runtime_profile_id)
        if not runtime_profile or runtime_profile.owner_user_id not in {user.id, agent.owner_user_id}:
            raise HTTPException(status_code=404, detail="RuntimeProfile not found")

        config_base = parse_runtime_profile_config_json(runtime_profile.config_json, fallback_to_empty=True)
        config_payload, merge_error = _settings_merge_payload(config_base, form)
        if merge_error:
            return JSONResponse({"ok": False, "target": target, "message": merge_error})
        ok, message = await runtime_profile_test_service.run_test(target, config_payload)
        return JSONResponse({"ok": bool(ok), "target": target, "message": message})
    finally:
        db.close()


@router.get("/app/runtime-profiles/{profile_id}/panel")
async def app_runtime_profile_panel(request: Request, profile_id: str):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    db = SessionLocal()
    try:
        service = RuntimeProfileService(db)
        profile = service.get_for_user(user, profile_id)
        if not profile:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RuntimeProfile not found")
        profile_repo = RuntimeProfileRepository(db)
        return templates.TemplateResponse(
            "partials/runtime_profile_panel.html",
            _runtime_profile_panel_context(request, profile, profile_repo),
        )
    finally:
        db.close()


@router.post("/app/runtime-profiles/{profile_id}/test/{target}")
async def app_runtime_profile_test(request: Request, profile_id: str, target: str):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    target = _validate_managed_test_target(target)
    form = await request.form()
    db = SessionLocal()
    try:
        service = RuntimeProfileService(db)
        profile = service.get_for_user(user, profile_id)
        if not profile:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RuntimeProfile not found")

        config_base = parse_runtime_profile_config_json(profile.config_json, fallback_to_empty=True)
        config_payload, merge_error = _settings_merge_payload(config_base, form)
        if merge_error:
            return JSONResponse({"ok": False, "target": target, "message": merge_error})

        ok, message = await runtime_profile_test_service.run_test(target, config_payload)
        return JSONResponse({"ok": bool(ok), "target": target, "message": message})
    finally:
        db.close()


@router.post("/app/runtime-profiles/{profile_id}/save")
async def app_runtime_profile_save(request: Request, profile_id: str):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    form = await request.form()
    db = SessionLocal()
    try:
        service = RuntimeProfileService(db)
        profile = service.get_for_user(user, profile_id)
        if not profile:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RuntimeProfile not found")

        profile_repo = RuntimeProfileRepository(db)
        config_base = parse_runtime_profile_config_json(profile.config_json, fallback_to_empty=True)
        config_payload, merge_error = _settings_merge_payload(config_base, form)
        if merge_error:
            return templates.TemplateResponse(
                "partials/runtime_profile_panel.html",
                _runtime_profile_panel_context(request, profile, profile_repo, status_type="error", status_message=merge_error),
            )

        sanitized_config = sanitize_runtime_profile_config_dict(config_payload)
        is_default = str(form.get("is_default") or "").lower() in {"1", "true", "on", "yes"}
        updated, config_changed = service.update_for_user(
            user,
            profile_id,
            name=(form.get("name") or profile.name).strip(),
            description=(form.get("description") or "").strip() or None,
            config_json=dump_runtime_profile_config_json(sanitized_config),
            is_default=is_default,
        )

        status_type = "success"
        status_message = "Runtime profile saved."
        if config_changed:
            try:
                await runtime_profile_sync_service.sync_profile_to_bound_agents(db, updated)
            except Exception:
                logger.exception("runtime profile fan-out sync failed after profile save profile_id=%s", updated.id)
                status_type = "error"
                status_message = "Runtime profile saved, but fan-out sync failed."

        response = templates.TemplateResponse(
            "partials/runtime_profile_panel.html",
            _runtime_profile_panel_context(request, updated, profile_repo, status_type=status_type, status_message=status_message),
        )
        response.headers["HX-Trigger"] = "runtimeProfilesChanged"
        return response
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

        extra_headers = build_portal_agent_identity_headers(user, agent)

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
            raise HTTPException(status_code=502, detail=_normalize_runtime_error_detail(content))

        data = json.loads(content.decode("utf-8"))
        
        normalized_payload = normalize_assistant_chat_payload(
            data,
            fallback_session_id=session_id or "",
        )
        
        return templates.TemplateResponse(
            "partials/chat_response.html",
            {
                "request": request,
                "user_message": message,
                "assistant_message": normalized_payload["assistant_message"],
                "session_id": normalized_payload["session_id"],
                "agent_name": agent.name if agent else "Assistant",
                "user_message_id": normalized_payload["user_message_id"],
                "events": normalized_payload["events"],
                "display_blocks": normalized_payload["display_blocks"],
                "timestamp": datetime.now().strftime("%H:%M"),
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


def _triggered_work_authorize(db, user, agent_id: str):
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not _can_access(agent, user):
        raise HTTPException(status_code=403, detail="Forbidden")
    return agent


def _render_agent_identity_bindings_panel(
    request: Request,
    *,
    agent,
    user,
    db,
    error: str = "",
    success: str = "",
    form: dict | None = None,
):
    return templates.TemplateResponse(
        "partials/agent_identity_bindings_panel.html",
        {
            "request": request,
            "agent_id": agent.id,
            "bindings": AgentIdentityBindingRepository(db).list_by_agent(agent.id),
            "error": error,
            "success": success,
            "form": form or {},
            "read_only": not _can_write(agent, user),
        },
    )



@router.get("/app/agents/{agent_id}/external-identities/panel")
async def app_agent_triggered_work_bindings_panel(request: Request, agent_id: str):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    db = SessionLocal()
    try:
        agent = _triggered_work_authorize(db, user, agent_id)
        return _render_agent_identity_bindings_panel(request, agent=agent, user=user, db=db)
    finally:
        db.close()


@router.post("/app/agents/{agent_id}/external-identities/create")
async def app_agent_triggered_work_bindings_create(request: Request, agent_id: str):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    db = SessionLocal()
    try:
        agent = _triggered_work_authorize(db, user, agent_id)
        if not _can_write(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")

        form = await request.form()
        form_data = {
            "system_type": str(form.get("system_type") or "").strip().lower(),
            "external_account_id": str(form.get("external_account_id") or "").strip(),
            "username": str(form.get("username") or "").strip() or None,
            "scope_json": str(form.get("scope_json") or "").strip(),
            "enabled": _parse_form_bool(form.get("enabled")),
        }
        error = ""
        scope_json, scope_error = _parse_json_textarea(form_data["scope_json"], field_name="scope_json")
        if not form_data["system_type"] or not form_data["external_account_id"]:
            error = "system_type and external_account_id are required"
        elif scope_error:
            error = scope_error
        else:
            existing = AgentIdentityBindingRepository(db).get_by_agent_and_binding_key(
                agent_id=agent_id,
                system_type=form_data["system_type"],
                external_account_id=form_data["external_account_id"],
                enabled_only=False,
            )
            if existing:
                error = "External identity already exists for this agent/system/account"

        if not error:
            AgentIdentityBindingRepository(db).create(
                agent_id=agent_id,
                system_type=form_data["system_type"],
                external_account_id=form_data["external_account_id"],
                username=form_data["username"],
                scope_json=scope_json,
                enabled=form_data["enabled"],
            )

        return _render_agent_identity_bindings_panel(
            request,
            agent=agent,
            user=user,
            db=db,
            error=error,
            success="External identity added" if not error else "",
            form=form_data,
        )
    finally:
        db.close()


@router.post("/app/agents/{agent_id}/external-identities/{binding_id}/delete")
async def app_agent_triggered_work_bindings_delete(request: Request, agent_id: str, binding_id: str):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    db = SessionLocal()
    try:
        agent = _triggered_work_authorize(db, user, agent_id)
        if not _can_write(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")

        repo = AgentIdentityBindingRepository(db)
        binding = repo.get_by_id(binding_id)
        if binding and binding.agent_id == agent_id:
            repo.delete(binding)

        return _render_agent_identity_bindings_panel(
            request,
            agent=agent,
            user=user,
            db=db,
            success="Binding deleted",
        )
    finally:
        db.close()
