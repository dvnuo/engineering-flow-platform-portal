from itsdangerous import BadSignature, URLSafeSerializer
import logging
logger = logging.getLogger(__name__)
from passlib.context import CryptContext

from app.config import get_settings
from typing import Optional

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
settings = get_settings()
serializer = URLSafeSerializer(settings.secret_key, salt="portal-session")


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
