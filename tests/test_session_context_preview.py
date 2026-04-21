from datetime import datetime
from types import SimpleNamespace

from app.services.session_context_preview import (
    extract_context_preview,
    merge_runtime_sessions_with_metadata,
    parse_metadata_json,
    serialize_agent_session_metadata_with_preview,
)


def test_parse_metadata_json_handles_none_invalid_and_non_dict():
    assert parse_metadata_json(None) == {}
    assert parse_metadata_json("{not-valid-json") == {}
    assert parse_metadata_json('["list"]') == {}


def test_extract_context_preview_reads_expected_fields():
    record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="7",
        metadata_json='{"context_compaction_level":"high","context_objective_preview":"Objective","context_summary_preview":"Summary","context_next_step_preview":"Next step"}',
    )
    extracted = extract_context_preview(record)

    assert extracted["latest_event_state"] == "running"
    assert extracted["snapshot_version"] == "7"
    assert extracted["context_compaction_level"] == "high"
    assert extracted["context_objective_preview"] == "Objective"
    assert extracted["context_summary_preview"] == "Summary"
    assert extracted["context_next_step_preview"] == "Next step"


def test_extract_context_preview_reads_active_skill_flat_fields():
    record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="7",
        metadata_json='{"active_skill_name":"review-pull-request","active_skill_status":"active","active_skill_goal":"Review PR #12","active_skill_hash":"abc123","active_skill_turn_count":2,"active_skill_activation_reason":"continued","active_skill_tool_policy_declared":true}',
    )

    extracted = extract_context_preview(record)

    assert extracted["active_skill_name"] == "review-pull-request"
    assert extracted["active_skill_status"] == "active"
    assert extracted["active_skill_goal"] == "Review PR #12"
    assert extracted["active_skill_hash"] == "abc123"
    assert extracted["active_skill_turn_count"] == 2
    assert extracted["active_skill_activation_reason"] == "continued"
    assert extracted["active_skill_tool_policy_declared"] is True


def test_extract_context_preview_reads_active_skill_nested_session():
    record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="7",
        metadata_json='{"active_skill_session":{"skill_name":"create-pull-request","status":"active","goal":"Create PR","turn_count":3,"activation_reason":"matched","skill_hash":"def456","tool_policy_declared":true}}',
    )

    extracted = extract_context_preview(record)

    assert extracted["active_skill_name"] == "create-pull-request"
    assert extracted["active_skill_status"] == "active"
    assert extracted["active_skill_goal"] == "Create PR"
    assert extracted["active_skill_hash"] == "def456"
    assert extracted["active_skill_turn_count"] == 3
    assert extracted["active_skill_activation_reason"] == "matched"
    assert extracted["active_skill_tool_policy_declared"] is True


def test_serialize_agent_session_metadata_with_preview_contains_base_and_preview_fields():
    now = datetime.utcnow()
    record = SimpleNamespace(
        id="meta-1",
        session_id="s-1",
        agent_id="a-1",
        group_id="g-1",
        current_task_id="t-1",
        current_delegation_id=None,
        current_coordination_run_id=None,
        source_type="runtime",
        source_ref="ref-1",
        last_execution_id="exec-1",
        latest_event_type="tick",
        latest_event_state="running",
        snapshot_version="11",
        pending_delegations_json="[]",
        runtime_events_json="[]",
        metadata_json='{"context_compaction_level":"medium","context_objective_preview":"Ship","context_summary_preview":"Done","context_next_step_preview":"Review","active_skill_name":"review-pull-request","active_skill_status":"active","active_skill_goal":"Review PR #12","active_skill_hash":"abc123","active_skill_turn_count":2,"active_skill_activation_reason":"continued","active_skill_tool_policy_declared":true}',
        created_at=now,
        updated_at=now,
    )

    serialized = serialize_agent_session_metadata_with_preview(record)

    assert serialized["id"] == "meta-1"
    assert serialized["session_id"] == "s-1"
    assert serialized["agent_id"] == "a-1"
    assert serialized["latest_event_state"] == "running"
    assert serialized["metadata_json"] is not None
    assert serialized["context_compaction_level"] == "medium"
    assert serialized["context_objective_preview"] == "Ship"
    assert serialized["context_summary_preview"] == "Done"
    assert serialized["context_next_step_preview"] == "Review"
    assert serialized["active_skill_name"] == "review-pull-request"
    assert serialized["active_skill_status"] == "active"
    assert serialized["active_skill_goal"] == "Review PR #12"
    assert serialized["active_skill_hash"] == "abc123"
    assert serialized["active_skill_turn_count"] == 2
    assert serialized["active_skill_activation_reason"] == "continued"
    assert serialized["active_skill_tool_policy_declared"] is True


