from dataclasses import dataclass
import json

from app.contracts.runtime_capabilities import get_default_runtime_adapter_actions_by_system


def normalize_action_name(value: str | None) -> str:
    return (value or "").strip().lower()


@dataclass(frozen=True)
class RuntimeCapabilityCatalogEntry:
    capability_id: str
    capability_type: str
    action_alias: str | None = None


class RuntimeCapabilityCatalogProvider:
    def __init__(self, entries: list[RuntimeCapabilityCatalogEntry]):
        self._action_aliases_to_ids: dict[str, list[str]] = {}
        self._known_adapter_capability_ids: set[str] = set()

        for entry in entries:
            normalized_capability_id = normalize_action_name(entry.capability_id)
            if not normalized_capability_id:
                continue

            if normalized_capability_id.startswith("adapter:"):
                self._known_adapter_capability_ids.add(normalized_capability_id)

            normalized_alias = normalize_action_name(entry.action_alias)
            if normalized_alias:
                self._action_aliases_to_ids.setdefault(normalized_alias, []).append(normalized_capability_id)

    @classmethod
    def from_seed_mapping(cls, actions_by_system: dict[str, dict[str, str]]) -> "RuntimeCapabilityCatalogProvider":
        entries: list[RuntimeCapabilityCatalogEntry] = []
        for action_map in actions_by_system.values():
            for action_alias, capability_id in action_map.items():
                entries.append(
                    RuntimeCapabilityCatalogEntry(
                        capability_id=capability_id,
                        capability_type="adapter_action",
                        action_alias=action_alias,
                    )
                )
        return cls(entries)

    @classmethod
    def from_runtime_catalog_payload(cls, payload: list[dict]) -> "RuntimeCapabilityCatalogProvider":
        entries: list[RuntimeCapabilityCatalogEntry] = []
        for item in payload:
            capability_id = item.get("capability_id") or item.get("id")
            capability_type = item.get("capability_type") or item.get("type") or ""
            action_alias = item.get("action_alias") or item.get("name")
            if not isinstance(capability_id, str):
                continue
            entries.append(
                RuntimeCapabilityCatalogEntry(
                    capability_id=capability_id,
                    capability_type=str(capability_type),
                    action_alias=action_alias if isinstance(action_alias, str) else None,
                )
            )
        return cls(entries)

    def list_known_action_aliases(self) -> set[str]:
        return set(self._action_aliases_to_ids.keys())

    def list_known_adapter_capability_ids(self) -> set[str]:
        return set(self._known_adapter_capability_ids)

    def resolve_action_to_capability_id(self, action_name: str | None) -> str | None:
        normalized = normalize_action_name(action_name)
        if not normalized:
            return None
        if normalized.startswith("adapter:"):
            parts = normalized.split(":")
            if len(parts) >= 3 and all(parts):
                return normalized
            return None

        candidates = self._action_aliases_to_ids.get(normalized, [])
        if len(candidates) == 1:
            return candidates[0]
        return None


def build_default_runtime_capability_catalog_provider() -> RuntimeCapabilityCatalogProvider:
    return RuntimeCapabilityCatalogProvider.from_seed_mapping(get_default_runtime_adapter_actions_by_system())


class RuntimeCapabilityCatalogLoader:
    """Canonical source/loader for building capability catalog providers.

    Runtime snapshot payload (when present) is the preferred source.
    Static seed mappings are a deterministic fallback.
    """
    def __init__(self, runtime_catalog_snapshot_payload: list[dict] | None = None):
        self.runtime_catalog_snapshot_payload = runtime_catalog_snapshot_payload

    def build_provider(self) -> RuntimeCapabilityCatalogProvider:
        if self.runtime_catalog_snapshot_payload:
            return RuntimeCapabilityCatalogProvider.from_runtime_catalog_payload(self.runtime_catalog_snapshot_payload)
        return build_default_runtime_capability_catalog_provider()

    @classmethod
    def from_snapshot_json(cls, snapshot_json: str | None) -> "RuntimeCapabilityCatalogLoader":
        if not snapshot_json or not snapshot_json.strip():
            return cls(runtime_catalog_snapshot_payload=None)
        try:
            parsed = json.loads(snapshot_json)
        except json.JSONDecodeError:
            return cls(runtime_catalog_snapshot_payload=None)
        if not isinstance(parsed, list):
            return cls(runtime_catalog_snapshot_payload=None)
        return cls(runtime_catalog_snapshot_payload=parsed)


def build_runtime_capability_catalog_provider(runtime_catalog_snapshot_payload: list[dict] | None = None) -> RuntimeCapabilityCatalogProvider:
    return RuntimeCapabilityCatalogLoader(runtime_catalog_snapshot_payload=runtime_catalog_snapshot_payload).build_provider()


def build_runtime_capability_catalog_loader_from_settings(snapshot_json: str | None = None) -> RuntimeCapabilityCatalogLoader:
    """Main-path loader construction for service wiring.

    This allows runtime snapshot data to be injected via settings while keeping
    local seed-data fallback behavior for resilience.
    """
    if snapshot_json is None:
        from app.config import get_settings

        snapshot_json = get_settings().runtime_capability_catalog_snapshot_json
    return RuntimeCapabilityCatalogLoader.from_snapshot_json(snapshot_json)


def build_runtime_capability_catalog_provider_from_settings(snapshot_json: str | None = None) -> RuntimeCapabilityCatalogProvider:
    return build_runtime_capability_catalog_loader_from_settings(snapshot_json=snapshot_json).build_provider()
