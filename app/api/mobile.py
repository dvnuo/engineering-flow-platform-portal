"""What the Mobile testing panel needs from Portal, and the hosted Appium Inspector.

Portal never talks to BrowserStack. The Mobile testing panel in an assistant's chat
drives the local bridge on the member's own computer (the program of the Local
bridge connector, `efp-bridge`), whose /mobile/* routes start and hold a
BrowserStack device with mobile-auto and proxy Appium Inspector's WebDriver
traffic for it. This module hands the panel the member's BrowserStack settings
for those calls and serves the Inspector's web build, which the panel opens
pointed at the bridge. Test runs go through the Jenkins pipeline; the
assistant's pod only ever handles files.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.repositories.agent_repo import AgentRepository
from app.schemas.runtime_profile import parse_runtime_profile_config_json
from app.services.runtime_profile_service import RuntimeProfileService

router = APIRouter(tags=["mobile"])

# Where the panel writes recordings in the assistant's workspace; the
# record-mobile-segment skill compiles them from there.
RECORDINGS_DIR = "mobile/recordings"


def appium_inspector_dir() -> Optional[Path]:
    """The hosted Inspector's web build, if this deployment has one."""
    raw = (get_settings().appium_inspector_dir or "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if (path / "index.html").is_file() else None


def _section(config: dict[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key)
    return value if isinstance(value, dict) else {}


@router.get("/api/mobile/recording-config")
def recording_config(agent_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """The member's BrowserStack settings, for the local bridge to use.

    The panel records on the owner's own device session and writes into the
    assistant's workspace, so only the owner may record. The credentials are
    the ones the member's connector page already shows them.
    """
    agent = AgentRepository(db).get_by_id(agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assistant not found")
    if agent.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the assistant's owner can record on it")
    profile = RuntimeProfileService(db).get_or_create_for_user(user)
    config = parse_runtime_profile_config_json(getattr(profile, "config_json", None), fallback_to_empty=True)
    mobile = _section(config, "mobile-auto")
    bs = _section(mobile, "browserstack")
    defaults = _section(mobile, "defaults")
    username = str(bs.get("username") or "").strip()
    access_key = str(bs.get("access_key") or "").strip()
    problem = None
    if not mobile.get("enabled"):
        problem = "Turn the BrowserStack connector on first (Connectors > BrowserStack)."
    elif not username or not access_key:
        problem = "Add your BrowserStack username and access key in Connectors > BrowserStack."
    body: dict[str, Any] = {
        "configured": problem is None,
        "problem": problem,
        "inspector_available": appium_inspector_dir() is not None,
        "recordings_dir": RECORDINGS_DIR,
        "defaults": {
            "platform": str(defaults.get("platform") or ""),
            "network": str(defaults.get("network_mode") or get_settings().mobile_default_network or "").strip(),
            "idle_timeout_seconds": defaults.get("idle_timeout_seconds"),
            "video": defaults.get("video"),
            "interactive_debugging": defaults.get("interactive_debugging"),
            "appium_version": str(defaults.get("appium_version") or ""),
        },
        "credentials": None,
    }
    if problem is None:
        body["credentials"] = {
            "username": username,
            "access_key": access_key,
            "api_base_url": str(bs.get("api_base_url") or "").strip(),
            "appium_base_url": str(bs.get("appium_base_url") or "").strip(),
        }
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


@router.get("/inspector")
@router.get("/inspector/{asset_path:path}")
def hosted_inspector(request: Request, asset_path: str = ""):
    """Appium Inspector's web build; its assets hard-code the /inspector/ prefix."""
    from app.web import _authorized_web_user  # the web module owns the login redirects

    _user, access_response = _authorized_web_user(request)
    if access_response is not None:
        return access_response
    base = appium_inspector_dir()
    if base is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The hosted Appium Inspector is not installed on this Portal")
    if not asset_path and not request.url.path.endswith("/"):
        target = "/inspector/" + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(target, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    root = base.resolve()
    candidate = (root / asset_path).resolve() if asset_path else root / "index.html"
    if root not in candidate.parents and candidate != root / "index.html":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not candidate.is_file():
        # A client-side route; the app handles it.
        candidate = root / "index.html"
    headers = {"Cache-Control": "public, max-age=31536000, immutable"} if "/assets/" in f"/{asset_path}" else {"Cache-Control": "no-cache"}
    return FileResponse(candidate, headers=headers)
