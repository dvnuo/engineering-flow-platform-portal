from typing import Any, Optional

from pydantic import BaseModel, Field


class ConnectorResponse(BaseModel):
    type: str
    label: str
    kind: str
    category: str
    description: str
    enabled: bool
    config: dict[str, Any] = Field(default_factory=dict)
    # Deployment-level values for the page (for local_browser: start_url).
    settings: dict[str, Any] = Field(default_factory=dict)
    last_verified_at: Optional[str] = None


class ConnectorUpdateRequest(BaseModel):
    enabled: bool
    config: dict[str, Any] = Field(default_factory=dict)


class ConnectorVerifyRequest(BaseModel):
    ok: bool
    details: dict[str, Any] = Field(default_factory=dict)


class ConnectorVerifyResponse(BaseModel):
    ok: bool
    last_verified_at: Optional[str] = None
