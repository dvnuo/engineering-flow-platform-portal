import json
from typing import Any, Optional


def parse_workflow_rule_config(config_json: Optional[str]) -> tuple[Optional[str], Optional[dict[str, Any]], Optional[str]]:
    if config_json is None:
        return None, None, None

    raw = config_json.strip()
    if not raw:
        return None, None, None

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None, None, "config_json must be valid JSON"

    if not isinstance(parsed, dict):
        return None, None, "config_json must decode to a JSON object"

    normalized = json.dumps(parsed, sort_keys=True)
    return normalized, parsed, None
