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
        metadata_json='{"context_compaction_level":"medium","context_objective_preview":"Ship","context_summary_preview":"Done","context_next_step_preview":"Review"}',
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
