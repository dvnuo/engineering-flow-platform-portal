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
        ("chat.incomplete", {"incomplete_reason": "no final"}, "warning", "warning", "Chat Incomplete"),
        ("chat.failed", {"error": "failed"}, "error", "error", "Chat Failed"),
        ("chat.completed", {}, "success", "success", "Chat Completed"),
        ("event_bridge.connected", {}, "success", "success", "Event Bridge Connected"),
        ("event_bridge.disconnected", {}, "warning", "warning", "Event Bridge Disconnected"),
        ("event_bridge.reconnected", {}, "success", "success", "Event Bridge Reconnected"),
        ("runtime.raw", {}, "info", "info", "Runtime Event"),
        ("runtime.status.validated", {}, "info", "info", "Runtime Status Validated"),
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


def test_thinking_process_view_keeps_legacy_runtime_recovery_events_generic():
    legacy_types = [
        "chat." + "stream_" + "detached",
        "chat." + "run.started",
        "portal." + "reconcile.started",
        "portal." + "active_" + "request.cleared",
        "opencode." + "session.missing",
        "opencode." + "status.inactive",
        "continuation." + "completed",
        "chat." + "timeout_" + "recovery.exhausted",
    ]
    view = build_thinking_process_view({
        "runtime_events": [
            _event(event_type, {"message": "legacy event", "authorization": "Bearer hidden"})
            for event_type in legacy_types
        ],
    })

    expected_types = [
        "chat." + "stream_" + "detached",
        "chat." + "run.started",
        "portal." + "reconcile.started",
        "portal." + "active_" + "request.cleared",
        "runtime.raw",
        "runtime.raw",
        "continuation." + "completed",
        "chat." + "timeout_" + "recovery.exhausted",
    ]
    assert [event["type"] for event in view["events"]] == expected_types
    for event in view["events"]:
        assert event["display_title"] in {"Runtime event", "Runtime Event"}
        assert "OpenCode" not in event["display_title"]
        assert "opencode" not in event["display_detail"].lower()
        assert event["kind"] == "info"
        assert event["severity"] == "info"
        assert event["safe_detail_payload"]["authorization"] == "[redacted]"
    assert view["events"][4]["safe_detail_payload"]["raw_event_type"] == "opencode.session.missing"


def test_thinking_process_view_keeps_event_types_without_runtime_aliases():
    aliases = [
        "continuation." + "no_progress_" + "timeout",
        "chat." + "timeout_" + "recovery.recovery_exhausted",
        "timeout_" + "recovery.started",
    ]
    view = build_thinking_process_view({
        "runtime_events": [_event(alias) for alias in aliases],
    })

    for event, alias in zip(view["events"], aliases):
        assert event["type"] == alias
        assert event["event_type"] == alias
        assert event["display_title"] == "Runtime event"
        assert event["safe_detail_payload"]["event_type"] == alias
        assert "raw_event_type" not in event["safe_detail_payload"]


def test_thinking_process_view_does_not_special_case_long_run_event_names():
    src = Path("app/services/thinking_process_view.py").read_text(encoding="utf-8")
    forbidden = [
        "chat." + "stream_" + "detached",
        "chat." + "run.completed",
        "chat." + "run.started",
        "continuation." + "completed",
        "chat." + "timeout_" + "recovery",
        "timeout_" + "recovery",
        "portal." + "reconcile",
        "portal." + "active_" + "request.cleared",
    ]

    for marker in forbidden:
        assert marker not in src


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


def test_non_success_hint_contract_without_runtime_run_special_case():
    src = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    script = (
        _extract_js_function(src, "nonSuccessHintForPayload")
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");

            const busy = nonSuccessHintForPayload({ status: "busy" });
            assert.equal(busy.includes('send "continue"'), false);
            assert.match(busy, /still working/);

            const stillActive = nonSuccessHintForPayload({ error: "opencode_abort_still_active" });
            assert.equal(stillActive.includes('send "continue"'), false);
            assert.match(stillActive, /Reset the session|start a new chat/);

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


def test_thinking_process_view_maps_runtime_canonical_part_events():
    view = build_thinking_process_view({
        "runtime_events": [
            _event("runtime.reasoning", {"text": "checked files", "status": "completed"}),
            _event("runtime.tool", {"tool": "bash", "status": "running"}),
            _event("runtime.step.started", {"message": "Step started"}),
            _event("runtime.step.finished", {"reason": "done"}),
            _event("permission_request", {"permission_id": "perm-1", "status": "pending"}),
        ],
    })

    titles = [event["display_title"] for event in view["events"]]
    assert titles == [
        "Runtime Reasoning",
        "Runtime Tool",
        "Runtime Step Started",
        "Runtime Step Finished",
        "Permission Requested",
    ]
    assert view["events"][0]["kind"] == "success"
    assert view["events"][1]["kind"] == "running"


def test_thinking_process_view_maps_legacy_opencode_events_to_runtime_labels():
    view = build_thinking_process_view({
        "runtime_events": [
            _event("opencode.reasoning", {"text": "checked files", "status": "completed"}),
            _event("opencode.tool", {"tool": "bash", "status": "running"}),
            _event("opencode.step.started", {"message": "Step started"}),
            _event("opencode.step.finished", {"reason": "done"}),
            _event("opencode.raw", {"summary": "raw"}),
            _event("opencode.status.validated", {"message": "active"}),
        ],
    })

    assert [event["type"] for event in view["events"]] == [
        "runtime.reasoning",
        "runtime.tool",
        "runtime.step.started",
        "runtime.step.finished",
        "runtime.raw",
        "runtime.status.validated",
    ]
    assert [event["display_title"] for event in view["events"]] == [
        "Runtime Reasoning",
        "Runtime Tool",
        "Runtime Step Started",
        "Runtime Step Finished",
        "Runtime Event",
        "Runtime Status Validated",
    ]
    assert all("OpenCode" not in event["display_title"] for event in view["events"])
    assert view["events"][0]["safe_detail_payload"]["raw_event_type"] == "opencode.reasoning"
