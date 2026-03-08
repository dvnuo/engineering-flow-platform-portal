from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.db import SessionLocal
from app.repositories.user_repo import UserRepository
from app.services.auth_service import parse_session_token

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory="app/templates")
settings = get_settings()


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


@router.get("/partials/tools/{tool_name}")
def tool_partial(request: Request, tool_name: str):
    if not _current_user_from_cookie(request):
        return RedirectResponse(url="/login", status_code=302)

    mapping = {
        "empty": "partials/tool_empty.html",
        "server-files": "partials/tool_server_files.html",
        "uploads": "partials/tool_uploads.html",
        "settings": "partials/tool_settings.html",
    }
    template = mapping.get(tool_name, "partials/tool_empty.html")
    return templates.TemplateResponse(template, {"request": request, "title": "tool"})
