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
            assert.match(busy, /previous OpenCode run is still active/i);
            assert.match(busy, /Stop run/);
            assert.equal(busy.includes('send "continue"'), false);

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
                _extract_js_function(src, "hasActiveChatRequestForAgent"),
                _extract_js_function(src, "guardNoActiveChatRequestForAgent"),
                _extract_js_function(src, "normalizeChatRunStatus"),
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
                _extract_js_function(src, "hasActiveChatRequestForAgent"),
                _extract_js_function(src, "guardNoActiveChatRequestForAgent"),
                _extract_js_function(src, "normalizeChatRunStatus"),
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
            _extract_js_function(src, "hasActiveChatRequestForAgent"),
            _extract_js_function(src, "normalizeChatRunStatus"),
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
                _extract_js_function(src, "hasActiveChatRequestForAgent"),
                _extract_js_function(src, "requestContextIdCandidates"),
                _extract_js_function(src, "activeRequestMatchesRequestContext"),
                _extract_js_function(src, "fallbackRequestContextForAgent"),
                _extract_js_function(src, "clearStaleActiveRequest"),
                _extract_js_function(src, "runtimeAbortSucceeded"),
                _extract_js_function(src, "runtimeAbortIndicatesInactive"),
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
            async function agentApiFor(agentId, path, options) {
              calls.push(["api", path, options.method]);
              return { success: true, abort_result: { success: true }, run: { status: "aborted" } };
            }

            (async () => {
              await abortActiveChatRequestForSelectedAgent();
              assert.equal(chatState.activeRequest, null);
              assert.equal(chatState.inflightThinking.completed, true);
              assert.equal(chatState.inflightThinking.stale, true);
              assert.ok(calls.some((item) => item[0] === "api" && item[1] === "/api/chat/runs/runtime-1/abort" && item[2] === "POST"));
              assert.ok(calls.some((item) => item[0] === "event" && item[1] === "portal.abort.completed"));
              assert.ok(calls.some((item) => item[0] === "toast" && item[1] === "Stopped current run."));
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
                _extract_js_function(src, "runtimeAbortSucceeded"),
                _extract_js_function(src, "runtimeAbortIndicatesInactive"),
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
            async function agentApiFor(agentId, path, options) {
              calls.push(["api", path, options.method]);
              return { success: false, abort_result: { success: false, errors: ["opencode refused abort"] } };
            }

            (async () => {
              await abortActiveChatRequestForSelectedAgent();
              assert.equal(chatState.activeRequest, requestCtx);
              assert.equal(chatState.inflightThinking.completed, false);
              assert.ok(calls.some((item) => item[0] === "api" && item[1] === "/api/chat/runs/runtime-1/abort" && item[2] === "POST"));
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
