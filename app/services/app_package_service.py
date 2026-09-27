"""App packages: mobile builds a member uploads to BrowserStack through Portal.

Portal streams the file straight on to BrowserStack App Automate and keeps
only the reference (bs:// URL, custom id, note). The assistant never sees the
file; a recording or a run names the package and mobile-auto starts the
session from its bs:// URL or custom id. BrowserStack deletes uploads after 30
days, which is the expiry shown here.

The member's own BrowserStack connector supplies the credentials, so an
upload lands in the account their assistants use.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.app_package import AppPackage
from app.schemas.runtime_profile import parse_runtime_profile_config_json
from app.services.outbound_http import browserstack_client_kwargs
from app.services.runtime_profile_service import RuntimeProfileService

APP_PACKAGE_RETENTION = timedelta(days=30)
DEFAULT_API_BASE_URL = "https://api-cloud.browserstack.com"
DEFAULT_HUB_URL = "https://hub-cloud.browserstack.com/wd/hub"
PLATFORM_BY_EXTENSION = {".apk": "android", ".aab": "android", ".ipa": "ios"}
_CUSTOM_ID_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_UPLOAD_TIMEOUT_SECONDS = 900.0
_API_TIMEOUT_SECONDS = 30.0


class AppPackageError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class BrowserStackAccount:
    username: str
    access_key: str
    api_base_url: str
    hub_url: str

    @property
    def auth(self) -> tuple[str, str]:
        return (self.username, self.access_key)


def browserstack_account_for_user(db: Session, user) -> BrowserStackAccount:
    """The BrowserStack account in the member's connector, or a clear error."""

    profile = RuntimeProfileService(db).get_or_create_for_user(user)
    config = parse_runtime_profile_config_json(getattr(profile, "config_json", None), fallback_to_empty=True)
    mobile = config.get("mobile-auto") if isinstance(config.get("mobile-auto"), dict) else {}
    if not mobile.get("enabled"):
        raise AppPackageError("Turn the BrowserStack connector on first (Connectors > BrowserStack).", 409)
    bs = mobile.get("browserstack") if isinstance(mobile.get("browserstack"), dict) else {}
    username = str(bs.get("username") or "").strip()
    access_key = str(bs.get("access_key") or "").strip()
    if not username or not access_key:
        raise AppPackageError("Add your BrowserStack username and access key in Connectors > BrowserStack.", 409)
    return BrowserStackAccount(
        username=username,
        access_key=access_key,
        api_base_url=str(bs.get("api_base_url") or DEFAULT_API_BASE_URL).strip().rstrip("/"),
        hub_url=str(bs.get("appium_base_url") or DEFAULT_HUB_URL).strip().rstrip("/"),
    )


def platform_for_file(file_name: str) -> Optional[str]:
    return PLATFORM_BY_EXTENSION.get(PurePosixPath(str(file_name or "").lower()).suffix)


def sanitize_custom_id(value: Optional[str]) -> Optional[str]:
    """BrowserStack custom ids: letters, digits, dot, dash, underscore; 100 max."""

    cleaned = _CUSTOM_ID_UNSAFE.sub("-", str(value or "").strip()).strip("-.")
    return cleaned[:100] or None


def default_custom_id(file_name: str, platform: str) -> Optional[str]:
    stem = PurePosixPath(str(file_name or "")).stem
    # Drop a trailing version so every build of the app shares one custom id:
    # fxapp-1.4.2-uat -> fxapp-uat, fxapp_2026.09.27 -> fxapp.
    stem = re.sub(r"[-_.]v?\d+(?:[._]\d+)*(?=$|[-_])", "", stem)
    base = sanitize_custom_id(stem.lower())
    if not base:
        return None
    return base if base.endswith(platform) else f"{base}-{platform}"[:100]


def app_id_from_url(app_url: str) -> str:
    return str(app_url or "").removeprefix("bs://").strip()


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        return str(body.get("error") or body.get("message") or "")[:240]
    return response.text[:240]


