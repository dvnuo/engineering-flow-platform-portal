from fastapi import Response
from itsdangerous import BadSignature, URLSafeSerializer
from passlib.context import CryptContext

from app.config import get_settings
from app.security.session_cookie import DEFAULT_SESSION_COOKIE_NAME, cookies_are_hardened, session_cookie_name
from typing import Optional

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
settings = get_settings()
serializer = URLSafeSerializer(settings.secret_key, salt="portal-session")

# A login stays valid until the member signs out. The cookie itself has to
# carry a long max_age: without one browsers treat it as a session cookie
# and drop it on exit, which shows up as "I have to log in again every day".
SESSION_COOKIE_MAX_AGE_SECONDS = 365 * 24 * 60 * 60


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def issue_session_token(user_id: int) -> str:
    return serializer.dumps({"user_id": user_id})


def parse_session_token(token: str) -> Optional[int]:
    try:
        payload = serializer.loads(token)
    except BadSignature:
        return None
    return payload.get("user_id")


def set_session_cookie(response: Response, token: str) -> None:
    """Attach the portal session cookie the same way for every login path.

    Over https the cookie is ``__Host-portal_session`` with ``Secure``
    (app/security/session_cookie.py); the cookie under the old name is
    expired alongside, since nothing reads it any more.
    """
    name = session_cookie_name()
    hardened = cookies_are_hardened()
    response.set_cookie(
        key=name,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_COOKIE_MAX_AGE_SECONDS,
        path="/",
        secure=hardened,
    )
    if hardened:
        response.delete_cookie(DEFAULT_SESSION_COOKIE_NAME, path="/")


def clear_session_cookie(response: Response) -> None:
    """Expire the session cookie; the deletion carries the flags the cookie was set with."""
    response.delete_cookie(session_cookie_name(), path="/", secure=cookies_are_hardened())
