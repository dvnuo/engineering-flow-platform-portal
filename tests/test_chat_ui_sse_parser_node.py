import subprocess
import textwrap
from pathlib import Path

from tests._js_extract_helpers import _extract_js_function


SRC = Path("app/static/js/chat_ui.js")


def test_sse_parser_handles_split_multiline_malformed_and_heartbeat():
    """Node harness for the pure SSE parser helpers; no jsdom dependency required."""
    src = SRC.read_text(encoding="utf-8")
    parser_js = "\n".join(
        [
            _extract_js_function(src, "parseSseEvent"),
            _extract_js_function(src, "parseSseEventsFromChunk"),
        ]
    )
    script = (
        parser_js
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");

            const first = parseSseEventsFromChunk(
              "",
              "event: runtime_event\ndata: {\"type\":\"llm_thinking\","
            );
            assert.equal(first.events.length, 0);
            assert.match(first.buffer, /llm_thinking/);

            const second = parseSseEventsFromChunk(first.buffer, "\"delta\":\"x\"}\n\n");
            assert.equal(second.buffer, "");
            assert.equal(second.events.length, 1);
            assert.equal(second.events[0].eventName, "runtime_event");
            assert.equal(second.events[0].data.type, "llm_thinking");
            assert.equal(second.events[0].data.delta, "x");

            const multiline = parseSseEvent(
              "event: runtime_event\n"
                + "data: {\"type\":\"tool.started\",\n"
                + "data: \"tool\":\"bash\"}"
            );
            assert.equal(multiline.eventName, "runtime_event");
            assert.equal(multiline.data.type, "tool.started");
            assert.equal(multiline.data.tool, "bash");

            const malformed = parseSseEvent("event: runtime_event\ndata: {\"bad\"");
            assert.equal(malformed.eventName, "runtime_event");
            assert.equal(typeof malformed.data, "string");
            assert.match(malformed.data, /\{"bad"/);

            const heartbeat = parseSseEvent("event: heartbeat\ndata: {}\n\n");
            assert.equal(heartbeat.eventName, "heartbeat");
            assert.deepEqual(heartbeat.data, {});
            """
        )
    )

    result = subprocess.run(
        ["node", "-e", script],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_merge_final_thinking_snapshot_node_smoke_for_non_success_payload():
    src = SRC.read_text(encoding="utf-8")
    snapshot_js = "\n".join(
        [
            _extract_js_function(src, "mergeThinkingEvents"),
            _extract_js_function(src, "normalizeThinkingEvents"),
            _extract_js_function(src, "normalizePayloadThinkingEvents"),
            _extract_js_function(src, "getCompletionState"),
            _extract_js_function(src, "mergeFinalThinkingSnapshot"),
        ]
    )
    runtime_helpers = "\n".join(
        [
            "const completionRuntimeState = { terminal: new Set([\"completed\",\"error\",\"failed\",\"blocked\",\"incomplete\",\"empty_final\"]) };",
            "function normalizeRuntimeEvent(event) { return event && typeof event === \"object\" ? event : null; }",
        ]
    )
    script = (
        runtime_helpers
        + "\n"
        + snapshot_js
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");

            const chatState = {
              lastThinkingSnapshot: { events: [] },
              inflightThinking: { events: [] },
              lastCompletedRequestId: "",
            };

            const state = {
              selectedAgentId: "agent-1",
              toolPanelOpen: false,
              activeUtilityPanel: "",
            };

            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }

            function isThinkingPanelActiveForAgent() {
              return false;
            }

            function renderThinkingPanelFromClientState() {
              throw new Error("should not render in this test");
            }

            mergeFinalThinkingSnapshot(
              "agent-1",
              {
                requestId: "req-1",
                clientRequestId: "req-1",
                sessionIdAtSend: "sess-1",
              },
              {
                completion_state: "incomplete",
                incomplete_reason: "auto_continue_max_turns_reached",
                continuation_count: 3,
                request_id: "req-1",
                session_id: "sess-1",
                context_state: {
                  summary: "repo scan",
                  current_state: "incomplete",
                  next_step: "retry",
                },
                runtime_events: [
                  {
                    type: "chat.incomplete",
                    request_id: "req-1",
                    session_id: "sess-1",
                    data: {
                      message: "Incomplete after auto-continue",
                      request_id: "req-1",
                      session_id: "sess-1",
                    },
                  },
                ],
              }
            );

            assert.equal(chatState.lastThinkingSnapshot.requestId, "req-1");
            assert.equal(chatState.lastThinkingSnapshot.sessionId, "sess-1");
            assert.equal(chatState.lastThinkingSnapshot.completion_state, "incomplete");
            assert.equal(chatState.lastThinkingSnapshot.incomplete_reason, "auto_continue_max_turns_reached");
            assert.equal(chatState.lastThinkingSnapshot.continuation_count, 3);
            assert.equal(chatState.lastThinkingSnapshot.events.length, 1);
            assert.equal(chatState.lastThinkingSnapshot.events[0].type, "chat.incomplete");
            assert.equal(chatState.lastThinkingSnapshot.contextState.summary, "repo scan");
            """
        )
    )

    result = subprocess.run(
        ["node", "-e", script],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_thinking_normalizer_regression_guards():
    src = SRC.read_text(encoding="utf-8")
    merge_final = _extract_js_function(src, "mergeFinalThinkingSnapshot")
    success = _extract_js_function(src, "handleAgentChatSuccess")

    assert "normalizeEvents(" not in merge_final
    assert "normalizePayloadThinkingEvents(finalPayload?.runtime_events || [])" in merge_final
    assert "mergeThinkingEvents(existing.events || [], finalPayloadEvents)" in merge_final
    assert "const normalizeEvents" not in success
    assert "function normalizePayloadThinkingEvents(events)" in src or "function normalizeThinkingEvents(events)" in src


def test_canonical_snapshot_conversion_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(
            [
                _extract_js_function(src, "getCanonicalMessagesFromSessionPayload"),
                _extract_js_function(src, "canonicalPartText"),
                _extract_js_function(src, "canonicalMessageVisibleText"),
                _extract_js_function(src, "canonicalMessageToLegacyDisplayMessage"),
                _extract_js_function(src, "canonicalMessagesToLegacyDisplayMessages"),
                _extract_js_function(src, "canonicalPartToThinkingItem"),
                _extract_js_function(src, "canonicalMessagesToThinkingItems"),
            ]
        )
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const canonical = getCanonicalMessagesFromSessionPayload({
              canonical_messages: [
                {
                  message_id: "user-1",
                  role: "user",
                  info: { id: "user-1", role: "user" },
                  parts: [{ id: "u-part-1", type: "text", text: "Please inspect" }],
                },
                {
                  message_id: "assistant-1",
                  role: "assistant",
                  info: { id: "assistant-1", role: "assistant", requestID: "run-1" },
                  parts: [
                    { id: "r-part-1", type: "reasoning", text: "hidden reasoning" },
                    { id: "t-part-1", type: "tool", tool: "bash", state: { status: "completed" }, output: "ok" },
                    { id: "a-part-1", type: "text", text: "Visible answer" },
                  ],
                },
              ],
            });
            const legacy = canonicalMessagesToLegacyDisplayMessages(canonical);
            const assistant = legacy.find((message) => message.role === "assistant");
            const thinking = canonicalMessagesToThinkingItems(canonical);

            assert.equal(canonical.length, 2);
            assert.equal(legacy.length, 2);
            assert.equal(legacy[0].content, "Please inspect");
            assert.equal(assistant.content, "Visible answer");
            assert.equal(assistant.content.includes("hidden reasoning"), false);
            assert.equal(assistant.metadata.source_of_truth, "opencode");
            assert.equal(assistant.metadata.canonical_parts.length, 3);
            assert.equal(thinking.some((item) => item.kind === "reasoning" && item.text === "hidden reasoning"), true);
            assert.equal(thinking.some((item) => item.kind === "tool" && item.tool === "bash"), true);
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_completed_lifecycle_without_final_fetches_session_snapshot_instead_of_detaching():
    src = SRC.read_text(encoding="utf-8")
    stream_js = "\n".join(
        [
            _extract_js_function(src, "parseSseEvent"),
            _extract_js_function(src, "parseSseEventsFromChunk"),
            _extract_js_function(src, "normalizeChatStreamEventName"),
            _extract_js_function(src, "normalizeChatStreamEventData"),
            _extract_js_function(src, "getChatStreamTextPayload"),
            _extract_js_function(src, "getChatStreamRoleMarker"),
            _extract_js_function(src, "getChatStreamRawType"),
            _extract_js_function(src, "isChatStreamSnapshotPayload"),
            _extract_js_function(src, "isChatStreamWrapperEventName"),
            _extract_js_function(src, "isDirectCompletionEventName"),
            _extract_js_function(src, "isChatStreamDeltaEventName"),
            _extract_js_function(src, "isChatStreamFinalEventName"),
            _extract_js_function(src, "rememberAssociatedRuntimeDeltaEvent"),
            _extract_js_function(src, "getAssociatedRuntimeDeltaEvent"),
            _extract_js_function(src, "isSyntheticFinalDeltaEvent"),
            _extract_js_function(src, "buildAssistantStreamDeltaGuardSource"),
            _extract_js_function(src, "shouldIgnoreAssistantStreamDelta"),
            _extract_js_function(src, "handleChatStreamEvent"),
            _extract_js_function(src, "finalizeFromSessionSnapshotAfterCompletedLifecycle"),
            _extract_js_function(src, "trySubmitChatStreamForSelectedAgent"),
        ]
    )
    script = (
        stream_js
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const encoder = new TextEncoder();
            const calls = [];
            const requestCtx = {
              clientRequestId: "req-1",
              requestId: "req-1",
              runtimeRequestId: "req-1",
              sessionIdAtSend: "sess-1",
              streamedText: "",
            };
            const chatState = {
              activeRequest: requestCtx,
              inflightThinking: { events: [], completed: false },
              isSubmitting: true,
              sessionId: "sess-1",
              openCodeProjection: {
                sessionStatus: "busy",
                sessionStatusPayload: { active: true, status_type: "busy", active_run: { request_id: "req-1" } },
              },
            };
            const state = { selectedAgentId: "agent-1" };
            const dom = {
              sendChatBtn: { disabled: true },
              abortChatRunBtn: {
                disabled: false,
                classList: { toggle(name, hidden) { calls.push(["abortHidden", name, hidden]); } },
                setAttribute(name, value) { this[name] = value; },
              },
            };

            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }
            function fetch() {
              const text = [
                "event: runtime_event",
                "data: {\"type\":\"assistant.message.completed\",\"status\":\"completed\",\"text\":\"preview\",\"request_id\":\"req-1\",\"session_id\":\"sess-1\"}",
                "",
                "event: runtime_event",
                "data: {\"type\":\"chat.run.completed\",\"status\":\"completed\",\"request_id\":\"req-1\",\"session_id\":\"sess-1\"}",
                "",
                "event: done",
                "data: {}",
                "",
                "",
              ].join("\n");
              const chunks = [encoder.encode(text)];
              return Promise.resolve({
                status: 200,
                ok: true,
                body: {
                  getReader() {
                    return {
                      async read() {
                        if (!chunks.length) return { done: true };
                        return { done: false, value: chunks.shift() };
                      },
                    };
                  },
                },
              });
            }
            function handleAgentEventMessage(raw) { calls.push(["runtime", JSON.parse(raw).type]); }
            function clearWaitingForRuntimeEventsTimer() {}
            function updatePendingAssistantStreamContent() {}
            function queueAssistantTypewriter() {}
            function isChatRunAlreadyActivePayload() { return false; }
            async function handleChatRunAlreadyActive() { throw new Error("should not handle already-active"); }
            function getCompletionState(payload) { return String(payload?.completion_state || payload?.status || "").trim().toLowerCase(); }
            function isCompletedFinalPayload(payload) { return getCompletionState(payload) === "completed"; }
            function isNonSuccessFinalPayload(payload) { return payload?.ok === false; }
            function finalResponseText(payload) { return payload?.response || ""; }
            function normalizeAssistantMessageIds() { return []; }
            function finalizeNonSuccessChatResponse() { throw new Error("should not finalize non-success"); }
            async function handleIncompleteChatStream() { throw new Error("should not mark incomplete"); }
            async function handleAgentChatSuccess() { throw new Error("should not use preview as success"); }
            function currentSessionIdForAgent(agentId) {
              calls.push(["currentSession", agentId]);
              return chatState.sessionId;
            }
            async function agentApiFor(agentId, path) {
              calls.push(["api", agentId, path]);
              assert.equal(path, "/api/sessions/sess-1");
              return {
                canonical_messages: [
                  { role: "user", message_id: "user-1", parts: [{ type: "text", text: "what skills do you" }] },
                  { role: "assistant", message_id: "assistant-1", parts: [{ type: "text", text: "full canonical answer" }] },
                ],
                metadata: { active_run: null },
              };
            }
            async function applySessionProjectionThenClearStaleRun(agentId, ctx, sessionPayload, reason) {
              calls.push(["snapshot", agentId, ctx.clientRequestId, reason, sessionPayload.canonical_messages.length]);
              chatState.activeRequest = null;
              chatState.inflightThinking = null;
              chatState.openCodeProjection.sessionStatus = "idle";
              chatState.openCodeProjection.sessionStatusPayload = {
                ...chatState.openCodeProjection.sessionStatusPayload,
                active: false,
                status_type: "idle",
                active_run: null,
              };
              setChatSubmittingForAgent(agentId, false);
              setChatStatus("Ready");
              syncSelectedAgentChatActionControls();
              return "terminal";
            }
            function setChatSubmittingForAgent(agentId, active) {
              calls.push(["submitting", agentId, active]);
              chatState.isSubmitting = active;
            }
            function setChatStatus(message) { calls.push(["status", message]); }
            function syncSelectedAgentChatActionControls() {
              const busy = Boolean(chatState.activeRequest || chatState.openCodeProjection?.sessionStatusPayload?.active === true);
              dom.sendChatBtn.disabled = busy;
              dom.abortChatRunBtn.disabled = !busy;
              dom.abortChatRunBtn.setAttribute("aria-hidden", busy ? "false" : "true");
              dom.abortChatRunBtn.classList.toggle("hidden", !busy);
              calls.push(["sync", busy]);
            }
            function handleChatStreamMissingFinal() {
              calls.push(["detached"]);
              throw new Error("should fetch session snapshot instead of detaching");
            }
            function appendPortalChatRuntimeEvent() {}
            function startChatRunReconcileLoop() {}

            (async () => {
              const result = await trySubmitChatStreamForSelectedAgent("agent-1", requestCtx, {});
              assert.equal(result, "handled");
              assert.equal(requestCtx.sawRunCompleted, true);
              assert.equal(requestCtx.awaitingAuthoritativeFinal, true);
              assert.ok(calls.some((item) => item[0] === "api" && item[2] === "/api/sessions/sess-1"));
              assert.ok(calls.some((item) => item[0] === "snapshot" && item[3] === "stream_final_missing_after_completed_event"));
              assert.equal(calls.some((item) => item[0] === "detached"), false);
              assert.equal(calls.some((item) => item[0] === "status" && String(item[1]).includes("Still running")), false);
              assert.equal(chatState.activeRequest, null);
              assert.equal(chatState.inflightThinking, null);
              assert.equal(dom.abortChatRunBtn["aria-hidden"], "true");
              assert.equal(dom.sendChatBtn.disabled, false);
            })();
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_stream_final_overrides_truncated_assistant_completed_preview_and_clears_busy_state():
    src = SRC.read_text(encoding="utf-8")
    stream_js = "\n".join(
        [
            _extract_js_function(src, "normalizeChatStreamEventName"),
            _extract_js_function(src, "normalizeChatStreamEventData"),
            _extract_js_function(src, "getChatStreamTextPayload"),
            _extract_js_function(src, "getChatStreamRoleMarker"),
            _extract_js_function(src, "getChatStreamRawType"),
            _extract_js_function(src, "isChatStreamSnapshotPayload"),
            _extract_js_function(src, "isChatStreamWrapperEventName"),
            _extract_js_function(src, "isDirectCompletionEventName"),
            _extract_js_function(src, "isChatStreamDeltaEventName"),
            _extract_js_function(src, "isChatStreamFinalEventName"),
            _extract_js_function(src, "rememberAssociatedRuntimeDeltaEvent"),
            _extract_js_function(src, "getAssociatedRuntimeDeltaEvent"),
            _extract_js_function(src, "isSyntheticFinalDeltaEvent"),
            _extract_js_function(src, "isLikelySyntheticFinalPreviewDelta"),
            _extract_js_function(src, "buildAssistantStreamDeltaGuardSource"),
            _extract_js_function(src, "shouldIgnoreAssistantStreamDelta"),
            _extract_js_function(src, "isAssistantMessageRuntimeEvent"),
            _extract_js_function(src, "handleAssistantMessageRuntimeEvent"),
            _extract_js_function(src, "handleChatStreamEvent"),
            _extract_js_function(src, "handleAgentChatSuccess"),
        ]
    )
    script = (
        stream_js
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const syntheticPreview = "I can load these skills:\n\n- `jira-to-manual-test…";
            const shorterDirectDelta = "I can load these skills:\n\n- `generate-…";
            const fullResponse = [
              "I can load these skills:",
              "",
              "- `jira-to-manual-test-cases`",
              "- `mobilex-test-cases-generator`",
              "- `pr-to-test-points`",
              "- `test-simple-ref`",
            ].join("\n");
            const calls = [];
            const assistantUpdates = [];
            const chatState = {
              activeRequest: null,
              inflightThinking: { events: [], completed: false },
              isSubmitting: true,
              sessionId: "sess-1",
              openCodeProjection: {
                sessionStatus: "busy",
                sessionStatusPayload: {
                  active: true,
                  status_type: "busy",
                  active_run: { request_id: "req-1" },
                },
                needsSnapshot: true,
              },
            };
            const requestCtx = {
              clientRequestId: "req-1",
              requestId: "req-1",
              runtimeRequestId: "req-1",
              sessionIdAtSend: "sess-1",
              streamedText: "",
              usedStream: true,
            };
            chatState.activeRequest = requestCtx;
            const state = {
              selectedAgentId: "agent-1",
              mineAgents: [{ id: "agent-1", name: "Agent One" }],
            };
            const dom = {
              messageList: {},
              sendChatBtn: { disabled: true },
              abortChatRunBtn: {
                disabled: false,
                classList: {
                  toggle(name, hidden) { calls.push(["abortHidden", name, hidden]); },
                },
                setAttribute(name, value) { this[name] = value; },
              },
            };
            const document = { hidden: false };
            let assistantMarkdown = "";

            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }
            function extractAssistantVisibleText(data) { return data.response || data.text || data.summary || ""; }
            function extractAssistantDisplayBlocks(data) { return Array.isArray(data.display_blocks) ? data.display_blocks : []; }
            function normalizeAssistantMessageIds(payload = {}) {
              const ids = Array.isArray(payload.assistant_message_ids) ? [...payload.assistant_message_ids] : [];
              if (payload.assistant_message_id && !ids.includes(payload.assistant_message_id)) ids.push(payload.assistant_message_id);
              return ids;
            }
            function updateOrCreateAssistantRowForRequest(agentId, ctx, payload, options = {}) {
              assistantMarkdown = payload.response || assistantMarkdown;
              assistantUpdates.push({ agentId, requestId: ctx.clientRequestId, response: payload.response || "", completed: options.completed === true, partial: options.partial === true });
              return {};
            }
            function updatePendingAssistantStreamContent(agentId, markdownText, options = {}) {
              assistantMarkdown = markdownText;
              calls.push(["pending", agentId, markdownText, options.streaming === true]);
            }
            function queueAssistantTypewriter() { throw new Error("synthetic final delta should not enqueue ordinary typewriter append"); }
            function clearWaitingForRuntimeEventsTimer() {}
            function handleAgentEventMessage(raw) {
              const payload = JSON.parse(raw);
              calls.push(["runtime", payload.type]);
              handleAssistantMessageRuntimeEvent("agent-1", chatState, {
                type: payload.type,
                event_type: payload.event_type,
                request_id: payload.request_id,
                session_id: payload.session_id,
                data: payload.data || {},
              }, true, false);
            }
            function getCompletionState(payload) { return String(payload?.completion_state || payload?.status || payload?.state || "").trim().toLowerCase(); }
            function isCompletedFinalPayload(payload) { return getCompletionState(payload) === "completed" && typeof payload.response === "string"; }
            function isNonSuccessFinalPayload(payload) { return payload?.ok === false; }
            function finalResponseText(payload) { return payload?.response || ""; }
            function isChatRunAlreadyActivePayload() { return false; }
            async function handleChatRunAlreadyActive() { throw new Error("should not handle active run"); }
            async function handleIncompleteChatStream() { throw new Error("authoritative final should be complete"); }
            function finalizeNonSuccessChatResponse() { throw new Error("should not finalize non-success"); }
            function startChatRunReconcileLoop(agentId, ctx, options) { calls.push(["reconcile", agentId, ctx.clientRequestId, options?.immediate]); }
            function stopChatRunReconcileLoop(ctx) { calls.push(["stopReconcile", ctx.clientRequestId]); }
            function updateAgentSession(agentId, sessionId) { calls.push(["session", agentId, sessionId]); }
            function mergeThinkingEvents(first = [], second = []) { return [...first, ...second]; }
            function isThinkingPanelActiveForAgent() { return false; }
            function setChatSubmittingForAgent(agentId, active) {
              chatState.isSubmitting = active;
              calls.push(["submitting", agentId, active]);
            }
            function syncSelectedAgentChatActionControls() {
              const busy = Boolean(
                chatState.activeRequest
                || chatState.openCodeProjection?.sessionStatusPayload?.active === true
              );
              dom.sendChatBtn.disabled = busy;
              dom.abortChatRunBtn.disabled = !busy;
              dom.abortChatRunBtn.setAttribute("aria-hidden", busy ? "false" : "true");
              dom.abortChatRunBtn.classList.toggle("hidden", !busy);
              calls.push(["sync", busy]);
            }
            function setChatStatus(message) { calls.push(["status", message]); }
            function getLatestOptimisticUserArticle() { return { dataset: { messageId: "user-1" } }; }
            async function flushAssistantTypewriter(agentId, ctx, finalText) {
              calls.push(["flush", agentId, finalText]);
              assistantMarkdown = finalText;
            }
            function finalizePendingAssistantRow(agentId, ctx, payload) {
              assistantMarkdown = payload.response || "";
              calls.push(["finalizeRow", agentId, ctx.clientRequestId, assistantMarkdown]);
              return true;
            }
            function renderMarkdown() {}
            function decorateToolMessages() {}
            function renderIcons() {}
            function addEditButtonsToMessages() {}
            function scrollToBottom() {}
            function removeTemporaryAssistantRows() {}
            function loadSessionForAgent() { throw new Error("should not reload session for renderable final"); }
            function markAgentUnread() {}
            function renderAgentList() {}
            function notifyAgentCompletion() {}
            function getSelectedAssistantDisplayName() { return "Assistant"; }
            function buildAssistantMessageArticle() { throw new Error("pending row should finalize"); }

            const realHandleAgentChatSuccess = handleAgentChatSuccess;
            let successCalls = 0;
            handleAgentChatSuccess = async function(agentId, ctx, payload, options = {}) {
              successCalls += 1;
              calls.push(["success", agentId, ctx.clientRequestId, payload.response, options.source, options.allowFinalWithoutActiveRequest === true]);
              return await realHandleAgentChatSuccess(agentId, ctx, payload, options);
            };

            (async () => {
              const completedResult = await handleChatStreamEvent("agent-1", requestCtx, "runtime_event", {
                type: "assistant.message.completed",
                state: "completed",
                text: syntheticPreview,
                status: "completed",
                request_id: "req-1",
                session_id: "sess-1",
                assistant_message_id: "assistant-preview",
              });
              assert.equal(completedResult, "event");
              assert.equal(successCalls, 0);
              assert.equal(requestCtx.sawAssistantMessageCompleted, true);
              assert.equal(requestCtx.awaitingAuthoritativeFinal, true);
              assert.equal(chatState.activeRequest, requestCtx);
              assert.equal(assistantUpdates.at(-1).completed, false);

              await handleChatStreamEvent("agent-1", requestCtx, "runtime_event", {
                type: "assistant_delta",
                synthetic_final_delta: true,
                delta: syntheticPreview,
                request_id: "req-1",
                session_id: "sess-1",
              });
              const deltaResult = await handleChatStreamEvent("agent-1", requestCtx, "delta", {
                delta: shorterDirectDelta,
                raw_type: "",
                message_role: "",
                part_type: "",
                message_id: "",
                part_id: "",
                request_id: "req-1",
                session_id: "sess-1",
              });
              assert.equal(deltaResult, "event");
              assert.equal(requestCtx.streamedText, syntheticPreview);
              assert.notEqual(requestCtx.streamedText, syntheticPreview + shorterDirectDelta);
              assert.equal(requestCtx.syntheticFinalDeltaPreview.response, syntheticPreview);
              assert.ok(calls.some((item) => item[0] === "pending" && item[2] === syntheticPreview && item[3] === true));
              assert.equal(successCalls, 0);

              await handleChatStreamEvent("agent-1", requestCtx, "runtime_event", { type: "complete", state: "success", request_id: "req-1", session_id: "sess-1" });
              await handleChatStreamEvent("agent-1", requestCtx, "runtime_event", { type: "execution.completed", state: "success", request_id: "req-1", session_id: "sess-1" });
              await handleChatStreamEvent("agent-1", requestCtx, "runtime_event", { type: "chat.run.completed", status: "completed", request_id: "req-1", session_id: "sess-1" });
              assert.equal(requestCtx.sawRunCompleted, true);
              assert.equal(chatState.activeRequest, requestCtx);

              const finalResult = await handleChatStreamEvent("agent-1", requestCtx, "final", {
                ok: true,
                completion_state: "completed",
                response: fullResponse,
                request_id: "req-1",
                session_id: "sess-1",
                user_message_id: "user-1",
                assistant_message_id: "assistant-final",
                events: [],
                runtime_events: [],
              });
              assert.equal(finalResult, "final");

              const doneResult = await handleChatStreamEvent("agent-1", requestCtx, "done", {});
              assert.equal(doneResult, "done");
              assert.equal(successCalls, 1);
              assert.equal(requestCtx.streamCompleted, true);
              assert.equal(requestCtx.streamSawFinal, true);
              assert.equal(requestCtx.authoritativeFinalReceived, true);
              assert.equal(assistantMarkdown, fullResponse);
              assert.match(assistantMarkdown, /jira-to-manual-test-cases/);
              assert.match(assistantMarkdown, /test-simple-ref/);
              assert.equal(assistantMarkdown.includes("jira-to-manual-test…"), false);
              assert.equal(assistantMarkdown.includes(syntheticPreview + shorterDirectDelta), false);
              assert.equal(chatState.activeRequest, null);
              assert.equal(chatState.inflightThinking, null);
              assert.equal(chatState.openCodeProjection.sessionStatus, "idle");
              assert.equal(chatState.openCodeProjection.sessionStatusPayload.active, false);
              assert.equal(chatState.openCodeProjection.sessionStatusPayload.active_run, null);
              assert.equal(chatState.openCodeProjection.needsSnapshot, false);
              assert.equal(dom.abortChatRunBtn["aria-hidden"], "true");
              assert.equal(dom.abortChatRunBtn.disabled, true);
              assert.equal(dom.sendChatBtn.disabled, false);
              assert.ok(calls.some((item) => item[0] === "status" && item[1] === "Ready"));
              assert.ok(calls.some((item) => item[0] === "success" && item[4] === "stream_final" && item[5] === true));
            })();
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_opencode_message_part_updated_upserts_by_part_id_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        _extract_js_function(src, "applyOpenCodeCanonicalEventToChatState")
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const chatState = {};
            applyOpenCodeCanonicalEventToChatState(chatState, {
              data: {
                raw_type: "message.part.updated",
                message_id: "message-1",
                part_id: "part-1",
                part_type: "tool",
                part: { id: "part-1", type: "tool", state: { status: "running" } },
              },
            });

            assert.equal(chatState.openCodeProjection.partsById["part-1"].id, "part-1");
            assert.equal(chatState.openCodeProjection.partsById["part-1"].messageID, "message-1");
            assert.equal(chatState.openCodeProjection.partsById["part-1"].type, "tool");
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_opencode_message_part_delta_appends_by_part_id_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        _extract_js_function(src, "applyOpenCodeCanonicalEventToChatState")
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const chatState = {};
            applyOpenCodeCanonicalEventToChatState(chatState, {
              type: "message.part.delta",
              data: { part_id: "part-text", part_type: "text", delta: "hello " },
            });
            applyOpenCodeCanonicalEventToChatState(chatState, {
              data: { raw_type: "message.part.delta", part_id: "part-text", part_type: "text", delta: "world" },
            });

            assert.equal(chatState.openCodeProjection.partsById["part-text"].text, "hello world");
            assert.equal(chatState.openCodeProjection.partsById["part-text"].type, "text");
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_assistant_update_does_not_write_into_user_article_with_same_client_request_id():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(
            [
                _extract_js_function(src, "getChatStreamRoleMarker"),
                _extract_js_function(src, "extractAssistantVisibleText"),
                _extract_js_function(src, "extractAssistantDisplayBlocks"),
                _extract_js_function(src, "normalizeAssistantMessageIds"),
                _extract_js_function(src, "getPrimaryAssistantMessageId"),
                _extract_js_function(src, "getRequestIdCandidatesForAssistantRow"),
                _extract_js_function(src, "getAssistantMessageIdCandidates"),
                _extract_js_function(src, "articleContainsAssistantMessageId"),
                _extract_js_function(src, "isAssistantArticle"),
                _extract_js_function(src, "findAssistantArticleForRequest"),
                _extract_js_function(src, "updateOrCreateAssistantRowForRequest"),
            ]
        )
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");

            class ClassList {
              constructor(names = []) { this.names = new Set(names); }
              contains(name) { return this.names.has(name); }
              add(...names) { names.forEach((name) => this.names.add(name)); }
              remove(...names) { names.forEach((name) => this.names.delete(name)); }
            }

            function datasetKey(attr) {
              return String(attr || "").replace(/^data-/, "").replace(/-([a-z])/g, (_, ch) => ch.toUpperCase());
            }

            function matchesSelector(article, selector) {
              const trimmed = selector.trim();
              if (!trimmed.startsWith("article")) return false;
              if (trimmed.includes(".assistant-message") && !article.classList.contains("assistant-message")) return false;
              const attrPattern = /\[([^=\]]+)(?:=(["'])(.*?)\2)?\]/g;
              let match = null;
              while ((match = attrPattern.exec(trimmed))) {
                const key = datasetKey(match[1]);
                if (match[3] == null) {
                  if (!(key in article.dataset)) return false;
                } else if (String(article.dataset[key] || "") !== match[3]) {
                  return false;
                }
              }
              return true;
            }

            function createRow(article) {
              return {
                dataset: {},
                classList: new ClassList(["message-row"]),
                removeAttribute(name) { delete this.dataset[datasetKey(name)]; },
                querySelectorAll(selector) { return matchesSelector(article, selector) ? [article] : []; },
              };
            }

            function createMarkdown(text = "") {
              return {
                dataset: { md: text, displayBlocks: "[]" },
                className: "message-markdown md-render max-w-none text-sm",
                textContent: text,
                insertAdjacentElement() {},
              };
            }

            function createArticle({ classes, dataset, bodyText = "", markdown = null }) {
              const article = {
                dataset: { ...dataset },
                classList: new ClassList(classes),
                body: { textContent: bodyText },
                markdownEl: markdown,
                children: [],
                closest(selector) { return selector === ".message-row" ? this.parentRow : null; },
                querySelector(selector) {
                  if (selector === ".message-body") return this.body;
                  if (selector === ".message-markdown") return this.markdownEl;
                  if (selector === ".assistant-waiting-indicator") return this.waitingEl || null;
                  return null;
                },
                appendChild(child) {
                  this.children.push(child);
                  if (String(child.className || "").includes("message-markdown")) this.markdownEl = child;
                  return child;
                },
                removeAttribute(name) { delete this.dataset[datasetKey(name)]; },
              };
              article.parentRow = createRow(article);
              return article;
            }

            const userArticle = createArticle({
              classes: ["message-surface", "user-message"],
              dataset: { localUser: "1", clientRequestId: "req-1" },
              bodyText: "hi",
            });
            const assistantArticle = createArticle({
              classes: ["message-surface", "assistant-message", "pending-assistant"],
              dataset: { pendingAssistant: "1", clientRequestId: "req-1" },
              markdown: createMarkdown(""),
            });

            const articles = [userArticle, assistantArticle];
            const dom = {
              messageList: {
                querySelector(selector) {
                  const selectors = selector.split(",");
                  return articles.find((article) => selectors.some((item) => matchesSelector(article, item))) || null;
                },
                querySelectorAll(selector) {
                  const selectors = selector.split(",");
                  return articles.filter((article) => selectors.some((item) => matchesSelector(article, item)));
                },
                insertAdjacentHTML() { throw new Error("assistant row already exists; should not insert"); },
              },
            };
            const state = { selectedAgentId: "agent-1" };
            const chatState = { activeRequest: { clientRequestId: "req-1" } };
            const CSS = { escape(value) { return String(value); } };
            const document = {
              createElement(tag) {
                return {
                  tagName: tag.toUpperCase(),
                  dataset: {},
                  className: "",
                  textContent: "",
                  setAttribute() {},
                  insertAdjacentElement() {},
                };
              },
            };

            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }
            function hasRenderableDisplayBlock() { return false; }
            function renderMarkdown(article) {
              const md = article.querySelector(".message-markdown");
              if (md) md.textContent = md.dataset.md || "";
            }
            function decorateToolMessages() {}
            function renderIcons() {}
            function addEditButtonsToMessages() {}
            function scrollToBottom() {}

            const updated = updateOrCreateAssistantRowForRequest(
              "agent-1",
              { clientRequestId: "req-1" },
              { response: "hello", client_request_id: "req-1", assistant_message_id: "assistant-1" },
              { partial: true }
            );

            assert.equal(updated, assistantArticle);
            assert.equal(userArticle.body.textContent, "hi");
            assert.equal(userArticle.markdownEl, null);
            assert.equal(userArticle.dataset.copyText, undefined);
            assert.equal(assistantArticle.markdownEl.dataset.md, "hello");
            assert.equal(assistantArticle.markdownEl.textContent, "hello");
            assert.equal(assistantArticle.dataset.messageId, "assistant-1");
            assert.equal(assistantArticle.dataset.clientRequestId, "req-1");
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_opencode_session_status_and_idle_projection_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(
            [
                _opencode_projection_state_js(src),
                _extract_js_function(src, "applyOpenCodeCanonicalEventToChatState"),
            ]
        )
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const chatState = {};
            applyOpenCodeCanonicalEventToChatState(chatState, {
              data: { raw_type: "session.status", status_type: "busy" },
            });
            assert.equal(chatState.openCodeProjection.sessionStatus, "busy");
            assert.equal(chatState.openCodeProjection.needsSnapshot, true);

            applyOpenCodeCanonicalEventToChatState(chatState, {
              type: "session.idle",
              data: {},
            });
            assert.equal(chatState.openCodeProjection.sessionStatus, "idle");
            assert.equal(chatState.openCodeProjection.needsSnapshot, true);
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_opencode_runtime_ui_state_three_way_model_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(
            [
                _extract_js_function(src, "isActiveRequestBlocking"),
                _opencode_projection_state_js(src),
                _extract_js_function(src, "normalizeRuntimeHealthStatus"),
                _extract_js_function(src, "computeOpenCodeRuntimeUiState"),
                _extract_js_function(src, "openCodeRuntimeUiStatusText"),
            ]
        )
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");

            const ready = computeOpenCodeRuntimeUiState(
              { runtime_status: "running" },
              { openCodeProjection: { sessionStatus: "idle" }, activeRequest: { clientRequestId: "stale-local" }, inflightThinking: { completed: false } }
            );
            assert.equal(ready.normalizedRuntimeHealth, "online");
            assert.equal(ready.sessionStatus, "idle");
            assert.equal(ready.messageProgress, "idle");
            assert.match(openCodeRuntimeUiStatusText(ready), /Ready/);

            const busy = computeOpenCodeRuntimeUiState(
              { runtime_status: "running" },
              {
                openCodeProjection: {
                  sessionStatus: "busy",
                  sessionStatusPayload: { active: true, status_type: "busy", action_hint: "wait_reconnect_or_stop" },
                },
                activeRequest: null,
                inflightThinking: null,
              }
            );
            assert.equal(busy.sessionStatus, "busy");
            assert.equal(busy.messageProgress, "reconnecting");
            assert.match(openCodeRuntimeUiStatusText(busy), /Previous message still running/);

            const offline = computeOpenCodeRuntimeUiState(
              { status: "stopped" },
              { openCodeProjection: { sessionStatus: "idle" }, activeRequest: null, inflightThinking: null }
            );
            assert.equal(offline.normalizedRuntimeHealth, "offline");
            assert.equal(offline.sessionStatus, "unknown");
            assert.equal(offline.messageProgress, "unknown");
            assert.match(openCodeRuntimeUiStatusText(offline), /Runtime offline/);
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_assistant_completed_event_stays_preview_and_terminal_marker_is_null_safe():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(
            [
                _extract_js_function(src, "normalizeRuntimeEventTypeAlias"),
                _extract_js_function(src, "isCompletionRuntimeState"),
                _extract_js_function(src, "normalizeRuntimeEvent"),
                _extract_js_function(src, "isAssistantMessageRuntimeEvent"),
                _extract_js_function(src, "handleAssistantMessageRuntimeEvent"),
                _extract_js_function(src, "markThinkingTerminalFromEvent"),
                _extract_js_function(src, "handleAgentEventMessage"),
            ]
        )
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const COMPLETION_RUNTIME_STATES = new Set(["complete", "completed", "done", "finished"]);
            const calls = [];
            const state = { selectedAgentId: "agent-1" };
            const chatState = {
              sessionId: "sess-1",
              activeRequest: {
                clientRequestId: "req-1",
                requestId: "req-1",
                runtimeRequestId: "req-1",
                sessionIdAtSend: "sess-1",
              },
              inflightThinking: {
                id: "req-1",
                requestId: "req-1",
                sessionId: "sess-1",
                events: [],
                completed: false,
              },
              lastThinkingSnapshot: null,
            };

            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }
            function isTrackableThinkingEvent() { return true; }
            function updateOrCreateAssistantRowForRequest(agentId, requestCtx, payload, options) {
              calls.push(["assistantRow", agentId, requestCtx.clientRequestId, payload.response, options?.completed === true]);
              return {};
            }
            function extractAssistantVisibleText(data) { return data.response || data.text || ""; }
            function extractAssistantDisplayBlocks() { return []; }
            function getChatStreamTextPayload(data) { return data.delta || ""; }
            function normalizeAssistantMessageIds() { return []; }
            function getCompletionState(payload) { return String(payload?.completion_state || payload?.status || "").trim().toLowerCase(); }
            function normalizeChatRunStatus(status) { return String(status || "").trim().toLowerCase(); }
            function isChatRunCompletedStatus(status) { return status === "completed"; }
            function shouldIgnoreAssistantStreamDelta() { return false; }
            function handleAgentChatSuccess(agentId, requestCtx, payload) {
              calls.push(["success", agentId, requestCtx.clientRequestId, payload.completion_state]);
              chatState.activeRequest = null;
              chatState.inflightThinking = null;
              chatState.lastThinkingSnapshot = { completed: true, events: [] };
            }
            function startChatRunReconcileLoop() { calls.push(["reconcile"]); }
            function maybeStartStalledAssistantReconcile() { calls.push(["stalled"]); }
            function isThinkingPanelActiveForAgent() { return false; }
            function scheduleThinkingPanelRefresh() { calls.push(["panel"]); }
            function syncSelectedAgentChatActionControls() { calls.push(["sync"]); }
            function applyOpenCodeCanonicalEventToChatState() { return false; }
            function maybeRefreshSessionSnapshotForOpenCodeEvent() { calls.push(["snapshot"]); }
            function isOpenCodeCanonicalSnapshotEvent() { return false; }
            function isOpenCodeSessionStateOnlyEvent() { return false; }
            function updateThinkingContextFromEvent() {}
            function mergeThinkingEvents(first = [], second = []) { return [...first, ...second]; }
            function fallbackRequestContextForAgent() { return {}; }
            function appendPortalChatRuntimeEvent(_agentId, _ctx, type) { calls.push(["portal", type]); }
            function hasActiveChatRequestForAgent() {
              return Boolean(chatState.activeRequest || (chatState.inflightThinking && chatState.inflightThinking.completed === false));
            }

            let threw = false;
            try {
              handleAgentEventMessage(JSON.stringify({
                type: "assistant.message.completed",
                event_type: "assistant.message.completed",
                state: "completed",
                session_id: "sess-1",
                request_id: "req-1",
                data: {
                  status: "completed",
                  completion_state: "completed",
                  response: "hello",
                  request_id: "req-1",
                  session_id: "sess-1"
                }
              }), { agentId: "agent-1", sessionId: "sess-1", requestId: "req-1" });
            } catch (error) {
              threw = true;
            }

            assert.equal(threw, false);
            assert.equal(chatState.activeRequest.clientRequestId, "req-1");
            assert.equal(chatState.inflightThinking.completed, true);
            assert.equal(chatState.lastThinkingSnapshot.completed, true);
            assert.equal(hasActiveChatRequestForAgent(), true);
            assert.equal(chatState.activeRequest.awaitingAuthoritativeFinal, true);
            assert.equal(chatState.activeRequest.sawAssistantMessageCompleted, true);
            assert.equal(calls.some((item) => item[0] === "success"), false);
            assert.equal(calls.some((item) => item[0] === "reconcile"), true);
            assert.equal(calls.some((item) => item[0] === "sync"), true);
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_opencode_session_status_blocks_send_and_shows_stop_run_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(
            [
                _extract_js_function(src, "isActiveRequestBlocking"),
                _extract_js_function(src, "hasIncompleteInflightThinking"),
                _opencode_projection_state_js(src),
                _extract_js_function(src, "activeChatRequestMessage"),
                _extract_js_function(src, "syncSelectedAgentChatActionControls"),
                _extract_js_function(src, "normalizeRuntimeHealthStatus"),
                _extract_js_function(src, "computeOpenCodeRuntimeUiState"),
                _extract_js_function(src, "openCodeRuntimeUiStatusText"),
            ]
        )
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const toggles = [];
            const chatState = {
              isSubmitting: false,
              activeRequest: null,
              inflightThinking: null,
              openCodeProjection: {
                sessionStatus: "busy",
                sessionStatusPayload: {
                  active: true,
                  status_type: "busy",
                  action_hint: "wait_reconnect_or_stop",
                },
              },
            };
            const state = { selectedAgentId: "agent-1" };
            const sessionsBtn = {};
            const dom = {
              headerNewChatBtn: {},
              homeStartChatBtn: {},
              sendChatBtn: { disabled: false },
              abortChatRunBtn: {
                disabled: true,
                classList: {
                  toggle(name, hidden) { toggles.push([name, hidden]); },
                  add() {},
                  remove() {},
                },
                setAttribute(name, value) { this[name] = value; },
              },
            };
            const document = { getElementById(id) { return id === "btn-sessions" ? sessionsBtn : null; } };

            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }
            function getSelectedAgentStatus() { return "running"; }
            function setButtonDisabled(button, disabled, title = "") {
              if (!button) return;
              button.disabled = !!disabled;
              button.title = title;
            }

            assert.equal(normalizeOpenCodeSessionStatusType(chatState.openCodeProjection.sessionStatusPayload), "busy");
            assert.equal(isOpenCodeSessionStatusBlockingPayload(chatState.openCodeProjection.sessionStatusPayload), true);
            assert.equal(isOpenCodeSessionBlocking(chatState), true);
            assert.equal(hasActiveChatRequestForAgent("agent-1"), true);
            assert.equal(shouldShowAbortChatRunButton("agent-1"), true);
            assert.match(activeChatRequestMessage("agent-1", "send"), /Stop run/);

            syncSelectedAgentChatActionControls();
            assert.equal(dom.sendChatBtn.disabled, true);
            assert.equal(dom.abortChatRunBtn.disabled, false);
            assert.equal(dom.abortChatRunBtn["aria-hidden"], "false");
            assert.deepEqual(toggles[0], ["hidden", false]);
            assert.equal(sessionsBtn.disabled, true);

            const uiState = computeOpenCodeRuntimeUiState({ runtime_status: "running" }, chatState);
            assert.equal(uiState.normalizedRuntimeHealth, "online");
            assert.equal(uiState.sessionStatus, "busy");
            assert.equal(uiState.messageProgress, "reconnecting");
            assert.match(openCodeRuntimeUiStatusText(uiState), /Session busy/);
            assert.match(openCodeRuntimeUiStatusText(uiState), /Previous message still running/);
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def _opencode_projection_state_js(src: str) -> str:
    return "\n".join(
        [
            _extract_js_function(src, "normalizeChatRunStatus"),
            _extract_js_function(src, "normalizeOpenCodeSessionStatusType"),
            _extract_js_function(src, "isOpenCodeSessionInactivePayload"),
            _extract_js_function(src, "buildOpenCodeInactiveSessionStatusPayload"),
            _extract_js_function(src, "isOpenCodeSessionStatusBlockingPayload"),
            _extract_js_function(src, "isOpenCodeSessionBlocking"),
            _extract_js_function(src, "requestContextIdCandidates"),
            _extract_js_function(src, "activeRequestMatchesRequestContext"),
            _extract_js_function(src, "fallbackRequestContextForAgent"),
            _extract_js_function(src, "clearStaleActiveRequest"),
            _extract_js_function(src, "markOpenCodeProjectionInactive"),
            _extract_js_function(src, "hasActiveChatRequestForAgent"),
            _extract_js_function(src, "shouldShowAbortChatRunButton"),
        ]
    )


def test_session_idle_event_clears_stale_active_payload_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(
            [
                _extract_js_function(src, "normalizeRuntimeEventTypeAlias"),
                _extract_js_function(src, "normalizeRuntimeEvent"),
                _extract_js_function(src, "isCompletionRuntimeState"),
                _extract_js_function(src, "isOpenCodeSessionStateOnlyEvent"),
                _extract_js_function(src, "isOpenCodeCanonicalSnapshotEvent"),
                _extract_js_function(src, "applyOpenCodeCanonicalEventToChatState"),
                _extract_js_function(src, "handleAgentEventMessage"),
                _opencode_projection_state_js(src),
            ]
        )
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const COMPLETION_RUNTIME_STATES = new Set(["complete", "completed", "done", "finished"]);
            const requestCtx = {
              clientRequestId: "client-1",
              requestId: "runtime-1",
              runtimeRequestId: "runtime-1",
              sessionIdAtSend: "sess-1",
            };
            const chatState = {
              sessionId: "sess-1",
              isSubmitting: false,
              activeRequest: requestCtx,
              inflightThinking: { id: "runtime-1", requestId: "runtime-1", sessionId: "sess-1", completed: false },
              openCodeProjection: {
                messagesById: {},
                partsById: {},
                sessionStatus: "busy",
                sessionStatusPayload: {
                  source_of_truth: "opencode",
                  active: true,
                  status: { type: "busy" },
                  status_type: "busy",
                  action_hint: "wait_reconnect_or_stop",
                  active_run: { status: "busy" },
                },
              },
            };
            const state = { selectedAgentId: "agent-1" };
            const calls = [];

            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }
            function clearWaitingForRuntimeEventsTimer() {}
            function cancelAssistantTypewriter() {}
            function stopChatRunReconcileLoop(ctx) { ctx.reconcileStopped = true; }
            function setChatSubmittingForAgent(_agentId, active) { chatState.isSubmitting = active; }
            function appendPortalChatRuntimeEvent(agentId, ctx, type, data) { calls.push(["event", type, data?.reason || ""]); }
            function setChatStatus(message) { calls.push(["status", message]); }
            function syncSelectedAgentChatActionControls() { calls.push(["sync"]); }
            function maybeRefreshSessionSnapshotForOpenCodeEvent() { calls.push(["snapshot"]); }
            function isThinkingPanelActiveForAgent() { return false; }
            function scheduleThinkingPanelRefresh() {}
            function isTrackableThinkingEvent() { return false; }
            function updateThinkingContextFromEvent() {}
            function mergeThinkingEvents(first = [], second = []) { return [...first, ...second]; }

            handleAgentEventMessage(JSON.stringify({
              type: "session.idle",
              event_type: "session.idle",
              session_id: "sess-1",
              data: { raw_type: "session.idle", session_id: "sess-1" },
            }), { agentId: "agent-1", sessionId: "sess-1" });

            assert.equal(isOpenCodeSessionBlocking(chatState), false);
            assert.equal(hasActiveChatRequestForAgent("agent-1"), false);
            assert.equal(shouldShowAbortChatRunButton("agent-1"), false);
            assert.equal(chatState.openCodeProjection.sessionStatus, "idle");
            assert.equal(chatState.openCodeProjection.sessionStatusPayload.active, false);
            assert.equal(chatState.openCodeProjection.sessionStatusPayload.action_hint, "safe_to_send");
            assert.equal(chatState.openCodeProjection.sessionStatusPayload.active_run, null);
            assert.equal(chatState.activeRequest, null);
            assert.equal(chatState.inflightThinking.completed, true);
            assert.equal(chatState.inflightThinking.stale, true);
            assert.ok(calls.some((item) => item[0] === "event" && item[1] === "portal.active_request.cleared"));
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_session_updated_idle_event_clears_stale_active_payload_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(
            [
                _extract_js_function(src, "normalizeRuntimeEventTypeAlias"),
                _extract_js_function(src, "normalizeRuntimeEvent"),
                _extract_js_function(src, "isCompletionRuntimeState"),
                _extract_js_function(src, "isOpenCodeSessionStateOnlyEvent"),
                _extract_js_function(src, "isOpenCodeCanonicalSnapshotEvent"),
                _extract_js_function(src, "applyOpenCodeCanonicalEventToChatState"),
                _extract_js_function(src, "handleAgentEventMessage"),
                _opencode_projection_state_js(src),
            ]
        )
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const COMPLETION_RUNTIME_STATES = new Set(["complete", "completed", "done", "finished"]);
            const requestCtx = {
              clientRequestId: "client-1",
              requestId: "runtime-1",
              runtimeRequestId: "runtime-1",
              sessionIdAtSend: "sess-1",
            };
            const chatState = {
              sessionId: "sess-1",
              isSubmitting: false,
              activeRequest: requestCtx,
              inflightThinking: { id: "runtime-1", requestId: "runtime-1", sessionId: "sess-1", completed: false },
              openCodeProjection: {
                messagesById: {},
                partsById: {},
                sessionStatus: "busy",
                sessionStatusPayload: {
                  source_of_truth: "opencode",
                  active: true,
                  status: { type: "busy" },
                  status_type: "busy",
                  action_hint: "wait_reconnect_or_stop",
                  active_run: { status: "busy" },
                },
              },
            };
            const state = { selectedAgentId: "agent-1" };
            const calls = [];

            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }
            function clearWaitingForRuntimeEventsTimer() {}
            function cancelAssistantTypewriter() {}
            function stopChatRunReconcileLoop(ctx) { ctx.reconcileStopped = true; }
            function setChatSubmittingForAgent(_agentId, active) { chatState.isSubmitting = active; }
            function appendPortalChatRuntimeEvent(agentId, ctx, type, data) { calls.push(["event", type, data?.reason || ""]); }
            function setChatStatus(message) { calls.push(["status", message]); }
            function syncSelectedAgentChatActionControls() { calls.push(["sync"]); }
            function maybeRefreshSessionSnapshotForOpenCodeEvent() { calls.push(["snapshot"]); }
            function isThinkingPanelActiveForAgent() { return false; }
            function scheduleThinkingPanelRefresh() {}
            function isTrackableThinkingEvent() { return false; }
            function updateThinkingContextFromEvent() {}
            function mergeThinkingEvents(first = [], second = []) { return [...first, ...second]; }

            handleAgentEventMessage(JSON.stringify({
              type: "session.updated",
              event_type: "session.updated",
              session_id: "sess-1",
              data: {
                session_id: "sess-1",
                status: { type: "idle" },
              },
            }), { agentId: "agent-1", sessionId: "sess-1" });

            assert.equal(chatState.openCodeProjection.sessionStatus, "idle");
            assert.equal(chatState.openCodeProjection.sessionStatusPayload.active, false);
            assert.equal(chatState.openCodeProjection.sessionStatusPayload.action_hint, "safe_to_send");
            assert.equal(chatState.openCodeProjection.sessionStatusPayload.active_run, null);
            assert.equal(isOpenCodeSessionBlocking(chatState), false);
            assert.equal(hasActiveChatRequestForAgent("agent-1"), false);
            assert.equal(shouldShowAbortChatRunButton("agent-1"), false);
            assert.equal(chatState.activeRequest, null);
            assert.equal(requestCtx.stale, true);
            assert.equal(chatState.inflightThinking.completed, true);
            assert.equal(chatState.inflightThinking.stale, true);
            assert.ok(calls.some((item) => item[0] === "event" && item[1] === "portal.active_request.cleared"));
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_session_updated_busy_event_sets_blocking_payload_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(
            [
                _extract_js_function(src, "normalizeRuntimeEventTypeAlias"),
                _extract_js_function(src, "normalizeRuntimeEvent"),
                _extract_js_function(src, "isCompletionRuntimeState"),
                _extract_js_function(src, "isOpenCodeSessionStateOnlyEvent"),
                _extract_js_function(src, "isOpenCodeCanonicalSnapshotEvent"),
                _extract_js_function(src, "applyOpenCodeCanonicalEventToChatState"),
                _extract_js_function(src, "handleAgentEventMessage"),
                _opencode_projection_state_js(src),
            ]
        )
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const COMPLETION_RUNTIME_STATES = new Set(["complete", "completed", "done", "finished"]);
            const chatState = {
              sessionId: "sess-1",
              isSubmitting: false,
              activeRequest: null,
              inflightThinking: null,
              openCodeProjection: {
                messagesById: {},
                partsById: {},
                sessionStatus: "idle",
                sessionStatusPayload: {
                  source_of_truth: "opencode",
                  active: false,
                  status: { type: "idle" },
                  status_type: "idle",
                  action_hint: "safe_to_send",
                  active_run: null,
                },
              },
            };
            const state = { selectedAgentId: "agent-1" };
            const calls = [];

            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }
            function clearWaitingForRuntimeEventsTimer() {}
            function cancelAssistantTypewriter() {}
            function stopChatRunReconcileLoop() {}
            function setChatSubmittingForAgent(_agentId, active) { chatState.isSubmitting = active; }
            function appendPortalChatRuntimeEvent(agentId, ctx, type) { calls.push(["event", type]); }
            function setChatStatus(message) { calls.push(["status", message]); }
            function syncSelectedAgentChatActionControls() { calls.push(["sync"]); }
            function maybeRefreshSessionSnapshotForOpenCodeEvent() { calls.push(["snapshot"]); }
            function isThinkingPanelActiveForAgent() { return false; }
            function scheduleThinkingPanelRefresh() {}
            function isTrackableThinkingEvent() { return false; }
            function updateThinkingContextFromEvent() {}
            function mergeThinkingEvents(first = [], second = []) { return [...first, ...second]; }

            handleAgentEventMessage(JSON.stringify({
              type: "session.updated",
              event_type: "session.updated",
              session_id: "sess-1",
              data: {
                session_id: "sess-1",
                status: { type: "busy" },
                active: true,
              },
            }), { agentId: "agent-1", sessionId: "sess-1" });

            assert.equal(chatState.openCodeProjection.sessionStatus, "busy");
            assert.equal(chatState.openCodeProjection.sessionStatusPayload.active, true);
            assert.equal(chatState.openCodeProjection.sessionStatusPayload.status_type, "busy");
            assert.equal(chatState.openCodeProjection.sessionStatusPayload.action_hint, "wait_reconnect_or_stop");
            assert.equal(isOpenCodeSessionBlocking(chatState), true);
            assert.equal(shouldShowAbortChatRunButton("agent-1"), true);
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_local_active_request_with_opencode_idle_does_not_show_stop_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        _opencode_projection_state_js(src)
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const requestCtx = { clientRequestId: "client-1", requestId: "runtime-1", runtimeRequestId: "runtime-1", sessionIdAtSend: "sess-1" };
            const chatState = {
              activeRequest: requestCtx,
              isSubmitting: false,
              sessionId: "sess-1",
              inflightThinking: { id: "runtime-1", requestId: "runtime-1", sessionId: "sess-1", completed: false },
              openCodeProjection: {
                sessionStatus: "idle",
                sessionStatusPayload: {
                  source_of_truth: "opencode",
                  active: false,
                  status: { type: "idle" },
                  status_type: "idle",
                  action_hint: "safe_to_send",
                },
              },
            };
            const state = { selectedAgentId: "agent-1" };
            const dom = { sendChatBtn: { disabled: true }, abortChatRunBtn: { classList: { toggle() {} }, setAttribute() {}, disabled: false } };
            const calls = [];

            function ensureChatState(agentId) { assert.equal(agentId, "agent-1"); return chatState; }
            function clearWaitingForRuntimeEventsTimer() {}
            function cancelAssistantTypewriter() {}
            function stopChatRunReconcileLoop(ctx) { ctx.reconcileStopped = true; }
            function setChatSubmittingForAgent(_agentId, active) { chatState.isSubmitting = active; }
            function appendPortalChatRuntimeEvent(agentId, ctx, type) { calls.push(["event", type]); }
            function setChatStatus(message) { calls.push(["status", message]); }
            function syncSelectedAgentChatActionControls() { dom.sendChatBtn.disabled = hasActiveChatRequestForAgent("agent-1"); }

            assert.equal(hasActiveChatRequestForAgent("agent-1"), false);
            assert.equal(shouldShowAbortChatRunButton("agent-1"), false);
            syncSelectedAgentChatActionControls();
            assert.equal(dom.sendChatBtn.disabled, false);
            assert.equal(chatState.activeRequest, null);
            assert.equal(chatState.inflightThinking.completed, true);
            assert.equal(chatState.inflightThinking.stale, true);
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_stop_click_on_stale_local_state_does_not_abort_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(
            [
                _opencode_projection_state_js(src),
                _extract_js_function(src, "refreshOpenCodeSessionStatusForAgent"),
                _extract_js_function(src, "abortActiveChatRequestForSelectedAgent"),
            ]
        )
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const requestCtx = { clientRequestId: "client-1", requestId: "runtime-1", runtimeRequestId: "runtime-1", sessionIdAtSend: "sess-1" };
            const chatState = {
              activeRequest: requestCtx,
              isSubmitting: false,
              sessionId: "sess-1",
              inflightThinking: { id: "runtime-1", requestId: "runtime-1", sessionId: "sess-1", completed: false },
              openCodeProjection: {
                sessionStatus: "idle",
                sessionStatusPayload: { source_of_truth: "opencode", active: false, status_type: "idle", action_hint: "safe_to_send" },
              },
            };
            const state = { selectedAgentId: "agent-1" };
            const calls = [];

            function ensureChatState(agentId) { assert.equal(agentId, "agent-1"); return chatState; }
            function currentSessionIdForAgent() { return "sess-1"; }
            function clearWaitingForRuntimeEventsTimer() {}
            function cancelAssistantTypewriter() {}
            function stopChatRunReconcileLoop(ctx) { ctx.reconcileStopped = true; }
            function setChatSubmittingForAgent(_agentId, active) { chatState.isSubmitting = active; }
            function appendPortalChatRuntimeEvent(agentId, ctx, type) { calls.push(["event", type]); }
            function setChatStatus(message) { calls.push(["status", message]); }
            function showToast(message) { calls.push(["toast", message]); }
            function syncSelectedAgentChatActionControls() { calls.push(["sync"]); }
            async function agentApiFor(agentId, path) {
              calls.push(["api", path]);
              assert.equal(path, "/api/sessions/sess-1/status");
              return { source_of_truth: "opencode", active: false, status: { type: "idle" }, status_type: "idle", action_hint: "safe_to_send" };
            }
            async function fetch(url) {
              calls.push(["fetch", url]);
              throw new Error("stale stop must not abort");
            }

            (async () => {
              await abortActiveChatRequestForSelectedAgent();
              assert.equal(calls.some((item) => item[0] === "fetch"), false);
              assert.ok(calls.some((item) => item[0] === "api" && item[1] === "/api/sessions/sess-1/status"));
              assert.ok(calls.some((item) => item[0] === "status" && item[1] === "No running OpenCode session to stop."));
              assert.ok(calls.some((item) => item[0] === "toast" && item[1] === "No running OpenCode session to stop."));
              assert.equal(chatState.activeRequest, null);
              assert.equal(chatState.inflightThinking.completed, true);
              assert.equal(chatState.openCodeProjection.sessionStatusPayload.active, false);
            })();
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_opencode_session_status_does_not_create_inflight_thinking_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(
            [
                _extract_js_function(src, "normalizeRuntimeEventTypeAlias"),
                _extract_js_function(src, "isCompletionRuntimeState"),
                _extract_js_function(src, "normalizeRuntimeEvent"),
                _extract_js_function(src, "normalizeChatRunStatus"),
                _extract_js_function(src, "normalizeOpenCodeSessionStatusType"),
                _extract_js_function(src, "isOpenCodeSessionInactivePayload"),
                _extract_js_function(src, "buildOpenCodeInactiveSessionStatusPayload"),
                _extract_js_function(src, "markOpenCodeProjectionInactive"),
                _extract_js_function(src, "isOpenCodeSessionStatusBlockingPayload"),
                _extract_js_function(src, "applyOpenCodeCanonicalEventToChatState"),
                _extract_js_function(src, "isOpenCodeSessionStateOnlyEvent"),
                _extract_js_function(src, "isOpenCodeCanonicalSnapshotEvent"),
                _extract_js_function(src, "maybeRefreshSessionSnapshotForOpenCodeEvent"),
                _extract_js_function(src, "handleAgentEventMessage"),
                _extract_js_function(src, "isActiveRequestBlocking"),
                _extract_js_function(src, "isOpenCodeSessionBlocking"),
                _extract_js_function(src, "normalizeRuntimeHealthStatus"),
                _extract_js_function(src, "computeOpenCodeRuntimeUiState"),
            ]
        )
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const COMPLETION_RUNTIME_STATES = new Set(["complete", "completed", "done", "finished"]);
            const calls = [];
            const state = { selectedAgentId: "agent-1" };
            const chatState = {
              sessionId: "sess-1",
              openCodeProjection: null,
              inflightThinking: null,
              activeRequest: null,
            };

            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }
            function loadSessionForAgent() { calls.push(["load"]); return Promise.resolve(); }
            function isThinkingPanelActiveForAgent() { return false; }
            function scheduleThinkingPanelRefresh() { calls.push(["panel"]); }
            function syncSelectedAgentChatActionControls() { calls.push(["sync"]); }
            function fallbackRequestContextForAgent() { return {}; }
            function appendPortalChatRuntimeEvent(_agentId, _ctx, type) { calls.push(["portal", type]); }

            handleAgentEventMessage(JSON.stringify({
              type: "session.updated",
              data: { raw_type: "session.status", status_type: "idle" },
              session_id: "sess-1",
            }), { agentId: "agent-1", sessionId: "sess-1" });

            const uiState = computeOpenCodeRuntimeUiState(
              { runtime_status: "running" },
              chatState
            );
            assert.equal(chatState.openCodeProjection.sessionStatus, "idle");
            assert.equal(chatState.inflightThinking == null, true);
            assert.equal(calls.some((item) => item[1] === "execution.started"), false);
            assert.equal(uiState.messageProgress, "idle");
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_opencode_session_idle_sets_snapshot_without_inflight_thinking_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(
            [
                _extract_js_function(src, "normalizeRuntimeEventTypeAlias"),
                _extract_js_function(src, "isCompletionRuntimeState"),
                _extract_js_function(src, "normalizeRuntimeEvent"),
                _extract_js_function(src, "normalizeChatRunStatus"),
                _extract_js_function(src, "normalizeOpenCodeSessionStatusType"),
                _extract_js_function(src, "isOpenCodeSessionInactivePayload"),
                _extract_js_function(src, "buildOpenCodeInactiveSessionStatusPayload"),
                _extract_js_function(src, "markOpenCodeProjectionInactive"),
                _extract_js_function(src, "applyOpenCodeCanonicalEventToChatState"),
                _extract_js_function(src, "isOpenCodeSessionStateOnlyEvent"),
                _extract_js_function(src, "isOpenCodeCanonicalSnapshotEvent"),
                _extract_js_function(src, "maybeRefreshSessionSnapshotForOpenCodeEvent"),
                _extract_js_function(src, "handleAgentEventMessage"),
            ]
        )
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const COMPLETION_RUNTIME_STATES = new Set(["complete", "completed", "done", "finished"]);
            const state = { selectedAgentId: "agent-1" };
            const calls = [];
            const chatState = {
              sessionId: "sess-1",
              openCodeProjection: null,
              inflightThinking: null,
              activeRequest: null,
            };

            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }
            function loadSessionForAgent(agentId, sessionId) {
              calls.push(["load", agentId, sessionId]);
              return new Promise(() => {});
            }
            function isThinkingPanelActiveForAgent() { return false; }
            function scheduleThinkingPanelRefresh() { calls.push(["panel"]); }
            function syncSelectedAgentChatActionControls() { calls.push(["sync"]); }
            function fallbackRequestContextForAgent() { return {}; }
            function appendPortalChatRuntimeEvent(_agentId, _ctx, type) { calls.push(["portal", type]); }

            handleAgentEventMessage(JSON.stringify({
              type: "session.updated",
              data: { raw_type: "session.idle", reconcile_hint: "fetch_session_messages" },
              session_id: "sess-1",
            }), { agentId: "agent-1", sessionId: "sess-1" });

            assert.equal(chatState.openCodeProjection.sessionStatus, "idle");
            assert.equal(chatState.openCodeProjection.needsSnapshot, true);
            assert.equal(chatState.openCodeProjection.snapshotRefreshInFlight, true);
            assert.equal(chatState.inflightThinking == null, true);
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_opencode_message_event_without_active_request_refreshes_snapshot_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(
            [
                _extract_js_function(src, "normalizeRuntimeEventTypeAlias"),
                _extract_js_function(src, "isCompletionRuntimeState"),
                _extract_js_function(src, "normalizeRuntimeEvent"),
                _extract_js_function(src, "normalizeChatRunStatus"),
                _extract_js_function(src, "normalizeOpenCodeSessionStatusType"),
                _extract_js_function(src, "isOpenCodeSessionInactivePayload"),
                _extract_js_function(src, "buildOpenCodeInactiveSessionStatusPayload"),
                _extract_js_function(src, "isOpenCodeSessionStatusBlockingPayload"),
                _extract_js_function(src, "applyOpenCodeCanonicalEventToChatState"),
                _extract_js_function(src, "isOpenCodeSessionStateOnlyEvent"),
                _extract_js_function(src, "isOpenCodeCanonicalSnapshotEvent"),
                _extract_js_function(src, "maybeRefreshSessionSnapshotForOpenCodeEvent"),
                _extract_js_function(src, "handleAgentEventMessage"),
            ]
        )
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const COMPLETION_RUNTIME_STATES = new Set(["complete", "completed", "done", "finished"]);
            const state = { selectedAgentId: "agent-1" };
            const calls = [];
            const chatState = {
              sessionId: "sess-1",
              openCodeProjection: {
                messagesById: {},
                partsById: {},
                sessionStatus: "busy",
                needsSnapshot: false,
              },
              inflightThinking: null,
              activeRequest: null,
            };

            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }
            function loadSessionForAgent(agentId, sessionId) {
              calls.push(["load", agentId, sessionId]);
              return new Promise(() => {});
            }
            function isThinkingPanelActiveForAgent() { return false; }
            function scheduleThinkingPanelRefresh() { calls.push(["panel"]); }
            function syncSelectedAgentChatActionControls() { calls.push(["sync"]); }
            function fallbackRequestContextForAgent() { return {}; }
            function appendPortalChatRuntimeEvent(_agentId, _ctx, type) { calls.push(["portal", type]); }

            (async () => {
              handleAgentEventMessage(JSON.stringify({
                type: "message.part.updated",
                request_id: "real-run-1",
                session_id: "sess-1",
                data: {
                  raw_type: "message.part.updated",
                  message_id: "message-1",
                  part_id: "part-1",
                  part_type: "text",
                  part: { id: "part-1", type: "text", text: "hello" },
                },
              }), { agentId: "agent-1", sessionId: "sess-1", requestId: "opencode-session-sess-1" });

              assert.equal(chatState.openCodeProjection.partsById["part-1"].text, "hello");
              assert.equal(chatState.openCodeProjection.needsSnapshot, true);
              assert.equal(chatState.openCodeProjection.snapshotRefreshInFlight, true);
              assert.equal(chatState.inflightThinking == null, true);
              await Promise.resolve();
              assert.deepEqual(calls.find((item) => item[0] === "load"), ["load", "agent-1", "sess-1"]);
            })();
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_opencode_snapshot_refresh_failure_keeps_needs_snapshot_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        _extract_js_function(src, "maybeRefreshSessionSnapshotForOpenCodeEvent")
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const state = { selectedAgentId: "agent-1" };
            const calls = [];
            const chatState = { openCodeProjection: { needsSnapshot: true } };

            function loadSessionForAgent() { return Promise.reject(new Error("refresh failed")); }
            function fallbackRequestContextForAgent(agentId, reason) { return { agentId, reason }; }
            function appendPortalChatRuntimeEvent(_agentId, _ctx, type, data) {
              calls.push({ type, data });
            }

            (async () => {
              await maybeRefreshSessionSnapshotForOpenCodeEvent("agent-1", chatState, "sess-1", { type: "session.idle" });
              assert.equal(chatState.openCodeProjection.needsSnapshot, true);
              assert.match(chatState.openCodeProjection.snapshotRefreshError, /refresh failed/);
              assert.equal(chatState.openCodeProjection.snapshotRefreshInFlight, false);
              assert.equal(chatState.openCodeProjection.snapshotRefreshLastFailedAt > 0, true);
              assert.equal(calls[0].type, "portal.reconcile.failed");
            })();
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_opencode_snapshot_refresh_success_clears_needs_snapshot_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        _extract_js_function(src, "maybeRefreshSessionSnapshotForOpenCodeEvent")
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const state = { selectedAgentId: "agent-1" };
            const chatState = {
              openCodeProjection: {
                needsSnapshot: true,
                snapshotRefreshError: "old",
              },
            };

            function loadSessionForAgent() { return Promise.resolve({ ok: true }); }
            function fallbackRequestContextForAgent() { return {}; }
            function appendPortalChatRuntimeEvent() { throw new Error("should not report failure"); }

            (async () => {
              await maybeRefreshSessionSnapshotForOpenCodeEvent("agent-1", chatState, "sess-1", { type: "session.idle" });
              assert.equal(chatState.openCodeProjection.needsSnapshot, false);
              assert.equal(chatState.openCodeProjection.snapshotRefreshError, "");
              assert.equal(chatState.openCodeProjection.snapshotRefreshInFlight, false);
            })();
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_load_session_refreshes_opencode_session_status_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(
            [
                _extract_js_function(src, "getCanonicalMessagesFromSessionPayload"),
                _extract_js_function(src, "normalizeChatRunStatus"),
                _extract_js_function(src, "normalizeOpenCodeSessionStatusType"),
                _extract_js_function(src, "isOpenCodeSessionInactivePayload"),
                _extract_js_function(src, "buildOpenCodeInactiveSessionStatusPayload"),
                _extract_js_function(src, "markOpenCodeProjectionInactive"),
                _extract_js_function(src, "isOpenCodeSessionStatusBlockingPayload"),
                _extract_js_function(src, "isOpenCodeSessionBlocking"),
                _extract_js_function(src, "refreshOpenCodeSessionStatusForAgent"),
                _extract_js_function(src, "isActiveRequestBlocking"),
                _extract_js_function(src, "normalizeRuntimeHealthStatus"),
                _extract_js_function(src, "computeOpenCodeRuntimeUiState"),
                _extract_js_function(src, "openCodeRuntimeUiStatusText"),
                _extract_js_function(src, "setChatStatus"),
                _extract_js_function(src, "loadSessionForAgent"),
            ]
        )
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const calls = [];
            let capturedMetadata = null;
            const chatState = {
              sessionId: "",
              openCodeProjection: null,
              inflightThinking: null,
              activeRequest: null,
              needsReload: true,
            };
            const state = {
              selectedAgentId: "agent-1",
              selectedAgentName: "",
              mineAgents: [{ id: "agent-1", name: "Agent" }],
            };
            const dom = {
              chatStatus: {
                dataset: {},
                setAttribute(name, value) { this[name] = value; },
              },
            };

            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }
            function getSelectedAgent() { return { id: "agent-1", runtime_status: "running" }; }
            function hasActiveChatRequestForAgent() { return false; }
            function updateAgentSession(_agentId, sessionId) { chatState.sessionId = sessionId; }
            function deriveSessionRecoveryNotice() { return null; }
            function canonicalMessagesToLegacyDisplayMessages() { throw new Error("legacy conversion should not run without canonical messages"); }
            function applyCanonicalMessagesToChatState() { throw new Error("canonical apply should not run without canonical messages"); }
            function renderChatHistory(_messages, metadata) { capturedMetadata = metadata; }
            function reconcileActiveRequestProjection() {}
            function addEditButtonsToMessages() {}
            function currentSessionIdForAgent() { return chatState.sessionId; }
            function setLastSessionId() {}
            function showToast() {}
            async function openSessionsPanel() {}
            async function agentApiFor(_agentId, path) {
              calls.push(path);
              if (path === "/api/sessions/sess-1/status") {
                return { status_type: "idle", active: false };
              }
              if (path === "/api/sessions/sess-1") {
                return { messages: [], metadata: {} };
              }
              throw new Error(`unexpected path ${path}`);
            }

            (async () => {
              await loadSessionForAgent("agent-1", "sess-1", { render: true });
              assert.deepEqual(calls, ["/api/sessions/sess-1", "/api/sessions/sess-1/status"]);
              assert.equal(chatState.openCodeProjection.sessionStatus, "idle");
              assert.equal(capturedMetadata.session_status.status_type, "idle");
              assert.equal(dom.chatStatus.dataset.sessionStatus, "idle");
            })();
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_load_session_busy_status_hydrates_active_request_and_reconnects_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(
            [
                _extract_js_function(src, "getCanonicalMessagesFromSessionPayload"),
                _extract_js_function(src, "normalizeChatRunStatus"),
                _extract_js_function(src, "isActiveRequestBlocking"),
                _extract_js_function(src, "hasIncompleteInflightThinking"),
                _extract_js_function(src, "normalizeOpenCodeSessionStatusType"),
                _extract_js_function(src, "isOpenCodeSessionInactivePayload"),
                _extract_js_function(src, "buildOpenCodeInactiveSessionStatusPayload"),
                _extract_js_function(src, "markOpenCodeProjectionInactive"),
                _extract_js_function(src, "isOpenCodeSessionStatusBlockingPayload"),
                _extract_js_function(src, "isOpenCodeSessionBlocking"),
                _extract_js_function(src, "hasActiveChatRequestForAgent"),
                _extract_js_function(src, "shouldShowAbortChatRunButton"),
                _extract_js_function(src, "syncSelectedAgentChatActionControls"),
                _extract_js_function(src, "normalizeRuntimeHealthStatus"),
                _extract_js_function(src, "computeOpenCodeRuntimeUiState"),
                _extract_js_function(src, "openCodeRuntimeUiStatusText"),
                _extract_js_function(src, "setChatStatus"),
                _extract_js_function(src, "refreshOpenCodeSessionStatusForAgent"),
                _extract_js_function(src, "isRuntimeRunActuallyActive"),
                _extract_js_function(src, "hydrateActiveRequestFromRun"),
                _extract_js_function(src, "buildSyntheticRunFromSessionStatus"),
                _extract_js_function(src, "hydrateActiveRequestFromSessionStatus"),
                _extract_js_function(src, "loadSessionForAgent"),
            ]
        )
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const calls = [];
            let capturedMetadata = null;
            const chatState = {
              sessionId: "",
              isSubmitting: false,
              openCodeProjection: null,
              inflightThinking: null,
              activeRequest: null,
              needsReload: true,
            };
            const state = {
              selectedAgentId: "agent-1",
              selectedAgentName: "",
              mineAgents: [{ id: "agent-1", name: "Agent", runtime_status: "running", status: "running" }],
            };
            const sessionsBtn = {};
            const abortClassState = { hidden: true };
            const dom = {
              chatStatus: {
                dataset: {},
                textContent: "",
                className: "",
                setAttribute(name, value) { this[name] = value; },
              },
              sendChatBtn: { disabled: false },
              abortChatRunBtn: {
                disabled: true,
                classList: {
                  toggle(name, hidden) { abortClassState[name] = hidden; },
                  add(name) { abortClassState[name] = true; },
                  remove(name) { abortClassState[name] = false; },
                },
                setAttribute(name, value) { this[name] = value; },
              },
              headerNewChatBtn: {},
              homeStartChatBtn: {},
            };
            const document = { getElementById(id) { return id === "btn-sessions" ? sessionsBtn : null; } };

            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }
            function getSelectedAgent() { return state.mineAgents[0]; }
            function getSelectedAgentStatus() { return "running"; }
            function setButtonDisabled(button, disabled, title = "") {
              if (!button) return;
              button.disabled = !!disabled;
              button.title = title;
            }
            function updateAgentSession(_agentId, sessionId) { chatState.sessionId = sessionId; }
            function deriveSessionRecoveryNotice() { return null; }
            function canonicalMessagesToLegacyDisplayMessages() { throw new Error("legacy conversion should not run without canonical messages"); }
            function applyCanonicalMessagesToChatState() { throw new Error("canonical apply should not run without canonical messages"); }
            function renderChatHistory(_messages, metadata) { capturedMetadata = metadata; }
            function reconcileActiveRequestProjection(_agentId, _sessionId, metadata) {
              calls.push(["projection", metadata.session_status.status_type]);
            }
            function addEditButtonsToMessages() {}
            function currentSessionIdForAgent() { return chatState.sessionId; }
            function setLastSessionId() {}
            function showToast() {}
            async function openSessionsPanel() {}
            function ensureEventSocketForAgent(agentId, sessionId, requestId) {
              calls.push(["events", agentId, sessionId, requestId]);
            }
            function startChatRunReconcileLoop(agentId, requestCtx, options) {
              calls.push(["reconcile", agentId, requestCtx.runtimeRequestId, options?.immediate]);
            }
            function setChatSubmittingForAgent(_agentId, active) {
              chatState.isSubmitting = active;
              syncSelectedAgentChatActionControls();
            }
            async function agentApiFor(_agentId, path) {
              calls.push(["api", path]);
              if (path === "/api/sessions/sess-1/status") {
                return { active: true, status_type: "busy", action_hint: "wait_reconnect_or_stop" };
              }
              if (path === "/api/sessions/sess-1") {
                return { messages: [], metadata: {} };
              }
              throw new Error(`unexpected path ${path}`);
            }

            (async () => {
              await loadSessionForAgent("agent-1", "sess-1", { render: true });
              assert.deepEqual(
                calls.filter((item) => item[0] === "api").map((item) => item[1]),
                ["/api/sessions/sess-1", "/api/sessions/sess-1/status"]
              );
              assert.equal(chatState.openCodeProjection.sessionStatus, "busy");
              assert.equal(chatState.activeRequest.runtimeRequestId, "opencode-session-sess-1");
              assert.equal(chatState.activeRequest.sessionIdAtSend, "sess-1");
              assert.equal(chatState.inflightThinking.contextSource, "opencode_session_state");
              assert.equal(hasActiveChatRequestForAgent("agent-1"), true);
              assert.equal(dom.sendChatBtn.disabled, true);
              assert.equal(dom.abortChatRunBtn.disabled, false);
              assert.equal(dom.abortChatRunBtn["aria-hidden"], "false");
              assert.equal(abortClassState.hidden, false);
              assert.match(dom.chatStatus.textContent, /Assistant online\. Session busy/);
              assert.match(dom.chatStatus.textContent, /Previous message still running/);
              assert.match(dom.chatStatus.title, /Session: busy/);
              assert.match(dom.chatStatus["aria-label"], /Runtime: online/);
              assert.match(dom.chatStatus["aria-label"], /Message: reconnecting/);
              assert.equal(capturedMetadata.session_status.status_type, "busy");
              assert.ok(calls.some((item) => item[0] === "events" && item[3] === "opencode-session-sess-1"));
              assert.ok(calls.some((item) => item[0] === "reconcile" && item[2] === "opencode-session-sess-1" && item[3] === true));
              assert.ok(calls.some((item) => item[0] === "projection" && item[1] === "busy"));
            })();
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_acceptance_refresh_busy_then_session_abort_and_completion_snapshot_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(
            [
                _extract_js_function(src, "getCanonicalMessagesFromSessionPayload"),
                _extract_js_function(src, "canonicalPartText"),
                _extract_js_function(src, "canonicalMessageVisibleText"),
                _extract_js_function(src, "canonicalMessageToLegacyDisplayMessage"),
                _extract_js_function(src, "canonicalMessagesToLegacyDisplayMessages"),
                _extract_js_function(src, "canonicalPartToThinkingItem"),
                _extract_js_function(src, "canonicalMessagesToThinkingItems"),
                _extract_js_function(src, "canonicalThinkingItemToRuntimeEvent"),
                _extract_js_function(src, "canonicalMessagesToThinkingEvents"),
                _extract_js_function(src, "applyCanonicalMessagesToChatState"),
                _extract_js_function(src, "normalizeChatRunStatus"),
                _extract_js_function(src, "normalizeOpenCodeSessionStatusType"),
                _extract_js_function(src, "isOpenCodeSessionInactivePayload"),
                _extract_js_function(src, "buildOpenCodeInactiveSessionStatusPayload"),
                _extract_js_function(src, "isOpenCodeSessionStatusBlockingPayload"),
                _extract_js_function(src, "isOpenCodeSessionBlocking"),
                _extract_js_function(src, "isActiveRequestBlocking"),
                _extract_js_function(src, "hasIncompleteInflightThinking"),
                _extract_js_function(src, "hasActiveChatRequestForAgent"),
                _extract_js_function(src, "shouldShowAbortChatRunButton"),
                _extract_js_function(src, "syncSelectedAgentChatActionControls"),
                _extract_js_function(src, "normalizeRuntimeHealthStatus"),
                _extract_js_function(src, "computeOpenCodeRuntimeUiState"),
                _extract_js_function(src, "openCodeRuntimeUiStatusText"),
                _extract_js_function(src, "setChatStatus"),
                _extract_js_function(src, "refreshOpenCodeSessionStatusForAgent"),
                _extract_js_function(src, "isRuntimeRunActuallyActive"),
                _extract_js_function(src, "hydrateActiveRequestFromRun"),
                _extract_js_function(src, "buildSyntheticRunFromSessionStatus"),
                _extract_js_function(src, "hydrateActiveRequestFromSessionStatus"),
                _extract_js_function(src, "loadSessionForAgent"),
                _extract_js_function(src, "isSyntheticOpenCodeSessionRequest"),
                _extract_js_function(src, "requestContextIdCandidates"),
                _extract_js_function(src, "activeRequestMatchesRequestContext"),
                _extract_js_function(src, "clearStaleActiveRequest"),
                _extract_js_function(src, "markOpenCodeProjectionInactive"),
                _extract_js_function(src, "runtimeAbortSucceeded"),
                _extract_js_function(src, "runtimeAbortIndicatesInactive"),
                _extract_js_function(src, "abortSessionForAgent"),
                _extract_js_function(src, "handleSessionAbortSuccess"),
                _extract_js_function(src, "abortActiveChatRequestForSelectedAgent"),
                _extract_js_function(src, "normalizeRuntimeEventTypeAlias"),
                _extract_js_function(src, "isTrackableThinkingEvent"),
                _extract_js_function(src, "isCompletionRuntimeState"),
                _extract_js_function(src, "normalizeRuntimeEvent"),
                _extract_js_function(src, "applyOpenCodeCanonicalEventToChatState"),
                _extract_js_function(src, "isOpenCodeSessionStateOnlyEvent"),
                _extract_js_function(src, "isOpenCodeCanonicalSnapshotEvent"),
                _extract_js_function(src, "maybeRefreshSessionSnapshotForOpenCodeEvent"),
                _extract_js_function(src, "handleAgentEventMessage"),
            ]
        )
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const COMPLETION_RUNTIME_STATES = new Set(["complete", "completed", "done", "finished"]);
            const calls = [];
            const renderCalls = [];
            let sessionMode = "busy";

            const chatState = {
              sessionId: "",
              isSubmitting: false,
              openCodeProjection: null,
              inflightThinking: null,
              activeRequest: null,
              needsReload: true,
            };
            const state = {
              selectedAgentId: "agent-1",
              selectedAgentName: "",
              mineAgents: [{ id: "agent-1", name: "Agent", runtime_status: "running", status: "running" }],
            };
            const sessionsBtn = {};
            const abortClassState = { hidden: true };
            const dom = {
              chatStatus: {
                dataset: {},
                textContent: "",
                className: "",
                setAttribute(name, value) { this[name] = value; },
              },
              sendChatBtn: { disabled: false },
              abortChatRunBtn: {
                disabled: true,
                classList: {
                  toggle(name, hidden) { abortClassState[name] = hidden; },
                  add(name) { abortClassState[name] = true; },
                  remove(name) { abortClassState[name] = false; },
                },
                setAttribute(name, value) { this[name] = value; },
              },
              headerNewChatBtn: {},
              homeStartChatBtn: {},
            };
            const document = {
              hidden: false,
              getElementById(id) { return id === "btn-sessions" ? sessionsBtn : null; },
            };

            function canonicalUserHi() {
              return {
                message_id: "user-1",
                role: "user",
                info: { id: "user-1", role: "user" },
                parts: [{ id: "u-part-1", type: "text", text: "hi" }],
              };
            }
            function canonicalAssistantHello() {
              return {
                message_id: "assistant-1",
                role: "assistant",
                info: { id: "assistant-1", role: "assistant", requestID: "opencode-session-op-1" },
                parts: [{ id: "a-part-1", type: "text", text: "hello" }],
              };
            }
            function sessionPayload() {
              const canonical = sessionMode === "completed"
                ? [canonicalUserHi(), canonicalAssistantHello()]
                : [canonicalUserHi()];
              return {
                messages: [{ role: "user", content: "hi" }],
                canonical_messages: canonical,
                metadata: {},
              };
            }
            function statusPayload() {
              if (sessionMode === "busy") {
                return {
                  success: true,
                  active: true,
                  status_type: "busy",
                  action_hint: "wait_reconnect_or_stop",
                  can_abort: true,
                  active_run: {
                    request_id: "opencode-session-op-1",
                    session_id: "sess-1",
                    opencode_session_id: "op-1",
                    source_of_truth: "opencode",
                    opencode_active: true,
                    status: "busy",
                    can_abort: true,
                    action_hint: "wait_reconnect_or_stop",
                  },
                };
              }
              return {
                success: true,
                active: false,
                status_type: "idle",
                action_hint: "safe_to_send",
                can_abort: false,
                active_run: null,
              };
            }

            function mergeThinkingEvents(first = [], second = []) { return [...first, ...second]; }
            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }
            function getSelectedAgent() { return state.mineAgents[0]; }
            function getSelectedAgentStatus() { return "running"; }
            function setButtonDisabled(button, disabled, title = "") {
              if (!button) return;
              button.disabled = !!disabled;
              button.title = title;
            }
            function updateAgentSession(_agentId, sessionId) { chatState.sessionId = sessionId; }
            function deriveSessionRecoveryNotice() { return null; }
            function renderChatHistory(messages, metadata) {
              renderCalls.push({
                messages: messages.map((message) => ({
                  role: message.role,
                  content: message.display_content || message.content || "",
                })),
                metadata,
              });
            }
            function reconcileActiveRequestProjection(_agentId, _sessionId, metadata) {
              calls.push(["projection", metadata?.session_status?.status_type || ""]);
            }
            function addEditButtonsToMessages() {}
            function currentSessionIdForAgent() { return chatState.sessionId; }
            function setLastSessionId() {}
            function showToast(message) { calls.push(["toast", message]); }
            async function openSessionsPanel() {}
            function ensureEventSocketForAgent(agentId, sessionId, requestId) {
              calls.push(["events", agentId, sessionId, requestId]);
            }
            function startChatRunReconcileLoop(agentId, requestCtx, options) {
              calls.push(["reconcile", agentId, requestCtx.runtimeRequestId, options?.immediate]);
            }
            function stopChatRunReconcileLoop(requestCtx) {
              if (requestCtx) requestCtx.reconcileStopped = true;
              calls.push(["stopReconcile", requestCtx?.runtimeRequestId || ""]);
            }
            function setChatSubmittingForAgent(_agentId, active) {
              chatState.isSubmitting = active;
              syncSelectedAgentChatActionControls();
            }
            function clearWaitingForRuntimeEventsTimer() {}
            function cancelAssistantTypewriter() {}
            function fallbackRequestContextForAgent(agentId, reason) { return { agentId, reason, sessionIdAtSend: chatState.sessionId }; }
            function appendPortalChatRuntimeEvent(_agentId, _ctx, type, data) { calls.push(["portalEvent", type, data]); }
            function updateThinkingContextFromEvent() {}
            function isThinkingPanelActiveForAgent() { return false; }
            function scheduleThinkingPanelRefresh(agentId) { calls.push(["panel", agentId]); }
            async function agentApiFor(_agentId, path) {
              calls.push(["api", path]);
              if (path === "/api/sessions/sess-1") return sessionPayload();
              if (path === "/api/sessions/sess-1/status") return statusPayload();
              if (path.includes("/api/chat/runs/")) throw new Error("chat run abort endpoint should not be used for synthetic session runs");
              throw new Error(`unexpected path ${path}`);
            }
            async function fetch(url, options) {
              calls.push(["fetch", url, options?.method || "GET"]);
              assert.equal(url, "/a/agent-1/api/sessions/sess-1/abort");
              assert.equal(options?.method, "POST");
              sessionMode = "idle";
                  return {
                    ok: true,
                    status: 200,
                    async json() {
                      return {
                        success: true,
                        active: false,
                        aborted: true,
                        action_hint: "safe_to_send",
                        status: { type: "idle" },
                        run: null,
                        abort_result: { success: true },
                      };
                    },
                  };
            }

            const realSyncSelectedAgentChatActionControls = syncSelectedAgentChatActionControls;
            syncSelectedAgentChatActionControls = function() {
              calls.push(["syncControls"]);
              return realSyncSelectedAgentChatActionControls();
            };
            const realLoadSessionForAgent = loadSessionForAgent;
            loadSessionForAgent = async function(agentId, sessionId, options = {}) {
              calls.push(["load", agentId, sessionId, options.render === true]);
              return realLoadSessionForAgent(agentId, sessionId, options);
            };

            (async () => {
              await loadSessionForAgent("agent-1", "sess-1", { render: true });

              assert.ok(chatState.activeRequest);
              assert.equal(chatState.activeRequest.runtimeRequestId, "opencode-session-op-1");
              assert.equal(hasActiveChatRequestForAgent("agent-1"), true);
              assert.equal(dom.sendChatBtn.disabled, true);
              assert.equal(dom.abortChatRunBtn.disabled, false);
              assert.equal(dom.abortChatRunBtn["aria-hidden"], "false");
              assert.equal(abortClassState.hidden, false);
              assert.match(dom.chatStatus.textContent, /Session busy|Previous message still running/);
              assert.ok(calls.some((item) => item[0] === "events" && item[3] === "opencode-session-op-1"));
              assert.ok(calls.some((item) => item[0] === "reconcile" && item[2] === "opencode-session-op-1" && item[3] === true));
              assert.deepEqual(renderCalls.at(-1).messages, [{ role: "user", content: "hi" }]);

              await abortActiveChatRequestForSelectedAgent();

              assert.ok(calls.some((item) => item[0] === "fetch" && item[1] === "/a/agent-1/api/sessions/sess-1/abort" && item[2] === "POST"));
              assert.equal(calls.some((item) => item[0] === "api" && item[1].includes("/api/chat/runs/opencode-session-op-1/abort")), false);
              assert.equal(chatState.activeRequest, null);
              assert.equal(chatState.inflightThinking.completed, true);
              assert.equal(chatState.inflightThinking.stale, true);
              assert.equal(chatState.openCodeProjection.sessionStatus, "idle");
              assert.equal(chatState.openCodeProjection.sessionStatusPayload.active, false);
              assert.equal(chatState.openCodeProjection.sessionStatusPayload.active_run, null);
              assert.ok(calls.some((item) => item[0] === "syncControls"));
              assert.ok(calls.some((item) => item[0] === "load" && item[2] === "sess-1" && item[3] === true));
              assert.equal(dom.sendChatBtn.disabled, false);

              chatState.activeRequest = null;
              chatState.inflightThinking = null;
              chatState.openCodeProjection.sessionStatus = "busy";
              sessionMode = "completed";
              const callsBeforeCompletion = calls.length;

              handleAgentEventMessage(JSON.stringify({
                type: "message.completed",
                session_id: "sess-1",
                data: {
                  raw_type: "message.completed",
                  message_id: "assistant-1",
                },
              }), { agentId: "agent-1", sessionId: "sess-1" });

              assert.equal(chatState.openCodeProjection.needsSnapshot, true);
              assert.equal(chatState.openCodeProjection.snapshotRefreshInFlight, true);
              await new Promise((resolve) => setTimeout(resolve, 0));

              const latestRender = renderCalls.at(-1).messages;
              assert.deepEqual(latestRender, [
                { role: "user", content: "hi" },
                { role: "assistant", content: "hello" },
              ]);
              assert.ok(calls.some((item) => item[0] === "load" && item[2] === "sess-1" && item[3] === true));
              assert.equal(chatState.openCodeProjection.sessionStatus, "idle");
              assert.equal(chatState.openCodeProjection.sessionStatusPayload.active, false);
              assert.equal(chatState.openCodeProjection.sessionStatusPayload.active_run, null);
              assert.equal(dom.sendChatBtn.disabled, false);
              assert.equal(latestRender.find((message) => message.role === "user").content, "hi");
              assert.equal(latestRender.find((message) => message.role === "user").content.includes("hello"), false);
              const completionCalls = calls.slice(callsBeforeCompletion);
              assert.equal(completionCalls.some((item) => item[0] === "toast"), false);
              assert.equal(completionCalls.some((item) => item[0] === "portalEvent" && /failed|error/.test(item[1])), false);
            })();
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_stream_error_event_smoke_terminal_and_no_missing_final():
    src = SRC.read_text(encoding="utf-8")
    stream_js = "\n".join(
        [
            _extract_js_function(src, "normalizeChatStreamEventName"),
            _extract_js_function(src, "normalizeChatStreamEventData"),
            _extract_js_function(src, "getChatStreamTextPayload"),
            _extract_js_function(src, "getChatStreamEventType"),
            _extract_js_function(src, "isChatStreamWrapperEventName"),
            _extract_js_function(src, "isDirectCompletionEventName"),
            _extract_js_function(src, "isChatStreamDeltaEventName"),
            _extract_js_function(src, "isChatStreamFinalEventName"),
            _extract_js_function(src, "isChatRunAlreadyActivePayload"),
            _extract_js_function(src, "handleChatStreamEvent"),
            _extract_js_function(src, "handleChatStreamMissingFinal"),
        ]
    )
    script = (
        stream_js
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const state = { selectedAgentId: "agent-1" };
            const calls = [];
            function clearWaitingForRuntimeEventsTimer() {}
            function getCompletionState(payload) { return String(payload?.completion_state || "").trim().toLowerCase(); }
            function isCompletedFinalPayload() { return false; }
            function isNonSuccessFinalPayload() { return true; }
            function finalResponseText(payload) { return payload?.response || ""; }
            async function handleIncompleteChatStream() { throw new Error("should not be called"); }
            function normalizeAssistantMessageIds() { return []; }
            function handleAgentEventMessage() {}
            function rememberAssociatedRuntimeDeltaEvent() {}
            function getAssociatedRuntimeDeltaEvent() { return null; }
            function shouldIgnoreAssistantStreamDelta() { return false; }
            function queueAssistantTypewriter() {}
            function updatePendingAssistantStreamContent() {}
            async function handleAgentChatSuccess() { throw new Error("should not succeed"); }
            function finalizeNonSuccessChatResponse(agentId, requestCtx, payload, reason) {
              calls.push({ agentId, requestCtx, payload, reason });
            }

            (async () => {
              const requestCtx = { clientRequestId: "req-1", sessionIdAtSend: "sess-1" };
              const r = await handleChatStreamEvent("agent-1", requestCtx, "error", {
                error: "opencode_error",
                detail: "upstream failed",
                request_id: "req-1",
                session_id: "sess-1"
              });
              assert.equal(r, "error");
              assert.equal(requestCtx.streamFailed, true);
              assert.equal(calls.length, 1);
              assert.equal(calls[0].reason, "stream_error");
              const tail = await handleChatStreamMissingFinal("agent-1", requestCtx);
              assert.equal(tail, "handled");
            })();
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_non_success_hint_avoids_continue_for_active_run_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        _extract_js_function(src, "nonSuccessHintForPayload")
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");

            const busy = nonSuccessHintForPayload({ error: "chat_run_already_active" });
            assert.match(busy, /previous message is still running/i);
            assert.match(busy, /stop the run|reset this session/);
            assert.equal(busy.includes('send "continue"'), false);

            const stillActive = nonSuccessHintForPayload({ error: "opencode_abort_still_active" });
            assert.match(stillActive, /OpenCode still reports/i);
            assert.match(stillActive, /Reset the session|start a new chat/);
            assert.equal(stillActive.includes('send "continue"'), false);

            const running = nonSuccessHintForPayload({ status: "busy" });
            assert.match(running, /previous message is still running/i);
            assert.equal(running.includes('send "continue"'), false);

            const incomplete = nonSuccessHintForPayload({
              completion_state: "incomplete",
              incomplete_reason: "max turns"
            });
            assert.match(incomplete, /send "continue"/);
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_active_run_preflight_blocks_submit_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(
            [
                _extract_js_function(src, "isActiveRequestBlocking"),
                _extract_js_function(src, "hasIncompleteInflightThinking"),
                _extract_js_function(src, "normalizeChatRunStatus"),
                _extract_js_function(src, "normalizeOpenCodeSessionStatusType"),
                _extract_js_function(src, "isOpenCodeSessionInactivePayload"),
                _extract_js_function(src, "buildOpenCodeInactiveSessionStatusPayload"),
                _extract_js_function(src, "isOpenCodeSessionStatusBlockingPayload"),
                _extract_js_function(src, "isOpenCodeSessionBlocking"),
                _extract_js_function(src, "requestContextIdCandidates"),
                _extract_js_function(src, "activeRequestMatchesRequestContext"),
                _extract_js_function(src, "fallbackRequestContextForAgent"),
                _extract_js_function(src, "clearStaleActiveRequest"),
                _extract_js_function(src, "markOpenCodeProjectionInactive"),
                _extract_js_function(src, "hasActiveChatRequestForAgent"),
                _extract_js_function(src, "guardNoActiveChatRequestForAgent"),
                _extract_js_function(src, "isRuntimeRunActuallyActive"),
                _extract_js_function(src, "getActiveRunFromPayload"),
                _extract_js_function(src, "hydrateActiveRequestFromRun"),
                _extract_js_function(src, "preflightActiveRunForSession"),
                _extract_js_function(src, "submitChatForSelectedAgent"),
            ]
        )
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const calls = [];
            const fetchCalls = [];
            const inserted = [];
            const chatState = {
              activeRequest: null,
              isSubmitting: false,
              inflightThinking: null,
              pendingFiles: [{ status: "uploaded", file_id: "file-1", name: "notes.txt" }],
              sessionId: "sess-1",
              modelOverride: "",
              profileDefaultModel: "",
            };
            const state = {
              selectedAgentId: "agent-1",
              chatStatesByAgent: new Map([["agent-1", chatState]]),
              agentSessionIds: new Map([["agent-1", "sess-1"]]),
            };
            const dom = {
              chatInput: { value: "please inspect this" },
              messageList: { insertAdjacentHTML(_where, html) { inserted.push(html); } },
              sendChatBtn: { disabled: false },
              abortChatRunBtn: { classList: { toggle() {}, add() {}, remove() {} }, setAttribute() {}, disabled: true },
              headerNewChatBtn: {},
              homeStartChatBtn: {},
              chatModelSelect: null,
            };
            const document = {
              hidden: false,
              getElementById(id) {
                if (id === "btn-sessions") return {};
                if (id === "chat-attachments") return { value: "kept" };
                if (id === "chat-session-id") return { value: "sess-1" };
                return null;
              }
            };
            globalThis.crypto = { randomUUID() { throw new Error("clientRequestId should not be allocated"); } };

            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }
            function buildAttachmentsFromChatState() { return []; }
            function ensureChatSessionId(agentId) {
              calls.push(["ensureSession", agentId]);
              return "sess-1";
            }
            async function agentApiFor(agentId, path) {
              calls.push(["api", agentId, path]);
              return {
                active_run: {
                  request_id: "runtime-1",
                  session_id: "sess-1",
                  opencode_active: true,
                  source_of_truth: "opencode",
                  status: "busy",
                }
              };
            }
            function ensureEventSocketForAgent(agentId, sessionId, requestId) { calls.push(["events", agentId, sessionId, requestId]); }
            function startChatRunReconcileLoop(agentId, ctx, options) { calls.push(["reconcile", agentId, ctx.runtimeRequestId, options?.immediate]); }
            function setChatSubmittingForAgent(_agentId, active) {
              chatState.isSubmitting = active;
              syncSelectedAgentChatActionControls();
            }
            function setChatStatus(message) { calls.push(["status", message]); }
            function showToast(message) { calls.push(["toast", message]); }
            function syncSelectedAgentChatActionControls() {
              dom.sendChatBtn.disabled = hasActiveChatRequestForAgent("agent-1");
            }
            function activeChatRequestMessage() { return "busy"; }
            async function fetch(url) { fetchCalls.push(url); throw new Error("submit should be blocked before fetch"); }
            function appendPortalChatRuntimeEvent(agentId, ctx, type, data) {
              calls.push(["portalEvent", agentId, ctx.runtimeRequestId, type, data.message]);
            }

            (async () => {
              await submitChatForSelectedAgent();
              assert.deepEqual(fetchCalls, []);
              assert.equal(dom.chatInput.value, "please inspect this");
              assert.equal(inserted.length, 0);
              assert.equal(chatState.pendingFiles.length, 1);
              assert.equal(chatState.activeRequest.runtimeRequestId, "runtime-1");
              assert.equal(chatState.activeRequest.sessionIdAtSend, "sess-1");
              assert.equal(dom.sendChatBtn.disabled, true);
              assert.ok(calls.some((item) => item[0] === "api" && item[2] === "/api/sessions/sess-1/active-run"));
              assert.ok(calls.some((item) => item[0] === "portalEvent" && item[3] === "portal.chat_run_already_active"));
              assert.ok(calls.some((item) => item[0] === "reconcile" && item[3] === true));
              assert.ok(calls.some((item) => item[0] === "status" && item[1].includes("Previous message still running")));
            })();
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_chat_run_already_active_sse_error_is_not_generic_failure_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(
            [
                _extract_js_function(src, "normalizeChatRunStatus"),
                _extract_js_function(src, "isRuntimeRunActuallyActive"),
                _extract_js_function(src, "hydrateActiveRequestFromRun"),
                _extract_js_function(src, "cssEscapeForSelector"),
                _extract_js_function(src, "removeOptimisticUserRowForRequest"),
                _extract_js_function(src, "isChatRunAlreadyActivePayload"),
                _extract_js_function(src, "handleChatRunAlreadyActive"),
                _extract_js_function(src, "handleChatStreamEvent"),
            ]
        )
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const calls = [];
            const requestCtx = {
              clientRequestId: "client-1",
              requestId: "client-1",
              sessionIdAtSend: "sess-1",
              backupMessage: "retry me",
              streamedText: "",
            };
            const chatState = {
              activeRequest: requestCtx,
              isSubmitting: true,
              inflightThinking: { completed: false },
              pendingFiles: [],
              sessionId: "sess-1",
            };
            const state = { selectedAgentId: "agent-1" };
            const dom = { chatInput: { value: "" }, sendChatBtn: { disabled: false } };

            function normalizeChatStreamEventName(value) { return String(value || "").trim().toLowerCase(); }
            function normalizeChatStreamEventData(value) { return value && typeof value === "object" ? value : {}; }
            function getChatStreamTextPayload() { return ""; }
            function isChatStreamWrapperEventName() { return false; }
            function isDirectCompletionEventName() { return false; }
            function isChatStreamDeltaEventName() { return false; }
            function isChatStreamFinalEventName() { return false; }
            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }
            function removeTemporaryAssistantRows(options) { calls.push(["removeAssistant", options.requestId, options.onlyEmpty]); }
            function removeLatestOptimisticUserRow() { calls.push(["removeUser"]); }
            function syncChatInputHeight() { calls.push(["syncHeight"]); }
            function setChatSubmittingForAgent(_agentId, active) { chatState.isSubmitting = active; }
            function stopChatRunReconcileLoop(ctx) { calls.push(["stopReconcile", ctx.clientRequestId]); }
            function ensureEventSocketForAgent(agentId, sessionId, requestId) { calls.push(["events", agentId, sessionId, requestId]); }
            function startChatRunReconcileLoop(agentId, ctx, options) { calls.push(["reconcile", agentId, ctx.runtimeRequestId, options?.immediate]); }
            function appendPortalChatRuntimeEvent(agentId, ctx, type) { calls.push(["event", type]); }
            function setChatStatus(message) { calls.push(["status", message]); }
            function showToast(message) { calls.push(["toast", message]); }
            function syncSelectedAgentChatActionControls() { dom.sendChatBtn.disabled = true; }
            function finalizeNonSuccessChatResponse() { calls.push(["genericFinal"]); }

            (async () => {
              const result = await handleChatStreamEvent("agent-1", requestCtx, "error", {
                error: "chat_run_already_active",
                active_run: {
                  request_id: "runtime-2",
                  opencode_active: true,
                  source_of_truth: "opencode",
                  status: "running",
                },
              });
              assert.equal(result, "error");
              assert.equal(calls.some((item) => item[0] === "genericFinal"), false);
              assert.ok(calls.some((item) => item[0] === "removeAssistant" && item[2] === false));
              assert.ok(calls.some((item) => item[0] === "removeUser"));
              assert.equal(dom.chatInput.value, "retry me");
              assert.equal(chatState.isSubmitting, false);
              assert.notEqual(chatState.activeRequest.clientRequestId, "client-1");
              assert.equal(chatState.activeRequest.runtimeRequestId, "runtime-2");
              assert.equal(chatState.activeRequest.requestId, "runtime-2");
              assert.ok(calls.some((item) => item[0] === "stopReconcile" && item[1] === "client-1"));
              assert.ok(calls.some((item) => item[0] === "reconcile" && item[2] === "runtime-2" && item[3] === true));
            })();
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_chat_run_already_active_without_active_run_uses_session_status_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(
            [
                _extract_js_function(src, "normalizeChatRunStatus"),
                _extract_js_function(src, "normalizeOpenCodeSessionStatusType"),
                _extract_js_function(src, "isOpenCodeSessionInactivePayload"),
                _extract_js_function(src, "buildOpenCodeInactiveSessionStatusPayload"),
                _extract_js_function(src, "isOpenCodeSessionStatusBlockingPayload"),
                _extract_js_function(src, "refreshOpenCodeSessionStatusForAgent"),
                _extract_js_function(src, "requestContextIdCandidates"),
                _extract_js_function(src, "activeRequestMatchesRequestContext"),
                _extract_js_function(src, "fallbackRequestContextForAgent"),
                _extract_js_function(src, "clearStaleActiveRequest"),
                _extract_js_function(src, "markOpenCodeProjectionInactive"),
                _extract_js_function(src, "isRuntimeRunActuallyActive"),
                _extract_js_function(src, "hydrateActiveRequestFromRun"),
                _extract_js_function(src, "buildSyntheticRunFromSessionStatus"),
                _extract_js_function(src, "hydrateActiveRequestFromSessionStatus"),
                _extract_js_function(src, "handleChatRunAlreadyActive"),
            ]
        )
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const calls = [];
            const requestCtx = {
              clientRequestId: "client-1",
              requestId: "client-1",
              sessionIdAtSend: "sess-1",
              backupMessage: "retry me",
              backupPendingFiles: [],
            };
            const chatState = {
              activeRequest: requestCtx,
              isSubmitting: true,
              inflightThinking: { completed: false },
              pendingFiles: [],
              sessionId: "sess-1",
            };
            const state = { selectedAgentId: "agent-1" };
            const dom = { chatInput: { value: "" } };

            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }
            function removeTemporaryAssistantRows(options) { calls.push(["removeAssistant", options.requestId, options.onlyEmpty]); }
            function removeOptimisticUserRowForRequest() { calls.push(["removeUser"]); return true; }
            function removeLatestOptimisticUserRow() { calls.push(["fallbackRemoveUser"]); }
            function syncChatInputHeight() { calls.push(["syncHeight"]); }
            function renderInputPreview() { calls.push(["renderInput"]); }
            function setChatSubmittingForAgent(_agentId, active) { chatState.isSubmitting = active; }
            function stopChatRunReconcileLoop(ctx) { calls.push(["stopReconcile", ctx.clientRequestId]); }
            function ensureEventSocketForAgent(agentId, sessionId, requestId) { calls.push(["events", agentId, sessionId, requestId]); }
            function startChatRunReconcileLoop(agentId, ctx, options) { calls.push(["reconcile", agentId, ctx.runtimeRequestId, options?.immediate]); }
            function appendPortalChatRuntimeEvent(agentId, ctx, type, data) { calls.push(["event", type, data.message]); }
            function setChatStatus(message) { calls.push(["status", message]); }
            function showToast(message) { calls.push(["toast", message]); }
            function syncSelectedAgentChatActionControls() { calls.push(["syncControls"]); }
            async function preflightActiveRunForSession() { throw new Error("status should hydrate before preflight"); }
            async function agentApiFor(agentId, path) {
              calls.push(["api", agentId, path]);
              assert.equal(path, "/api/sessions/sess-1/status");
              return { active: true, status_type: "busy", action_hint: "wait_reconnect_or_stop" };
            }

            (async () => {
              const result = await handleChatRunAlreadyActive("agent-1", requestCtx, {
                error: "chat_run_already_active",
              });
              assert.equal(result, "handled");
              assert.ok(calls.some((item) => item[0] === "api"));
              assert.ok(calls.some((item) => item[0] === "removeAssistant" && item[2] === false));
              assert.ok(calls.some((item) => item[0] === "removeUser"));
              assert.equal(dom.chatInput.value, "retry me");
              assert.equal(chatState.isSubmitting, false);
              assert.notEqual(chatState.activeRequest.clientRequestId, "client-1");
              assert.equal(chatState.activeRequest.runtimeRequestId, "opencode-session-sess-1");
              assert.equal(chatState.inflightThinking.contextSource, "opencode_session_state");
              assert.ok(calls.some((item) => item[0] === "stopReconcile" && item[1] === "client-1"));
              assert.ok(calls.some((item) => item[0] === "events" && item[3] === "opencode-session-sess-1"));
              assert.ok(calls.some((item) => item[0] === "reconcile" && item[2] === "opencode-session-sess-1" && item[3] === true));
              assert.ok(calls.some((item) => item[0] === "event" && item[1] === "portal.chat_run_already_active"));
              assert.ok(calls.some((item) => item[0] === "status" && item[1].includes("Previous message still running")));
            })();
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_chat_run_already_active_removes_only_rejected_optimistic_user_row_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(
            [
                _extract_js_function(src, "normalizeChatRunStatus"),
                _extract_js_function(src, "isRuntimeRunActuallyActive"),
                _extract_js_function(src, "hydrateActiveRequestFromRun"),
                _extract_js_function(src, "cssEscapeForSelector"),
                _extract_js_function(src, "removeOptimisticUserRowForRequest"),
                _extract_js_function(src, "handleChatRunAlreadyActive"),
            ]
        )
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const calls = [];
            const requestCtx = {
              clientRequestId: "rejected-1",
              requestId: "rejected-1",
              sessionIdAtSend: "sess-1",
              backupMessage: "please keep this",
              backupPendingFiles: [],
            };
            const persistedRow = { removed: false, remove() { this.removed = true; } };
            const persistedArticle = {
              dataset: { messageId: "m_old", persisted: "1" },
              closest(selector) { return selector === ".message-row" ? persistedRow : null; },
              remove() { this.removed = true; },
            };
            const localRow = { removed: false, remove() { this.removed = true; } };
            const localArticle = {
              dataset: { localUser: "1", clientRequestId: "rejected-1" },
              closest(selector) { return selector === ".message-row" ? localRow : null; },
              remove() { this.removed = true; },
            };
            const chatState = {
              activeRequest: requestCtx,
              isSubmitting: true,
              inflightThinking: { completed: false },
              pendingFiles: [],
              sessionId: "sess-1",
            };
            const state = { selectedAgentId: "agent-1" };
            const dom = {
              chatInput: { value: "" },
              messageList: {
                querySelector(selector) {
                  calls.push(["query", selector]);
                  return selector.includes("rejected-1") ? localArticle : null;
                },
                querySelectorAll(selector) {
                  calls.push(["queryAll", selector]);
                  return selector.includes("data-local-user") ? [localArticle] : [];
                },
              },
            };

            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }
            function removeTemporaryAssistantRows(options) { calls.push(["removeAssistant", options.requestId, options.onlyEmpty]); }
            function removeLatestOptimisticUserRow() { calls.push(["fallbackRemoveUser"]); }
            function syncChatInputHeight() {}
            function renderInputPreview() {}
            function setChatSubmittingForAgent(_agentId, active) { chatState.isSubmitting = active; }
            function stopChatRunReconcileLoop(ctx) { calls.push(["stopReconcile", ctx.clientRequestId]); }
            function ensureEventSocketForAgent(agentId, sessionId, requestId) { calls.push(["events", agentId, sessionId, requestId]); }
            function startChatRunReconcileLoop(agentId, ctx, options) { calls.push(["reconcile", agentId, ctx.runtimeRequestId, options?.immediate]); }
            function appendPortalChatRuntimeEvent(agentId, ctx, type) { calls.push(["event", type]); }
            function setChatStatus(message) { calls.push(["status", message]); }
            function showToast(message) { calls.push(["toast", message]); }
            function syncSelectedAgentChatActionControls() {}
            async function preflightActiveRunForSession() { throw new Error("active run should be handled from payload"); }

            (async () => {
              await handleChatRunAlreadyActive("agent-1", requestCtx, {
                error: "chat_run_already_active",
                active_run: {
                  request_id: "old-runtime-1",
                  opencode_active: true,
                  source_of_truth: "opencode",
                  status: "running",
                },
              });

              assert.equal(localRow.removed, true);
              assert.equal(persistedRow.removed, false);
              assert.equal(persistedArticle.dataset.messageId, "m_old");
              assert.equal(calls.some((item) => item[0] === "fallbackRemoveUser"), false);
              assert.equal(dom.chatInput.value, "please keep this");
              assert.equal(chatState.activeRequest.runtimeRequestId, "old-runtime-1");
            })();
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_chat_run_already_active_fallback_409_json_is_not_generic_failure_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(
            [
                _extract_js_function(src, "isActiveRequestBlocking"),
                _extract_js_function(src, "hasIncompleteInflightThinking"),
                _extract_js_function(src, "normalizeChatRunStatus"),
                _extract_js_function(src, "normalizeOpenCodeSessionStatusType"),
                _extract_js_function(src, "isOpenCodeSessionInactivePayload"),
                _extract_js_function(src, "buildOpenCodeInactiveSessionStatusPayload"),
                _extract_js_function(src, "isOpenCodeSessionStatusBlockingPayload"),
                _extract_js_function(src, "isOpenCodeSessionBlocking"),
                _extract_js_function(src, "requestContextIdCandidates"),
                _extract_js_function(src, "activeRequestMatchesRequestContext"),
                _extract_js_function(src, "fallbackRequestContextForAgent"),
                _extract_js_function(src, "clearStaleActiveRequest"),
                _extract_js_function(src, "markOpenCodeProjectionInactive"),
                _extract_js_function(src, "hasActiveChatRequestForAgent"),
                _extract_js_function(src, "guardNoActiveChatRequestForAgent"),
                _extract_js_function(src, "isRuntimeRunActuallyActive"),
                _extract_js_function(src, "getActiveRunFromPayload"),
                _extract_js_function(src, "hydrateActiveRequestFromRun"),
                _extract_js_function(src, "preflightActiveRunForSession"),
                _extract_js_function(src, "cssEscapeForSelector"),
                _extract_js_function(src, "removeOptimisticUserRowForRequest"),
                _extract_js_function(src, "isChatRunAlreadyActivePayload"),
                _extract_js_function(src, "handleChatRunAlreadyActive"),
                _extract_js_function(src, "submitChatForSelectedAgent"),
            ]
        )
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const calls = [];
            const inserted = [];
            let rejectedClientRequestId = "";
            const chatState = {
              activeRequest: null,
              isSubmitting: false,
              inflightThinking: null,
              pendingFiles: [],
              sessionId: "sess-1",
              modelOverride: "",
              profileDefaultModel: "",
            };
            const state = {
              selectedAgentId: "agent-1",
              chatStatesByAgent: new Map([["agent-1", chatState]]),
              agentSessionIds: new Map([["agent-1", "sess-1"]]),
            };
            const dom = {
              chatInput: { value: "send after current run" },
              messageList: {
                insertAdjacentHTML(_where, html) { inserted.push(html); },
                querySelector() { return null; },
                querySelectorAll() { return []; },
              },
              sendChatBtn: { disabled: false },
              abortChatRunBtn: { classList: { toggle() {}, add() {}, remove() {} }, setAttribute() {}, disabled: true },
              headerNewChatBtn: {},
              homeStartChatBtn: {},
              chatModelSelect: null,
            };
            const document = {
              hidden: false,
              getElementById(id) {
                if (id === "btn-sessions") return {};
                if (id === "chat-attachments") return { value: "" };
                if (id === "chat-session-id") return { value: "sess-1" };
                return null;
              }
            };
            globalThis.crypto = { randomUUID() { return "client-1"; } };

            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }
            function buildAttachmentsFromChatState() { return []; }
            function ensureChatSessionId() { return "sess-1"; }
            async function agentApiFor(agentId, path) {
              calls.push(["api", agentId, path]);
              return { active_run: null };
            }
            function maybeRequestNotificationPermission() {}
            function parseSkillSlashInput() { return null; }
            function findCachedSkillForSlash() { return null; }
            function removeWelcomeMessageIfPresent() {}
            function removeTemporaryAssistantRows(options) { calls.push(["removeAssistant", options.requestId, options.onlyEmpty]); }
            function removeLatestOptimisticUserRow() { calls.push(["removeUser"]); }
            function hideSuggest() {}
            function buildUserMessageArticle(_message, _attachments, options) {
              rejectedClientRequestId = options?.clientRequestId || "";
              return "<user-row>";
            }
            function buildPendingAssistantArticle() { return "<assistant-row>"; }
            function ensureEventSocketForAgent(agentId, sessionId, requestId) { calls.push(["events", agentId, sessionId, requestId]); }
            function isThinkingPanelActiveForAgent() { return false; }
            function scrollToBottom() {}
            function renderInputPreview() { calls.push(["renderInput"]); }
            function resetChatInputHeight() { calls.push(["resetHeight"]); }
            function syncChatInputHeight() { calls.push(["syncHeight"]); }
            function setChatStatus(message, isError) { calls.push(["status", message, isError]); }
            function showToast(message) { calls.push(["toast", message]); }
            function syncSelectedAgentChatActionControls() { dom.sendChatBtn.disabled = hasActiveChatRequestForAgent("agent-1"); }
            function setChatSubmittingForAgent(_agentId, active) {
              chatState.isSubmitting = active;
              syncSelectedAgentChatActionControls();
            }
            function stopChatRunReconcileLoop(ctx) { calls.push(["stopReconcile", ctx.clientRequestId]); }
            function activeChatRequestMessage() { return "busy"; }
            async function trySubmitChatStreamForSelectedAgent() {
              calls.push(["streamUnsupported"]);
              return "unsupported";
            }
            async function fetch(url) {
              calls.push(["fetch", url]);
              assert.equal(url, "/a/agent-1/api/chat");
              return {
                ok: false,
                status: 409,
                clone() {
                  return {
                    async json() {
                      calls.push(["cloneJson"]);
                      return {
                        error: "chat_run_already_active",
                        active_run: {
                          request_id: "runtime-409",
                          opencode_active: true,
                          status: "busy",
                        },
                      };
                    },
                  };
                },
              };
            }
            async function handleErrorResponse() { calls.push(["genericError"]); return "Send failed"; }
            function handleAgentChatFailure() { calls.push(["failure"]); }
            function appendPortalChatRuntimeEvent(agentId, ctx, type) { calls.push(["event", type]); }
            function startChatRunReconcileLoop(agentId, ctx, options) { calls.push(["reconcile", agentId, ctx.runtimeRequestId, options?.immediate]); }

            (async () => {
              await submitChatForSelectedAgent();
              assert.equal(inserted.length, 2);
              assert.equal(calls.some((item) => item[0] === "streamUnsupported"), true);
              assert.equal(calls.some((item) => item[0] === "cloneJson"), true);
              assert.equal(calls.some((item) => item[0] === "genericError"), false);
              assert.equal(calls.some((item) => item[0] === "failure"), false);
              assert.ok(calls.some((item) => item[0] === "removeAssistant" && item[2] === false));
              assert.ok(calls.some((item) => item[0] === "removeUser"));
              assert.equal(dom.chatInput.value, "send after current run");
              assert.ok(rejectedClientRequestId);
              assert.notEqual(chatState.activeRequest.clientRequestId, rejectedClientRequestId);
              assert.equal(chatState.activeRequest.runtimeRequestId, "runtime-409");
              assert.ok(calls.some((item) => item[0] === "stopReconcile" && item[1] === rejectedClientRequestId));
              assert.ok(calls.some((item) => item[0] === "reconcile" && item[3] === true));
              assert.ok(!calls.some((item) => item[0] === "status" && String(item[1]).includes("Send failed")));
            })();
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_missing_final_detaches_and_starts_reconcile_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    stream_js = "\n".join(
        [
            _extract_js_function(src, "handleChatStreamMissingFinal"),
            _extract_js_function(src, "handleChatStreamDetached"),
        ]
    )
    script = (
        stream_js
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const chatState = {
              activeRequest: null,
              sessionId: "sess-1",
              inflightThinking: { events: [], completed: false },
            };
            const state = { selectedAgentId: "agent-1" };
            const calls = [];

            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }
            function clearWaitingForRuntimeEventsTimer(ctx) { calls.push(["clear", ctx.clientRequestId]); }
            function cancelAssistantTypewriter(ctx) { calls.push(["cancel", ctx.clientRequestId]); }
            function setChatSubmittingForAgent(agentId, active) { calls.push(["submitting", agentId, active]); }
            function setChatStatus(message) { calls.push(["status", message]); }
            function appendPortalChatRuntimeEvent(agentId, ctx, type, data) { calls.push(["event", agentId, type, data.reason]); }
            function ensureEventSocketForAgent(agentId, sessionId, requestId) { calls.push(["events", agentId, sessionId, requestId]); }
            function startChatRunReconcileLoop(agentId, ctx, options) { calls.push(["reconcile", agentId, ctx.clientRequestId, options.immediate]); }
            async function handleIncompleteChatStream() { throw new Error("should not mark incomplete"); }

            (async () => {
              const requestCtx = {
                clientRequestId: "client-1",
                requestId: "client-1",
                sessionIdAtSend: "sess-1",
                streamCompleted: false,
                streamFailed: false,
                streamIncomplete: false,
              };
              chatState.activeRequest = requestCtx;
              const result = await handleChatStreamMissingFinal("agent-1", requestCtx);

              assert.equal(result, "detached");
              assert.equal(requestCtx.streamDetached, true);
              assert.equal(requestCtx.streamIncomplete, false);
              assert.equal(requestCtx.streamFailed, false);
              assert.deepEqual(calls.find((item) => item[0] === "status"), ["status", "Still running. Reconnecting…"]);
              assert.deepEqual(calls.find((item) => item[0] === "event").slice(0, 3), ["event", "agent-1", "portal.stream_detached"]);
              assert.deepEqual(calls.find((item) => item[0] === "reconcile"), ["reconcile", "agent-1", "client-1", true]);
              assert.equal(chatState.activeRequest, requestCtx);
            })();
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def _active_run_reconcile_js(src: str) -> str:
    return "\n".join(
        [
            _extract_js_function(src, "isActiveRequestBlocking"),
            _extract_js_function(src, "hasIncompleteInflightThinking"),
            _extract_js_function(src, "normalizeChatRunStatus"),
            _extract_js_function(src, "normalizeOpenCodeSessionStatusType"),
            _extract_js_function(src, "isOpenCodeSessionInactivePayload"),
            _extract_js_function(src, "buildOpenCodeInactiveSessionStatusPayload"),
            _extract_js_function(src, "isOpenCodeSessionStatusBlockingPayload"),
            _extract_js_function(src, "isOpenCodeSessionBlocking"),
            _extract_js_function(src, "hasActiveChatRequestForAgent"),
            _extract_js_function(src, "isChatRunRunningStatus"),
            _extract_js_function(src, "isRuntimeRunActuallyActive"),
            _extract_js_function(src, "isValidatedRuntimeActiveRun"),
            _extract_js_function(src, "isChatRunCompletedStatus"),
            _extract_js_function(src, "isChatRunNonSuccessStatus"),
            _extract_js_function(src, "isUnsupportedRunLookupError"),
            _extract_js_function(src, "getChatRunObject"),
            _extract_js_function(src, "isNullOrStaleRunPayload"),
            _extract_js_function(src, "getActiveRunFromPayload"),
            _extract_js_function(src, "runPayloadHasTerminalStatus"),
            _extract_js_function(src, "requestContextIdCandidates"),
            _extract_js_function(src, "activeRequestMatchesRequestContext"),
            _extract_js_function(src, "fallbackRequestContextForAgent"),
            _extract_js_function(src, "clearStaleActiveRequest"),
            _extract_js_function(src, "markOpenCodeProjectionInactive"),
            _extract_js_function(src, "applySessionProjectionThenClearStaleRun"),
            _extract_js_function(src, "reconcileChatRunOnce"),
        ]
    )


