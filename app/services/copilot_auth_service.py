from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import httpx

from app.redaction import sanitize_exception_message
from app.utils.github_url import normalize_github_oauth_base_url

logger = logging.getLogger(__name__)

COPILOT_OAUTH_CLIENT_IDS = {"native": "Iv1.b507a08c87ecfe98", "efp": "Iv1.b507a08c87ecfe98", "opencode": "Ov23li8tweQw6odWQebz"}


def normalize_copilot_runtime_type(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return "opencode"
    if raw == "efp":
        return "native"
    if raw in {"native", "opencode"}:
        return raw
    raise ValueError("runtime_type must be one of: native, opencode")

_pending_authorizations: dict[str, dict[str, Any]] = {}


class CopilotAuthService:
    _HEADERS = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "engineering-flow-platform-portal",
    }

    def _utc_now(self) -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _safe_response_json(response: httpx.Response) -> dict:
        if not response.content:
            return {}
        try:
            payload = response.json()
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _cleanup_expired(self, exclude_auth_id: str | None = None) -> None:
        now = self._utc_now()
        expired_ids = [k for k, v in _pending_authorizations.items() if k != exclude_auth_id and isinstance(v.get("expires_at"), datetime) and v["expires_at"] <= now]
        for auth_id in expired_ids:
            _pending_authorizations.pop(auth_id, None)

    async def start_authorization(self, user_id: str, github_base_url: str | None, runtime_type: str | None = None) -> tuple[int, dict]:
        self._cleanup_expired()
        runtime_type_normalized = normalize_copilot_runtime_type(runtime_type)
        selected_client_id = COPILOT_OAUTH_CLIENT_IDS[runtime_type_normalized]
        oauth_base_url = "https://github.com"
        device_url = f"{oauth_base_url}/login/device/code"
        access_token_url = f"{oauth_base_url}/login/oauth/access_token"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(device_url, headers=self._HEADERS, json={"client_id": selected_client_id, "scope": "read:user"})
        except Exception as exc:
            logger.exception("Copilot start authorization failed")
            return 500, {"error": "Failed to start authorization", "details": sanitize_exception_message(exc)}

        if response.status_code not in (200, 201):
            return 502, {"error": "GitHub authorization start failed", "details": sanitize_exception_message(response.text)}
        data = self._safe_response_json(response)
        missing = [f for f in ["device_code", "user_code", "verification_uri"] if not data.get(f)]
        if missing:
            return 502, {"error": "GitHub authorization start failed", "details": f"Missing fields in GitHub response: {', '.join(missing)}"}

        auth_id = str(uuid4())
        expires_in = int(data.get("expires_in") or 900)
        interval = int(data.get("interval") or 5)
        created_at = self._utc_now()
        _pending_authorizations[auth_id] = {
            "auth_id": auth_id, "user_id": str(user_id), "device_code": data.get("device_code", ""), "user_code": data.get("user_code", ""),
            "verification_uri": data.get("verification_uri", ""), "oauth_base_url": oauth_base_url, "access_token_url": access_token_url, "runtime_type": runtime_type_normalized, "client_id": selected_client_id,
            "expires_at": created_at + timedelta(seconds=expires_in), "interval": interval, "status": "pending", "oauth": None, "created_at": created_at,
        }
        return 200, {"auth_id": auth_id, "device_code": data.get("device_code", ""), "user_code": data.get("user_code", ""),
                     "verification_url": data.get("verification_uri", ""), "verification_complete_url": data.get("verification_uri_complete") or data.get("verification_uri") or "",
                     "expires_in": expires_in, "interval": interval, "runtime_type": runtime_type_normalized}

    async def check_authorization(self, user_id: str, auth_id: str, device_code: str) -> tuple[int, dict]:
        auth_id = (auth_id or "").strip()
        device_code = (device_code or "").strip()
        self._cleanup_expired(exclude_auth_id=auth_id)
        if not auth_id or not device_code:
            return 400, {"error": "auth_id and device_code required"}
        record = _pending_authorizations.get(auth_id)
        if not record or str(record.get("user_id")) != str(user_id):
            return 404, {"error": "Authorization not found or expired"}
        if isinstance(record.get("expires_at"), datetime) and record["expires_at"] <= self._utc_now():
            _pending_authorizations.pop(auth_id, None)
            return 200, {"status": "expired", "message": "Authorization expired"}

        if record.get("status") == "authorized" and isinstance(record.get("oauth"), dict):
            oauth = record["oauth"]
            _pending_authorizations.pop(auth_id, None)
            return 200, {"status": "authorized", "oauth": oauth, "token": oauth.get("access", ""), "runtime_type": record.get("runtime_type", "opencode")}

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(record["access_token_url"], headers=self._HEADERS, json={"client_id": record.get("client_id") or COPILOT_OAUTH_CLIENT_IDS["opencode"], "device_code": record["device_code"], "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
        except Exception as exc:
            logger.exception("Copilot verify failed auth_id=%s", auth_id)
            return 500, {"status": "failed", "message": sanitize_exception_message(exc)}

        body = self._safe_response_json(response)
        error = body.get("error") if isinstance(body, dict) else None
        if error == "authorization_pending":
            return 200, {"status": "pending"}
        if error == "slow_down":
            record["interval"] = int(record.get("interval") or 5) + 5
            return 200, {"status": "pending", "interval": record["interval"]}
        if error == "expired_token":
            _pending_authorizations.pop(auth_id, None)
            return 200, {"status": "expired", "message": "Authorization expired"}
        if error in {"access_denied", "authorization_declined"}:
            _pending_authorizations.pop(auth_id, None)
            return 200, {"status": "declined", "message": "User declined authorization"}

        access_token = (body.get("access_token") or "").strip() if isinstance(body, dict) else ""
        if access_token:
            oauth = {"type": "oauth", "access": access_token, "refresh": access_token, "expires": 0}
            if record.get("oauth_base_url") and record["oauth_base_url"] != "https://github.com":
                oauth["enterpriseUrl"] = record["oauth_base_url"]
            record["status"] = "authorized"
            record["oauth"] = oauth
            _pending_authorizations.pop(auth_id, None)
            return 200, {"status": "authorized", "oauth": oauth, "token": access_token, "runtime_type": record.get("runtime_type", "opencode")}

        return 200, {"status": "failed", "message": sanitize_exception_message(error or f"GitHub API returned {response.status_code}")}


copilot_auth_service = CopilotAuthService()
