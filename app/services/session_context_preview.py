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


def _derive_source_preview(metadata: dict) -> dict:
    if not isinstance(metadata, dict):
        return {}

    context_state = metadata.get("context_state") if isinstance(metadata.get("context_state"), dict) else {}
    source = context_state.get("source") if isinstance(context_state.get("source"), dict) else {}
    budget = context_state.get("budget") if isinstance(context_state.get("budget"), dict) else {}
    generation = context_state.get("generation") if isinstance(context_state.get("generation"), dict) else {}

    flat_preview = {
        "context_source_complete": metadata.get("source_complete"),
        "context_comments_loaded": metadata.get("comments_loaded"),
        "context_comments_total": metadata.get("comments_total"),
        "context_attachments_loaded": metadata.get("attachments_loaded"),
        "context_attachments_total": metadata.get("attachments_total"),
        "context_source_partial_reasons_count": metadata.get("source_partial_reasons_count"),
        "context_generation_mode": _normalize_preview_value(metadata.get("generation_mode")),
        "context_current_generation_phase": _normalize_preview_value(metadata.get("current_generation_phase")),
        "context_large_generation_guard_reason": _normalize_preview_value(metadata.get("large_generation_guard_reason")),
        "context_source_type": _normalize_preview_value(metadata.get("source_type")),
        "context_source_digest_chunk_count": metadata.get("source_digest_chunk_count"),
        "context_children_loaded": metadata.get("children_loaded"),
        "context_children_total": metadata.get("children_total"),
        "context_output_risk_level": _normalize_preview_value(metadata.get("output_risk_level")),
        "context_max_chat_output_chars": metadata.get("max_chat_output_chars"),
        "context_max_output_recovery_applied": metadata.get("max_output_recovery_applied"),
        "context_max_output_recovery_attempts": metadata.get("max_output_recovery_attempts"),
        "context_output_token_limit": metadata.get("output_token_limit"),
        "context_input_context_usage_percent": metadata.get("input_context_usage_percent"),
        "context_comments_complete": metadata.get("comments_complete"),
        "context_attachments_complete": metadata.get("attachments_complete"),
        "context_children_complete": metadata.get("children_complete"),
        "context_text_attachments_loaded": metadata.get("text_attachments_loaded"),
        "context_text_attachments_total": metadata.get("text_attachments_total"),
        "context_text_attachments_complete": metadata.get("text_attachments_complete"),
        "context_text_attachments_preview_only": metadata.get("text_attachments_preview_only"),
        "context_binary_attachment_bodies_skipped_count": metadata.get("binary_attachment_bodies_skipped_count"),
        "context_attachment_body_complete": metadata.get("attachment_body_complete"),
        "context_max_chat_output_enforced": metadata.get("max_chat_output_enforced"),
        "context_oversized_output_saved": metadata.get("oversized_output_saved"),
        "context_oversized_output_ref_count": metadata.get("oversized_output_ref_count"),
        "context_generation_completed_phases_count": metadata.get("generation_completed_phases_count"),
        "context_generation_next_phase": _normalize_preview_value(metadata.get("generation_next_phase")),
        "context_generation_state_active": metadata.get("generation_state_active"),
        "context_output_controller_applied": metadata.get("output_controller_applied"),
        "context_source_context_mode": _normalize_preview_value(metadata.get("source_context_mode")),
        "context_default_source_complete_applied": metadata.get("default_source_complete_applied"),
        "context_source_preview_tool_used": metadata.get("source_preview_tool_used"),
        "context_source_complete_definition": _normalize_preview_value(metadata.get("source_complete_definition")),
        "context_issue_fields_complete": metadata.get("issue_fields_complete"),
        "context_page_body_complete": metadata.get("page_body_complete"),
        "context_attachment_metadata_complete": metadata.get("attachment_metadata_complete"),
        "context_text_attachment_bodies_complete": metadata.get("text_attachment_bodies_complete"),
        "context_binary_attachment_bodies_available": metadata.get("binary_attachment_bodies_available"),
        "context_binary_attachment_bodies_skipped_count": metadata.get("binary_attachment_bodies_skipped_count"),
        "context_descendants_supported": metadata.get("descendants_supported"),
        "context_descendants_complete": metadata.get("descendants_complete"),
        "context_partial_output_saved": metadata.get("partial_output_saved"),
        "context_partial_output_ref_count": metadata.get("partial_output_ref_count"),
        "context_source_ref_session_valid": metadata.get("source_ref_session_valid"),
        "context_default_source_complete_ref_session": _normalize_preview_value(metadata.get("default_source_complete_ref_session")),
        "context_model_facing_preview_tool_available": metadata.get("model_facing_preview_tool_available"),
        "context_preview_tool_used": metadata.get("preview_tool_used"),
        "context_output_controller_stage": _normalize_preview_value(metadata.get("output_controller_stage")),
        "context_output_controller_recovery_reason": _normalize_preview_value(metadata.get("output_controller_recovery_reason")),
        "context_source_complete_for_generation": metadata.get("source_complete_for_generation"),
        "context_source_complete_including_binary_bodies": metadata.get("source_complete_including_binary_bodies"),
        "context_source_metadata_complete": metadata.get("source_metadata_complete"),
        "context_source_text_complete": metadata.get("source_text_complete"),
        "context_source_tree_complete": metadata.get("source_tree_complete"),
        "context_descendants_loaded": metadata.get("descendants_loaded"),
        "context_descendants_total": metadata.get("descendants_total"),
        "context_generated_artifact_ref_count": metadata.get("generated_artifact_ref_count"),
        "context_generation_done": metadata.get("generation_done"),
        "context_generation_current_phase": _normalize_preview_value(metadata.get("generation_current_phase") or metadata.get("current_generation_phase")),
    }

    nested_preview = {
        "context_source_complete": source.get("source_complete"),
        "context_comments_loaded": source.get("comments_loaded"),
        "context_comments_total": source.get("comments_total"),
        "context_attachments_loaded": source.get("attachments_loaded"),
        "context_attachments_total": source.get("attachments_total"),
        "context_source_partial_reasons_count": source.get("source_partial_reasons_count"),
        "context_generation_mode": _normalize_preview_value(source.get("generation_mode") or budget.get("generation_mode")),
        "context_current_generation_phase": _normalize_preview_value(source.get("current_generation_phase") or budget.get("current_generation_phase")),
        "context_large_generation_guard_reason": _normalize_preview_value(
            source.get("large_generation_guard_reason") or budget.get("large_generation_guard_reason")
        ),
        "context_source_type": _normalize_preview_value(source.get("source_type")),
        "context_source_digest_chunk_count": source.get("source_digest_chunk_count"),
        "context_children_loaded": source.get("children_loaded"),
        "context_children_total": source.get("children_total"),
        "context_output_risk_level": _normalize_preview_value(source.get("output_risk_level") or budget.get("output_risk_level")),
        "context_max_chat_output_chars": source.get("max_chat_output_chars") if source.get("max_chat_output_chars") is not None else budget.get("max_chat_output_chars"),
        "context_max_output_recovery_applied": source.get("max_output_recovery_applied") if source.get("max_output_recovery_applied") is not None else budget.get("max_output_recovery_applied"),
        "context_max_output_recovery_attempts": source.get("max_output_recovery_attempts") if source.get("max_output_recovery_attempts") is not None else budget.get("max_output_recovery_attempts"),
        "context_output_token_limit": source.get("output_token_limit") if source.get("output_token_limit") is not None else budget.get("output_token_limit"),
        "context_input_context_usage_percent": source.get("input_context_usage_percent") if source.get("input_context_usage_percent") is not None else budget.get("input_context_usage_percent"),
        "context_comments_complete": source.get("comments_complete"),
        "context_attachments_complete": source.get("attachments_complete"),
        "context_children_complete": source.get("children_complete"),
        "context_text_attachments_loaded": source.get("text_attachments_loaded"),
        "context_text_attachments_total": source.get("text_attachments_total"),
        "context_text_attachments_complete": source.get("text_attachments_complete"),
        "context_text_attachments_preview_only": source.get("text_attachments_preview_only"),
        "context_binary_attachment_bodies_skipped_count": source.get("binary_attachment_bodies_skipped_count"),
        "context_attachment_body_complete": source.get("attachment_body_complete") if source.get("attachment_body_complete") is not None else budget.get("attachment_body_complete"),
        "context_max_chat_output_enforced": source.get("max_chat_output_enforced") if source.get("max_chat_output_enforced") is not None else budget.get("max_chat_output_enforced"),
        "context_oversized_output_saved": source.get("oversized_output_saved") if source.get("oversized_output_saved") is not None else budget.get("oversized_output_saved"),
        "context_oversized_output_ref_count": source.get("oversized_output_ref_count") if source.get("oversized_output_ref_count") is not None else budget.get("oversized_output_ref_count"),
        "context_generation_completed_phases_count": generation.get("completed_phases_count") if generation.get("completed_phases_count") is not None else budget.get("generation_completed_phases_count"),
        "context_generation_next_phase": _normalize_preview_value(generation.get("next_phase") or budget.get("generation_next_phase")),
        "context_generation_state_active": generation.get("state_active") if generation.get("state_active") is not None else budget.get("generation_state_active"),
        "context_output_controller_applied": source.get("output_controller_applied") if source.get("output_controller_applied") is not None else budget.get("output_controller_applied"),
        "context_source_context_mode": _normalize_preview_value(source.get("source_context_mode") or budget.get("source_context_mode")),
        "context_default_source_complete_applied": source.get("default_source_complete_applied") if source.get("default_source_complete_applied") is not None else budget.get("default_source_complete_applied"),
        "context_source_preview_tool_used": source.get("source_preview_tool_used") if source.get("source_preview_tool_used") is not None else budget.get("source_preview_tool_used"),
        "context_source_complete_definition": _normalize_preview_value(source.get("source_complete_definition") or budget.get("source_complete_definition")),
        "context_issue_fields_complete": source.get("issue_fields_complete") if source.get("issue_fields_complete") is not None else budget.get("issue_fields_complete"),
        "context_page_body_complete": source.get("page_body_complete") if source.get("page_body_complete") is not None else budget.get("page_body_complete"),
        "context_attachment_metadata_complete": source.get("attachment_metadata_complete") if source.get("attachment_metadata_complete") is not None else budget.get("attachment_metadata_complete"),
        "context_text_attachment_bodies_complete": source.get("text_attachment_bodies_complete") if source.get("text_attachment_bodies_complete") is not None else budget.get("text_attachment_bodies_complete"),
        "context_binary_attachment_bodies_available": source.get("binary_attachment_bodies_available") if source.get("binary_attachment_bodies_available") is not None else budget.get("binary_attachment_bodies_available"),
        "context_binary_attachment_bodies_skipped_count": source.get("binary_attachment_bodies_skipped_count") if source.get("binary_attachment_bodies_skipped_count") is not None else budget.get("binary_attachment_bodies_skipped_count"),
        "context_descendants_supported": source.get("descendants_supported") if source.get("descendants_supported") is not None else budget.get("descendants_supported"),
        "context_descendants_complete": source.get("descendants_complete") if source.get("descendants_complete") is not None else budget.get("descendants_complete"),
        "context_partial_output_saved": source.get("partial_output_saved") if source.get("partial_output_saved") is not None else budget.get("partial_output_saved"),
        "context_partial_output_ref_count": source.get("partial_output_ref_count") if source.get("partial_output_ref_count") is not None else budget.get("partial_output_ref_count"),
        "context_source_ref_session_valid": source.get("source_ref_session_valid") if source.get("source_ref_session_valid") is not None else budget.get("source_ref_session_valid"),
        "context_default_source_complete_ref_session": _normalize_preview_value(source.get("default_source_complete_ref_session") or budget.get("default_source_complete_ref_session")),
        "context_model_facing_preview_tool_available": source.get("model_facing_preview_tool_available") if source.get("model_facing_preview_tool_available") is not None else budget.get("model_facing_preview_tool_available"),
        "context_preview_tool_used": source.get("preview_tool_used") if source.get("preview_tool_used") is not None else budget.get("preview_tool_used"),
        "context_output_controller_stage": _normalize_preview_value(source.get("output_controller_stage") or budget.get("output_controller_stage") or generation.get("output_controller_stage")),
        "context_output_controller_recovery_reason": _normalize_preview_value(source.get("output_controller_recovery_reason") or budget.get("output_controller_recovery_reason") or generation.get("output_controller_recovery_reason")),
        "context_source_complete_for_generation": source.get("source_complete_for_generation") if source.get("source_complete_for_generation") is not None else budget.get("source_complete_for_generation"),
        "context_source_complete_including_binary_bodies": source.get("source_complete_including_binary_bodies") if source.get("source_complete_including_binary_bodies") is not None else budget.get("source_complete_including_binary_bodies"),
        "context_source_metadata_complete": source.get("source_metadata_complete") if source.get("source_metadata_complete") is not None else budget.get("source_metadata_complete"),
        "context_source_text_complete": source.get("source_text_complete") if source.get("source_text_complete") is not None else budget.get("source_text_complete"),
        "context_source_tree_complete": source.get("source_tree_complete") if source.get("source_tree_complete") is not None else budget.get("source_tree_complete"),
        "context_descendants_loaded": source.get("descendants_loaded") if source.get("descendants_loaded") is not None else budget.get("descendants_loaded"),
        "context_descendants_total": source.get("descendants_total") if source.get("descendants_total") is not None else budget.get("descendants_total"),
        "context_generated_artifact_ref_count": generation.get("generated_artifact_ref_count") if generation.get("generated_artifact_ref_count") is not None else budget.get("generated_artifact_ref_count"),
        "context_generation_done": generation.get("generation_done") if generation.get("generation_done") is not None else generation.get("done"),
        "context_generation_current_phase": _normalize_preview_value(
            generation.get("current_phase")
            or generation.get("generation_current_phase")
            or budget.get("generation_current_phase")
            or source.get("generation_current_phase")
            or source.get("current_generation_phase")
        ),
    }

    merged = {
        key: (flat_preview.get(key) if flat_preview.get(key) is not None else nested_preview.get(key))
        for key in (
            "context_source_complete",
            "context_comments_loaded",
            "context_comments_total",
            "context_attachments_loaded",
            "context_attachments_total",
            "context_source_partial_reasons_count",
            "context_generation_mode",
            "context_current_generation_phase",
            "context_large_generation_guard_reason",
            "context_source_type",
            "context_source_digest_chunk_count",
            "context_children_loaded",
            "context_children_total",
            "context_output_risk_level",
            "context_max_chat_output_chars",
            "context_max_output_recovery_applied",
            "context_max_output_recovery_attempts",
            "context_output_token_limit",
            "context_input_context_usage_percent",
            "context_comments_complete",
            "context_attachments_complete",
            "context_children_complete",
            "context_text_attachments_loaded",
            "context_text_attachments_total",
            "context_text_attachments_complete",
            "context_text_attachments_preview_only",
            "context_binary_attachment_bodies_skipped_count",
            "context_attachment_body_complete",
            "context_max_chat_output_enforced",
            "context_oversized_output_saved",
            "context_oversized_output_ref_count",
            "context_generation_completed_phases_count",
            "context_generation_next_phase",
            "context_generation_state_active",
            "context_output_controller_applied",
            "context_source_context_mode",
            "context_default_source_complete_applied",
            "context_source_preview_tool_used",
            "context_source_complete_definition",
            "context_issue_fields_complete",
            "context_page_body_complete",
            "context_attachment_metadata_complete",
            "context_text_attachment_bodies_complete",
            "context_binary_attachment_bodies_available",
            "context_binary_attachment_bodies_skipped_count",
            "context_descendants_supported",
            "context_descendants_complete",
            "context_partial_output_saved",
            "context_partial_output_ref_count",
            "context_source_ref_session_valid",
            "context_default_source_complete_ref_session",
            "context_model_facing_preview_tool_available",
            "context_preview_tool_used",
            "context_output_controller_stage",
            "context_output_controller_recovery_reason",
            "context_source_complete_for_generation",
            "context_source_complete_including_binary_bodies",
            "context_source_metadata_complete",
            "context_source_text_complete",
            "context_source_tree_complete",
            "context_descendants_loaded",
            "context_descendants_total",
            "context_generated_artifact_ref_count",
            "context_generation_done",
            "context_generation_current_phase",
        )
    }

    return {key: value for key, value in merged.items() if value is not None}


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
        "context_projected_recent_assistant_messages": metadata.get("context_projected_recent_assistant_messages"),
        "context_projected_plain_assistant_messages": metadata.get("context_projected_plain_assistant_messages"),
        "context_assistant_projection_chars_saved": metadata.get("context_assistant_projection_chars_saved"),
        "context_projected_old_assistant_messages": metadata.get("context_projected_old_assistant_messages"),
        "context_projected_old_tool_messages": metadata.get("context_projected_old_tool_messages"),
        "context_output_size_guard_applied": metadata.get("context_output_size_guard_applied"),
        "context_large_generation_guard_applied": metadata.get("context_large_generation_guard_applied"),
        "context_context_blob_refs_created": _normalize_context_blob_refs_created(
            metadata.get("context_context_blob_refs_created")
        ),
        "context_request_over_budget": metadata.get("context_request_over_budget"),
        "context_request_budget_stage": _normalize_preview_value(metadata.get("context_request_budget_stage")),
    }
    context_state = metadata.get("context_state") if isinstance(metadata.get("context_state"), dict) else {}
    budget = context_state.get("budget") if isinstance(context_state.get("budget"), dict) else {}
    generation = context_state.get("generation") if isinstance(context_state.get("generation"), dict) else {}
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
        "context_projected_recent_assistant_messages": budget.get("projected_recent_assistant_messages"),
        "context_projected_plain_assistant_messages": budget.get("projected_plain_assistant_messages"),
        "context_assistant_projection_chars_saved": budget.get("assistant_projection_chars_saved"),
        "context_projected_old_assistant_messages": budget.get("projected_old_assistant_messages"),
        "context_projected_old_tool_messages": budget.get("projected_old_tool_messages"),
        "context_output_size_guard_applied": budget.get("output_size_guard_applied"),
        "context_large_generation_guard_applied": budget.get("large_generation_guard_applied"),
        "context_context_blob_refs_created": context_blob_refs_created,
        "context_request_over_budget": budget.get("request_over_budget"),
        "context_request_budget_stage": _normalize_preview_value(budget.get("request_budget_stage") or budget.get("stage")),
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
            "context_projected_recent_assistant_messages",
            "context_projected_plain_assistant_messages",
            "context_assistant_projection_chars_saved",
            "context_projected_old_assistant_messages",
            "context_projected_old_tool_messages",
            "context_output_size_guard_applied",
            "context_large_generation_guard_applied",
            "context_context_blob_refs_created",
            "context_request_over_budget",
            "context_request_budget_stage",
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
    source_preview = _derive_source_preview(metadata)
    active_skill_preview = _derive_active_skill_preview(metadata)
    snapshot_version = getattr(record, "snapshot_version", None)
    snapshot_version_text = _normalize_preview_value(str(snapshot_version) if snapshot_version is not None else None)
    preview = {
        "latest_event_state": _normalize_preview_value(getattr(record, "latest_event_state", None)),
        "snapshot_version": snapshot_version_text,
        **merged_preview,
        **budget_preview,
        **source_preview,
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
