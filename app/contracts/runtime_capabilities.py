from copy import deepcopy


# Fallback seed data used when no runtime catalog snapshot is provided.
# Runtime remains the source-of-truth; this seed keeps local behavior deterministic.
DEFAULT_RUNTIME_ADAPTER_ACTIONS_BY_SYSTEM: dict[str, dict[str, str]] = {
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


def get_default_runtime_adapter_actions_by_system() -> dict[str, dict[str, str]]:
    return deepcopy(DEFAULT_RUNTIME_ADAPTER_ACTIONS_BY_SYSTEM)
