import json

import httpx
from sqlalchemy.orm import Session

from app.repositories.agent_repo import AgentRepository
from app.repositories.runtime_capability_catalog_snapshot_repo import RuntimeCapabilityCatalogSnapshotRepository
from app.services.proxy_service import ProxyService


class RuntimeCapabilitySyncError(Exception):
    pass


class RuntimeCapabilitySyncService:
    def __init__(self) -> None:
        self.proxy_service = ProxyService()

    def sync_from_agent_runtime(self, db: Session, agent_id: str):
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise RuntimeCapabilitySyncError("Agent not found")

        try:
            base_url = self.proxy_service.build_agent_base_url(agent).rstrip("/")
        except Exception as exc:
            raise RuntimeCapabilitySyncError(f"Unable to resolve runtime endpoint: {exc}") from exc

        headers = self.proxy_service.build_runtime_internal_headers()

        try:
            response = httpx.get(f"{base_url}/api/capabilities", timeout=15.0, headers=headers)
        except Exception as exc:
            raise RuntimeCapabilitySyncError(f"Runtime capabilities API is unreachable: {exc}") from exc

        if response.status_code >= 400:
            raise RuntimeCapabilitySyncError(f"Runtime capabilities API returned HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeCapabilitySyncError("Runtime capabilities API returned invalid JSON") from exc

        catalog_version = None
        if isinstance(payload, dict):
            catalog_version = payload.get("catalog_version") or payload.get("version")

        snapshot = RuntimeCapabilityCatalogSnapshotRepository(db).create(
            source_agent_id=agent_id,
            catalog_version=str(catalog_version) if catalog_version else None,
            catalog_source="runtime_api",
            payload_json=json.dumps(payload),
        )
        return snapshot
