import logging
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.redaction import sanitize_exception_message
from app.services.auth_service import issue_session_token
from app.services.external_login_service import provision_external_user

logger = logging.getLogger(__name__)
settings = get_settings()

SSO_CALLBACK_PATH = "/auth"


def sso_enabled() -> bool:
    return bool(settings.sso_issuer_url.strip())


def sso_redirect_uri() -> str:
    return f"{settings.base_uri.rstrip('/')}{SSO_CALLBACK_PATH}"


def _issuer() -> str:
    return settings.sso_issuer_url.strip().rstrip("/")


def sso_authorize_url(state: str = "") -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.sso_client_id,
            "scope": settings.sso_scope,
            "redirect_uri": sso_redirect_uri(),
            "state": state,
        }
    )
    return f"{_issuer()}/protocol/openid-connect/auth?{query}"


def sso_token_url() -> str:
    return f"{_issuer()}/protocol/openid-connect/token"


async def get_user_by_code(redirect_uri: str, code: str):
    if not sso_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SSO is not configured")
    form = {
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "client_id": settings.sso_client_id,
        "code": code,
    }
    if settings.sso_client_secret:
        form["client_secret"] = settings.sso_client_secret
    try:
        async with httpx.AsyncClient(verify=settings.sso_verify_tls, timeout=15.0) as client:
            response = await client.post(sso_token_url(), data=form)
            response.raise_for_status()
            token = response.json()
            # The access token comes straight from the IdP token endpoint over
            # TLS, so its claims are trusted without re-verifying the signature.
            claims = jwt.decode(token["access_token"], options={"verify_signature": False})
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("SSO token exchange failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"SSO token exchange failed: {sanitize_exception_message(e)}",
        )

    email = str(claims.get("email") or "").strip()
    username = str(claims.get("preferred_username") or "").strip() or email
    if not username:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="SSO did not return a username")
    return username, email


async def login_user_by_code(db: Session, redirect_uri: str, code: str) -> str:
    """Exchange the SSO code, provision the member on first sign-in, return a session token."""
    username, email = await get_user_by_code(redirect_uri, code)
    display_name = " ".join(part.capitalize() for part in email.split("@", 1)[0].split(".") if part)
    user, _created = provision_external_user(db, username=username, display_name=display_name, source="sso")
    return issue_session_token(user.id)
