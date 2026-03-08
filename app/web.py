import json

from fastapi import APIRouter, HTTPException, Request, status
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


@router.get("/")
def index(request: Request) -> RedirectResponse:
    user = _current_user_from_cookie(request)
    return RedirectResponse(url="/app" if user else "/login", status_code=302)


@router.get("/login")
def login_page(request: Request):
    if _current_user_from_cookie(request):
        return RedirectResponse(url="/app", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "title": "Portal Login"})


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
            "role": user.role,
        },
    )




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
        return templates.TemplateResponse(
            "partials/settings_panel.html",
            {
                "request": request,
                "agent_id": agent_id,
                "llm": config_data.get("llm") or {},
                "status_type": "",
                "status_message": "",
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
    model = (form.get("model") or "").strip()
    api_base = (form.get("api_base") or "").strip()
    temperature_raw = (form.get("temperature") or "").strip()
    max_tokens_raw = (form.get("max_tokens") or "").strip()

    llm_payload = {}
    if model:
        llm_payload["model"] = model
    if api_base:
        llm_payload["api_base"] = api_base

    if temperature_raw:
        try:
            llm_payload["temperature"] = float(temperature_raw)
        except ValueError:
            return templates.TemplateResponse(
                "partials/settings_panel.html",
                {
                    "request": request,
                    "agent_id": agent_id,
                    "llm": {"model": model, "api_base": api_base, "temperature": temperature_raw, "max_tokens": max_tokens_raw},
                    "status_type": "error",
                    "status_message": "Temperature must be a number.",
                },
            )

    if max_tokens_raw:
        try:
            llm_payload["max_tokens"] = int(max_tokens_raw)
        except ValueError:
            return templates.TemplateResponse(
                "partials/settings_panel.html",
                {
                    "request": request,
                    "agent_id": agent_id,
                    "llm": {"model": model, "api_base": api_base, "temperature": temperature_raw, "max_tokens": max_tokens_raw},
                    "status_type": "error",
                    "status_message": "Max tokens must be an integer.",
                },
            )

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
            body=json.dumps({"llm": llm_payload}).encode("utf-8"),
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

        llm = llm_payload.copy()
        if read_status < 400:
            payload = json.loads(read_content.decode("utf-8"))
            llm = (payload.get("config") or {}).get("llm") or llm
        elif status_type != "error":
            status_type = "error"
            status_message = f"Saved but failed to reload panel: {read_content.decode('utf-8', errors='ignore')}"

        return templates.TemplateResponse(
            "partials/settings_panel.html",
            {
                "request": request,
                "agent_id": agent_id,
                "llm": llm,
                "status_type": status_type,
                "status_message": status_message,
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

    if not agent_id:
        raise HTTPException(status_code=400, detail="Agent not selected")
    if not message:
        raise HTTPException(status_code=400, detail="Message required")

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
            },
        )
    finally:
        db.close()