async def upload_file_to_browserstack(
    account: BrowserStackAccount, path: str, file_name: str, custom_id: Optional[str]
) -> dict[str, Any]:
    data = {"custom_id": custom_id} if custom_id else {}
    try:
        async with httpx.AsyncClient(**browserstack_client_kwargs(timeout=_UPLOAD_TIMEOUT_SECONDS)) as client:
            with open(path, "rb") as fh:
                response = await client.post(
                    f"{account.api_base_url}/app-automate/upload",
                    auth=account.auth,
                    files={"file": (file_name, fh, "application/octet-stream")},
                    data=data,
                )
    except httpx.HTTPError as exc:
        raise AppPackageError(f"Could not reach BrowserStack: {exc.__class__.__name__}", 502) from exc
    return _upload_result(response)


async def upload_url_to_browserstack(account: BrowserStackAccount, url: str, custom_id: Optional[str]) -> dict[str, Any]:
    """BrowserStack downloads a public URL itself; nothing passes through Portal."""

    data = {"url": url}
    if custom_id:
        data["custom_id"] = custom_id
    try:
        async with httpx.AsyncClient(**browserstack_client_kwargs(timeout=_UPLOAD_TIMEOUT_SECONDS)) as client:
            response = await client.post(f"{account.api_base_url}/app-automate/upload", auth=account.auth, data=data)
    except httpx.HTTPError as exc:
        raise AppPackageError(f"Could not reach BrowserStack: {exc.__class__.__name__}", 502) from exc
    return _upload_result(response)


def _upload_result(response: httpx.Response) -> dict[str, Any]:
    if response.status_code in (401, 403):
        raise AppPackageError("BrowserStack refused the credentials in your BrowserStack connector.", 502)
    if response.status_code >= 400:
        raise AppPackageError(f"BrowserStack rejected the upload (HTTP {response.status_code}): {_error_detail(response)}", 502)
    try:
        body = response.json()
    except ValueError as exc:
        raise AppPackageError("BrowserStack answered the upload with something other than JSON.", 502) from exc
    app_url = str(body.get("app_url") or "") if isinstance(body, dict) else ""
    if not app_url.startswith("bs://"):
        raise AppPackageError("BrowserStack did not return an app URL for the upload.", 502)
    return body


async def list_recent_browserstack_apps(account: BrowserStackAccount) -> list[dict[str, Any]]:
    try:
        async with httpx.AsyncClient(**browserstack_client_kwargs(timeout=_API_TIMEOUT_SECONDS)) as client:
            response = await client.get(f"{account.api_base_url}/app-automate/recent_apps", auth=account.auth)
    except httpx.HTTPError as exc:
        raise AppPackageError(f"Could not reach BrowserStack: {exc.__class__.__name__}", 502) from exc
    if response.status_code >= 400:
        raise AppPackageError(f"BrowserStack could not list apps (HTTP {response.status_code}).", 502)
    try:
        body = response.json()
    except ValueError:
        return []
    # An account with no uploads answers {"message": "No results found"}.
    return [item for item in body if isinstance(item, dict)] if isinstance(body, list) else []


async def delete_browserstack_app(account: BrowserStackAccount, app_url: str) -> None:
    app_id = app_id_from_url(app_url)
    if not app_id:
        return
    try:
        async with httpx.AsyncClient(**browserstack_client_kwargs(timeout=_API_TIMEOUT_SECONDS)) as client:
            response = await client.delete(f"{account.api_base_url}/app-automate/app/delete/{app_id}", auth=account.auth)
    except httpx.HTTPError as exc:
        raise AppPackageError(f"Could not reach BrowserStack: {exc.__class__.__name__}", 502) from exc
    # 404: BrowserStack already dropped it (expired); the reference can go.
    if response.status_code >= 400 and response.status_code != 404:
        raise AppPackageError(f"BrowserStack could not delete the app (HTTP {response.status_code}).", 502)