def test_merge_runtime_sessions_with_metadata_handles_present_and_missing_records():
    runtime_sessions = [
        {"session_id": "s-1", "name": "Session 1", "last_message": "runtime message"},
        {"session_id": "s-2", "name": "Session 2"},
    ]
    metadata_records = [
        SimpleNamespace(
            session_id="s-1",
            latest_event_state="running",
            snapshot_version="1",
            metadata_json='{"context_summary_preview":"Metadata summary"}',
        )
    ]

    merged = merge_runtime_sessions_with_metadata(runtime_sessions, metadata_records)

    assert len(merged) == 2
    assert merged[0]["context_summary_preview"] == "Metadata summary"
    assert merged[0]["last_message"] == "runtime message"
    assert "context_summary_preview" not in merged[1]


def test_merge_runtime_sessions_with_metadata_appends_metadata_only_when_enabled():
    runtime_sessions = [
        {"session_id": "s-1", "name": "Session 1", "last_message": "runtime message"},
    ]
    metadata_records = [
        SimpleNamespace(
            session_id="s-metadata-only",
            latest_event_state="running",
            snapshot_version="2",
            metadata_json='{"context_summary_preview":"Metadata-only summary","context_next_step_preview":"Metadata-only next"}',
            updated_at=datetime.utcnow(),
        ),
        SimpleNamespace(
            session_id="s-1",
            latest_event_state="done",
            snapshot_version="1",
            metadata_json='{"context_summary_preview":"Runtime+metadata summary"}',
            updated_at=datetime.utcnow(),
        ),
    ]

    merged = merge_runtime_sessions_with_metadata(
        runtime_sessions,
        metadata_records,
        include_metadata_only=True,
    )

    assert merged[0]["session_id"] == "s-1"
    assert merged[0]["context_summary_preview"] == "Runtime+metadata summary"
    assert merged[1]["session_id"] == "s-metadata-only"
    assert merged[1]["is_metadata_only"] is True
    assert merged[1]["context_summary_preview"] == "Metadata-only summary"


def test_extract_context_preview_prefers_flat_preview_keys_over_nested_context_state():
    record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="5",
        metadata_json='{"context_summary_preview":"Flat summary","context_objective_preview":"Flat objective","context_state":{"summary":"Nested summary","objective":"Nested objective","next_step":"Nested next","compaction_level":"full"}}',
    )

    extracted = extract_context_preview(record)

    assert extracted["context_summary_preview"] == "Flat summary"
    assert extracted["context_objective_preview"] == "Flat objective"
    assert extracted["context_next_step_preview"] == "Nested next"
    assert extracted["context_compaction_level"] == "full"


def test_extract_context_preview_falls_back_to_nested_context_state():
    record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="5",
        metadata_json='{"context_state":{"compaction_level":"full","objective":"Stabilize progressive context rollout","summary":"Context summary from nested state","next_step":"Run final verification"}}',
    )

    extracted = extract_context_preview(record)

    assert extracted["context_compaction_level"] == "full"
    assert extracted["context_objective_preview"] == "Stabilize progressive context rollout"
    assert extracted["context_summary_preview"] == "Context summary from nested state"
    assert extracted["context_next_step_preview"] == "Run final verification"


def test_extract_context_preview_filters_blank_strings():
    record = SimpleNamespace(
        latest_event_state="   ",
        snapshot_version="   ",
        metadata_json='{"context_compaction_level":"   ","context_summary_preview":"","context_state":{"objective":"   ","summary":"Nested summary"}}',
    )

    extracted = extract_context_preview(record)

    assert "latest_event_state" not in extracted
    assert "snapshot_version" not in extracted
    assert "context_compaction_level" not in extracted
    assert "context_objective_preview" not in extracted
    assert extracted["context_summary_preview"] == "Nested summary"


