import json
from typing import Any


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _safe_json_dict(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_json_list(raw: Any) -> list:
    if isinstance(raw, list):
        return raw
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def _normalize_context_blob_refs_created(value: Any):
    if isinstance(value, list):
        return len(value)
    return value


def _build_source_diagnostics(metadata_dict: dict, context_state: dict, budget: dict) -> dict:
    source = _as_dict(context_state.get("source"))
    budget = _as_dict(budget)
    generation = _as_dict(context_state.get("generation"))

    diagnostics = {
        "source_complete": metadata_dict.get("source_complete") if metadata_dict.get("source_complete") is not None else source.get("source_complete"),
        "comments_loaded": metadata_dict.get("comments_loaded") if metadata_dict.get("comments_loaded") is not None else source.get("comments_loaded"),
        "comments_total": metadata_dict.get("comments_total") if metadata_dict.get("comments_total") is not None else source.get("comments_total"),
        "attachments_loaded": metadata_dict.get("attachments_loaded") if metadata_dict.get("attachments_loaded") is not None else source.get("attachments_loaded"),
        "attachments_total": metadata_dict.get("attachments_total") if metadata_dict.get("attachments_total") is not None else source.get("attachments_total"),
        "source_partial_reasons_count": metadata_dict.get("source_partial_reasons_count") if metadata_dict.get("source_partial_reasons_count") is not None else source.get("source_partial_reasons_count"),
        "generation_mode": metadata_dict.get("generation_mode") if metadata_dict.get("generation_mode") is not None else (source.get("generation_mode") if source.get("generation_mode") is not None else budget.get("generation_mode")),
        "current_generation_phase": metadata_dict.get("current_generation_phase") if metadata_dict.get("current_generation_phase") is not None else (source.get("current_generation_phase") if source.get("current_generation_phase") is not None else budget.get("current_generation_phase")),
        "large_generation_guard_reason": metadata_dict.get("large_generation_guard_reason") if metadata_dict.get("large_generation_guard_reason") is not None else (source.get("large_generation_guard_reason") if source.get("large_generation_guard_reason") is not None else budget.get("large_generation_guard_reason")),
        "source_type": metadata_dict.get("source_type") if metadata_dict.get("source_type") is not None else source.get("source_type"),
        "source_digest_chunk_count": metadata_dict.get("source_digest_chunk_count") if metadata_dict.get("source_digest_chunk_count") is not None else source.get("source_digest_chunk_count"),
        "children_loaded": metadata_dict.get("children_loaded") if metadata_dict.get("children_loaded") is not None else source.get("children_loaded"),
        "children_total": metadata_dict.get("children_total") if metadata_dict.get("children_total") is not None else source.get("children_total"),
        "output_risk_level": metadata_dict.get("output_risk_level") if metadata_dict.get("output_risk_level") is not None else (source.get("output_risk_level") if source.get("output_risk_level") is not None else budget.get("output_risk_level")),
        "max_chat_output_chars": metadata_dict.get("max_chat_output_chars") if metadata_dict.get("max_chat_output_chars") is not None else (source.get("max_chat_output_chars") if source.get("max_chat_output_chars") is not None else budget.get("max_chat_output_chars")),
        "max_context_window_tokens": metadata_dict.get("max_context_window_tokens") if metadata_dict.get("max_context_window_tokens") is not None else (source.get("max_context_window_tokens") if source.get("max_context_window_tokens") is not None else budget.get("max_context_window_tokens")),
        "max_prompt_tokens": metadata_dict.get("max_prompt_tokens") if metadata_dict.get("max_prompt_tokens") is not None else (source.get("max_prompt_tokens") if source.get("max_prompt_tokens") is not None else budget.get("max_prompt_tokens")),
        "max_output_tokens": metadata_dict.get("max_output_tokens") if metadata_dict.get("max_output_tokens") is not None else (source.get("max_output_tokens") if source.get("max_output_tokens") is not None else budget.get("max_output_tokens")),
        "max_chat_output_tokens": metadata_dict.get("max_chat_output_tokens") if metadata_dict.get("max_chat_output_tokens") is not None else (source.get("max_chat_output_tokens") if source.get("max_chat_output_tokens") is not None else budget.get("max_chat_output_tokens")),
        "output_boundary_source": metadata_dict.get("output_boundary_source") if metadata_dict.get("output_boundary_source") is not None else (source.get("output_boundary_source") if source.get("output_boundary_source") is not None else budget.get("output_boundary_source")),
        "chars_per_token_estimate": metadata_dict.get("chars_per_token_estimate") if metadata_dict.get("chars_per_token_estimate") is not None else (source.get("chars_per_token_estimate") if source.get("chars_per_token_estimate") is not None else budget.get("chars_per_token_estimate")),
        "max_output_recovery_applied": metadata_dict.get("max_output_recovery_applied") if metadata_dict.get("max_output_recovery_applied") is not None else (source.get("max_output_recovery_applied") if source.get("max_output_recovery_applied") is not None else budget.get("max_output_recovery_applied")),
        "max_output_recovery_attempts": metadata_dict.get("max_output_recovery_attempts") if metadata_dict.get("max_output_recovery_attempts") is not None else (source.get("max_output_recovery_attempts") if source.get("max_output_recovery_attempts") is not None else budget.get("max_output_recovery_attempts")),
        "output_token_limit": metadata_dict.get("output_token_limit") if metadata_dict.get("output_token_limit") is not None else (source.get("output_token_limit") if source.get("output_token_limit") is not None else budget.get("output_token_limit")),
        "input_context_usage_percent": metadata_dict.get("input_context_usage_percent") if metadata_dict.get("input_context_usage_percent") is not None else (source.get("input_context_usage_percent") if source.get("input_context_usage_percent") is not None else budget.get("input_context_usage_percent")),
        "comments_complete": metadata_dict.get("comments_complete") if metadata_dict.get("comments_complete") is not None else source.get("comments_complete"),
        "attachments_complete": metadata_dict.get("attachments_complete") if metadata_dict.get("attachments_complete") is not None else source.get("attachments_complete"),
        "children_complete": metadata_dict.get("children_complete") if metadata_dict.get("children_complete") is not None else source.get("children_complete"),
        "text_attachments_loaded": metadata_dict.get("text_attachments_loaded") if metadata_dict.get("text_attachments_loaded") is not None else source.get("text_attachments_loaded"),
        "text_attachments_total": metadata_dict.get("text_attachments_total") if metadata_dict.get("text_attachments_total") is not None else source.get("text_attachments_total"),
        "text_attachments_complete": metadata_dict.get("text_attachments_complete") if metadata_dict.get("text_attachments_complete") is not None else source.get("text_attachments_complete"),
        "text_attachments_preview_only": metadata_dict.get("text_attachments_preview_only") if metadata_dict.get("text_attachments_preview_only") is not None else source.get("text_attachments_preview_only"),
        "binary_attachment_bodies_skipped_count": metadata_dict.get("binary_attachment_bodies_skipped_count") if metadata_dict.get("binary_attachment_bodies_skipped_count") is not None else source.get("binary_attachment_bodies_skipped_count"),
        "attachment_body_complete": metadata_dict.get("attachment_body_complete") if metadata_dict.get("attachment_body_complete") is not None else (source.get("attachment_body_complete") if source.get("attachment_body_complete") is not None else budget.get("attachment_body_complete")),
        "max_chat_output_enforced": metadata_dict.get("max_chat_output_enforced") if metadata_dict.get("max_chat_output_enforced") is not None else (source.get("max_chat_output_enforced") if source.get("max_chat_output_enforced") is not None else budget.get("max_chat_output_enforced")),
        "oversized_output_saved": metadata_dict.get("oversized_output_saved") if metadata_dict.get("oversized_output_saved") is not None else (source.get("oversized_output_saved") if source.get("oversized_output_saved") is not None else budget.get("oversized_output_saved")),
        "oversized_output_ref_count": metadata_dict.get("oversized_output_ref_count") if metadata_dict.get("oversized_output_ref_count") is not None else (source.get("oversized_output_ref_count") if source.get("oversized_output_ref_count") is not None else budget.get("oversized_output_ref_count")),
        "generation_completed_phases_count": metadata_dict.get("generation_completed_phases_count") if metadata_dict.get("generation_completed_phases_count") is not None else (generation.get("completed_phases_count") if generation.get("completed_phases_count") is not None else budget.get("generation_completed_phases_count")),
        "generation_next_phase": metadata_dict.get("generation_next_phase") if metadata_dict.get("generation_next_phase") is not None else (generation.get("next_phase") if generation.get("next_phase") is not None else budget.get("generation_next_phase")),
        "generation_state_active": metadata_dict.get("generation_state_active") if metadata_dict.get("generation_state_active") is not None else (generation.get("state_active") if generation.get("state_active") is not None else budget.get("generation_state_active")),
        "output_controller_applied": metadata_dict.get("output_controller_applied") if metadata_dict.get("output_controller_applied") is not None else (source.get("output_controller_applied") if source.get("output_controller_applied") is not None else budget.get("output_controller_applied")),
        "source_context_mode": metadata_dict.get("source_context_mode") if metadata_dict.get("source_context_mode") is not None else (source.get("source_context_mode") if source.get("source_context_mode") is not None else budget.get("source_context_mode")),
        "default_source_complete_applied": metadata_dict.get("default_source_complete_applied") if metadata_dict.get("default_source_complete_applied") is not None else (source.get("default_source_complete_applied") if source.get("default_source_complete_applied") is not None else budget.get("default_source_complete_applied")),
        "source_preview_tool_used": metadata_dict.get("source_preview_tool_used") if metadata_dict.get("source_preview_tool_used") is not None else (source.get("source_preview_tool_used") if source.get("source_preview_tool_used") is not None else budget.get("source_preview_tool_used")),
        "source_complete_definition": metadata_dict.get("source_complete_definition") if metadata_dict.get("source_complete_definition") is not None else (source.get("source_complete_definition") if source.get("source_complete_definition") is not None else budget.get("source_complete_definition")),
        "issue_fields_complete": metadata_dict.get("issue_fields_complete") if metadata_dict.get("issue_fields_complete") is not None else (source.get("issue_fields_complete") if source.get("issue_fields_complete") is not None else budget.get("issue_fields_complete")),
        "page_body_complete": metadata_dict.get("page_body_complete") if metadata_dict.get("page_body_complete") is not None else (source.get("page_body_complete") if source.get("page_body_complete") is not None else budget.get("page_body_complete")),
        "attachment_metadata_complete": metadata_dict.get("attachment_metadata_complete") if metadata_dict.get("attachment_metadata_complete") is not None else (source.get("attachment_metadata_complete") if source.get("attachment_metadata_complete") is not None else budget.get("attachment_metadata_complete")),
        "text_attachment_bodies_complete": metadata_dict.get("text_attachment_bodies_complete") if metadata_dict.get("text_attachment_bodies_complete") is not None else (source.get("text_attachment_bodies_complete") if source.get("text_attachment_bodies_complete") is not None else budget.get("text_attachment_bodies_complete")),
        "binary_attachment_bodies_available": metadata_dict.get("binary_attachment_bodies_available") if metadata_dict.get("binary_attachment_bodies_available") is not None else (source.get("binary_attachment_bodies_available") if source.get("binary_attachment_bodies_available") is not None else budget.get("binary_attachment_bodies_available")),
        "binary_attachment_bodies_skipped_count": metadata_dict.get("binary_attachment_bodies_skipped_count") if metadata_dict.get("binary_attachment_bodies_skipped_count") is not None else (source.get("binary_attachment_bodies_skipped_count") if source.get("binary_attachment_bodies_skipped_count") is not None else budget.get("binary_attachment_bodies_skipped_count")),
        "binary_attachment_body_policy": metadata_dict.get("binary_attachment_body_policy") if metadata_dict.get("binary_attachment_body_policy") is not None else (source.get("binary_attachment_body_policy") if source.get("binary_attachment_body_policy") is not None else budget.get("binary_attachment_body_policy")),
        "descendants_supported": metadata_dict.get("descendants_supported") if metadata_dict.get("descendants_supported") is not None else (source.get("descendants_supported") if source.get("descendants_supported") is not None else budget.get("descendants_supported")),
        "descendants_complete": metadata_dict.get("descendants_complete") if metadata_dict.get("descendants_complete") is not None else (source.get("descendants_complete") if source.get("descendants_complete") is not None else budget.get("descendants_complete")),
        "partial_output_saved": metadata_dict.get("partial_output_saved") if metadata_dict.get("partial_output_saved") is not None else (source.get("partial_output_saved") if source.get("partial_output_saved") is not None else budget.get("partial_output_saved")),
        "partial_output_ref_count": metadata_dict.get("partial_output_ref_count") if metadata_dict.get("partial_output_ref_count") is not None else (source.get("partial_output_ref_count") if source.get("partial_output_ref_count") is not None else budget.get("partial_output_ref_count")),
        "source_ref_session_valid": metadata_dict.get("source_ref_session_valid") if metadata_dict.get("source_ref_session_valid") is not None else (source.get("source_ref_session_valid") if source.get("source_ref_session_valid") is not None else budget.get("source_ref_session_valid")),
        "default_source_complete_ref_session": metadata_dict.get("default_source_complete_ref_session") if metadata_dict.get("default_source_complete_ref_session") is not None else (source.get("default_source_complete_ref_session") if source.get("default_source_complete_ref_session") is not None else budget.get("default_source_complete_ref_session")),
        "model_facing_preview_tool_available": metadata_dict.get("model_facing_preview_tool_available") if metadata_dict.get("model_facing_preview_tool_available") is not None else (source.get("model_facing_preview_tool_available") if source.get("model_facing_preview_tool_available") is not None else budget.get("model_facing_preview_tool_available")),
        "preview_tool_used": metadata_dict.get("preview_tool_used") if metadata_dict.get("preview_tool_used") is not None else (source.get("preview_tool_used") if source.get("preview_tool_used") is not None else budget.get("preview_tool_used")),
        "output_controller_stage": metadata_dict.get("output_controller_stage") if metadata_dict.get("output_controller_stage") is not None else (source.get("output_controller_stage") if source.get("output_controller_stage") is not None else (budget.get("output_controller_stage") if budget.get("output_controller_stage") is not None else generation.get("output_controller_stage"))),
        "output_controller_recovery_reason": metadata_dict.get("output_controller_recovery_reason") if metadata_dict.get("output_controller_recovery_reason") is not None else (source.get("output_controller_recovery_reason") if source.get("output_controller_recovery_reason") is not None else (budget.get("output_controller_recovery_reason") if budget.get("output_controller_recovery_reason") is not None else generation.get("output_controller_recovery_reason"))),
        "source_complete_for_generation": metadata_dict.get("source_complete_for_generation") if metadata_dict.get("source_complete_for_generation") is not None else (source.get("source_complete_for_generation") if source.get("source_complete_for_generation") is not None else budget.get("source_complete_for_generation")),
        "source_complete_including_binary_bodies": metadata_dict.get("source_complete_including_binary_bodies") if metadata_dict.get("source_complete_including_binary_bodies") is not None else (source.get("source_complete_including_binary_bodies") if source.get("source_complete_including_binary_bodies") is not None else budget.get("source_complete_including_binary_bodies")),
        "source_metadata_complete": metadata_dict.get("source_metadata_complete") if metadata_dict.get("source_metadata_complete") is not None else (source.get("source_metadata_complete") if source.get("source_metadata_complete") is not None else budget.get("source_metadata_complete")),
        "source_text_complete": metadata_dict.get("source_text_complete") if metadata_dict.get("source_text_complete") is not None else (source.get("source_text_complete") if source.get("source_text_complete") is not None else budget.get("source_text_complete")),
        "source_tree_complete": metadata_dict.get("source_tree_complete") if metadata_dict.get("source_tree_complete") is not None else (source.get("source_tree_complete") if source.get("source_tree_complete") is not None else budget.get("source_tree_complete")),
        "descendants_loaded": metadata_dict.get("descendants_loaded") if metadata_dict.get("descendants_loaded") is not None else (source.get("descendants_loaded") if source.get("descendants_loaded") is not None else budget.get("descendants_loaded")),
        "descendants_total": metadata_dict.get("descendants_total") if metadata_dict.get("descendants_total") is not None else (source.get("descendants_total") if source.get("descendants_total") is not None else budget.get("descendants_total")),
        "generated_artifact_ref_count": metadata_dict.get("generated_artifact_ref_count") if metadata_dict.get("generated_artifact_ref_count") is not None else (generation.get("generated_artifact_ref_count") if generation.get("generated_artifact_ref_count") is not None else budget.get("generated_artifact_ref_count")),
        "generation_done": metadata_dict.get("generation_done") if metadata_dict.get("generation_done") is not None else (generation.get("generation_done") if generation.get("generation_done") is not None else generation.get("done")),
        "generation_current_phase": metadata_dict.get("generation_current_phase") if metadata_dict.get("generation_current_phase") is not None else (generation.get("current_phase") if generation.get("current_phase") is not None else (generation.get("generation_current_phase") if generation.get("generation_current_phase") is not None else (source.get("generation_current_phase") if source.get("generation_current_phase") is not None else source.get("current_generation_phase")))),
        "completion_criteria_count": metadata_dict.get("completion_criteria_count") if metadata_dict.get("completion_criteria_count") is not None else (generation.get("completion_criteria_count") if generation.get("completion_criteria_count") is not None else budget.get("completion_criteria_count")),
        "source_digest_chunk_coverage_count": metadata_dict.get("source_digest_chunk_coverage_count") if metadata_dict.get("source_digest_chunk_coverage_count") is not None else (generation.get("source_digest_chunk_coverage_count") if generation.get("source_digest_chunk_coverage_count") is not None else budget.get("source_digest_chunk_coverage_count")),
        "descendants_pages_complete": metadata_dict.get("descendants_pages_complete") if metadata_dict.get("descendants_pages_complete") is not None else (source.get("descendants_pages_complete") if source.get("descendants_pages_complete") is not None else budget.get("descendants_pages_complete")),
        "descendants_comments_complete": metadata_dict.get("descendants_comments_complete") if metadata_dict.get("descendants_comments_complete") is not None else (source.get("descendants_comments_complete") if source.get("descendants_comments_complete") is not None else budget.get("descendants_comments_complete")),
        "descendants_attachments_complete": metadata_dict.get("descendants_attachments_complete") if metadata_dict.get("descendants_attachments_complete") is not None else (source.get("descendants_attachments_complete") if source.get("descendants_attachments_complete") is not None else budget.get("descendants_attachments_complete")),
        "completion_criteria_status_count": metadata_dict.get("completion_criteria_status_count") if metadata_dict.get("completion_criteria_status_count") is not None else (generation.get("completion_criteria_status_count") if generation.get("completion_criteria_status_count") is not None else budget.get("completion_criteria_status_count")),
        "completion_criteria_satisfied_count": metadata_dict.get("completion_criteria_satisfied_count") if metadata_dict.get("completion_criteria_satisfied_count") is not None else (generation.get("completion_criteria_satisfied_count") if generation.get("completion_criteria_satisfied_count") is not None else budget.get("completion_criteria_satisfied_count")),
        "next_incomplete_phase": metadata_dict.get("next_incomplete_phase") if metadata_dict.get("next_incomplete_phase") is not None else (generation.get("next_incomplete_phase") if generation.get("next_incomplete_phase") is not None else budget.get("next_incomplete_phase")),
        "comments_bundle_ref_count": metadata_dict.get("comments_bundle_ref_count") if metadata_dict.get("comments_bundle_ref_count") is not None else (source.get("comments_bundle_ref_count") if source.get("comments_bundle_ref_count") is not None else budget.get("comments_bundle_ref_count")),
        "children_bundle_ref_count": metadata_dict.get("children_bundle_ref_count") if metadata_dict.get("children_bundle_ref_count") is not None else (source.get("children_bundle_ref_count") if source.get("children_bundle_ref_count") is not None else budget.get("children_bundle_ref_count")),
        "jira_comments_bundle_ref_count": metadata_dict.get("jira_comments_bundle_ref_count") if metadata_dict.get("jira_comments_bundle_ref_count") is not None else (source.get("jira_comments_bundle_ref_count") if source.get("jira_comments_bundle_ref_count") is not None else budget.get("jira_comments_bundle_ref_count")),
        "confluence_children_bundle_ref_count": metadata_dict.get("confluence_children_bundle_ref_count") if metadata_dict.get("confluence_children_bundle_ref_count") is not None else (source.get("confluence_children_bundle_ref_count") if source.get("confluence_children_bundle_ref_count") is not None else budget.get("confluence_children_bundle_ref_count")),
        "auxiliary_source_session_valid": metadata_dict.get("auxiliary_source_session_valid") if metadata_dict.get("auxiliary_source_session_valid") is not None else (source.get("auxiliary_source_session_valid") if source.get("auxiliary_source_session_valid") is not None else budget.get("auxiliary_source_session_valid")),
        "auxiliary_source_complete": metadata_dict.get("auxiliary_source_complete") if metadata_dict.get("auxiliary_source_complete") is not None else (source.get("auxiliary_source_complete") if source.get("auxiliary_source_complete") is not None else budget.get("auxiliary_source_complete")),
        "generated_artifacts_by_phase_count": metadata_dict.get("generated_artifacts_by_phase_count") if metadata_dict.get("generated_artifacts_by_phase_count") is not None else (generation.get("generated_artifacts_by_phase_count") if generation.get("generated_artifacts_by_phase_count") is not None else budget.get("generated_artifacts_by_phase_count")),
        "current_phase_artifact_count": metadata_dict.get("current_phase_artifact_count") if metadata_dict.get("current_phase_artifact_count") is not None else (generation.get("current_phase_artifact_count") if generation.get("current_phase_artifact_count") is not None else budget.get("current_phase_artifact_count")),
        "generation_completion_criteria_met": metadata_dict.get("generation_completion_criteria_met") if metadata_dict.get("generation_completion_criteria_met") is not None else (generation.get("generation_completion_criteria_met") if generation.get("generation_completion_criteria_met") is not None else budget.get("generation_completion_criteria_met")),
        "generation_completion_criteria_total": metadata_dict.get("generation_completion_criteria_total") if metadata_dict.get("generation_completion_criteria_total") is not None else (generation.get("generation_completion_criteria_total") if generation.get("generation_completion_criteria_total") is not None else budget.get("generation_completion_criteria_total")),
    }
    return {key: value for key, value in diagnostics.items() if value is not None}


def _build_budget_from_metadata(metadata_dict: dict) -> dict:
    if not isinstance(metadata_dict, dict):
        return {}
    mapping = {
        "context_usage_percent": "usage_percent",
        "context_estimated_tokens": "estimated_tokens",
        "context_window_tokens": "context_window_tokens",
        "context_next_compaction_action": "next_compaction_action",
        "context_next_pruning_policy": "next_pruning_policy",
        "context_tokens_until_soft_threshold": "tokens_until_soft_threshold",
        "context_tokens_until_hard_threshold": "tokens_until_hard_threshold",
        "context_prompt_budget_tokens": "prompt_budget_tokens",
        "context_request_estimated_tokens": "request_estimated_tokens",
        "context_reserved_output_tokens": "reserved_output_tokens",
        "context_safety_margin_tokens": "safety_margin_tokens",
        "context_max_output_tokens": "max_output_tokens",
        "context_max_prompt_tokens": "max_prompt_tokens",
        "context_projection_chars_saved": "projection_chars_saved",
        "context_projected_recent_assistant_messages": "projected_recent_assistant_messages",
        "context_projected_plain_assistant_messages": "projected_plain_assistant_messages",
        "context_assistant_projection_chars_saved": "assistant_projection_chars_saved",
        "context_projected_old_assistant_messages": "projected_old_assistant_messages",
        "context_projected_old_tool_messages": "projected_old_tool_messages",
        "context_output_size_guard_applied": "output_size_guard_applied",
        "context_large_generation_guard_applied": "large_generation_guard_applied",
        "context_context_blob_refs_created": "context_blob_refs_created",
        "context_request_over_budget": "request_over_budget",
        "context_request_budget_stage": "request_budget_stage",
    }
    budget = {}
    for source_key, target_key in mapping.items():
        value = metadata_dict.get(source_key)
        if value is None:
            continue
        if target_key == "context_blob_refs_created":
            value = _normalize_context_blob_refs_created(value)
        budget[target_key] = value
    return budget


def _has_meaningful_context_state(value: Any) -> bool:
    if not isinstance(value, dict) or not value:
        return False

    if _has_meaningful_context_contents(value):
        return True

    if str(value.get("compaction_level") or "").strip():
        return True

    budget = value.get("budget")
    if isinstance(budget, dict):
        for item in budget.values():
            if item is None or item == "":
                continue
            if isinstance(item, list) and len(item) == 0:
                continue
            if isinstance(item, dict) and len(item) == 0:
                continue
            return True

    source = value.get("source")
    if isinstance(source, dict):
        for item in source.values():
            if item is None or item == "":
                continue
            if isinstance(item, list) and len(item) == 0:
                continue
            if isinstance(item, dict) and len(item) == 0:
                continue
            return True

    return False


def _has_meaningful_context_contents(value: Any) -> bool:
    if not isinstance(value, dict) or not value:
        return False

    scalar_keys = (
        "objective",
        "summary",
        "current_state",
        "next_step",
        "recovery_context_message",
    )
    if any(str(value.get(key) or "").strip() for key in scalar_keys):
        return True

    list_keys = ("constraints", "decisions", "open_loops")
    for key in list_keys:
        items = value.get(key)
        if isinstance(items, list) and any(str(item or "").strip() for item in items):
            return True

    return False


def _pick_context_with_source(chatlog: dict, metadata: dict, events: list, metadata_dict: dict) -> tuple[dict, str]:
    candidates: list[tuple[str, Any]] = [
        ("chatlog", chatlog.get("context_state")),
        ("metadata", metadata.get("context_state")),
    ]
    for event in reversed(events):
        event_type = event.get("type") or event.get("event_type")
        if event_type != "context_snapshot":
            continue
        data = {
            **_as_dict(event.get("data")),
            **_as_dict(event.get("detail_payload")),
        }
        candidates.append(("event", data.get("context_state")))
    candidates.append(("metadata_record", metadata_dict.get("context_state")))
    preview = {}
    if metadata_dict.get("context_objective_preview"):
        preview["objective"] = metadata_dict.get("context_objective_preview")
    if metadata_dict.get("context_summary_preview"):
        preview["summary"] = metadata_dict.get("context_summary_preview")
    if metadata_dict.get("context_next_step_preview"):
        preview["next_step"] = metadata_dict.get("context_next_step_preview")
    budget = _build_budget_from_metadata(metadata_dict)
    if budget:
        preview["budget"] = budget
    candidates.append(("metadata_preview", preview))

    for source, candidate in candidates:
        if _has_meaningful_context_contents(candidate):
            return _as_dict(candidate), source
    for source, candidate in candidates:
        if _has_meaningful_context_state(candidate):
            return _as_dict(candidate), source
    return {}, "none"


def _pick_context_budget(chatlog: dict, metadata: dict, events: list, metadata_dict: dict, selected_context: dict) -> dict:
    selected_budget = _as_dict(selected_context.get("budget"))
    if selected_budget:
        return selected_budget

    candidates = [
        chatlog.get("context_state"),
        metadata.get("context_state"),
        metadata_dict.get("context_state"),
    ]
    for event in reversed(events):
        data = {
            **_as_dict(event.get("data")),
            **_as_dict(event.get("detail_payload")),
        }
        candidates.append(data.get("context_state"))
        event_budget = _as_dict(data.get("budget"))
        if event_budget:
            return event_budget

    for candidate in candidates:
        budget = _as_dict(_as_dict(candidate).get("budget"))
        if budget:
            return budget

    return _build_budget_from_metadata(metadata_dict)


def _pick_context(chatlog: dict, metadata: dict, events: list, metadata_dict: dict) -> dict:
    return _pick_context_with_source(chatlog, metadata, events, metadata_dict)[0]


def _merge_events(chatlog: dict, metadata_events: list, llm_debug: dict) -> list:
    merged = []
    seen = set()
    for source in (
        _as_list(chatlog.get("events")),
        _as_list(chatlog.get("runtime_events")),
        _as_list(chatlog.get("thinking_events")),
        _as_list(llm_debug.get("thinking_events")),
        metadata_events,
    ):
        for event in source:
            if not isinstance(event, dict):
                continue
            event_type = event.get("type") or event.get("event_type") or "event"
            data = {
                **_as_dict(event.get("data")),
                **_as_dict(event.get("detail_payload")),
            }
            context_state = _as_dict(data.get("context_state"))
            message = (
                data.get("message")
                or event.get("summary")
                or context_state.get("summary")
                or context_state.get("next_step")
                or event_type
            )
            request_id = event.get("request_id") or data.get("request_id") or ""
            session_id = event.get("session_id") or data.get("session_id") or ""
            agent_id = event.get("agent_id") or data.get("agent_id") or ""
            normalized = {
                "type": event_type,
                "event_type": event_type,
                "title": str(event.get("title") or event.get("summary") or event_type).replace("_", " ").title(),
                "message": message,
                "data": data,
                "ts": event.get("ts") or event.get("created_at") or "",
                "state": event.get("state") or data.get("state") or "",
                "request_id": request_id,
                "session_id": session_id,
                "agent_id": agent_id,
            }
            key = f"{normalized['type']}|{request_id}|{session_id}|{normalized['ts']}|{json.dumps(data, sort_keys=True, default=str)}"
            if key in seen:
                continue
            seen.add(key)
            merged.append(normalized)
    return merged


def _extract_active_skill(chatlog: dict, metadata: dict, metadata_dict: dict, events: list) -> dict:
    skill_session = _as_dict(chatlog.get("skill_session")) or _as_dict(metadata.get("active_skill_session"))
    skill = {
        "name": skill_session.get("skill_name") or skill_session.get("skill") or metadata.get("active_skill_name") or metadata_dict.get("active_skill_name"),
        "status": skill_session.get("status") or metadata.get("active_skill_status") or metadata_dict.get("active_skill_status"),
        "goal": skill_session.get("goal") or skill_session.get("original_user_request") or metadata.get("active_skill_goal") or metadata_dict.get("active_skill_goal"),
        "turn_count": skill_session.get("turn_count") if skill_session.get("turn_count") is not None else metadata.get("active_skill_turn_count") if metadata.get("active_skill_turn_count") is not None else metadata_dict.get("active_skill_turn_count"),
        "reason": skill_session.get("activation_reason") or metadata.get("active_skill_activation_reason") or metadata_dict.get("active_skill_activation_reason"),
        "hash": skill_session.get("skill_hash") or metadata.get("active_skill_hash") or metadata_dict.get("active_skill_hash"),
        "allowed_tools": skill_session.get("allowed_tools") or skill_session.get("task_tools") or metadata_dict.get("active_skill_allowed_tools") or [],
    }
    if not skill["name"]:
        for event in reversed(events):
            if event.get("type") == "skill_contract_active":
                data = _as_dict(event.get("data"))
                skill["name"] = data.get("skill") or data.get("skill_name")
                skill["reason"] = skill["reason"] or data.get("reason")
                skill["goal"] = skill["goal"] or data.get("goal")
                skill["turn_count"] = skill["turn_count"] if skill["turn_count"] is not None else data.get("turn_count")
                if isinstance(data.get("allowed_tools"), list):
                    skill["allowed_tools"] = data.get("allowed_tools")
                break
    return skill


def build_thinking_process_view(chatlog: dict | None, metadata_record=None) -> dict:
    chatlog = _as_dict(chatlog)
    metadata = _as_dict(chatlog.get("metadata"))
    llm_debug = _as_dict(chatlog.get("llm_debug"))

    metadata_dict = _safe_json_dict(getattr(metadata_record, "metadata_json", None))
    metadata_events = _safe_json_list(getattr(metadata_record, "runtime_events_json", None))

    events = _merge_events(chatlog, metadata_events, llm_debug)
    context_state, context_source = _pick_context_with_source(chatlog, metadata, events, metadata_dict)
    budget = _pick_context_budget(chatlog, metadata, events, metadata_dict, context_state)

    llm_request = _as_dict(llm_debug.get("llm_request"))
    llm_request_request = _as_dict(llm_request.get("request"))
    llm_response = _as_dict(llm_request.get("response"))

    status = str(chatlog.get("status") or metadata.get("status") or getattr(metadata_record, "latest_event_state", "") or "unknown").lower()
    if status not in {"running", "success", "error", "unknown", "failed", "completed"}:
        status = "unknown"

    active_skill = _extract_active_skill(chatlog, metadata, metadata_dict, events)
    fallback = {
        "latest_event_type": getattr(metadata_record, "latest_event_type", "") if metadata_record else "",
        "latest_event_state": getattr(metadata_record, "latest_event_state", "") if metadata_record else "",
        "last_execution_id": getattr(metadata_record, "last_execution_id", "") if metadata_record else "",
    }
    has_context = _has_meaningful_context_contents(context_state)
    if not has_context and budget:
        context_source_label = "Context window only — no context contents captured"
    else:
        context_source_label = {
            "chatlog": "Final Context Snapshot",
            "metadata": "Final Context Snapshot",
            "event": "Final Context Snapshot",
            "metadata_record": "Persisted Context Snapshot",
            "metadata_preview": "Persisted Context Preview",
            "none": "No context snapshot captured",
        }.get(context_source, "Final Context Snapshot")
    source_diagnostics = _build_source_diagnostics(metadata_dict, context_state, budget)

    has_data = bool(
        events
        or budget
        or has_context
        or source_diagnostics
        or active_skill.get("name")
        or fallback.get("latest_event_type")
        or fallback.get("latest_event_state")
        or fallback.get("last_execution_id")
    )

    view = {
        "session_id": chatlog.get("session_id") or getattr(metadata_record, "session_id", "") or "",
        "timestamp": chatlog.get("timestamp") or metadata.get("timestamp") or metadata_dict.get("timestamp") or "",
        "status": status,
        "request_id": chatlog.get("request_id") or metadata.get("request_id") or getattr(metadata_record, "last_execution_id", "") or "",
        "model": llm_request_request.get("model") or metadata_dict.get("model") or "",
        "active_skill": active_skill,
        "source_diagnostics": source_diagnostics,
        "context_source": context_source,
        "context_source_label": context_source_label,
        "has_context": has_context,
        "context": {
            "objective": context_state.get("objective") or metadata_dict.get("context_objective_preview") or "",
            "summary": context_state.get("summary") or metadata_dict.get("context_summary_preview") or "",
            "current_state": context_state.get("current_state") or "",
            "next_step": context_state.get("next_step") or metadata_dict.get("context_next_step_preview") or "",
            "constraints": _as_list(context_state.get("constraints")),
            "decisions": _as_list(context_state.get("decisions")),
            "open_loops": _as_list(context_state.get("open_loops")),
            "recovery_context_message": context_state.get("recovery_context_message") or "",
        },
        "budget": {
            "usage_percent": budget.get("usage_percent"),
            "prepared_usage_percent": budget.get("prepared_usage_percent"),
            "estimated_tokens": budget.get("estimated_tokens"),
            "prepared_tokens": budget.get("prepared_tokens"),
            "context_window_tokens": budget.get("context_window_tokens"),
            "prompt_budget_tokens": budget.get("prompt_budget_tokens") if budget.get("prompt_budget_tokens") is not None else budget.get("max_prompt_tokens"),
            "request_estimated_tokens": budget.get("request_estimated_tokens"),
            "reserved_output_tokens": budget.get("reserved_output_tokens"),
            "safety_margin_tokens": budget.get("safety_margin_tokens"),
            "max_output_tokens": budget.get("max_output_tokens"),
            "max_prompt_tokens": budget.get("max_prompt_tokens"),
            "projection_chars_saved": budget.get("projection_chars_saved"),
            "projected_recent_assistant_messages": budget.get("projected_recent_assistant_messages"),
            "projected_plain_assistant_messages": budget.get("projected_plain_assistant_messages"),
            "assistant_projection_chars_saved": budget.get("assistant_projection_chars_saved"),
            "projected_old_assistant_messages": budget.get("projected_old_assistant_messages"),
            "projected_old_tool_messages": budget.get("projected_old_tool_messages"),
            "output_size_guard_applied": budget.get("output_size_guard_applied"),
            "large_generation_guard_applied": budget.get("large_generation_guard_applied"),
            "context_blob_refs_created": _normalize_context_blob_refs_created(budget.get("context_blob_refs_created")),
            "request_over_budget": budget.get("request_over_budget"),
            "request_budget_stage": budget.get("request_budget_stage"),
            "soft_threshold_percent": budget.get("soft_threshold_percent"),
            "hard_threshold_percent": budget.get("hard_threshold_percent"),
            "tokens_until_soft_threshold": budget.get("tokens_until_soft_threshold"),
            "tokens_until_hard_threshold": budget.get("tokens_until_hard_threshold"),
            "next_compaction_action": budget.get("next_compaction_action"),
            "next_pruning_policy": budget.get("next_pruning_policy"),
            "output_risk_level": budget.get("output_risk_level"),
            "max_chat_output_chars": budget.get("max_chat_output_chars"),
            "max_output_recovery_applied": budget.get("max_output_recovery_applied"),
            "max_output_recovery_attempts": budget.get("max_output_recovery_attempts"),
            "output_token_limit": budget.get("output_token_limit"),
            "input_context_usage_percent": budget.get("input_context_usage_percent"),
            "max_chat_output_enforced": budget.get("max_chat_output_enforced"),
            "oversized_output_saved": budget.get("oversized_output_saved"),
            "oversized_output_ref_count": budget.get("oversized_output_ref_count"),
            "attachment_body_complete": budget.get("attachment_body_complete"),
        } if budget else {},
        "events": events,
        "debug": {
            "system_prompt": llm_request_request.get("instructions") or "",
            "request_items": _as_list(llm_request_request.get("input")),
            "available_tools": _as_list(llm_request_request.get("tools")),
            "final_response": llm_debug.get("final_response") or llm_response.get("content") or "",
            "usage": _as_dict(llm_response.get("usage")),
        },
        "warning": None,
        "fallback": fallback,
        "has_data": has_data,
    }
    return view