def ci_auth_for_url(config: dict[str, Any], url: str) -> Optional[tuple[str, str] | bool]:
    """Credentials for a build URL on one of the member's Jenkins or Nexus instances.

    Returns (user, secret), True for a matching instance without credentials,
    or None when the URL is on neither. Only those URLs are fetched by Portal:
    anything else goes to BrowserStack to download, so Portal never becomes a
    way to reach arbitrary hosts inside the cluster.
    """

    for section in ("jenkins", "nexus"):
        section_cfg = config.get(section) if isinstance(config.get(section), dict) else {}
        if not section_cfg.get("enabled"):
            continue
        for instance in section_cfg.get("instances") or []:
            if not isinstance(instance, dict) or instance.get("enabled") is False:
                continue
            base = str(instance.get("url") or "").strip().rstrip("/")
            if not base or not (url == base or url.startswith(base + "/")):
                continue
            username = str(instance.get("username") or "").strip()
            secret = str(instance.get("token") or instance.get("password") or "").strip()
            return (username, secret) if username and secret else True
    return None


async def spool_stream(chunks: AsyncIterator[bytes], limit_bytes: int, suffix: str) -> tuple[str, str, int]:
    """Write a request body to a temporary file, hashing it on the way.

    Returns (path, sha256, size). Raises AppPackageError(413) past the limit;
    the caller removes the file.
    """

    digest = hashlib.sha256()
    size = 0
    fd, path = tempfile.mkstemp(prefix="efp-app-", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as out:
            async for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                if size > limit_bytes:
                    raise AppPackageError(f"The file is larger than {limit_bytes // (1024 * 1024)} MB.", 413)
                digest.update(chunk)
                out.write(chunk)
    except BaseException:
        _remove_quietly(path)
        raise
    if size == 0:
        _remove_quietly(path)
        raise AppPackageError("The upload was empty.", 400)
    return path, digest.hexdigest(), size


def _remove_quietly(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


class AppPackageService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()

    @property
    def limit_bytes(self) -> int:
        return max(1, int(self.settings.max_app_package_mb)) * 1024 * 1024

    def list_for_user(self, user) -> list[AppPackage]:
        query = select(AppPackage).where(AppPackage.owner_user_id == user.id).order_by(AppPackage.uploaded_at.desc())
        return list(self.db.scalars(query).all())

    def get_for_user(self, user, package_id: str) -> Optional[AppPackage]:
        row = self.db.get(AppPackage, package_id)
        return row if row is not None and row.owner_user_id == user.id else None

    def _record(self, user, *, platform: str, result: dict[str, Any], file_name: str, custom_id: Optional[str],
                note: Optional[str], sha256: Optional[str], size: Optional[int], source: str,
                source_url: Optional[str]) -> AppPackage:
        now = datetime.utcnow()
        app_url = str(result.get("app_url"))
        # Re-uploading the same file returns the same bs:// URL; keep one row.
        existing = self.db.scalar(
            select(AppPackage).where(AppPackage.owner_user_id == user.id, AppPackage.app_url == app_url)
        )
        row = existing or AppPackage(owner_user_id=user.id)
        row.platform = platform
        row.app_url = app_url
        row.custom_id = str(result.get("custom_id") or custom_id or "") or None
        row.file_name = file_name[:255]
        row.note = (note or "").strip()[:255] or (existing.note if existing else None)
        row.sha256 = sha256
        row.size_bytes = size
        row.source = source
        row.source_url = source_url[:1024] if source_url else None
        row.uploaded_at = now
        row.expires_at = now + APP_PACKAGE_RETENTION
        if existing is None:
            self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    async def create_from_file(self, user, account: BrowserStackAccount, path: str, file_name: str, *,
                               custom_id: Optional[str], note: Optional[str], sha256: Optional[str], size: Optional[int],
                               source: str = "upload", source_url: Optional[str] = None) -> AppPackage:
        platform = platform_for_file(file_name)
        if platform is None:
            raise AppPackageError("Upload an .apk, .aab, or .ipa file.", 415)
        custom = sanitize_custom_id(custom_id) or default_custom_id(file_name, platform)
        result = await upload_file_to_browserstack(account, path, file_name, custom)
        return self._record(user, platform=platform, result=result, file_name=file_name, custom_id=custom, note=note,
                            sha256=sha256, size=size, source=source, source_url=source_url)

    async def create_from_url(self, user, account: BrowserStackAccount, url: str, *, custom_id: Optional[str],
                              note: Optional[str]) -> AppPackage:
        url = str(url or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise AppPackageError("Give an http(s) URL of the build.", 400)
        file_name = PurePosixPath(parsed.path).name or "app"
        platform = platform_for_file(file_name)
        if platform is None:
            raise AppPackageError("The URL must end in the .apk, .aab, or .ipa file name.", 415)
        custom = sanitize_custom_id(custom_id) or default_custom_id(file_name, platform)
        profile = RuntimeProfileService(self.db).get_or_create_for_user(user)
        config = parse_runtime_profile_config_json(getattr(profile, "config_json", None), fallback_to_empty=True)
        auth = ci_auth_for_url(config, url)
        if auth is None:
            if parsed.scheme != "https":
                raise AppPackageError("Only https URLs can be handed to BrowserStack.", 400)
            result = await upload_url_to_browserstack(account, url, custom)
            return self._record(user, platform=platform, result=result, file_name=file_name, custom_id=custom, note=note,
                                sha256=None, size=None, source="url", source_url=url)
        path, sha256, size = await self._download(url, auth)
        try:
            return await self.create_from_file(user, account, path, file_name, custom_id=custom, note=note, sha256=sha256,
                                               size=size, source="url", source_url=url)
        finally:
            _remove_quietly(path)

    async def _download(self, url: str, auth) -> tuple[str, str, int]:
        credentials = auth if isinstance(auth, tuple) else None
        suffix = PurePosixPath(urlparse(url).path).suffix
        try:
            async with httpx.AsyncClient(timeout=_UPLOAD_TIMEOUT_SECONDS, follow_redirects=False) as client:
                async with client.stream("GET", url, auth=credentials) as response:
                    if response.status_code >= 400:
                        raise AppPackageError(f"Could not download the build (HTTP {response.status_code}).", 502)
                    return await spool_stream(response.aiter_bytes(), self.limit_bytes, suffix)
        except httpx.HTTPError as exc:
            raise AppPackageError(f"Could not download the build: {exc.__class__.__name__}", 502) from exc

    async def delete(self, user, account: BrowserStackAccount, package: AppPackage) -> None:
        await delete_browserstack_app(account, package.app_url)
        self.db.delete(package)
        self.db.commit()


def package_view(row: AppPackage, *, now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or datetime.utcnow()
    days_left = (row.expires_at - now).days if row.expires_at else None
    return {
        "id": row.id,
        "platform": row.platform,
        "app_url": row.app_url,
        "custom_id": row.custom_id,
        "file_name": row.file_name,
        "note": row.note,
        "size_bytes": row.size_bytes,
        "sha256": row.sha256,
        "source": row.source,
        "uploaded_at": row.uploaded_at.isoformat() + "Z" if row.uploaded_at else None,
        "expires_at": row.expires_at.isoformat() + "Z" if row.expires_at else None,
        "days_left": days_left,
        "expired": bool(row.expires_at and row.expires_at <= now),
        "expiring_soon": bool(row.expires_at and row.expires_at > now and days_left is not None and days_left < 3),
    }


def remote_app_view(item: dict[str, Any]) -> dict[str, Any]:
    name = str(item.get("app_name") or "")
    return {
        "app_url": str(item.get("app_url") or ""),
        "custom_id": str(item.get("custom_id") or "") or None,
        "file_name": name,
        "app_version": str(item.get("app_version") or "") or None,
        "platform": platform_for_file(name),
        "uploaded_at": str(item.get("uploaded_at") or "") or None,
    }
