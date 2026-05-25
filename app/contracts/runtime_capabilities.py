from copy import deepcopy


# Fallback seed data used when no runtime catalog snapshot is provided.
# Runtime remains the source-of-truth; this seed keeps local behavior deterministic.
DEFAULT_RUNTIME_ADAPTER_ACTIONS_BY_SYSTEM: dict[str, dict[str, str]] = {
    "github": {
        "create_pull_request": "adapter:github:create_pull_request",
        "review_pull_request": "adapter:github:review_pull_request",
        "add_comment": "adapter:github:add_comment",
        "reply_review_comment": "adapter:github:reply_review_comment",
        "add_commit_comment": "adapter:github:add_commit_comment",
        "add_discussion_comment": "adapter:github:add_discussion_comment",
    },
    "jira": {
        "read_issue": "adapter:jira:read_issue",
        "update_issue": "adapter:jira:update_issue",
        "assign_issue": "adapter:jira:assign_issue",
        "transition_issue": "adapter:jira:transition_issue",
        "add_comment": "adapter:jira:add_comment",
    },
}


def get_default_runtime_adapter_actions_by_system() -> dict[str, dict[str, str]]:
    return deepcopy(DEFAULT_RUNTIME_ADAPTER_ACTIONS_BY_SYSTEM)