def test_extract_context_preview_derives_budget_preview_from_nested_context_state_budget():
    record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="3",
        metadata_json='{"context_state":{"budget":{"prepared_usage_percent":44.2,"prepared_tokens":98000,"context_window_tokens":200000,"next_compaction_action":"approaching_micro_compaction"}}}',
    )
    extracted = extract_context_preview(record)
    assert extracted["context_usage_percent"] == 44.2
    assert extracted["context_estimated_tokens"] == 98000
    assert extracted["context_window_tokens"] == 200000
    assert extracted["context_next_compaction_action"] == "approaching_micro_compaction"


def test_extract_context_preview_derives_new_projection_and_budget_fields():
    record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="3",
        metadata_json='{"context_state":{"budget":{"prompt_budget_tokens":32000,"request_estimated_tokens":28000,"reserved_output_tokens":4000,"safety_margin_tokens":1000,"projection_chars_saved":9000,"projected_old_assistant_messages":7,"projected_old_tool_messages":3,"context_blob_refs_created":2}}}',
    )
    extracted = extract_context_preview(record)
    assert extracted["context_prompt_budget_tokens"] == 32000
    assert extracted["context_request_estimated_tokens"] == 28000
    assert extracted["context_reserved_output_tokens"] == 4000
    assert extracted["context_safety_margin_tokens"] == 1000
    assert extracted["context_projection_chars_saved"] == 9000
    assert extracted["context_projected_old_assistant_messages"] == 7
    assert extracted["context_projected_old_tool_messages"] == 3
    assert extracted["context_context_blob_refs_created"] == 2


def test_extract_context_preview_derives_optional_projection_output_guard_fields():
    record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="3",
        metadata_json='{"context_state":{"budget":{"projected_recent_assistant_messages":2,"projected_plain_assistant_messages":1,"assistant_projection_chars_saved":700,"output_size_guard_applied":true,"large_generation_guard_applied":true}}}',
    )
    extracted = extract_context_preview(record)
    assert extracted["context_projected_recent_assistant_messages"] == 2
    assert extracted["context_projected_plain_assistant_messages"] == 1
    assert extracted["context_assistant_projection_chars_saved"] == 700
    assert extracted["context_output_size_guard_applied"] is True
    assert extracted["context_large_generation_guard_applied"] is True


def test_extract_context_preview_converts_context_blob_ref_list_to_count():
    record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="3",
        metadata_json='{"context_state":{"budget":{"context_blob_refs_created":["ctx://context/1","ctx://context/2"]}}}',
    )
    extracted = extract_context_preview(record)
    assert extracted["context_context_blob_refs_created"] == 2


def test_extract_context_preview_converts_flat_context_blob_ref_list_to_count():
    record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="3",
        metadata_json='{"context_context_blob_refs_created":["ctx://context/a","ctx://context/b"]}',
    )
    extracted = extract_context_preview(record)
    assert extracted["context_context_blob_refs_created"] == 2


def test_extract_context_preview_preserves_request_over_budget_from_nested_and_flat():
    nested_record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="3",
        metadata_json='{"context_state":{"budget":{"request_over_budget":true}}}',
    )
    nested_extracted = extract_context_preview(nested_record)
    assert nested_extracted["context_request_over_budget"] is True

    flat_record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="3",
        metadata_json='{"context_request_over_budget":true}',
    )
    flat_extracted = extract_context_preview(flat_record)
    assert flat_extracted["context_request_over_budget"] is True


def test_extract_context_preview_preserves_max_output_and_prompt_tokens_nested_and_flat():
    nested_record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="3",
        metadata_json='{"context_state":{"budget":{"max_output_tokens":64000,"max_prompt_tokens":32000}}}',
    )
    nested_extracted = extract_context_preview(nested_record)
    assert nested_extracted["context_max_output_tokens"] == 64000
    assert nested_extracted["context_max_prompt_tokens"] == 32000

    flat_record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="3",
        metadata_json='{"context_max_output_tokens":128000,"context_max_prompt_tokens":96000}',
    )
    flat_extracted = extract_context_preview(flat_record)
    assert flat_extracted["context_max_output_tokens"] == 128000
    assert flat_extracted["context_max_prompt_tokens"] == 96000


def test_extract_context_preview_omits_missing_max_output_and_prompt_tokens():
    record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="3",
        metadata_json='{"context_state":{"budget":{"request_estimated_tokens":1234}}}',
    )
    extracted = extract_context_preview(record)
    assert "context_max_output_tokens" not in extracted
    assert "context_max_prompt_tokens" not in extracted


