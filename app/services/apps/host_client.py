"""The app host's manager API (docs/CONTRACTS.md, section 4), as the Portal calls it.

The host is a service of the same cluster: calls go straight to
``APPS_HOST_INTERNAL_URL`` with the shared bearer token and never through an
outbound proxy. Three outcomes are told apart because a member reads them
differently: the host is not configured, the host cannot be reached (nothing
was changed there; trying again is right), and the host answered with an
error of its own (``code`` is the host's: ``slug_taken``, ``app_not_found``,
``deploy_in_progress``, ``release_not_found`` ...).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import json
import logging
from typing import Any, AsyncIterator, BinaryIO
from urllib.parse import quote

import httpx

from app.i18n import N_
from app.services.apps import settings as apps_settings
from app.services.apps.errors import AppHostError, AppHostNotConfigured, AppHostUnavailable
from app.services.tls_trust import trust_context

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 15.0
# Calls the host answers only after it stopped a process (it waits up to ten
# seconds for one to end) or removed a directory tree (slow on a network
# volume). With the default a slow stop read as "host unreachable" although
# the host had done it, and the Portal kept the state it had before.
SLOW_TIMEOUT_SECONDS = 90.0
UPLOAD_TIMEOUT_SECONDS = 300.0
STREAM_READ_TIMEOUT_SECONDS = 60.0
BUNDLE_FILENAME = "bundle.tar.gz"

# The host's own error codes a caller may want to tell apart.
HOST_SLUG_TAKEN = "slug_taken"
HOST_APP_NOT_FOUND = "app_not_found"
HOST_DEPLOY_IN_PROGRESS = "deploy_in_progress"
HOST_RELEASE_NOT_FOUND = "release_not_found"
HOST_UNAUTHORIZED = "unauthorized"


def _error_from_response(response: httpx.Response) -> AppHostError:
    code = ""
    message = ""
    try:
        data = response.json()
    except ValueError:
        data = None
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        code = str(error.get("code") or "").strip()
        message = str(error.get("message") or "").strip()
    if response.status_code == 401 or code == HOST_UNAUTHORIZED:
        return AppHostError(
            N_("The app host refused the Portal's token. Ask an administrator to check APPS_HOST_API_TOKEN on both sides."),
            code="host_unauthorized",
            host_code=code or HOST_UNAUTHORIZED,
            host_status=response.status_code,
        )
    reason = message or code or f"HTTP {response.status_code}"
    return AppHostError(code=f"host_{code}" if code else "host_error", reason=reason[:500], host_code=code, host_status=response.status_code)


class AppHostClient:
    """One method per call of the manager API. Holds no connection between calls."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        transport: httpx.BaseTransport | None = None,
        async_transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = (apps_settings.host_internal_url() if base_url is None else str(base_url or "")).rstrip("/")
        self.token = apps_settings.host_api_token() if token is None else str(token or "")
        self.transport = transport
        self.async_transport = async_transport
        self.timeout = timeout

    # ------------------------------------------------------------------ plumbing

    def _require_configured(self) -> None:
        if not self.base_url or not self.token:
            raise AppHostNotConfigured()

    def _client_kwargs(self, timeout: float | httpx.Timeout | None) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "headers": {"Authorization": f"Bearer {self.token}"},
            "timeout": timeout if timeout is not None else httpx.Timeout(self.timeout, connect=5.0),
            # A cluster service: the pod's proxy variables do not apply to it.
            "trust_env": False,
            "verify": trust_context(),
        }

    def _request(self, method: str, path: str, *, timeout: float | httpx.Timeout | None = None, **kwargs: Any) -> httpx.Response:
        self._require_configured()
        client_kwargs = self._client_kwargs(timeout)
        if self.transport is not None:
            client_kwargs["transport"] = self.transport
        try:
            with httpx.Client(**client_kwargs) as client:
                response = client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            # The exception text may carry the URL, never the token (it is a header).
            logger.info("app host %s %s failed: %s", method, path, exc.__class__.__name__)
            raise AppHostUnavailable() from exc
        if response.status_code >= 400:
            raise _error_from_response(response)
        return response

    def _json(self, method: str, path: str, **kwargs: Any) -> dict:
        response = self._request(method, path, **kwargs)
        try:
            data = response.json()
        except ValueError:
            raise AppHostError(reason="the app host did not answer with JSON") from None
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _app_path(app_id: str, suffix: str = "") -> str:
        return f"/v1/apps/{quote(str(app_id), safe='')}{suffix}"

    @staticmethod
    def _slow() -> httpx.Timeout:
        """The timeout of a call in which the host stops a process or removes files before it answers."""
        return httpx.Timeout(SLOW_TIMEOUT_SECONDS, connect=5.0)

    # ---------------------------------------------------------------------- apps

    def put_app(self, app_id: str, spec: dict) -> dict:
        """4.1: create or update the app's record; the status object.

        The host stops the app when the record disables it and restarts it
        when its environment changed, before it answers.
        """
        return self._json("PUT", self._app_path(app_id), json=spec, timeout=self._slow())

    def deploy(self, app_id: str, spec: dict, bundle: BinaryIO) -> str:
        """4.2: send the bundle; the host job's id."""
        files = {
            "spec": (None, json.dumps(spec), "application/json"),
            "bundle": (BUNDLE_FILENAME, bundle, "application/gzip"),
        }
        data = self._json(
            "POST",
            self._app_path(app_id, "/releases"),
            files=files,
            timeout=httpx.Timeout(UPLOAD_TIMEOUT_SECONDS, connect=5.0),
        )
        job_id = str(data.get("job_id") or "").strip()
        if not job_id:
            raise AppHostError(reason="the app host did not name a deployment job")
        return job_id

    def get_job(self, job_id: str) -> dict:
        """4.3: the job with its steps, error and log tail."""
        return self._json("GET", f"/v1/jobs/{quote(str(job_id), safe='')}")

    def get_job_log(self, job_id: str, offset: int = 0) -> tuple[str, int]:
        """4.3: the job's log from byte ``offset`` on, and the log's size."""
        response = self._request("GET", f"/v1/jobs/{quote(str(job_id), safe='')}/log", params={"offset": max(0, int(offset))})
        try:
            size = int(response.headers.get("X-Log-Size") or 0)
        except ValueError:
            size = 0
        return response.text, size

    def activate(self, app_id: str, sha: str) -> dict:
        """4.4: switch to a retained release."""
        return self._json("POST", self._app_path(app_id, "/activate"), json={"sha": sha}, timeout=self._slow())

    def start(self, app_id: str) -> dict:
        return self._json("POST", self._app_path(app_id, "/start"))

    def stop(self, app_id: str) -> dict:
        return self._json("POST", self._app_path(app_id, "/stop"), timeout=self._slow())

    def restart(self, app_id: str) -> dict:
        return self._json("POST", self._app_path(app_id, "/restart"), timeout=self._slow())

    def put_env(self, app_id: str, env: dict[str, str]) -> dict:
        """4.4: replace the environment; the app restarts when it runs."""
        return self._json("PUT", self._app_path(app_id, "/env"), json={"env": env}, timeout=self._slow())

    def delete_data(self, app_id: str) -> dict:
        return self._json("DELETE", self._app_path(app_id, "/data"), timeout=self._slow())

    def delete_app(self, app_id: str, *, purge_data: bool = False) -> dict:
        return self._json(
            "DELETE",
            self._app_path(app_id),
            params={"purge_data": "true" if purge_data else "false"},
            timeout=self._slow(),
        )

    def get_app(self, app_id: str) -> dict:
        """4.6: the app's status object."""
        return self._json("GET", self._app_path(app_id))

    def list_apps(self) -> list[dict]:
        apps = self._json("GET", "/v1/apps").get("apps")
        return [item for item in apps if isinstance(item, dict)] if isinstance(apps, list) else []

    def host_info(self) -> dict:
        return self._json("GET", "/v1/host")

    def revoke_sessions(self, user_id: int) -> dict:
        """4.4: end every gateway session of that member."""
        return self._json("POST", "/v1/sessions/revoke", json={"user_id": int(user_id)})

    # ---------------------------------------------------------------------- logs

    def logs(self, app_id: str, tail: int = 200) -> dict:
        """4.5: ``{"lines": [...], "truncated": bool}``."""
        data = self._json("GET", self._app_path(app_id, "/logs"), params={"tail": max(1, min(int(tail), 5000))})
        lines = data.get("lines")
        return {
            "lines": [str(line) for line in lines] if isinstance(lines, list) else [],
            "truncated": bool(data.get("truncated")),
        }

    @asynccontextmanager
    async def stream_logs(self, app_id: str) -> AsyncIterator[AsyncIterator[bytes]]:
        """4.5: the host's ``text/event-stream`` of new log lines, as raw chunks to pass on.

        Raises before yielding when the host is not configured, cannot be
        reached or refuses; once the stream is open a broken connection just
        ends it.
        """
        self._require_configured()
        client_kwargs = self._client_kwargs(httpx.Timeout(STREAM_READ_TIMEOUT_SECONDS, connect=5.0))
        if self.async_transport is not None:
            client_kwargs["transport"] = self.async_transport
        async with httpx.AsyncClient(**client_kwargs) as client:
            try:
                request = client.build_request("GET", self._app_path(app_id, "/logs/stream"), headers={"Accept": "text/event-stream"})
                response = await client.send(request, stream=True)
            except httpx.HTTPError as exc:
                raise AppHostUnavailable() from exc
            try:
                if response.status_code >= 400:
                    await response.aread()
                    raise _error_from_response(response)

                async def chunks() -> AsyncIterator[bytes]:
                    try:
                        async for chunk in response.aiter_bytes():
                            yield chunk
                    except httpx.HTTPError:
                        return

                yield chunks()
            finally:
                await response.aclose()
