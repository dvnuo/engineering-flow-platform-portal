import subprocess
import textwrap
from pathlib import Path

from app.services.thinking_process_view import build_thinking_process_view
from tests._js_extract_helpers import _extract_js_function


def _event(event_type, detail=None, **extra):
    return {
        "event_type": event_type,
        "detail_payload": detail or {},
        **extra,
    }


def test_thinking_process_view_maps_required_runtime_events():
    event_cases = [
        ("chat.started", {}, "running", "running", "Chat Started"),
        ("chat.stream_attached", {}, "running", "running", "Stream Attached"),
        ("chat.stream_detached", {}, "warning", "running", "Stream Detached"),
        ("chat.run.started", {}, "running", "running", "Run Started"),
        ("chat.run.completed", {}, "success", "success", "Run Completed"),
        ("chat.run.incomplete", {"incomplete_reason": "max turns"}, "warning", "warning", "Run Incomplete"),
        ("chat.run.failed", {"error": "boom"}, "error", "error", "Run Failed"),
        ("chat.run.abort_failed", {"error": "abort boom"}, "error", "error", "Run Abort Failed"),
        ("chat.run.stale", {}, "warning", "warning", "Run Stale"),
        ("chat.run.aborted", {}, "warning", "warning", "Run Aborted"),
        ("heartbeat", {}, "running", "running", "Heartbeat"),
        ("status", {"status": "working"}, "info", "info", "Runtime Status"),
        ("llm_thinking", {}, "running", "running", "LLM Thinking"),
        ("message.delta", {"delta": "hi"}, "running", "running", "Message Streaming"),
        ("message.completed", {"message": "done"}, "success", "success", "Message Completed"),
        ("assistant.message.started", {}, "running", "running", "Assistant Message Started"),
        ("assistant.message.updated", {"delta": "hi"}, "running", "running", "Assistant Message Updated"),
        ("assistant.message.completed", {"message": "done"}, "success", "success", "Assistant Message Completed"),
        ("tool.started", {"tool": "bash"}, "running", "running", "Tool Started"),
        ("tool.completed", {"tool": "bash"}, "success", "success", "Tool Completed"),
        ("tool.failed", {"tool": "bash", "error": "boom"}, "error", "error", "Tool Failed"),
        ("tool_call", {"tool": "bash"}, "running", "running", "Tool Call"),
        ("tool_result", {"success": True}, "success", "success", "Tool Result"),
        ("permission_request", {}, "running", "warning", "Permission Requested"),
        ("permission_resolved", {}, "success", "success", "Permission Resolved"),
        ("permission.denied", {}, "error", "error", "Permission Denied"),
        ("permission.allowed", {}, "success", "success", "Permission Allowed"),
        ("provider.retry", {}, "warning", "warning", "Provider Retry"),
        ("provider.rate_limit", {}, "warning", "warning", "Provider Rate Limited"),
        ("model.retry", {}, "warning", "warning", "Model Retry"),
        ("continuation.started", {}, "running", "running", "Continuation Started"),
        ("continuation.prompt_sent", {}, "running", "running", "Continuation Prompt Sent"),
        ("continuation.completed", {}, "success", "success", "Continuation Completed"),
        ("continuation.failed", {}, "error", "error", "Continuation Failed"),
        ("continuation.max_turns_reached", {}, "warning", "warning", "Max Turns Reached"),
        ("continuation.wall_timeout", {}, "warning", "warning", "Continuation Timeout"),
        ("continuation.no_progress", {}, "warning", "warning", "No Progress"),
        ("continuation.suppressed", {"metadata": {"reason": "auto_continue_disabled"}}, "warning", "warning", "Continuation suppressed"),
        ("chat.timeout_recovery.started", {}, "running", "warning", "Timeout Recovery Started"),
        ("chat.timeout_recovery.poll", {}, "running", "running", "Timeout Recovery Poll"),
        ("chat.timeout_recovery.recovered", {}, "success", "success", "Timeout Recovery Recovered"),
        ("chat.timeout_recovery.exhausted", {}, "warning", "warning", "Timeout Recovery Exhausted"),
        ("chat.incomplete", {"incomplete_reason": "no final"}, "warning", "warning", "Chat Incomplete"),
        ("chat.failed", {"error": "failed"}, "error", "error", "Chat Failed"),
        ("chat.completed", {}, "success", "success", "Chat Completed"),
        ("portal.stream_detached", {}, "warning", "running", "Stream Detached"),
        ("portal.reconcile.started", {}, "info", "running", "Reconcile Started"),
        ("portal.reconcile.updated", {}, "running", "running", "Reconcile Updated"),
        ("portal.reconcile.completed", {}, "success", "success", "Reconcile Completed"),
        ("portal.reconcile.failed", {}, "error", "error", "Reconcile Failed"),
        ("portal.active_request.cleared", {}, "warning", "warning", "Active Request Cleared"),
        ("portal.chat_run_already_active", {"message": "Previous message still running"}, "warning", "running", "Previous Run Active"),
        ("portal.abort.completed", {}, "success", "success", "Abort Completed"),
        ("portal.abort.failed", {}, "error", "error", "Abort Failed"),
        ("event_bridge.connected", {}, "success", "success", "Event Bridge Connected"),
        ("event_bridge.disconnected", {}, "warning", "warning", "Event Bridge Disconnected"),
        ("event_bridge.reconnected", {}, "success", "success", "Event Bridge Reconnected"),
        ("opencode.raw", {}, "info", "info", "OpenCode Event"),
        ("opencode.session.aborted", {}, "success", "success", "OpenCode Session Aborted"),
        ("opencode.session.abort_failed", {"error": "abort boom"}, "error", "error", "OpenCode Session Abort Failed"),
        ("opencode.session.missing", {}, "warning", "warning", "OpenCode Session Missing"),
        ("opencode.status.inactive", {}, "warning", "warning", "OpenCode Inactive"),
    ]
    view = build_thinking_process_view({
        "runtime_events": [_event(event_type, detail) for event_type, detail, *_ in event_cases],
    })

    assert len(view["events"]) == len(event_cases)
    for event, (event_type, _detail, kind, severity, title) in zip(view["events"], event_cases):
        assert event["type"] == event_type
        assert event["display_title"] == title
        assert event["kind"] == kind
        assert event["severity"] == severity
        assert event["timestamp"] is not None
        assert event["source"] == "runtime"
        assert event["subtitle"] == event["display_detail"]
        assert event["safe_detail_payload"]["event_type"] == event_type


