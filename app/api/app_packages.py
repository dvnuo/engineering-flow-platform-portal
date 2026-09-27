"""App packages API: upload mobile builds to BrowserStack and list them.

The upload body is the raw file (not multipart) so it can be spooled to disk
as it arrives, whatever its size, and streamed on to BrowserStack; the name
comes in X-File-Name. See app/services/app_package_service.py.
"""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Optional
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.services.app_package_service import (
    AppPackageError,
    AppPackageService,
    browserstack_account_for_user,
    list_recent_browserstack_apps,
    package_view,
    platform_for_file,
    remote_app_view,
    spool_stream,
    _remove_quietly,
)

router = APIRouter(prefix="/api/app-packages", tags=["app-packages"])


class AppPackageFromUrlRequest(BaseModel):
    url: str
    custom_id: Optional[str] = None
    note: Optional[str] = None


def _http_error(exc: AppPackageError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("")
async def list_app_packages(remote: bool = False, user=Depends(get_current_user), db: Session = Depends(get_db)):
    service = AppPackageService(db)
    packages = [package_view(row) for row in service.list_for_user(user)]
    result: dict = {"packages": packages, "max_mb": service.settings.max_app_package_mb}
    if remote:
        # Builds already on BrowserStack (uploaded by CI or from its site) that
        # Portal has no row for; a failure here must not hide the local list.
        try:
            account = browserstack_account_for_user(db, user)
            known = {item["app_url"] for item in packages}
            result["remote"] = [
                view for view in (remote_app_view(item) for item in await list_recent_browserstack_apps(account))
                if view["app_url"] and view["app_url"] not in known
            ]
        except AppPackageError as exc:
            result["remote_error"] = exc.message
    return result


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_app_package(request: Request, user=Depends(get_current_user), db: Session = Depends(get_db)):
    file_name = PurePosixPath(unquote(request.headers.get("x-file-name") or "").replace("\\", "/")).name
    if not file_name or platform_for_file(file_name) is None:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Upload an .apk, .aab, or .ipa file.")
    service = AppPackageService(db)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > service.limit_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"The file is larger than {service.settings.max_app_package_mb} MB.",
        )
    try:
        # Check the connector before taking a few hundred megabytes of body.
        account = browserstack_account_for_user(db, user)
        path, sha256, size = await spool_stream(request.stream(), service.limit_bytes, PurePosixPath(file_name).suffix)
    except AppPackageError as exc:
        raise _http_error(exc) from exc
    try:
        row = await service.create_from_file(
            user,
            account,
            path,
            file_name,
            custom_id=request.query_params.get("custom_id"),
            note=request.query_params.get("note"),
            sha256=sha256,
            size=size,
        )
    except AppPackageError as exc:
        raise _http_error(exc) from exc
    finally:
        _remove_quietly(path)
    return package_view(row)


@router.post("/from-url", status_code=status.HTTP_201_CREATED)
async def upload_app_package_from_url(
    payload: AppPackageFromUrlRequest, user=Depends(get_current_user), db: Session = Depends(get_db)
):
    service = AppPackageService(db)
    try:
        account = browserstack_account_for_user(db, user)
        row = await service.create_from_url(user, account, payload.url, custom_id=payload.custom_id, note=payload.note)
    except AppPackageError as exc:
        raise _http_error(exc) from exc
    return package_view(row)


@router.delete("/{package_id}")
async def delete_app_package(package_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    service = AppPackageService(db)
    row = service.get_for_user(user, package_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="App package not found")
    try:
        account = browserstack_account_for_user(db, user)
        await service.delete(user, account, row)
    except AppPackageError as exc:
        raise _http_error(exc) from exc
    return {"ok": True}
