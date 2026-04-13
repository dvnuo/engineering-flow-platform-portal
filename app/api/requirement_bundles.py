from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.deps import get_current_user
from app.schemas.requirement_bundle import (
    RequirementBundleCreateRequest,
    RequirementBundleInspectResponse,
    RequirementBundleListItem,
)
from app.services.requirement_bundle_github_service import (
    RequirementBundleGithubService,
    RequirementBundleGithubServiceError,
)

router = APIRouter(prefix="/api/requirement-bundles", tags=["requirement-bundles"])
requirement_bundle_service = RequirementBundleGithubService()


@router.get("", response_model=list[RequirementBundleListItem])
def list_requirement_bundles(refresh: bool = Query(default=False), user=Depends(get_current_user)):
    _ = user
    try:
        return requirement_bundle_service.list_bundles(force_refresh=refresh)
    except RequirementBundleGithubServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("", response_model=RequirementBundleInspectResponse)
def create_requirement_bundle(payload: RequirementBundleCreateRequest, user=Depends(get_current_user)):
    _ = user
    try:
        bundle_ref = requirement_bundle_service.create_bundle(payload)
        return requirement_bundle_service.inspect_bundle(bundle_ref)
    except RequirementBundleGithubServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