def test_thinking_process_view_maps_continuation_suppressed_summary_and_metadata():
    view = build_thinking_process_view({
        "runtime_events": [
            _event(
                "continuation.suppressed",
                {},
                summary="Timeout recovery exhausted while runtime is still running",
                metadata={
                    "reason": "auto_continue_disabled",
                    "authorization": "Bearer hidden",
                },
            )
        ],
    })

    event = view["events"][0]
    assert event["type"] == "continuation.suppressed"
    assert event["display_title"] == "Continuation suppressed"
    assert event["display_detail"] == "Timeout recovery exhausted while runtime is still running"
    assert event["kind"] == "warning"
    assert event["severity"] == "warning"
    assert event["safe_detail_payload"]["metadata"]["reason"] == "auto_continue_disabled"
    assert event["safe_detail_payload"]["metadata"]["authorization"] == "[redacted]"


def test_thinking_process_view_normalizes_runtime_event_aliases():
    aliases = [
        ("continuation.no_progress_timeout", "continuation.no_progress", "No Progress"),
        ("chat.timeout_recovery.recovery_exhausted", "chat.timeout_recovery.exhausted", "Timeout Recovery Exhausted"),
        ("timeout_recovery.started", "chat.timeout_recovery.started", "Timeout Recovery Started"),
        ("timeout_recovery.poll", "chat.timeout_recovery.poll", "Timeout Recovery Poll"),
        ("timeout_recovery.recovered", "chat.timeout_recovery.recovered", "Timeout Recovery Recovered"),
        ("timeout_recovery.exhausted", "chat.timeout_recovery.exhausted", "Timeout Recovery Exhausted"),
    ]
    view = build_thinking_process_view({
        "runtime_events": [_event(alias) for alias, *_ in aliases],
    })

    for event, (alias, normalized_type, title) in zip(view["events"], aliases):
        assert event["type"] == normalized_type
        assert event["event_type"] == normalized_type
        assert event["display_title"] == title
        assert event["safe_detail_payload"]["event_type"] == normalized_type
        assert event["safe_detail_payload"]["raw_event_type"] == alias


