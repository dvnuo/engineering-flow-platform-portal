"""Which session ids belong to tasks, delegations and subagents rather than to chats.

The runtimes give a task's working session an id with one of these prefixes.
The sessions panel hides them, and they cannot be shared: they are not a
conversation a member had. The list mirrors the native runtime's
``TASK_SESSION_ID_PREFIXES`` (``src/efp_runtime/session/search.py``).
"""

TASK_SESSION_ID_PREFIXES = (
    "agent-task:",
    "agent-task-",
    "generic-task:",
    "generic-task-",
    "delegation:",
    "delegation-",
    "task-",
    # A subagent's child run, "subagent-<parent>-<task>": its first message is
    # the runtime's <task_prompt>, not anything a member wrote.
    "subagent-",
)


def is_task_session_id(session_id) -> bool:
    if not isinstance(session_id, str):
        return False
    return session_id.strip().startswith(TASK_SESSION_ID_PREFIXES)