def test_extract_context_preview_preserves_request_budget_stage_from_nested_and_flat():
    nested_record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="3",
        metadata_json='{"context_state":{"budget":{"request_budget_stage":"skill_finalizer"}}}',
    )
    nested_extracted = extract_context_preview(nested_record)
    assert nested_extracted["context_request_budget_stage"] == "skill_finalizer"

    flat_record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="3",
        metadata_json='{"context_request_budget_stage":"skill_finalizer"}',
    )
    flat_extracted = extract_context_preview(flat_record)
    assert flat_extracted["context_request_budget_stage"] == "skill_finalizer"


def test_extract_context_preview_derives_next_pruning_policy_from_nested_budget():
    record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="3",
        metadata_json='{"context_state":{"budget":{"next_pruning_policy":"No compaction planned yet."}}}',
    )
    extracted = extract_context_preview(record)
    assert extracted["context_next_pruning_policy"] == "No compaction planned yet."


def test_extract_context_preview_derives_next_pruning_policy_from_flat_metadata():
    record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="3",
        metadata_json='{"context_next_pruning_policy":"Approaching micro-compaction..."}',
    )
    extracted = extract_context_preview(record)
    assert extracted["context_next_pruning_policy"] == "Approaching micro-compaction..."


def test_serialize_agent_session_metadata_with_preview_supports_nested_context_state():
    now = datetime.utcnow()
    record = SimpleNamespace(
        id="meta-2",
        session_id="s-2",
        agent_id="a-1",
        group_id=None,
        current_task_id=None,
        current_delegation_id=None,
        current_coordination_run_id=None,
        source_type=None,
        source_ref=None,
        last_execution_id=None,
        latest_event_type=None,
        latest_event_state="running",
        snapshot_version="2",
        pending_delegations_json=None,
        runtime_events_json=None,
        metadata_json='{"context_state":{"compaction_level":"full","objective":"Nested objective","summary":"Nested summary","next_step":"Nested next"}}',
        created_at=now,
        updated_at=now,
    )

    serialized = serialize_agent_session_metadata_with_preview(record)

    assert serialized["context_compaction_level"] == "full"
    assert serialized["context_objective_preview"] == "Nested objective"
    assert serialized["context_summary_preview"] == "Nested summary"
    assert serialized["context_next_step_preview"] == "Nested next"


def test_extract_context_preview_maps_source_diagnostics_from_nested_and_flat_fields():
    nested_record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="9",
        metadata_json='{"context_state":{"source":{"source_complete":false,"comments_loaded":7,"comments_total":10,"attachments_loaded":2,"attachments_total":4,"source_partial_reasons_count":3,"generation_mode":"staged","current_generation_phase":"manifest","large_generation_guard_reason":"budget_guard"}}}',
    )

    nested = extract_context_preview(nested_record)
    assert nested["context_source_complete"] is False
    assert nested["context_comments_loaded"] == 7
    assert nested["context_comments_total"] == 10
    assert nested["context_attachments_loaded"] == 2
    assert nested["context_attachments_total"] == 4
    assert nested["context_source_partial_reasons_count"] == 3
    assert nested["context_generation_mode"] == "staged"
    assert nested["context_current_generation_phase"] == "manifest"
    assert nested["context_large_generation_guard_reason"] == "budget_guard"

    flat_record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="9",
        metadata_json='{"source_complete":true,"comments_loaded":11,"comments_total":12,"attachments_loaded":5,"attachments_total":6,"source_partial_reasons_count":1,"generation_mode":"staged","current_generation_phase":"feature","large_generation_guard_reason":"guard_flat","context_state":{"source":{"source_complete":false,"comments_loaded":1,"comments_total":99,"attachments_loaded":0,"attachments_total":99,"source_partial_reasons_count":8,"generation_mode":"legacy","current_generation_phase":"ignored","large_generation_guard_reason":"ignored"}}}',
    )

    flat = extract_context_preview(flat_record)
    assert flat["context_source_complete"] is True
    assert flat["context_comments_loaded"] == 11
    assert flat["context_comments_total"] == 12
    assert flat["context_attachments_loaded"] == 5
    assert flat["context_attachments_total"] == 6
    assert flat["context_source_partial_reasons_count"] == 1
    assert flat["context_generation_mode"] == "staged"
    assert flat["context_current_generation_phase"] == "feature"
    assert flat["context_large_generation_guard_reason"] == "guard_flat"


