"""The Portal-wide model catalog (``GET /api/models/catalog``).

One list for every member and assistant: Copilot's catalog as the first
Copilot runtime reported it (see ``app/services/model_catalog_service.py``),
or the static table until one has. The connector form reads it for its model
picker; the composer gets the same list through ``chat-model-profile``.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.services.model_catalog_service import current_catalog

router = APIRouter(prefix="/api/models", tags=["models"])


class ModelCatalogResponse(BaseModel):
    source: str = "static"
    fetched_at: Optional[str] = None
    runtime_fetched_at: Optional[str] = None
    stale: bool = False
    provider: str = "github_copilot"
    models: list[dict[str, Any]] = Field(default_factory=list)
    model_labels: dict[str, str] = Field(default_factory=dict)
    model_context_windows: dict[str, int] = Field(default_factory=dict)


@router.get("/catalog", response_model=ModelCatalogResponse)
def get_model_catalog(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """The Portal-wide list, as last reported by a runtime (or the static table)."""

    _ = user  # any signed-in member sees the same list
    return current_catalog(db).payload()
