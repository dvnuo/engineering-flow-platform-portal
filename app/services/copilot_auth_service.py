from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import httpx

from app.utils.github_url import normalize_github_api_base_url

logger = logging.getLogger(__name__)

_pending_authorizations: dict[str, dict[str, Any]] = {}


class CopilotAuthService:
    _HEADERS = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    def _utc_now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _cleanup_expired(self, exclude_auth_id: str | None = None) -> None:
        now = self._utc_now()
        expired_ids = [
            auth_id
            for auth_id, record in _pending_authorizations.items()
            if auth_id != exclude_auth_id and isinstance(record.get("expires_at"), datetime) and record["expires_at"] <= now
        ]
        for auth_id in expired_ids:
            _pending_authorizations.pop(auth_id, None)

    async def start_authorization(self, user_id: str, github_base_url: str | None) -> tuple[int, dict]:
        self._cleanup_expired()
        api_base_url = normalize_github_api_base_url(github_base_url)
        url = f"{api_base_url}/copilot/token_verification"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(url, headers=self._HEADERS, json={"action": "create"})
        except Exception as exc:  # pragma: no cover - safety net
            logger.exception("Copilot start authorization failed")
            return 500, {"error": "Failed to start authorization", "details": str(exc)}

        if response.status_code != 201:
            details = response.text
            try:
                details_json = response.json()
                if isinstance(details_json, dict):
                    details = details_json.get("error_description") or details_json.get("error") or str(details_json)
            except Exception:
                pass
            return 502, {"error": "GitHub authorization start failed", "details": details}

        data = response.json() if response.content else {}
        auth_id = str(uuid4())
        expires_in = int(data.get("expires_in") or 600)
        interval = int(data.get("interval") or 5)
        created_at = self._utc_now()

        _pending_authorizations[auth_id] = {
            "auth_id": auth_id,
            "user_id": str(user_id),
            "device_code": data.get("device_code", ""),
            "user_code": data.get("user_code", ""),
            "verification_uri": data.get("verification_uri", ""),
            "verification_uri_complete": data.get("verification_uri_complete", ""),
            "expires_at": created_at + timedelta(seconds=expires_in),
            "interval": interval,
            "status": "pending",
            "token": None,
            "api_base_url": api_base_url,
            "created_at": created_at,
        }
        logger.info("Created Copilot auth session auth_id=%s user_id=%s", auth_id, user_id)

        return 200, {
            "auth_id": auth_id,
            "device_code": data.get("device_code", ""),
            "user_code": data.get("user_code", ""),
            "verification_url": data.get("verification_uri", ""),
            "verification_complete_url": data.get("verification_uri_complete", ""),
            "expires_in": expires_in,
            "interval": interval,
        }

    async def check_authorization(self, user_id: str, auth_id: str, device_code: str) -> tuple[int, dict]:
        auth_id = (auth_id or "").strip()
        self._cleanup_expired(exclude_auth_id=auth_id)

        device_code = (device_code or "").strip()
        if not auth_id or not device_code:
            return 400, {"error": "auth_id and device_code required"}

        record = _pending_authorizations.get(auth_id)
        if not record:
            return 404, {"error": "Authorization not found or expired"}

        if str(record.get("user_id")) != str(user_id):
            return 404, {"error": "Authorization not found or expired"}

        if isinstance(record.get("expires_at"), datetime) and record["expires_at"] <= self._utc_now():
            _pending_authorizations.pop(auth_id, None)
            return 200, {"status": "expired", "message": "Authorization expired"}

        if record.get("status") == "authorized" and record.get("token"):
            token = record.get("token")
            _pending_authorizations.pop(auth_id, None)
            return 200, {"status": "authorized", "token": token}

        verify_url = f"{record.get('api_base_url', 'https://api.github.com')}/copilot/token_verification"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    verify_url,
                    headers=self._HEADERS,
                    json={"action": "verify", "device_code": device_code},
                )
        except Exception as exc:  # pragma: no cover - safety net
            logger.exception("Copilot verify failed auth_id=%s", auth_id)
            return 500, {"status": "failed", "message": str(exc)}

        body = {}
        try:
            body = response.json() if response.content else {}
        except Exception:
            body = {}

        if response.status_code == 200:
            token = body.get("token")
            if not token:
                _pending_authorizations.pop(auth_id, None)
                return 200, {"status": "failed", "message": "Missing token in authorization response"}
            record["status"] = "authorized"
            record["token"] = token
            _pending_authorizations.pop(auth_id, None)
            return 200, {"status": "authorized", "token": token}

        if response.status_code == 400:
            error = body.get("error") if isinstance(body, dict) else "unknown_error"
            if error == "authorization_pending":
                return 200, {"status": "pending"}
            if error == "expired_token":
                _pending_authorizations.pop(auth_id, None)
                return 200, {"status": "expired", "message": "Device code expired"}
            if error == "authorization_declined":
                _pending_authorizations.pop(auth_id, None)
                return 200, {"status": "declined", "message": "User declined authorization"}
            return 200, {"status": "failed", "message": str(error or "Authorization failed")}

        return 200, {"status": "failed", "message": f"GitHub API returned {response.status_code}"}


copilot_auth_service = CopilotAuthService()
