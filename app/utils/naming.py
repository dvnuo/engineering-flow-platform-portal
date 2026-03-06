import re

K8S_NAME_MAX = 63


def to_k8s_name(value: str, *, prefix: str = "robot") -> str:
    cleaned = re.sub(r"[^a-z0-9-]", "-", value.lower())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    if not cleaned:
        cleaned = "default"
    base = f"{prefix}-{cleaned}"
    return base[:K8S_NAME_MAX].rstrip("-")


def runtime_names(robot_id: str) -> tuple[str, str, str, str]:
    deploy = to_k8s_name(robot_id, prefix="robot")
    svc = to_k8s_name(f"{robot_id}-svc", prefix="robot")
    pvc = to_k8s_name(f"{robot_id}-pvc", prefix="robot")
    endpoint = f"/r/{robot_id}"
    return deploy, svc, pvc, endpoint