def test_extract_context_preview_maps_new_source_and_output_recovery_fields():
    nested_record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="10",
        metadata_json='{"context_state":{"source":{"source_type":"Jira","source_digest_chunk_count":6,"children_loaded":4,"children_total":7},"budget":{"output_risk_level":"medium","max_chat_output_chars":12000,"max_output_recovery_applied":true,"max_output_recovery_attempts":2,"output_token_limit":4096,"input_context_usage_percent":41.5}}}',
    )
    nested = extract_context_preview(nested_record)
    assert nested["context_source_type"] == "Jira"
    assert nested["context_source_digest_chunk_count"] == 6
    assert nested["context_children_loaded"] == 4
    assert nested["context_children_total"] == 7
    assert nested["context_output_risk_level"] == "medium"
    assert nested["context_max_chat_output_chars"] == 12000
    assert nested["context_max_output_recovery_applied"] is True
    assert nested["context_max_output_recovery_attempts"] == 2
    assert nested["context_output_token_limit"] == 4096
    assert nested["context_input_context_usage_percent"] == 41.5

    flat_record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="10",
        metadata_json='{"source_type":"Confluence","source_digest_chunk_count":9,"children_loaded":8,"children_total":8,"output_risk_level":"high","max_chat_output_chars":9000,"max_output_recovery_applied":false,"max_output_recovery_attempts":1,"output_token_limit":2048,"input_context_usage_percent":22.0,"context_state":{"source":{"source_type":"Jira","source_digest_chunk_count":1,"children_loaded":1,"children_total":99},"budget":{"output_risk_level":"low","max_chat_output_chars":1,"max_output_recovery_applied":true,"max_output_recovery_attempts":99,"output_token_limit":1,"input_context_usage_percent":99.0}}}',
    )
    flat = extract_context_preview(flat_record)
    assert flat["context_source_type"] == "Confluence"
    assert flat["context_source_digest_chunk_count"] == 9
    assert flat["context_children_loaded"] == 8
    assert flat["context_children_total"] == 8
    assert flat["context_output_risk_level"] == "high"
    assert flat["context_max_chat_output_chars"] == 9000
    assert flat["context_max_output_recovery_applied"] is False
    assert flat["context_max_output_recovery_attempts"] == 1
    assert flat["context_output_token_limit"] == 2048
    assert flat["context_input_context_usage_percent"] == 22.0


def test_extract_context_preview_maps_new_completeness_and_output_flags_from_flat_and_nested():
    nested_record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="11",
        metadata_json='{"context_state":{"source":{"comments_complete":true,"attachments_complete":false,"children_complete":true,"text_attachments_loaded":4,"text_attachments_total":6,"text_attachments_complete":false,"text_attachments_preview_only":2,"binary_attachment_bodies_skipped_count":3},"budget":{"attachment_body_complete":true,"max_chat_output_enforced":true,"oversized_output_saved":true,"oversized_output_ref_count":2}}}',
    )
    nested = extract_context_preview(nested_record)
    assert nested["context_comments_complete"] is True
    assert nested["context_attachments_complete"] is False
    assert nested["context_children_complete"] is True
    assert nested["context_text_attachments_loaded"] == 4
    assert nested["context_text_attachments_total"] == 6
    assert nested["context_text_attachments_complete"] is False
    assert nested["context_text_attachments_preview_only"] == 2
    assert nested["context_binary_attachment_bodies_skipped_count"] == 3
    assert nested["context_attachment_body_complete"] is True
    assert nested["context_max_chat_output_enforced"] is True
    assert nested["context_oversized_output_saved"] is True
    assert nested["context_oversized_output_ref_count"] == 2

    flat_record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="11",
        metadata_json='{"comments_complete":false,"attachments_complete":true,"children_complete":false,"text_attachments_loaded":9,"text_attachments_total":10,"text_attachments_complete":true,"text_attachments_preview_only":1,"binary_attachment_bodies_skipped_count":0,"attachment_body_complete":false,"max_chat_output_enforced":false,"oversized_output_saved":false,"oversized_output_ref_count":7,"context_state":{"source":{"comments_complete":true,"attachments_complete":false,"children_complete":true,"text_attachments_loaded":1,"text_attachments_total":99,"text_attachments_complete":false,"text_attachments_preview_only":5,"binary_attachment_bodies_skipped_count":9},"budget":{"attachment_body_complete":true,"max_chat_output_enforced":true,"oversized_output_saved":true,"oversized_output_ref_count":99}}}',
    )
    flat = extract_context_preview(flat_record)
    assert flat["context_comments_complete"] is False
    assert flat["context_attachments_complete"] is True
    assert flat["context_children_complete"] is False
    assert flat["context_text_attachments_loaded"] == 9
    assert flat["context_text_attachments_total"] == 10
    assert flat["context_text_attachments_complete"] is True
    assert flat["context_text_attachments_preview_only"] == 1
    assert flat["context_binary_attachment_bodies_skipped_count"] == 0
    assert flat["context_attachment_body_complete"] is False
    assert flat["context_max_chat_output_enforced"] is False
    assert flat["context_oversized_output_saved"] is False
    assert flat["context_oversized_output_ref_count"] == 7


