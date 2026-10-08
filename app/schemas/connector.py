from typing import Any, Optional

from pydantic import BaseModel, Field


class ConnectorResponse(BaseModel):
    type: str
    label: str
    kind: str
    category: str
    description: str
    icon: str = "plug"
    enabled: bool
    # "connected" | "off" | "not_set_up", and how the list says it.
    state: str = "not_set_up"
    status_label: str = ""
    # Settings connectors: "system" (follows the administrator's Default
    # connectors) or "custom" (the member's own values); null for local ones.
    mode: Optional[str] = None
    config: dict[str, Any] = Field(default_factory=dict)
    # Deployment-level values for the page (for local_bridge: start_url).
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
