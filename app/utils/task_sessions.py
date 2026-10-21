"""Which session ids belong to tasks and delegations rather than to chats.

The runtimes give a task's working session an id with one of these prefixes.
The sessions panel hides them, and they cannot be shared: they are not a
conversation a member had.
"""

TASK_SESSION_ID_PREFIXES = (
    "agent-task:",
    "agent-task-",
    "generic-task:",
    "generic-task-",
    "delegation:",
    "delegation-",
    "task-",
)


def is_task_session_id(session_id) -> bool:
    if not isinstance(session_id, str):
        return False
    return session_id.strip().startswith(TASK_SESSION_ID_PREFIXES)