def test_extract_context_preview_maps_output_controller_generation_fields_flat_and_nested():
    nested_record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="12",
        metadata_json='{"context_state":{"source":{"output_controller_applied":true,"source_context_mode":"preview","default_source_complete_applied":true,"source_preview_tool_used":true},"generation":{"completed_phases_count":3,"next_phase":"step_definitions","state_active":true}}}',
    )
    nested = extract_context_preview(nested_record)
    assert nested["context_output_controller_applied"] is True
    assert nested["context_source_context_mode"] == "preview"
    assert nested["context_default_source_complete_applied"] is True
    assert nested["context_source_preview_tool_used"] is True
    assert nested["context_generation_completed_phases_count"] == 3
    assert nested["context_generation_next_phase"] == "step_definitions"
    assert nested["context_generation_state_active"] is True

    flat_record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="12",
        metadata_json='{"output_controller_applied":false,"source_context_mode":"source_complete","default_source_complete_applied":false,"source_preview_tool_used":false,"generation_completed_phases_count":5,"generation_next_phase":"feature","generation_state_active":false,"context_state":{"source":{"output_controller_applied":true,"source_context_mode":"preview","default_source_complete_applied":true,"source_preview_tool_used":true},"generation":{"completed_phases_count":1,"next_phase":"ignored","state_active":true}}}',
    )
    flat = extract_context_preview(flat_record)
    assert flat["context_output_controller_applied"] is False
    assert flat["context_source_context_mode"] == "source_complete"
    assert flat["context_default_source_complete_applied"] is False
    assert flat["context_source_preview_tool_used"] is False
    assert flat["context_generation_completed_phases_count"] == 5
    assert flat["context_generation_next_phase"] == "feature"
    assert flat["context_generation_state_active"] is False


def test_extract_context_preview_maps_source_ref_and_controller_reason_fields():
    nested_record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="13",
        metadata_json='{"context_state":{"source":{"source_ref_session_valid":true,"default_source_complete_ref_session":"current","model_facing_preview_tool_available":true,"preview_tool_used":true,"output_controller_stage":"tool_loop","output_controller_recovery_reason":"oversized_output"}}}',
    )
    nested = extract_context_preview(nested_record)
    assert nested["context_source_ref_session_valid"] is True
    assert nested["context_default_source_complete_ref_session"] == "current"
    assert nested["context_model_facing_preview_tool_available"] is True
    assert nested["context_preview_tool_used"] is True
    assert nested["context_output_controller_stage"] == "tool_loop"
    assert nested["context_output_controller_recovery_reason"] == "oversized_output"

    flat_record = SimpleNamespace(
        latest_event_state="running",
        snapshot_version="13",
        metadata_json='{"source_ref_session_valid":false,"default_source_complete_ref_session":"mismatch","model_facing_preview_tool_available":false,"preview_tool_used":false,"output_controller_stage":"finalizer","output_controller_recovery_reason":"max_output_tokens","context_state":{"source":{"source_ref_session_valid":true,"default_source_complete_ref_session":"current","model_facing_preview_tool_available":true,"preview_tool_used":true,"output_controller_stage":"tool_loop","output_controller_recovery_reason":"oversized_output"}}}',
    )
    flat = extract_context_preview(flat_record)
    assert flat["context_source_ref_session_valid"] is False
    assert flat["context_default_source_complete_ref_session"] == "mismatch"
    assert flat["context_model_facing_preview_tool_available"] is False
    assert flat["context_preview_tool_used"] is False
    assert flat["context_output_controller_stage"] == "finalizer"
    assert flat["context_output_controller_recovery_reason"] == "max_output_tokens"
