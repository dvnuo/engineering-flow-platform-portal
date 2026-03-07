VALID_STATUSES = {"creating", "running", "stopped", "deleting", "failed"}

# source -> allowed target
ALLOWED_TRANSITIONS = {
    "creating": {"running", "failed", "deleting"},
    "running": {"stopped", "failed", "deleting"},
    "stopped": {"running", "failed", "deleting"},
    "failed": {"running", "deleting"},
    "deleting": set(),
}


def is_valid_status(status: str) -> bool:
    return status in VALID_STATUSES


def can_transition(current: str, target: str) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, set())
