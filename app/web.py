import app.logger  # Ensure logging is configured (intentional side-effect import)  # noqa: F401
import json

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.db import SessionLocal
from app.repositories.agent_repo import AgentRepository
from app.repositories.user_repo import UserRepository
from app.services.auth_service import parse_session_token
from app.services.proxy_service import ProxyService

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory="app/templates")
settings = get_settings()
proxy_service = ProxyService()


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


def _settings_view_payload(config_data: dict) -> dict:
    llm = config_data.get("llm") if isinstance(config_data.get("llm"), dict) else {}
    jira = config_data.get("jira") if isinstance(config_data.get("jira"), dict) else {}
    confluence = config_data.get("confluence") if isinstance(config_data.get("confluence"), dict) else {}

    jira_instances = jira.get("instances") if isinstance(jira.get("instances"), list) else []
    if not jira_instances and jira.get("url"):
        jira_instances = [{
            "name": "Default",
            "url": jira.get("url") or "",
            "username": jira.get("username") or "",
            "password": jira.get("password") or "",
            "token": jira.get("token") or "",
            "project": jira.get("project") or "",
        }]

    confluence_instances = confluence.get("instances") if isinstance(confluence.get("instances"), list) else []
    if not confluence_instances and confluence.get("url"):
        confluence_instances = [{
            "name": "Default",
            "url": confluence.get("url") or "",
            "username": confluence.get("username") or "",
            "password": confluence.get("password") or "",
            "token": confluence.get("token") or "",
            "space": confluence.get("space") or "",
        }]

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

        status_code, content, _ = await proxy_service.forward(
            agent=agent,
            method="GET",
            subpath="api/sessions",
            query_items=[("limit", limit)],
            body=None,
            headers={},
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

        status_code, content, _ = await proxy_service.forward(
            agent=agent,
            method="GET",
            subpath="api/skills",
            query_items=[],
            body=None,
            headers={},
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


@router.get("/api/agents/{agent_id}/usage")
async def api_agent_usage(request: Request, agent_id: str):
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

        status_code, content, _ = await proxy_service.forward(
            agent=agent,
            method="GET",
            subpath="api/usage",
            query_items=[("days", days)],
            body=None,
            headers={},
        )

        if status_code >= 400:
            return {"global": {}, "by_provider": {}, "by_model": {}, "daily": []}

        payload = json.loads(content.decode("utf-8"))
        return payload if isinstance(payload, dict) else {}
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
                {"request": request, "agent_id": agent_id, "usage": {}, "total_messages": 0, "total_cost": 0},
            )

        status_code, content, _ = await proxy_service.forward(
            agent=agent,
            method="GET",
            subpath="api/usage",
            query_items=[("days", days)],
            body=None,
            headers={},
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

        status_code, content, _ = await proxy_service.forward(
            agent=agent,
            method="GET",
            subpath="api/files/list",
            query_items=[],
            body=None,
            headers={},
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
        
        # Use proxy_service to get the correct EFP URL
        try:
            efp_base_url = proxy_service.build_agent_base_url(agent)
        except ValueError as e:
            raise HTTPException(status_code=502, detail=str(e))
        
        url = f"{efp_base_url}/api/files/upload"
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, files=files)
        
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Upload failed: {resp.text}")
        
        return Response(content=resp.content, media_type=resp.headers.get("content-type", "application/json"), status_code=resp.status_code)
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
        status_code, content, content_type = await proxy_service.forward(
            agent=agent,
            method="GET",
            subpath=f"api/files/{file_id}/preview",
            query_items=[("max_chars", str(max_chars))],
            body=None,
            headers={},
        )
        
        if status_code >= 400:
            raise HTTPException(status_code=502, detail="Preview failed")
        
        return Response(content=content, media_type=content_type, status_code=status_code)
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

        status_code, content, _ = await proxy_service.forward(
            agent=agent,
            method="GET",
            subpath="api/config",
            query_items=[],
            body=None,
            headers={},
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

    def as_bool(value) -> bool:
        return str(value or "").lower() in {"1", "true", "on", "yes"}

    def parse_instances(prefix: str, fields: list[str]) -> list[dict]:
        count_text = (form.get(f"{prefix}_instance_count") or "0").strip()
        try:
            count = max(0, int(count_text))
        except ValueError:
            count = 0

        instances = []
        for i in range(count):
            item = {}
            for field in fields:
                item[field] = (form.get(f"{prefix}_instances_{i}_{field}") or "").strip()
            if item.get("name") or item.get("url"):
                instances.append(item)
        return instances

    original_config_json = (form.get("original_config_json") or "").strip()
    try:
        config_payload = json.loads(original_config_json) if original_config_json else {}
    except Exception:
        config_payload = {}
    if not isinstance(config_payload, dict):
        config_payload = {}
    
    # Preserve any existing proxy configuration from the payload; do not strip fields here,
    # since this handler is processing a save request, not sending data back to the client.
    existing_proxy_password = None
    if "proxy" in config_payload and isinstance(config_payload["proxy"], dict):
        existing_proxy_password = config_payload["proxy"].get("password")
    
    # Preserve existing tokens for Jira and Confluence instances
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
    llm["provider"] = (form.get("llm_provider") or "").strip()
    llm["model"] = (form.get("llm_model") or "").strip()
    llm["api_key"] = (form.get("llm_api_key") or "").strip()
    
    # Set api_base based on provider (same logic as EFP) - only if empty
    provider = llm.get("provider", "")
    if not llm.get("api_base"):  # Only set if not already present
        if provider == "github_copilot":
            llm["api_base"] = "https://api.githubcopilot.com"
        elif provider == "anthropic":
            llm["api_base"] = "https://api.anthropic.com/v1"
        elif provider == "ollama":
            llm["api_base"] = "http://127.0.0.1:11434"
        else:  # openai or default
            llm["api_base"] = "https://api.openai.com/v1"

    temperature_text = (form.get("llm_temperature") or "").strip()
    if temperature_text:
        try:
            llm["temperature"] = float(temperature_text)
        except ValueError:
            view_data = _settings_view_payload(config_payload)
            return templates.TemplateResponse(
                "partials/settings_panel.html",
                {
                    "request": request,
                    "agent_id": agent_id,
                    "status_type": "error",
                    "status_message": "Temperature must be a number.",
                    **view_data,
                },
            )

    max_tokens_text = (form.get("llm_max_tokens") or "").strip()
    if max_tokens_text:
        try:
            llm["max_tokens"] = int(max_tokens_text)
        except ValueError:
            view_data = _settings_view_payload(config_payload)
            return templates.TemplateResponse(
                "partials/settings_panel.html",
                {
                    "request": request,
                    "agent_id": agent_id,
                    "status_type": "error",
                    "status_message": "Max tokens must be an integer.",
                    **view_data,
                },
            )

    jira = (config_payload.get("jira") if isinstance(config_payload.get("jira"), dict) else {}).copy()
    jira["enabled"] = as_bool(form.get("jira_enabled"))
    # Preserve existing tokens if not provided in form
    jira_new_instances = parse_instances("jira", ["name", "url", "username", "password", "token", "project"])
    for i, inst in enumerate(jira_new_instances):
        if not inst.get("token") and i < len(existing_jira_instances):
            inst["token"] = existing_jira_instances[i].get("token", "")
    jira["instances"] = jira_new_instances

    confluence = (config_payload.get("confluence") if isinstance(config_payload.get("confluence"), dict) else {}).copy()
    confluence["enabled"] = as_bool(form.get("confluence_enabled"))
    # Preserve existing tokens if not provided in form
    confluence_new_instances = parse_instances("confluence", ["name", "url", "username", "password", "token", "space"])
    for i, inst in enumerate(confluence_new_instances):
        if not inst.get("token") and i < len(existing_confluence_instances):
            inst["token"] = existing_confluence_instances[i].get("token", "")
    confluence["instances"] = confluence_new_instances

    github_cfg = (config_payload.get("github") if isinstance(config_payload.get("github"), dict) else {}).copy()
    github_cfg["enabled"] = as_bool(form.get("github_enabled"))
    github_cfg["api_token"] = (form.get("github_api_token") or "").strip()
    github_cfg["base_url"] = (form.get("github_base_url") or "").strip()

    git_cfg = (config_payload.get("git") if isinstance(config_payload.get("git"), dict) else {}).copy()
    git_user = (git_cfg.get("user") if isinstance(git_cfg.get("user"), dict) else {}).copy()
    git_user["name"] = (form.get("git_user_name") or "").strip()
    git_user["email"] = (form.get("git_user_email") or "").strip()
    git_cfg["user"] = git_user

    ssh_cfg = (config_payload.get("ssh") if isinstance(config_payload.get("ssh"), dict) else {}).copy()
    ssh_cfg["enabled"] = as_bool(form.get("ssh_enabled"))
    ssh_cfg["private_key_path"] = (form.get("ssh_private_key_path") or "").strip()

    proxy_cfg = (config_payload.get("proxy") if isinstance(config_payload.get("proxy"), dict) else {}).copy()
    proxy_cfg["enabled"] = as_bool(form.get("proxy_enabled"))
    proxy_cfg["url"] = (form.get("proxy_url") or "").strip()
    proxy_cfg["username"] = (form.get("proxy_username") or "").strip()
    # Only update password if provided (to preserve existing password)
    new_password = (form.get("proxy_password") or "").strip()
    if new_password:
        proxy_cfg["password"] = new_password
    elif existing_proxy_password:
        proxy_cfg["password"] = existing_proxy_password

    debug_cfg = (config_payload.get("debug") if isinstance(config_payload.get("debug"), dict) else {}).copy()
    debug_cfg["enabled"] = as_bool(form.get("debug_enabled"))

    config_payload["llm"] = llm
    config_payload["jira"] = jira
    config_payload["confluence"] = confluence
    config_payload["github"] = github_cfg
    config_payload["git"] = git_cfg
    config_payload["ssh"] = ssh_cfg
    config_payload["proxy"] = proxy_cfg
    config_payload["debug"] = debug_cfg

    db = SessionLocal()
    status_type = "success"
    status_message = "Settings saved. Runtime configuration reloaded."
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_access(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")

        status_code, content, _ = await proxy_service.forward(
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

        read_status, read_content, _ = await proxy_service.forward(
            agent=agent,
            method="GET",
            subpath="api/config",
            query_items=[],
            body=None,
            headers={},
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

        payload = {"message": message}
        if session_id:
            payload["session_id"] = session_id
        if attachments:
            payload["attachments"] = attachments

        status_code, content, _ = await proxy_service.forward(
            agent=agent,
            method="POST",
            subpath="api/chat",
            query_items=[],
            body=json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json"},
        )

        if status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Runtime error: {content.decode('utf-8', errors='ignore')}")

        data = json.loads(content.decode("utf-8"))
        return templates.TemplateResponse(
            "partials/chat_response.html",
            {
                "request": request,
                "user_message": message,
                "assistant_message": data.get("response") or "(empty response)",
                "session_id": data.get("session_id") or session_id or "",
                "agent_name": agent.name if agent else "Assistant",
            },
        )
    finally:
        db.close()
