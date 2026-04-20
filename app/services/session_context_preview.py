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


def _normalize_context_blob_refs_created(value):
    if isinstance(value, list):
        return len(value)
    return value


def _derive_budget_preview(metadata: dict) -> dict:
    if not isinstance(metadata, dict):
        return {}
    flat_preview = {
        "context_usage_percent": metadata.get("context_usage_percent"),
        "context_estimated_tokens": metadata.get("context_estimated_tokens"),
        "context_window_tokens": metadata.get("context_window_tokens"),
        "context_next_compaction_action": _normalize_preview_value(metadata.get("context_next_compaction_action")),
        "context_next_pruning_policy": _normalize_preview_value(metadata.get("context_next_pruning_policy")),
        "context_tokens_until_soft_threshold": metadata.get("context_tokens_until_soft_threshold"),
        "context_tokens_until_hard_threshold": metadata.get("context_tokens_until_hard_threshold"),
        "context_prompt_budget_tokens": metadata.get("context_prompt_budget_tokens"),
        "context_request_estimated_tokens": metadata.get("context_request_estimated_tokens"),
        "context_reserved_output_tokens": metadata.get("context_reserved_output_tokens"),
        "context_safety_margin_tokens": metadata.get("context_safety_margin_tokens"),
        "context_max_output_tokens": metadata.get("context_max_output_tokens"),
        "context_max_prompt_tokens": metadata.get("context_max_prompt_tokens"),
        "context_projection_chars_saved": metadata.get("context_projection_chars_saved"),
        "context_projected_old_assistant_messages": metadata.get("context_projected_old_assistant_messages"),
        "context_projected_old_tool_messages": metadata.get("context_projected_old_tool_messages"),
        "context_context_blob_refs_created": _normalize_context_blob_refs_created(
            metadata.get("context_context_blob_refs_created")
        ),
        "context_request_over_budget": metadata.get("context_request_over_budget"),
    }
    context_state = metadata.get("context_state") if isinstance(metadata.get("context_state"), dict) else {}
    budget = context_state.get("budget") if isinstance(context_state.get("budget"), dict) else {}
    context_blob_refs_created = _normalize_context_blob_refs_created(budget.get("context_blob_refs_created"))
    nested_preview = {
        "context_usage_percent": budget.get("prepared_usage_percent") if budget.get("prepared_usage_percent") is not None else budget.get("usage_percent"),
        "context_estimated_tokens": budget.get("prepared_tokens") if budget.get("prepared_tokens") is not None else budget.get("estimated_tokens"),
        "context_window_tokens": budget.get("context_window_tokens"),
        "context_next_compaction_action": _normalize_preview_value(budget.get("next_compaction_action")),
        "context_next_pruning_policy": _normalize_preview_value(budget.get("next_pruning_policy")),
        "context_tokens_until_soft_threshold": budget.get("tokens_until_soft_threshold"),
        "context_tokens_until_hard_threshold": budget.get("tokens_until_hard_threshold"),
        "context_prompt_budget_tokens": budget.get("prompt_budget_tokens") if budget.get("prompt_budget_tokens") is not None else budget.get("max_prompt_tokens"),
        "context_request_estimated_tokens": budget.get("request_estimated_tokens"),
        "context_reserved_output_tokens": budget.get("reserved_output_tokens"),
        "context_safety_margin_tokens": budget.get("safety_margin_tokens"),
        "context_max_output_tokens": budget.get("max_output_tokens"),
        "context_max_prompt_tokens": budget.get("max_prompt_tokens"),
        "context_projection_chars_saved": budget.get("projection_chars_saved"),
        "context_projected_old_assistant_messages": budget.get("projected_old_assistant_messages"),
        "context_projected_old_tool_messages": budget.get("projected_old_tool_messages"),
        "context_context_blob_refs_created": context_blob_refs_created,
        "context_request_over_budget": budget.get("request_over_budget"),
    }
    merged = {
        key: (flat_preview.get(key) if flat_preview.get(key) is not None else nested_preview.get(key))
        for key in (
            "context_usage_percent",
            "context_estimated_tokens",
            "context_window_tokens",
            "context_next_compaction_action",
            "context_next_pruning_policy",
            "context_tokens_until_soft_threshold",
            "context_tokens_until_hard_threshold",
            "context_prompt_budget_tokens",
            "context_request_estimated_tokens",
            "context_reserved_output_tokens",
            "context_safety_margin_tokens",
            "context_max_output_tokens",
            "context_max_prompt_tokens",
            "context_projection_chars_saved",
            "context_projected_old_assistant_messages",
            "context_projected_old_tool_messages",
            "context_context_blob_refs_created",
            "context_request_over_budget",
        )
    }
    return {key: value for key, value in merged.items() if value is not None}


def _derive_active_skill_preview(metadata: dict) -> dict:
    if not isinstance(metadata, dict):
        return {}

    active_skill = metadata.get("active_skill_session")
    if not isinstance(active_skill, dict):
        active_skill = {}

    preview = {
        "active_skill_name": _normalize_preview_value(
            metadata.get("active_skill_name")
            or active_skill.get("skill_name")
            or active_skill.get("skill")
        ),
        "active_skill_status": _normalize_preview_value(
            metadata.get("active_skill_status")
            or active_skill.get("status")
        ),
        "active_skill_goal": _normalize_preview_value(
            metadata.get("active_skill_goal")
            or active_skill.get("goal")
            or active_skill.get("original_user_request")
        ),
        "active_skill_hash": _normalize_preview_value(
            metadata.get("active_skill_hash")
            or active_skill.get("skill_hash")
        ),
        "active_skill_activation_reason": _normalize_preview_value(
            metadata.get("active_skill_activation_reason")
            or active_skill.get("activation_reason")
        ),
        "active_skill_turn_count": metadata.get("active_skill_turn_count")
        if metadata.get("active_skill_turn_count") is not None
        else active_skill.get("turn_count"),
        "active_skill_tool_policy_declared": metadata.get("active_skill_tool_policy_declared")
        if metadata.get("active_skill_tool_policy_declared") is not None
        else active_skill.get("tool_policy_declared"),
    }

    return {key: value for key, value in preview.items() if value not in (None, "")}


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
    budget_preview = _derive_budget_preview(metadata)
    active_skill_preview = _derive_active_skill_preview(metadata)
    snapshot_version = getattr(record, "snapshot_version", None)
    snapshot_version_text = _normalize_preview_value(str(snapshot_version) if snapshot_version is not None else None)
    preview = {
        "latest_event_state": _normalize_preview_value(getattr(record, "latest_event_state", None)),
        "snapshot_version": snapshot_version_text,
        **merged_preview,
        **budget_preview,
        **active_skill_preview,
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
