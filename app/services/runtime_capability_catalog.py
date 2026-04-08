from dataclasses import dataclass
import json

from app.contracts.runtime_capabilities import get_default_runtime_adapter_actions_by_system


def normalize_action_name(value: str | None) -> str:
    return (value or "").strip().lower()


@dataclass(frozen=True)
class RuntimeCapabilityCatalogEntry:
    capability_id: str
    capability_type: str
    logical_name: str
    enabled: bool = True
    action_alias: str | None = None
    adapter_system: str | None = None


class RuntimeCapabilityCatalogProvider:
    def __init__(
        self,
        entries: list[RuntimeCapabilityCatalogEntry],
        catalog_version: str | None = None,
        catalog_source: str = "seed_fallback",
        supports_snapshot_contract: bool = False,
    ):
        self._catalog_version = catalog_version or "seed-v1"
        self._catalog_source = catalog_source
        self._supports_snapshot_contract = supports_snapshot_contract

        self._tool_names_to_ids: dict[str, list[str]] = {}
        self._skill_names_to_ids: dict[str, list[str]] = {}
        self._channel_names_to_ids: dict[str, list[str]] = {}
        self._action_aliases_to_ids: dict[str, list[str]] = {}
        self._known_adapter_capability_ids: set[str] = set()

        for entry in entries:
            if not entry.enabled:
                continue
            capability_id = normalize_action_name(entry.capability_id)
            capability_type = normalize_action_name(entry.capability_type)
            logical_name = normalize_action_name(entry.logical_name)
            action_alias = normalize_action_name(entry.action_alias)
            if not capability_id:
                continue

            if capability_type == "tool" and logical_name:
                self._tool_names_to_ids.setdefault(logical_name, []).append(capability_id)
            elif capability_type == "skill" and logical_name:
                self._skill_names_to_ids.setdefault(logical_name, []).append(capability_id)
            elif capability_type == "channel_action" and logical_name:
                self._channel_names_to_ids.setdefault(logical_name, []).append(capability_id)
            elif capability_type == "adapter_action":
                self._known_adapter_capability_ids.add(capability_id)
                alias = action_alias or logical_name
                if alias:
                    self._action_aliases_to_ids.setdefault(alias, []).append(capability_id)

    @classmethod
    def from_seed_mapping(cls, actions_by_system: dict[str, dict[str, str]]) -> "RuntimeCapabilityCatalogProvider":
        entries: list[RuntimeCapabilityCatalogEntry] = []
        for system_type, action_map in actions_by_system.items():
            for action_alias, capability_id in action_map.items():
                entries.append(
                    RuntimeCapabilityCatalogEntry(
                        capability_id=capability_id,
                        capability_type="adapter_action",
                        logical_name=action_alias,
                        action_alias=action_alias,
                        adapter_system=system_type,
                    )
                )
        return cls(entries=entries, catalog_version="seed-v1", catalog_source="seed_fallback", supports_snapshot_contract=False)

    @classmethod
    def from_runtime_catalog_payload(cls, payload: list[dict] | dict, source: str = "runtime_api") -> "RuntimeCapabilityCatalogProvider":
        catalog_version = None
        supports_snapshot_contract = False
        items: list[dict]

        if isinstance(payload, dict):
            raw_items = payload.get("capabilities", payload.get("items", []))
            items = raw_items if isinstance(raw_items, list) else []
            catalog_version = payload.get("catalog_version") or payload.get("version")
            supports_snapshot_contract = bool(payload.get("supports_snapshot_contract"))
        elif isinstance(payload, list):
            items = payload
        else:
            items = []

        entries: list[RuntimeCapabilityCatalogEntry] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            capability_id = item.get("capability_id") or item.get("id")
            capability_type = item.get("capability_type") or item.get("type") or ""
            logical_name = item.get("logical_name") or item.get("name") or item.get("action_alias") or item.get("action") or ""
            enabled = item.get("enabled", True)
            action_alias = item.get("action_alias") or item.get("action")
            adapter_system = item.get("adapter_system") or item.get("external_system")
            if not isinstance(capability_id, str):
                continue
            entries.append(
                RuntimeCapabilityCatalogEntry(
                    capability_id=capability_id,
                    capability_type=str(capability_type),
                    logical_name=str(logical_name),
                    enabled=bool(enabled),
                    action_alias=action_alias if isinstance(action_alias, str) else None,
                    adapter_system=adapter_system if isinstance(adapter_system, str) else None,
                )
            )

        return cls(
            entries=entries,
            catalog_version=str(catalog_version) if catalog_version else "runtime-unknown",
            catalog_source=source,
            supports_snapshot_contract=supports_snapshot_contract,
        )

    @staticmethod
    def _resolve_from_index(index: dict[str, list[str]], value: str | None) -> str | None:
        normalized = normalize_action_name(value)
        if not normalized:
            return None
        candidates = index.get(normalized, [])
        return candidates[0] if len(candidates) == 1 else None

    def resolve_tool_name_to_capability_id(self, name: str | None) -> str | None:
        return self._resolve_from_index(self._tool_names_to_ids, name)

    def resolve_skill_name_to_capability_id(self, name: str | None) -> str | None:
        return self._resolve_from_index(self._skill_names_to_ids, name)

    def resolve_channel_name_to_capability_id(self, name: str | None) -> str | None:
        return self._resolve_from_index(self._channel_names_to_ids, name)

    def resolve_action_to_capability_id(self, action_name: str | None) -> str | None:
        normalized = normalize_action_name(action_name)
        if not normalized:
            return None
        if normalized.startswith("adapter:"):
            if normalized in self._known_adapter_capability_ids:
                return normalized
            return None
        return self._resolve_from_index(self._action_aliases_to_ids, normalized)

    def get_catalog_version(self) -> str:
        return self._catalog_version

    def get_catalog_source(self) -> str:
        return self._catalog_source

    def has_full_catalog(self) -> bool:
        return bool(self._tool_names_to_ids or self._skill_names_to_ids or self._channel_names_to_ids or self._supports_snapshot_contract)


