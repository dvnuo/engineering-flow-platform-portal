import subprocess
import textwrap


def test_opencode_chat_event_stream_projects_events_and_disconnect_node():
    script = textwrap.dedent(
        r"""
        import assert from "node:assert/strict";
        import { createOpenCodeChatStore } from "./app/static/js/opencode_chat/store.js";
        import { connectOpenCodeEvents } from "./app/static/js/opencode_chat/event_stream.js";

        class FakeSource {
          constructor() {
            this.listeners = new Map();
            this.closed = false;
          }
          addEventListener(name, fn) {
            const listeners = this.listeners.get(name) || [];
            listeners.push(fn);
            this.listeners.set(name, listeners);
          }
          emit(name, data = {}) {
            for (const fn of this.listeners.get(name) || []) fn({ data: JSON.stringify(data) });
          }
          close() {
            this.closed = true;
          }
        }

        const store = createOpenCodeChatStore({ agentId: "agent-1" });
        let source = null;
        let changes = 0;
        let snapshots = 0;
        const api = {
          connectEvents(conversationId, handlers) {
            assert.equal(conversationId, "conversation-1");
            source = new FakeSource();
            source.addEventListener("open", handlers.open);
            source.addEventListener("message", handlers.message);
            source.addEventListener("error", handlers.error);
            return source;
          },
        };

        const connection = connectOpenCodeEvents({
          api,
          store,
          conversationId: "conversation-1",
          maxReconnects: 0,
          onChange: () => { changes += 1; },
          onSnapshotNeeded: async () => { snapshots += 1; },
        });

        source.emit("open");
        assert.equal(store.eventConnection, "connected");

        source.emit("message", { type: "opencode.session.status", data: { status: { type: "busy", active: true } } });
        assert.equal(store.sessionStatus, "busy");

        source.emit("opencode.message.part.updated", { id: "part-1", message_id: "message-1", type: "text", text: "hel" });
        source.emit("opencode.message.part.delta", { part_id: "part-1", delta: "lo" });
        assert.equal(store.partsById.get("part-1").text, "hello");

        source.emit("error");
        await Promise.resolve();
        assert.equal(store.eventConnection, "disconnected");
        assert.equal(store.snapshotNeeded, true);
        assert.equal(snapshots, 1);
        assert.equal(changes > 0, true);

        connection.close();
        assert.equal(source.closed, true);
        """
    )

    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
