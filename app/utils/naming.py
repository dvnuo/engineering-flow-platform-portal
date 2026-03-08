import re

K8S_NAME_MAX = 63


def to_k8s_name(value: str, *, prefix: str = "agent") -> str:
    cleaned = re.sub(r"[^a-z0-9-]", "-", value.lower())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    if not cleaned:
        cleaned = "default"
    base = f"{prefix}-{cleaned}"
    return base[:K8S_NAME_MAX].rstrip("-")


def runtime_names(agent_id: str) -> tuple[str, str, str, str]:
    deploy = to_k8s_name(agent_id, prefix="agent")
    svc = to_k8s_name(f"{agent_id}-svc", prefix="agent")
    pvc = to_k8s_name(f"{agent_id}-pvc", prefix="agent")
    endpoint = f"/a/{agent_id}"
    return deploy, svc, pvc, endpoint