def build_default_runtime_capability_catalog_provider() -> RuntimeCapabilityCatalogProvider:
    return RuntimeCapabilityCatalogProvider.from_seed_mapping(get_default_runtime_adapter_actions_by_system())


class RuntimeCapabilityCatalogLoader:
    """Preferred provider construction path: runtime snapshot first, seed fallback second."""

    def __init__(self, runtime_catalog_snapshot_payload: list[dict] | dict | None = None, source: str = "settings_snapshot"):
        self.runtime_catalog_snapshot_payload = runtime_catalog_snapshot_payload
        self.source = source

    def build_provider(self) -> RuntimeCapabilityCatalogProvider:
        if self.runtime_catalog_snapshot_payload:
            return RuntimeCapabilityCatalogProvider.from_runtime_catalog_payload(self.runtime_catalog_snapshot_payload, source=self.source)
        return build_default_runtime_capability_catalog_provider()

    @classmethod
    def from_snapshot_json(cls, snapshot_json: str | None, source: str = "settings_snapshot") -> "RuntimeCapabilityCatalogLoader":
        if not snapshot_json or not snapshot_json.strip():
            return cls(runtime_catalog_snapshot_payload=None, source=source)
        try:
            parsed = json.loads(snapshot_json)
        except json.JSONDecodeError:
            return cls(runtime_catalog_snapshot_payload=None, source=source)
        if not isinstance(parsed, (list, dict)):
            return cls(runtime_catalog_snapshot_payload=None, source=source)
        return cls(runtime_catalog_snapshot_payload=parsed, source=source)


def build_runtime_capability_catalog_provider(runtime_catalog_snapshot_payload: list[dict] | dict | None = None) -> RuntimeCapabilityCatalogProvider:
    return RuntimeCapabilityCatalogLoader(runtime_catalog_snapshot_payload=runtime_catalog_snapshot_payload, source="runtime_snapshot_payload").build_provider()


def build_runtime_capability_catalog_loader_from_settings(snapshot_json: str | None = None) -> RuntimeCapabilityCatalogLoader:
    if snapshot_json is None:
        from app.config import get_settings

        snapshot_json = get_settings().runtime_capability_catalog_snapshot_json
    return RuntimeCapabilityCatalogLoader.from_snapshot_json(snapshot_json, source="settings_snapshot")


def build_runtime_capability_catalog_provider_from_settings(snapshot_json: str | None = None) -> RuntimeCapabilityCatalogProvider:
    return build_runtime_capability_catalog_loader_from_settings(snapshot_json=snapshot_json).build_provider()
