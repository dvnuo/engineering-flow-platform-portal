import subprocess
import textwrap


def test_opencode_chat_projector_state_transitions_node():
    script = textwrap.dedent(
        r"""
        import assert from "node:assert/strict";
        import { createOpenCodeChatStore } from "./app/static/js/opencode_chat/store.js";
        import {
          applyStatusSnapshot,
          applyMessageSnapshot,
          applyOpenCodeEvent,
          deriveViewState,
        } from "./app/static/js/opencode_chat/projector.js";

        const store = createOpenCodeChatStore({ agentId: "agent-1" });
        store.runtimeHealth = "online";

        applyStatusSnapshot(store, { ok: true, status: { type: "idle", active: false, can_send: true, can_abort: false } });
        let view = deriveViewState(store);
        assert.equal(store.sessionStatus, "idle");
        assert.equal(view.canSend, true);
        assert.equal(view.canStop, false);

        applyStatusSnapshot(store, { ok: true, status: { type: "busy", active: true, can_send: false, can_abort: true } });
        view = deriveViewState(store);
        assert.equal(store.sessionStatus, "busy");
        assert.equal(view.canSend, false);
        assert.equal(view.canStop, false);
        assert.equal(view.showReconnect, false);

        applyStatusSnapshot(store, { ok: true, status: "accepted", action_hint: "watch_events_then_reconcile" });
        view = deriveViewState(store);
        assert.equal(store.sessionStatus, "busy");
        assert.equal(view.canSend, false);
        assert.equal(view.canStop, false);

        const snapshot = {
          messages: [
            { info: { id: "m1", role: "user", time: "1" }, parts: [{ id: "p1", type: "text", text: "hello" }] },
            { info: { id: "m2", role: "assistant", time: "2" }, parts: [{ id: "p2", type: "text", text: "hi" }] },
          ],
        };
        applyMessageSnapshot(store, snapshot);
        applyMessageSnapshot(store, snapshot);
        assert.equal(store.messagesById.size, 2);
        assert.equal(store.partsById.size, 2);
        assert.deepEqual(store.messageOrder, ["m1", "m2"]);

        const messageCountBeforeEvent = store.messagesById.size;
        applyOpenCodeEvent(store, { type: "opencode.message.updated", data: { info: { id: "m3", role: "assistant", time: "3" } } });
        assert.equal(store.messagesById.size, messageCountBeforeEvent);

        store.localSubmit = { messageId: "local-1", text: "pending", startedAt: Date.now() };
        applyStatusSnapshot(store, { status: { type: "idle", active: false } });
        view = deriveViewState(store);
        assert.equal(store.sessionStatus, "idle");
        assert.equal(store.localSubmit, null);
        assert.equal(view.canSend, true);

        store.permissionsById.set("perm-1", { permission_id: "perm-1", tool: "bash" });
        view = deriveViewState(store);
        assert.equal(view.permissionRequests.length, 1);
        assert.equal(view.permissionRequests[0].permission_id, "perm-1");

        store.children = [{ id: "child-1", status: "busy" }];
        view = deriveViewState(store);
        assert.equal(view.canSend, true);
        assert.deepEqual(view.children, [{ id: "child-1", status: "busy" }]);

        const traceStore = createOpenCodeChatStore({ agentId: "agent-1" });
        applyMessageSnapshot(traceStore, {
          messages: [
            {
              info: { id: "trace-message", role: "assistant", time: "1" },
              parts: [
                { id: "reason-1", type: "reasoning", text: "inspect code" },
                { id: "tool-1", type: "tool", tool: "bash", text: "pytest" },
              ],
            },
          ],
        });
        const traceView = deriveViewState(traceStore);
        assert.equal(traceView.thinkingItems.length, 2);
        assert.equal(traceView.thinkingItems[0].type, "reasoning");
        assert.equal(traceView.thinkingItems[1].type, "tool");
        """
    )

    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
