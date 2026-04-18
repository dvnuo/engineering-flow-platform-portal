import json


def parse_metadata_json(metadata_json: str | None) -> dict:
    if not metadata_json:
        return {}
    try:
        parsed = json.loads(metadata_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize_preview_value(value):
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    return value


def _derive_preview_from_context_state(context_state: dict) -> dict:
    if not isinstance(context_state, dict):
        return {}
    preview = {
        "context_compaction_level": _normalize_preview_value(context_state.get("compaction_level")),
        "context_objective_preview": _normalize_preview_value(context_state.get("objective")),
        "context_summary_preview": _normalize_preview_value(context_state.get("summary")),
        "context_next_step_preview": _normalize_preview_value(context_state.get("next_step")),
    }
    return {key: value for key, value in preview.items() if value is not None}


def extract_context_preview(record) -> dict:
    metadata = parse_metadata_json(getattr(record, "metadata_json", None))
    flat_preview = {
        "context_compaction_level": _normalize_preview_value(metadata.get("context_compaction_level")),
        "context_objective_preview": _normalize_preview_value(metadata.get("context_objective_preview")),
        "context_summary_preview": _normalize_preview_value(metadata.get("context_summary_preview")),
        "context_next_step_preview": _normalize_preview_value(metadata.get("context_next_step_preview")),
    }
    context_state = metadata.get("context_state") if isinstance(metadata, dict) else None
    nested_preview = _derive_preview_from_context_state(context_state)
    merged_preview = {
        key: (flat_preview.get(key) if flat_preview.get(key) is not None else nested_preview.get(key))
        for key in (
            "context_compaction_level",
            "context_objective_preview",
            "context_summary_preview",
            "context_next_step_preview",
        )
    }
    snapshot_version = getattr(record, "snapshot_version", None)
    snapshot_version_text = _normalize_preview_value(str(snapshot_version) if snapshot_version is not None else None)
    preview = {
        "latest_event_state": _normalize_preview_value(getattr(record, "latest_event_state", None)),
        "snapshot_version": snapshot_version_text,
        **merged_preview,
    }
    return {key: value for key, value in preview.items() if value is not None}


def merge_runtime_sessions_with_metadata(
    runtime_sessions: list[dict],
    metadata_records: list,
    *,
    include_metadata_only: bool = False,
) -> list[dict]:
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
    if not include_metadata_only:
        return merged_sessions

    runtime_session_ids = {session.get("session_id") for session in runtime_sessions if session.get("session_id")}
    metadata_only_records = [
        record
        for record in metadata_records
        if getattr(record, "session_id", None) and getattr(record, "session_id", None) not in runtime_session_ids
    ]
    def _updated_at_sort_value(record) -> float:
        updated_at = getattr(record, "updated_at", None)
        if hasattr(updated_at, "timestamp"):
            return float(updated_at.timestamp())
        return 0.0

    metadata_only_records.sort(key=_updated_at_sort_value, reverse=True)
    for record in metadata_only_records:
        preview = extract_context_preview(record)
        merged_sessions.append(
            {
                "session_id": record.session_id,
                "name": record.session_id,
                "last_message": None,
                "is_metadata_only": True,
                **preview,
            }
        )
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