def test_reconcile_active_run_null_clears_busy_state_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        _active_run_reconcile_js(src)
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const requestCtx = {
              clientRequestId: "client-1",
              requestId: "runtime-1",
              runtimeRequestId: "runtime-1",
              sessionIdAtSend: "sess-1",
            };
            const chatState = {
              activeRequest: requestCtx,
              isSubmitting: true,
              sessionId: "sess-1",
              inflightThinking: { id: "client-1", requestId: "client-1", sessionId: "sess-1", completed: false },
            };
            const dom = {
              sendChatBtn: { disabled: true },
              abortChatRunBtn: { classList: { toggle() {}, add() {} }, setAttribute() {}, disabled: false },
            };
            const state = { selectedAgentId: "agent-1" };
            const calls = [];

            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }
            function syncSelectedAgentChatActionControls() {
              dom.sendChatBtn.disabled = hasActiveChatRequestForAgent("agent-1");
            }
            function setChatSubmittingForAgent(agentId, active) {
              assert.equal(agentId, "agent-1");
              chatState.isSubmitting = active;
              syncSelectedAgentChatActionControls();
            }
            function clearWaitingForRuntimeEventsTimer() {}
            function cancelAssistantTypewriter() {}
            function stopChatRunReconcileLoop(ctx) { ctx.reconcileStopped = true; }
            function appendPortalChatRuntimeEvent(agentId, ctx, type) { calls.push(["event", type]); }
            function setChatStatus(message) { calls.push(["status", message]); }
            function buildChatRunProjection() { return { status: "", run: {}, activeRun: null, text: "", displayBlocks: [] }; }
            async function applyChatRunProjection() { return "running"; }
            async function agentApiFor(agentId, path) {
              calls.push(["api", path]);
              if (path.includes("/api/chat/runs/")) return { run: null };
              if (path.includes("/active-run")) return { run: null };
              if (path.includes("/api/sessions/")) return { metadata: { active_run: null }, messages: [] };
              throw new Error("unexpected path " + path);
            }

            (async () => {
              const result = await reconcileChatRunOnce("agent-1", requestCtx);
              assert.equal(result, "terminal");
              assert.equal(chatState.activeRequest, null);
              assert.equal(chatState.inflightThinking.completed, true);
              assert.equal(chatState.inflightThinking.stale, true);
              assert.equal(dom.sendChatBtn.disabled, false);
              assert.equal(hasActiveChatRequestForAgent("agent-1"), false);
              assert.ok(calls.some((item) => item[0] === "api" && item[1].includes("?validate=opencode")));
              assert.ok(calls.some((item) => item[0] === "api" && item[1].endsWith("/api/sessions/sess-1/active-run")));
            })();
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_reconcile_stream_detached_without_opencode_active_releases_busy_lock_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        _active_run_reconcile_js(src)
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const requestCtx = {
              clientRequestId: "client-1",
              requestId: "runtime-1",
              runtimeRequestId: "runtime-1",
              sessionIdAtSend: "sess-1",
              streamDetached: true,
            };
            const chatState = {
              activeRequest: requestCtx,
              isSubmitting: false,
              sessionId: "sess-1",
              inflightThinking: { id: "client-1", requestId: "client-1", sessionId: "sess-1", completed: false },
            };
            const dom = { sendChatBtn: { disabled: true }, abortChatRunBtn: { classList: { toggle() {}, add() {} }, setAttribute() {}, disabled: false } };
            const state = { selectedAgentId: "agent-1" };
            const calls = [];

            function ensureChatState() { return chatState; }
            function syncSelectedAgentChatActionControls() { dom.sendChatBtn.disabled = hasActiveChatRequestForAgent("agent-1"); }
            function setChatSubmittingForAgent(_agentId, active) { chatState.isSubmitting = active; syncSelectedAgentChatActionControls(); }
            function clearWaitingForRuntimeEventsTimer() {}
            function cancelAssistantTypewriter() {}
            function stopChatRunReconcileLoop(ctx) { ctx.reconcileStopped = true; }
            function appendPortalChatRuntimeEvent(agentId, ctx, type) { calls.push(["event", type]); }
            function setChatStatus(message) { calls.push(["status", message]); }
            function buildChatRunProjection() { return { status: "", run: {}, activeRun: null, text: "", displayBlocks: [] }; }
            async function applyChatRunProjection() { return "running"; }
            async function agentApiFor(agentId, path) {
              calls.push(["api", path]);
              if (path.includes("/api/chat/runs/")) {
                return { run: { request_id: "runtime-1", status: "stream_detached", opencode_active: false, source_of_truth: "opencode" } };
              }
              if (path.includes("/active-run")) return { run: null };
              if (path.includes("/api/sessions/")) return { metadata: { active_run: null }, messages: [] };
              throw new Error("unexpected path " + path);
            }

            (async () => {
              const result = await reconcileChatRunOnce("agent-1", requestCtx);
              assert.equal(result, "terminal");
              assert.equal(chatState.activeRequest, null);
              assert.equal(dom.sendChatBtn.disabled, false);
              assert.equal(hasActiveChatRequestForAgent("agent-1"), false);
              assert.ok(calls.some((item) => item[0] === "event" && item[1] === "chat.run.stale"));
              assert.ok(calls.some((item) => item[0] === "event" && item[1] === "opencode.status.inactive"));
            })();
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_abort_active_chat_request_clears_active_request_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(
            [
                _extract_js_function(src, "isActiveRequestBlocking"),
                _extract_js_function(src, "hasIncompleteInflightThinking"),
                _extract_js_function(src, "normalizeChatRunStatus"),
                _extract_js_function(src, "normalizeOpenCodeSessionStatusType"),
                _extract_js_function(src, "isOpenCodeSessionInactivePayload"),
                _extract_js_function(src, "buildOpenCodeInactiveSessionStatusPayload"),
                _extract_js_function(src, "isOpenCodeSessionStatusBlockingPayload"),
                _extract_js_function(src, "isOpenCodeSessionBlocking"),
                _extract_js_function(src, "hasActiveChatRequestForAgent"),
                _extract_js_function(src, "requestContextIdCandidates"),
                _extract_js_function(src, "activeRequestMatchesRequestContext"),
                _extract_js_function(src, "fallbackRequestContextForAgent"),
                _extract_js_function(src, "clearStaleActiveRequest"),
                _extract_js_function(src, "runtimeAbortSucceeded"),
                _extract_js_function(src, "runtimeAbortIndicatesInactive"),
                _extract_js_function(src, "abortSessionForAgent"),
                _extract_js_function(src, "handleSessionAbortSuccess"),
                _extract_js_function(src, "abortActiveChatRequestForSelectedAgent"),
            ]
        )
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const requestCtx = {
              clientRequestId: "client-1",
              requestId: "runtime-1",
              runtimeRequestId: "runtime-1",
              sessionIdAtSend: "sess-1",
            };
            const chatState = {
              activeRequest: requestCtx,
              isSubmitting: false,
              sessionId: "sess-1",
              inflightThinking: { id: "client-1", requestId: "client-1", sessionId: "sess-1", completed: false },
              openCodeProjection: {
                sessionStatus: "busy",
                sessionStatusPayload: { active: true, status_type: "busy", action_hint: "wait_reconnect_or_stop" },
              },
            };
            const state = { selectedAgentId: "agent-1" };
            const calls = [];

            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }
            function setChatStatus(message) { calls.push(["status", message]); }
            function appendPortalChatRuntimeEvent(agentId, ctx, type) { calls.push(["event", type]); }
            function clearWaitingForRuntimeEventsTimer() {}
            function cancelAssistantTypewriter() {}
            function stopChatRunReconcileLoop(ctx) { ctx.reconcileStopped = true; }
            function syncSelectedAgentChatActionControls() {}
            function setChatSubmittingForAgent(_agentId, active) { chatState.isSubmitting = active; }
            function showToast(message) { calls.push(["toast", message]); }
            async function loadSessionForAgent(agentId, sessionId, options) { calls.push(["load", agentId, sessionId, options.render]); }
            async function fetch(url, options) {
              calls.push(["fetch", url, options.method, options.body]);
              return {
                ok: true,
                status: 200,
                async json() {
                  return { success: true, active: false, action_hint: "safe_to_send", status: { type: "idle" }, abort_result: { success: true } };
                },
              };
            }

            (async () => {
              await abortActiveChatRequestForSelectedAgent();
              assert.equal(chatState.activeRequest, null);
              assert.equal(chatState.inflightThinking.completed, true);
              assert.equal(chatState.inflightThinking.stale, true);
              assert.ok(calls.some((item) => item[0] === "fetch" && item[1] === "/a/agent-1/api/sessions/sess-1/abort" && item[2] === "POST"));
              assert.ok(calls.some((item) => item[0] === "fetch" && item[3] === JSON.stringify({ force_detach: true })));
              assert.ok(calls.some((item) => item[0] === "event" && item[1] === "portal.abort.completed"));
              assert.ok(calls.some((item) => item[0] === "toast" && item[1] === "Stopped current run."));
            })();
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_abort_still_active_triggers_hard_reset_once_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(
            [
                _extract_js_function(src, "requestContextIdCandidates"),
                _extract_js_function(src, "activeRequestMatchesRequestContext"),
                _extract_js_function(src, "fallbackRequestContextForAgent"),
                _extract_js_function(src, "clearStaleActiveRequest"),
                _extract_js_function(src, "normalizeChatRunStatus"),
                _extract_js_function(src, "buildOpenCodeInactiveSessionStatusPayload"),
                _extract_js_function(src, "runtimeAbortSucceeded"),
                _extract_js_function(src, "runtimeAbortIndicatesInactive"),
                _extract_js_function(src, "handleSessionAbortSuccess"),
            ]
        )
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const requestCtx = {
              clientRequestId: "client-1",
              requestId: "runtime-1",
              runtimeRequestId: "runtime-1",
              sessionIdAtSend: "sess-1",
            };
            const chatState = {
              activeRequest: requestCtx,
              isSubmitting: false,
              sessionId: "sess-1",
              inflightThinking: { id: "client-1", requestId: "client-1", sessionId: "sess-1", completed: false },
              openCodeProjection: {
                sessionStatus: "busy",
                sessionStatusPayload: { source_of_truth: "opencode", active: true, status_type: "busy" },
              },
            };
            const state = { selectedAgentId: "agent-1" };
            const calls = [];
            let hardResetCalls = 0;

            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }
            function setChatStatus(message) { calls.push(["status", message]); }
            function appendPortalChatRuntimeEvent(agentId, ctx, type) { calls.push(["event", type]); }
            function clearWaitingForRuntimeEventsTimer() {}
            function cancelAssistantTypewriter() {}
            function stopChatRunReconcileLoop(ctx) { ctx.reconcileStopped = true; calls.push(["stopReconcile", ctx.runtimeRequestId]); }
            function startChatRunReconcileLoop(agentId, ctx, options) { calls.push(["reconcile", ctx.runtimeRequestId, options?.immediate]); }
            function syncSelectedAgentChatActionControls() { calls.push(["syncControls"]); }
            function setChatSubmittingForAgent(_agentId, active) { chatState.isSubmitting = active; }
            function showToast(message) { calls.push(["toast", message]); }
            async function loadSessionForAgent(agentId, sessionId, options) { calls.push(["load", agentId, sessionId, options.render]); }
            async function hardResetSessionForAgent(agentId, sessionId) {
              hardResetCalls += 1;
              assert.equal(agentId, "agent-1");
              assert.equal(sessionId, "sess-1");
              return {
                success: true,
                detached_old_session: true,
                old_opencode_session_id: "old-opencode",
                opencode_session_id: "new-opencode",
                active: false,
                action_hint: "safe_to_send",
                status: { type: "idle" },
              };
            }

            (async () => {
              const result = await handleSessionAbortSuccess("agent-1", chatState, requestCtx, "sess-1", {
                success: false,
                error: "opencode_abort_still_active",
                active: true,
                action_hint: "hard_reset_or_new_session",
                can_hard_reset: true,
              });
              assert.equal(result, "inactive");
              assert.equal(hardResetCalls, 1);
              assert.equal(requestCtx.hardResetAttempted, true);
              assert.equal(chatState.activeRequest, null);
              assert.equal(chatState.inflightThinking.completed, true);
              assert.equal(chatState.inflightThinking.stale, true);
              assert.equal(chatState.openCodeProjection.sessionStatus, "idle");
              assert.equal(chatState.openCodeProjection.sessionStatusPayload.active, false);
              assert.ok(calls.some((item) => item[0] === "event" && item[1] === "portal.abort.still_active"));
              assert.ok(calls.some((item) => item[0] === "event" && item[1] === "portal.abort.detached_old_opencode_session"));
              assert.ok(calls.some((item) => item[0] === "load" && item[2] === "sess-1"));
              assert.ok(calls.some((item) => item[0] === "status" && item[1] === "Stopped old run and reset session."));
            })();
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_abort_hard_reset_failure_does_not_retry_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(
            [
                _extract_js_function(src, "requestContextIdCandidates"),
                _extract_js_function(src, "activeRequestMatchesRequestContext"),
                _extract_js_function(src, "fallbackRequestContextForAgent"),
                _extract_js_function(src, "clearStaleActiveRequest"),
                _extract_js_function(src, "normalizeChatRunStatus"),
                _extract_js_function(src, "runtimeAbortSucceeded"),
                _extract_js_function(src, "runtimeAbortIndicatesInactive"),
                _extract_js_function(src, "handleSessionAbortSuccess"),
            ]
        )
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const requestCtx = {
              clientRequestId: "client-1",
              requestId: "runtime-1",
              runtimeRequestId: "runtime-1",
              sessionIdAtSend: "sess-1",
            };
            const chatState = {
              activeRequest: requestCtx,
              isSubmitting: false,
              sessionId: "sess-1",
              inflightThinking: { id: "client-1", requestId: "client-1", sessionId: "sess-1", completed: false },
              openCodeProjection: {
                sessionStatus: "busy",
                sessionStatusPayload: { source_of_truth: "opencode", active: true, status_type: "busy" },
              },
            };
            const state = { selectedAgentId: "agent-1" };
            const calls = [];
            let hardResetCalls = 0;
            const stillActiveResult = {
              success: false,
              error: "opencode_abort_still_active",
              active: true,
              action_hint: "hard_reset_or_new_session",
              can_hard_reset: true,
            };

            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }
            function setChatStatus(message) { calls.push(["status", message]); }
            function appendPortalChatRuntimeEvent(agentId, ctx, type, data) { calls.push(["event", type, data]); }
            function clearWaitingForRuntimeEventsTimer() {}
            function cancelAssistantTypewriter() {}
            function stopChatRunReconcileLoop(ctx) { ctx.reconcileStopped = true; }
            function startChatRunReconcileLoop(agentId, ctx, options) { calls.push(["reconcile", ctx.runtimeRequestId, options?.immediate]); }
            function syncSelectedAgentChatActionControls() { calls.push(["syncControls"]); }
            function setChatSubmittingForAgent(_agentId, active) { chatState.isSubmitting = active; }
            function showToast(message) { calls.push(["toast", message]); }
            async function loadSessionForAgent() { throw new Error("failed reset must not load a fake idle session"); }
            async function hardResetSessionForAgent() {
              hardResetCalls += 1;
              throw new Error("reset unavailable");
            }

            (async () => {
              const first = await handleSessionAbortSuccess("agent-1", chatState, requestCtx, "sess-1", stillActiveResult);
              const second = await handleSessionAbortSuccess("agent-1", chatState, requestCtx, "sess-1", stillActiveResult);
              assert.equal(first, "failed");
              assert.equal(second, "still_active");
              assert.equal(hardResetCalls, 1);
              assert.equal(requestCtx.hardResetAttempted, true);
              assert.equal(chatState.activeRequest, requestCtx);
              assert.equal(chatState.openCodeProjection.sessionStatus, "busy");
              assert.equal(chatState.openCodeProjection.sessionStatusPayload.active, true);
              assert.ok(calls.some((item) => item[0] === "status" && item[1] === "Unable to reset session. Start a new chat."));
              assert.ok(calls.some((item) => item[0] === "toast" && item[1] === "Unable to reset session. Start a new chat."));
              assert.ok(calls.some((item) => item[0] === "reconcile" && item[2] === true));
            })();
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_abort_synthetic_opencode_session_run_uses_session_abort_endpoint_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(
            [
                _extract_js_function(src, "normalizeOpenCodeSessionStatusType"),
                _extract_js_function(src, "isOpenCodeSessionInactivePayload"),
                _extract_js_function(src, "buildOpenCodeInactiveSessionStatusPayload"),
                _extract_js_function(src, "isOpenCodeSessionStatusBlockingPayload"),
                _extract_js_function(src, "isOpenCodeSessionBlocking"),
                _extract_js_function(src, "isSyntheticOpenCodeSessionRequest"),
                _extract_js_function(src, "normalizeChatRunStatus"),
                _extract_js_function(src, "requestContextIdCandidates"),
                _extract_js_function(src, "activeRequestMatchesRequestContext"),
                _extract_js_function(src, "fallbackRequestContextForAgent"),
                _extract_js_function(src, "clearStaleActiveRequest"),
                _extract_js_function(src, "runtimeAbortSucceeded"),
                _extract_js_function(src, "runtimeAbortIndicatesInactive"),
                _extract_js_function(src, "abortSessionForAgent"),
                _extract_js_function(src, "handleSessionAbortSuccess"),
                _extract_js_function(src, "abortActiveChatRequestForSelectedAgent"),
            ]
        )
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const requestCtx = {
              clientRequestId: "opencode-session-op_123",
              requestId: "opencode-session-op_123",
              runtimeRequestId: "opencode-session-op_123",
              sessionIdAtSend: "portal-session-1",
              fromSessionStatus: true,
            };
            const chatState = {
              activeRequest: requestCtx,
              isSubmitting: false,
              sessionId: "portal-session-1",
              inflightThinking: { id: "opencode-session-op_123", requestId: "opencode-session-op_123", sessionId: "portal-session-1", completed: false },
              openCodeProjection: {
                sessionStatus: "busy",
                sessionStatusPayload: { active: true, status_type: "busy", active_run: { request_id: "opencode-session-op_123" } },
              },
            };
            const state = { selectedAgentId: "agent-1" };
            const dom = { abortChatRunBtn: { disabled: false } };
            const calls = [];

            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }
            function setChatStatus(message) { calls.push(["status", message]); }
            function appendPortalChatRuntimeEvent(agentId, ctx, type) { calls.push(["event", type]); }
            function clearWaitingForRuntimeEventsTimer() {}
            function cancelAssistantTypewriter() {}
            function stopChatRunReconcileLoop(ctx) { ctx.reconcileStopped = true; calls.push(["stopReconcile", ctx.runtimeRequestId]); }
            function syncSelectedAgentChatActionControls() { calls.push(["syncControls"]); }
            function setChatSubmittingForAgent(_agentId, active) { chatState.isSubmitting = active; }
            function showToast(message) { calls.push(["toast", message]); }
            async function loadSessionForAgent(agentId, sessionId, options) { calls.push(["load", agentId, sessionId, options.render]); }
            async function agentApiFor() { throw new Error("request abort endpoint should not be used"); }
            async function fetch(url, options) {
              calls.push(["fetch", url, options.method]);
              assert.equal(url, "/a/agent-1/api/sessions/portal-session-1/abort");
              return {
                ok: true,
                status: 200,
                async json() { return { success: true, active: false, action_hint: "safe_to_send", status: { type: "idle" }, abort_result: { success: true } }; },
              };
            }

            (async () => {
              await abortActiveChatRequestForSelectedAgent();
              assert.equal(calls.some((item) => item[0] === "fetch" && item[1].includes("/api/chat/runs/")), false);
              assert.ok(calls.some((item) => item[0] === "fetch" && item[1] === "/a/agent-1/api/sessions/portal-session-1/abort" && item[2] === "POST"));
              assert.ok(calls.some((item) => item[0] === "syncControls"));
              assert.equal(chatState.activeRequest, null);
              assert.equal(chatState.inflightThinking.completed, true);
              assert.equal(chatState.inflightThinking.stale, true);
              assert.equal(chatState.openCodeProjection.sessionStatus, "idle");
              assert.equal(chatState.openCodeProjection.sessionStatusPayload.active, false);
              assert.equal(chatState.openCodeProjection.sessionStatusPayload.active_run, null);
              assert.ok(calls.some((item) => item[0] === "load" && item[2] === "portal-session-1"));
            })();
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_abort_active_chat_request_failure_keeps_active_request_node_smoke():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(
            [
                _extract_js_function(src, "normalizeChatRunStatus"),
                _extract_js_function(src, "normalizeOpenCodeSessionStatusType"),
                _extract_js_function(src, "isOpenCodeSessionInactivePayload"),
                _extract_js_function(src, "isOpenCodeSessionStatusBlockingPayload"),
                _extract_js_function(src, "isOpenCodeSessionBlocking"),
                _extract_js_function(src, "runtimeAbortSucceeded"),
                _extract_js_function(src, "runtimeAbortIndicatesInactive"),
                _extract_js_function(src, "abortSessionForAgent"),
                _extract_js_function(src, "abortActiveChatRequestForSelectedAgent"),
            ]
        )
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            const requestCtx = {
              clientRequestId: "client-1",
              requestId: "runtime-1",
              runtimeRequestId: "runtime-1",
              sessionIdAtSend: "sess-1",
            };
            const chatState = {
              activeRequest: requestCtx,
              isSubmitting: false,
              sessionId: "sess-1",
              inflightThinking: { id: "client-1", requestId: "client-1", sessionId: "sess-1", completed: false },
              openCodeProjection: {
                sessionStatus: "busy",
                sessionStatusPayload: { active: true, status_type: "busy", action_hint: "wait_reconnect_or_stop" },
              },
            };
            const state = { selectedAgentId: "agent-1" };
            const calls = [];

            function ensureChatState(agentId) {
              assert.equal(agentId, "agent-1");
              return chatState;
            }
            function setChatStatus(message, isError) { calls.push(["status", message, isError]); }
            function appendPortalChatRuntimeEvent(agentId, ctx, type, data) { calls.push(["event", type, data]); }
            function syncSelectedAgentChatActionControls() { calls.push(["syncControls"]); }
            function startChatRunReconcileLoop(agentId, ctx, options) { calls.push(["reconcile", agentId, ctx.clientRequestId, options?.immediate]); }
            function showToast(message) { calls.push(["toast", message]); }
            async function fetch(url, options) {
              calls.push(["fetch", url, options.method]);
              return {
                ok: false,
                status: 409,
                async json() { return { success: false, error: "opencode_abort_failed" }; },
              };
            }

            (async () => {
              await abortActiveChatRequestForSelectedAgent();
              assert.equal(chatState.activeRequest, requestCtx);
              assert.equal(chatState.inflightThinking.completed, false);
              assert.ok(calls.some((item) => item[0] === "fetch" && item[1] === "/a/agent-1/api/sessions/sess-1/abort" && item[2] === "POST"));
              assert.ok(calls.some((item) => item[0] === "event" && item[1] === "portal.abort.failed"));
              assert.ok(calls.some((item) => item[0] === "status" && item[1] === "Unable to stop current run." && item[2] === true));
              assert.ok(calls.some((item) => item[0] === "toast" && item[1].startsWith("Unable to stop current run:")));
              assert.ok(calls.some((item) => item[0] === "reconcile" && item[1] === "agent-1" && item[3] === true));
              assert.ok(!calls.some((item) => item[0] === "event" && item[1] === "portal.abort.completed"));
              assert.ok(!calls.some((item) => item[0] === "toast" && item[1] === "Stopped current run."));
            })();
            """
        )
    )

    result = subprocess.run(["node", "-e", script], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
