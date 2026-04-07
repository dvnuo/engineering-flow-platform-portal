from dataclasses import dataclass


def normalize_action_name(value: str | None) -> str:
    return (value or "").strip().lower()


@dataclass(frozen=True)
class RuntimeCapabilityContract:
    adapter_actions_by_system: dict[str, dict[str, str]]

    def __post_init__(self) -> None:
        flattened_aliases: dict[str, list[str]] = {}
        for action_map in self.adapter_actions_by_system.values():
            for action_name, capability_id in action_map.items():
                flattened_aliases.setdefault(normalize_action_name(action_name), []).append(normalize_action_name(capability_id))
        object.__setattr__(self, "_action_aliases_to_ids", flattened_aliases)

    def list_known_action_aliases(self) -> set[str]:
        return set(self._action_aliases_to_ids.keys())

    def list_known_adapter_capability_ids(self) -> set[str]:
        capability_ids: set[str] = set()
        for ids in self._action_aliases_to_ids.values():
            capability_ids.update(ids)
        return capability_ids

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


def build_default_runtime_capability_contract() -> RuntimeCapabilityContract:
    return RuntimeCapabilityContract(
        adapter_actions_by_system={
            "github": {
                "review_pull_request": "adapter:github:review_pull_request",
                "add_comment": "adapter:github:add_comment",
            },
            "jira": {
                "read_issue": "adapter:jira:read_issue",
                "update_issue": "adapter:jira:update_issue",
                "assign_issue": "adapter:jira:assign_issue",
                "transition_issue": "adapter:jira:transition_issue",
                "add_comment": "adapter:jira:add_comment",
            },
            "portal": {
                "create_delegation": "adapter:portal:create_delegation",
                "list_group_delegations": "adapter:portal:list_group_delegations",
                "get_group_task_board": "adapter:portal:get_group_task_board",
                "list_group_coordination_runs": "adapter:portal:list_group_coordination_runs",
                "get_coordination_run": "adapter:portal:get_coordination_run",
                "get_specialist_pool": "adapter:portal:get_specialist_pool",
                "create_task_agent": "adapter:portal:create_task_agent",
                "delete_task_agent": "adapter:portal:delete_task_agent",
            },
        }
    )
