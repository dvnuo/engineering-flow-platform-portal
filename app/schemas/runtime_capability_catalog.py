from datetime import datetime
from pydantic import BaseModel
from typing import Optional


class RuntimeCapabilityCatalogSyncRequest(BaseModel):
    agent_id: str


class RuntimeCapabilityCatalogSnapshotResponse(BaseModel):
    id: str
    source_agent_id: Optional[str] = None
    catalog_version: Optional[str] = None
    catalog_source: str
    fetched_at: datetime
    payload_json: str

    class Config:
        from_attributes = True
