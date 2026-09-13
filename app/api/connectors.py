"""Per-member connector settings API (docs/CONNECTORS_CONTRACT.md §7)."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.schemas.connector import (
    ConnectorResponse,
    ConnectorUpdateRequest,
    ConnectorVerifyRequest,
    ConnectorVerifyResponse,
)
from app.services import connector_service

router = APIRouter(prefix="/api/connectors", tags=["connectors"])


def _require_feature() -> None:
    if not get_settings().connectors_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connectors are disabled")


@router.get("", response_model=list[ConnectorResponse])
def list_connectors(user=Depends(get_current_user), db: Session = Depends(get_db)):
    _require_feature()
    return [ConnectorResponse.model_validate(item) for item in connector_service.list_for_user(db, user)]


@router.get("/{connector_type}", response_model=ConnectorResponse)
def get_connector(connector_type: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _require_feature()
    try:
        entry = connector_service.get_for_user(db, user, connector_type)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown connector type")
    return ConnectorResponse.model_validate(entry)


@router.put("/{connector_type}", response_model=ConnectorResponse)
def update_connector(
    connector_type: str,
    payload: ConnectorUpdateRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_feature()
    try:
        entry = connector_service.update_for_user(
            db,
            user,
            connector_type,
            enabled=payload.enabled,
            config=payload.config,
        )
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown connector type")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return ConnectorResponse.model_validate(entry)


@router.post("/{connector_type}/verify", response_model=ConnectorVerifyResponse)
def verify_connector(
    connector_type: str,
    payload: ConnectorVerifyRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Record the outcome of the page's own connectivity test.

    The test runs in the member's browser (it is the only place that can reach
    the local bridge); Portal only remembers when it last succeeded.
    """

    _require_feature()
    try:
        result = connector_service.record_verification(
            db,
            user,
            connector_type,
            ok=payload.ok,
            details=payload.details,
        )
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown connector type")
    return ConnectorVerifyResponse.model_validate(result)
