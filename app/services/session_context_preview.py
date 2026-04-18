import json


def parse_metadata_json(metadata_json: str | None) -> dict:
    if not metadata_json:
        return {}
    try:
        parsed = json.loads(metadata_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def extract_context_preview(record) -> dict:
    metadata = parse_metadata_json(getattr(record, "metadata_json", None))
    preview = {
        "latest_event_state": getattr(record, "latest_event_state", None),
        "snapshot_version": getattr(record, "snapshot_version", None),
        "context_compaction_level": metadata.get("context_compaction_level"),
        "context_objective_preview": metadata.get("context_objective_preview"),
        "context_summary_preview": metadata.get("context_summary_preview"),
        "context_next_step_preview": metadata.get("context_next_step_preview"),
    }
    return {key: value for key, value in preview.items() if value is not None}


def merge_runtime_sessions_with_metadata(runtime_sessions: list[dict], metadata_records: list) -> list[dict]:
    metadata_by_session_id = {
        record.session_id: extract_context_preview(record)
        for record in metadata_records
        if getattr(record, "session_id", None)
    }
    merged_sessions: list[dict] = []
    for runtime_session in runtime_sessions:
        session = dict(runtime_session)
        session_id = session.get("session_id")
        metadata_preview = metadata_by_session_id.get(session_id, {})
        session.update(metadata_preview)
        merged_sessions.append(session)
    return merged_sessions


def serialize_agent_session_metadata_with_preview(record) -> dict:
    serialized = {
        "id": getattr(record, "id", None),
        "session_id": getattr(record, "session_id", None),
        "agent_id": getattr(record, "agent_id", None),
        "group_id": getattr(record, "group_id", None),
        "current_task_id": getattr(record, "current_task_id", None),
        "current_delegation_id": getattr(record, "current_delegation_id", None),
        "current_coordination_run_id": getattr(record, "current_coordination_run_id", None),
        "source_type": getattr(record, "source_type", None),
        "source_ref": getattr(record, "source_ref", None),
        "last_execution_id": getattr(record, "last_execution_id", None),
        "latest_event_type": getattr(record, "latest_event_type", None),
        "latest_event_state": getattr(record, "latest_event_state", None),
        "snapshot_version": getattr(record, "snapshot_version", None),
        "pending_delegations_json": getattr(record, "pending_delegations_json", None),
        "runtime_events_json": getattr(record, "runtime_events_json", None),
        "metadata_json": getattr(record, "metadata_json", None),
        "created_at": getattr(record, "created_at", None),
        "updated_at": getattr(record, "updated_at", None),
    }
    serialized.update(extract_context_preview(record))
    return serialized
