import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from _js_extract_helpers import _extract_js_function


SRC = Path("app/static/js/chat_ui.js")


def _src() -> str:
    return SRC.read_text(encoding="utf-8")


def _run_node(script: str) -> dict:
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping agent timeline reducer test")
    # The bundle is far past Windows' command-line limit, so it goes through a
    # file rather than `node -e`.
    with tempfile.TemporaryDirectory() as tmp:
        script_path = Path(tmp) / "agent_timeline_case.js"
        script_path.write_text(script, encoding="utf-8")
        result = subprocess.run([node_bin, str(script_path)], check=False, text=True, capture_output=True)
    if result.returncode != 0:
        raise AssertionError(f"node failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return json.loads(result.stdout.strip())


def _timeline_bundle(js: str) -> str:
    helper_names = [
        "normalizeRuntimeEventTypeAlias",
        "isTrackableStreamEvent",
        "isCompletionRuntimeState",
        "normalizeRuntimeEvent",
        "runtimeEventSummaryHash",
        "runtimeEventUniqueId",
        "runtimeEventDedupKey",
        "createAgentTimelineState",
        "isAgentTimelinePacedItem",
        "getAgentTimelineRenderableItems",
        "getAgentTimelineVisibleItems",
        "advanceAgentTimelineReveal",
        "clearAgentTimelineRevealTimer",
        "ensureAgentTimelineState",
        "getAgentTimelineEventData",
        "getAgentTimelineField",
        "normalizeAgentTimelineJsonText",
        "truncateAgentTimelineText",
        "getAgentTimelineCallId",
        "getAgentTimelinePartId",
        "getAgentTimelineEventKey",
        "findAgentTimelineItem",
        "upsertAgentTimelineItem",
        "appendAgentTimelineText",
        "agentTimelineToolName",
        "agentTimelineInputText",
        "agentTimelineOutputText",
        "agentTimelineReasoningText",
        "reduceAgentTimelineStepEvent",
        "reduceAgentTimelineTextEvent",
        "reduceAgentTimelineReasoningEvent",
        "reduceAgentTimelineToolEvent",
        "reduceAgentTimelineCompactionEvent",
        "reduceAgentTimelinePermissionEvent",
        "reduceAgentTimelineGenericEvent",
        "reduceAgentTimelineEvent",
        # The live model badge reads the answering model from the same events;
        # pulled in so the usage.updated case can prove it still does.
        "isAutoModelId",
        "assistantModelBareId",
        "assistantModelAttribution",
    ]
    return "\n".join(_extract_js_function(js, name) for name in helper_names)


def test_agent_timeline_reducer_handles_opencode_session_next_events():
    js = _src()
    script = f"""
const COMPLETION_RUNTIME_STATES = new Set(["complete", "completed", "done", "finished"]);
{_timeline_bundle(js)}

const chatState = {{
  sessionId: "sess-1",
  currentRequest: {{ requestId: "req-1", clientRequestId: "req-1", sessionIdAtSend: "sess-1" }},
}};
const opencodeTool = normalizeRuntimeEvent({{
  id: "evt-tool-called",
  type: "session.next.tool.called",
  properties: {{ callID: "call-1", tool: "bash", input: {{ command: "ls -la /tmp" }} }},
  sessionID: "sess-1",
  requestID: "req-1",
}});

const events = [
  normalizeRuntimeEvent({{ id: "evt-step-start", type: "session.next.step.started", properties: {{ model: "gpt-test", agent: "builder" }}, session_id: "sess-1", request_id: "req-1" }}),
  normalizeRuntimeEvent({{ id: "evt-text-start", type: "session.next.text.started", properties: {{ partID: "text-1" }}, session_id: "sess-1", request_id: "req-1" }}),
  normalizeRuntimeEvent({{ id: "evt-text-d1", type: "session.next.text.delta", properties: {{ partID: "text-1", delta: "hello " }}, session_id: "sess-1", request_id: "req-1" }}),
  normalizeRuntimeEvent({{ id: "evt-text-d1", type: "session.next.text.delta", properties: {{ partID: "text-1", delta: "hello " }}, session_id: "sess-1", request_id: "req-1" }}),
  normalizeRuntimeEvent({{ id: "evt-text-d2", type: "session.next.text.delta", properties: {{ partID: "text-1", delta: "world" }}, session_id: "sess-1", request_id: "req-1" }}),
  opencodeTool,
  normalizeRuntimeEvent({{ id: "evt-tool-progress", type: "session.next.tool.progress", properties: {{ callID: "call-1", tool: "bash", progress: "reading directory" }}, session_id: "sess-1", request_id: "req-1" }}),
  normalizeRuntimeEvent({{ id: "evt-tool-success", type: "session.next.tool.success", properties: {{ callID: "call-1", tool: "bash", output: "done" }}, session_id: "sess-1", request_id: "req-1" }}),
  normalizeRuntimeEvent({{ id: "evt-compact", type: "session.next.compaction.started", properties: {{ summary: "compressing context" }}, session_id: "sess-1", request_id: "req-1" }}),
  normalizeRuntimeEvent({{ id: "evt-permission", type: "permission.requested", properties: {{ permission_id: "perm-1", tool: "bash", message: "Allow bash?" }}, session_id: "sess-1", request_id: "req-1" }}),
  normalizeRuntimeEvent({{ id: "evt-step-end", type: "session.next.step.ended", properties: {{ message: "done" }}, session_id: "sess-1", request_id: "req-1" }}),
];
const results = events.map((event) => reduceAgentTimelineEvent(chatState, event));
const timeline = chatState.inflightAgentTimeline;
const toolItem = timeline.items.find((item) => item.kind === "tool");
console.log(JSON.stringify({{
  normalizedToolType: opencodeTool.type,
  normalizedToolCall: opencodeTool.data.callID,
  trackableTool: isTrackableStreamEvent("session.next.tool.success"),
  trackableQuestion: isTrackableStreamEvent("question.requested"),
  duplicateChanged: results[3].changed,
  assistantText: timeline.assistantText,
  completed: timeline.completed,
  status: timeline.status,
  model: timeline.model,
  kinds: timeline.items.map((item) => item.kind),
  toolStatus: toolItem.status,
  toolInput: toolItem.input,
  toolOutput: toolItem.output,
  eventCount: Object.keys(timeline.eventsById).length,
}}));
"""
    data = _run_node(script)
    assert data["normalizedToolType"] == "session.next.tool.called"
    assert data["normalizedToolCall"] == "call-1"
    assert data["trackableTool"] is True
    assert data["trackableQuestion"] is True
    assert data["duplicateChanged"] is False
    assert data["assistantText"] == "hello world"
    assert data["completed"] is True
    assert data["status"] == "completed"
    assert data["model"] == "gpt-test"
    assert {"text", "tool", "compaction", "permission"}.issubset(set(data["kinds"]))
    assert data["toolStatus"] == "completed"
    assert "ls -la" in data["toolInput"]
    assert data["toolOutput"] == "done"
    assert data["eventCount"] == 10


def test_agent_timeline_reducer_keeps_legacy_events_compatible():
    js = _src()
    script = f"""
const COMPLETION_RUNTIME_STATES = new Set(["complete", "completed", "done", "finished"]);
{_timeline_bundle(js)}

const chatState = {{
  sessionId: "sess-legacy",
  currentRequest: {{ requestId: "req-legacy", clientRequestId: "req-legacy", sessionIdAtSend: "sess-legacy" }},
}};
[
  normalizeRuntimeEvent({{ event_type: "assistant_delta", event_id: "legacy-delta", detail_payload: {{ delta: "old text" }}, session_id: "sess-legacy", request_id: "req-legacy" }}),
  normalizeRuntimeEvent({{ event_type: "llm_thinking", event_id: "legacy-think", detail_payload: {{ message: "planning" }}, session_id: "sess-legacy", request_id: "req-legacy" }}),
  normalizeRuntimeEvent({{ event_type: "tool.started", event_id: "legacy-tool-start", detail_payload: {{ tool: "search", input: "query" }}, session_id: "sess-legacy", request_id: "req-legacy" }}),
  normalizeRuntimeEvent({{ event_type: "tool.completed", event_id: "legacy-tool-done", detail_payload: {{ tool: "search", output: "result" }}, session_id: "sess-legacy", request_id: "req-legacy" }}),
  normalizeRuntimeEvent({{ event_type: "permission_request", event_id: "legacy-perm", detail_payload: {{ message: "approve?" }}, session_id: "sess-legacy", request_id: "req-legacy" }}),
  normalizeRuntimeEvent({{ event_type: "complete", event_id: "legacy-complete", detail_payload: {{ message: "done" }}, session_id: "sess-legacy", request_id: "req-legacy" }}),
].forEach((event) => reduceAgentTimelineEvent(chatState, event));
const timeline = chatState.inflightAgentTimeline;
const toolItem = timeline.items.find((item) => item.kind === "tool");
console.log(JSON.stringify({{
  assistantText: timeline.assistantText,
  completed: timeline.completed,
  status: timeline.status,
  hasReasoning: timeline.items.some((item) => item.kind === "reasoning" && item.summary.includes("planning")),
  hasPermission: timeline.items.some((item) => item.kind === "permission"),
  toolStatus: toolItem.status,
  toolOutput: toolItem.output,
}}));
"""
    data = _run_node(script)
    assert data["assistantText"] == "old text"
    assert data["completed"] is True
    assert data["status"] == "completed"
    assert data["hasReasoning"] is True
    assert data["hasPermission"] is True
    assert data["toolStatus"] == "completed"
    assert data["toolOutput"] == "result"


def test_agent_timeline_paces_visible_rows_without_dropping_events():
    js = _src()
    script = f"""
const COMPLETION_RUNTIME_STATES = new Set(["complete", "completed", "done", "finished"]);
{_timeline_bundle(js)}

const chatState = {{
  sessionId: "sess-paced",
  currentRequest: {{ requestId: "req-paced", clientRequestId: "req-paced", sessionIdAtSend: "sess-paced" }},
}};
const events = [
  normalizeRuntimeEvent({{ id: "evt-tool-1", type: "session.next.tool.called", properties: {{ callID: "call-1", tool: "bash", input: {{ command: "pwd" }} }}, session_id: "sess-paced", request_id: "req-paced" }}),
  normalizeRuntimeEvent({{ id: "evt-tool-2", type: "session.next.tool.called", properties: {{ callID: "call-2", tool: "read", input: {{ file: "README.md" }} }}, session_id: "sess-paced", request_id: "req-paced" }}),
  normalizeRuntimeEvent({{ id: "evt-perm-1", type: "permission.requested", properties: {{ permission_id: "perm-1", tool: "bash", message: "Allow bash?" }}, session_id: "sess-paced", request_id: "req-paced" }}),
  normalizeRuntimeEvent({{ id: "evt-compact-1", type: "session.next.compaction.started", properties: {{ partID: "compact-1", summary: "compressing context" }}, session_id: "sess-paced", request_id: "req-paced" }}),
];
events.forEach((event) => reduceAgentTimelineEvent(chatState, event));
const timeline = chatState.inflightAgentTimeline;
const totalRows = getAgentTimelineRenderableItems(timeline).length;
const initialVisibleRows = getAgentTimelineVisibleItems(timeline).length;
advanceAgentTimelineReveal(timeline);
const afterFirstVisibleRows = getAgentTimelineVisibleItems(timeline).length;
advanceAgentTimelineReveal(timeline);
const afterSecondVisibleRows = getAgentTimelineVisibleItems(timeline).length;
const visibleBeforeDuplicate = timeline.visibleItemCount;
const duplicateResult = reduceAgentTimelineEvent(chatState, events[1]);
console.log(JSON.stringify({{
  itemCount: timeline.items.length,
  kinds: timeline.items.map((item) => item.kind),
  totalRows,
  initialVisibleRows,
  afterFirstVisibleRows,
  afterSecondVisibleRows,
  duplicateChanged: duplicateResult.changed,
  visibleBeforeDuplicate,
  visibleAfterDuplicate: timeline.visibleItemCount,
  itemCountAfterDuplicate: timeline.items.length,
  eventCount: Object.keys(timeline.eventsById).length,
  assistantText: timeline.assistantText,
}}));
"""
    data = _run_node(script)
    assert data["itemCount"] == 4
    assert data["itemCountAfterDuplicate"] == 4
    assert {"tool", "permission", "compaction"}.issubset(set(data["kinds"]))
    assert data["totalRows"] == 4
    assert data["initialVisibleRows"] < data["totalRows"]
    assert data["afterFirstVisibleRows"] == data["initialVisibleRows"] + 1
    assert data["afterSecondVisibleRows"] == data["afterFirstVisibleRows"] + 1
    assert data["duplicateChanged"] is False
    assert data["visibleAfterDuplicate"] == data["visibleBeforeDuplicate"]
    assert data["eventCount"] == 4
    assert data["assistantText"] == ""


def test_agent_timeline_reducer_draws_no_row_for_usage_updated():
    # The native runtime sends usage.updated after every LLM call: token
    # counts plus the model that answered. The live model badge keeps reading
    # that model from the event, so it stays trackable, but the timeline must
    # not turn each one into a "Usage updated / completed" row.
    js = _src()
    script = f"""
const COMPLETION_RUNTIME_STATES = new Set(["complete", "completed", "done", "finished"]);
{_timeline_bundle(js)}

const chatState = {{
  sessionId: "sess-usage",
  currentRequest: {{ requestId: "req-usage", clientRequestId: "req-usage", sessionIdAtSend: "sess-usage" }},
}};
// The gateway projection's shape for the runner's usage.updated, as the chat
// stream wrapper hands it to handleAgentEventMessage.
const usageFields = {{
  type: "usage.updated", event_type: "usage.updated", engine: "native",
  session_id: "sess-usage", request_id: "req-usage", agent_id: "agent-1",
  raw_type: "usage.updated", created_at: "2026-10-22T01:02:03Z",
  state: "running", summary: "Usage updated",
  run_id: "run-1", iteration: 1, pricing_enabled: false,
  usage: {{ total_tokens: 12 }}, step_usage: {{ total_tokens: 12 }}, iteration_usage: {{ total_tokens: 12 }},
  model: "gpt-6-luna", resolved_model: "gpt-6-luna", requested_model: "auto", auto_tier: "balance",
}};
const projected = {{ id: "evt-usage-1", ...usageFields, properties: {{ ...usageFields }}, data: {{ ...usageFields }} }};
const usageEvent = normalizeRuntimeEvent({{
  type: "usage.updated", event_type: "usage.updated", stream_event: "runtime_event",
  request_id: "req-usage", session_id: "sess-usage", agent_id: "agent-1",
  data: {{ ...projected, stream_event: "runtime_event" }},
}});
const events = [
  normalizeRuntimeEvent({{ id: "evt-step-start", type: "session.next.step.started", properties: {{ model: "auto", agent: "builder" }}, session_id: "sess-usage", request_id: "req-usage" }}),
  normalizeRuntimeEvent({{ id: "evt-tool-1", type: "session.next.tool.called", properties: {{ callID: "call-1", tool: "bash", input: {{ command: "pwd" }} }}, session_id: "sess-usage", request_id: "req-usage" }}),
  usageEvent,
  normalizeRuntimeEvent({{ id: "evt-provider-1", type: "provider.status", properties: {{ message: "retrying after 429" }}, session_id: "sess-usage", request_id: "req-usage" }}),
];
const results = events.map((event) => reduceAgentTimelineEvent(chatState, event));
const timeline = chatState.inflightAgentTimeline;
const duplicate = reduceAgentTimelineEvent(chatState, usageEvent);
const attribution = assistantModelAttribution(projected);
console.log(JSON.stringify({{
  normalizedType: usageEvent.type,
  trackable: isTrackableStreamEvent(usageEvent.type),
  usageChanged: results[2].changed,
  providerChanged: results[3].changed,
  duplicateUsage: duplicate.duplicate === true,
  kinds: timeline.items.map((item) => item.kind),
  titles: timeline.items.map((item) => item.title),
  renderableRows: getAgentTimelineRenderableItems(timeline).length,
  eventCount: Object.keys(timeline.eventsById).length,
  badgeModel: attribution && attribution.model,
  badgeRequested: attribution && attribution.requestedModel,
}}));
"""
    data = _run_node(script)
    assert data["normalizedType"] == "usage.updated"
    assert data["trackable"] is True
    assert data["usageChanged"] is False
    assert data["providerChanged"] is True
    assert data["duplicateUsage"] is True
    assert data["kinds"] == ["step", "tool", "provider"]
    assert "Usage updated" not in data["titles"]
    assert data["renderableRows"] == 2
    assert data["eventCount"] == 4
    assert data["badgeModel"] == "gpt-6-luna"
    assert data["badgeRequested"] == "auto"