def test_thinking_process_view_final_severity_uses_completion_state_and_reason():
    view = build_thinking_process_view({
        "runtime_events": [
            _event("final", {"completion_state": "completed"}, event_id="final-1"),
            _event("final", {"completion_state": "incomplete", "incomplete_reason": "max turns"}, event_id="final-2"),
            _event("final", {"completion_state": "failed", "incomplete_reason": "runtime error"}, event_id="final-3"),
        ],
    })

    assert [event["kind"] for event in view["events"]] == ["success", "warning", "error"]
    assert [event["severity"] for event in view["events"]] == ["success", "warning", "error"]
    assert view["events"][1]["display_detail"] == "max turns"
    assert view["events"][2]["display_detail"] == "runtime error"


def test_thinking_process_view_unknown_event_keeps_safe_detail_payload():
    view = build_thinking_process_view({
        "runtime_events": [
            _event(
                "runtime.custom",
                {
                    "summary": "custom summary",
                    "metadata": {"safe": "yes", "access_token": "secret"},
                    "password": "hidden",
                },
            )
        ],
    })

    event = view["events"][0]
    assert event["display_title"] == "Runtime event"
    assert event["kind"] == "info"
    assert event["safe_detail_payload"]["event_type"] == "runtime.custom"
    assert event["safe_detail_payload"]["summary"] == "custom summary"
    assert event["safe_detail_payload"]["metadata"]["safe"] == "yes"
    assert event["safe_detail_payload"]["metadata"]["access_token"] == "[redacted]"
    assert event["safe_detail_payload"]["password"] == "[redacted]"


def test_chat_run_already_active_thinking_display_and_continue_hint_contract():
    view = build_thinking_process_view({
        "runtime_events": [
            _event("portal.chat_run_already_active", {"message": "Previous message still running"}),
        ],
    })
    event = view["events"][0]
    assert event["display_title"] == "Previous Run Active"
    assert event["kind"] == "warning"
    assert event["severity"] == "running"

    src = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    script = (
        _extract_js_function(src, "nonSuccessHintForPayload")
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");

            const active = nonSuccessHintForPayload({ error: "chat_run_already_active" });
            assert.equal(active.includes('send "continue"'), false);
            assert.match(active, /Stop run/);

            const incomplete = nonSuccessHintForPayload({
              completion_state: "incomplete",
              incomplete_reason: "idle incomplete",
            });
            assert.match(incomplete, /send "continue"/);
            """
        )
    )
    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_thinking_process_view_maps_opencode_canonical_part_events():
    view = build_thinking_process_view({
        "runtime_events": [
            _event("opencode.reasoning", {"text": "checked files", "status": "completed"}),
            _event("opencode.tool", {"tool": "bash", "status": "running"}),
            _event("opencode.step.started", {"message": "Step started"}),
            _event("opencode.step.finished", {"reason": "done"}),
            _event("permission_request", {"permission_id": "perm-1", "status": "pending"}),
        ],
    })

    titles = [event["display_title"] for event in view["events"]]
    assert titles == [
        "OpenCode Reasoning",
        "OpenCode Tool",
        "OpenCode Step Started",
        "OpenCode Step Finished",
        "Permission Requested",
    ]
    assert view["events"][0]["kind"] == "success"
    assert view["events"][1]["kind"] == "running"
