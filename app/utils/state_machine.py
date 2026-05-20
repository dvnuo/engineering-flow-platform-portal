VALID_STATUSES = {"creating", "restarting", "running", "stopped", "deleting", "failed"}

# source -> allowed target
ALLOWED_TRANSITIONS = {
    "creating": {"running", "failed", "deleting"},
    "running": {"restarting", "stopped", "failed", "deleting"},
    "stopped": {"restarting", "running", "failed", "deleting"},
    "failed": {"restarting", "running", "deleting"},
    "restarting": {"running", "failed", "stopped", "deleting"},
    "deleting": set(),
}


def is_valid_status(status: str) -> bool:
    return status in VALID_STATUSES


def can_transition(current: str, target: str) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, set())
