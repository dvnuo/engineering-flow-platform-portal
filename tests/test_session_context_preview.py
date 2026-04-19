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
