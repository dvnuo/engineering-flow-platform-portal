import subprocess
import textwrap


def test_opencode_chat_controller_flows_node():
    script = textwrap.dedent(
        r"""
        import assert from "node:assert/strict";
        import { OpenCodeChatController } from "./app/static/js/opencode_chat/controller.js";

        globalThis.setTimeout = () => 0;

        function makeApi(overrides = {}) {
          const calls = [];
          const api = {
            calls,
            async health() { calls.push(["health"]); return { ok: true, status: "online" }; },
            async listConversations() { calls.push(["listConversations"]); return { conversations: [] }; },
            async createConversation() { calls.push(["createConversation"]); return { id: "conversation-1" }; },
            async getStatus() { calls.push(["getStatus"]); return { status: { type: "idle", active: false } }; },
            async getMessages() { calls.push(["getMessages"]); return { messages: [] }; },
            async send(_conversationId, body) { calls.push(["send", body]); return { accepted: true, status: { type: "busy", active: true } }; },
            async abort() { calls.push(["abort"]); return { status: { type: "idle", active: false } }; },
            connectEvents() { calls.push(["connectEvents"]); return { close() { calls.push(["closeEvents"]); } }; },
            async respondPermission(_conversationId, permissionId, body) {
              calls.push(["respondPermission", permissionId, body]);
              return { ok: true };
            },
            ...overrides,
          };
          return api;
        }

        const initApi = makeApi();
        const initController = new OpenCodeChatController({ agentId: "agent-1", rootElement: null, api: initApi });
        await initController.init();
        assert.equal(initController.store.conversationId, "conversation-1");
        assert.deepEqual(initApi.calls.map((call) => call[0]).slice(0, 5), [
          "health",
          "listConversations",
          "createConversation",
          "getStatus",
          "getMessages",
        ]);

        const sendApi = makeApi({
          async getMessages() {
            this.calls.push(["getMessages"]);
            return {
              messages: [
                { info: { id: "conversation-user-message", role: "user", time: "1" }, parts: [{ id: "part-1", type: "text", text: "hello" }] },
              ],
            };
          },
        });
        const sendController = new OpenCodeChatController({ agentId: "agent-1", rootElement: null, api: sendApi });
        sendController.store.conversationId = "conversation-1";
        await sendController.send("hello");
        assert.equal(sendApi.calls.some((call) => call[0] === "send"), true);
        assert.equal(sendController.draftText, "");
        assert.notEqual(sendController.store.localSubmit, null);
        await sendController.refreshSnapshot();
        assert.equal(sendController.store.localSubmit, null);
        assert.equal(sendApi.calls.some((call) => JSON.stringify(call).includes("/api/chat")), false);

        const busyApi = makeApi({
          async getStatus() {
            this.calls.push(["getStatus"]);
            return { status: { type: "busy", active: true } };
          },
        });
        const busyController = new OpenCodeChatController({ agentId: "agent-1", rootElement: null, api: busyApi });
        busyController.store.conversationId = "conversation-1";
        await busyController.send("keep this");
        assert.equal(busyApi.calls.some((call) => call[0] === "send"), false);
        assert.equal(busyController.draftText, "keep this");
        assert.match(busyController.store.errors.at(-1).message, /Previous message still running/);

        const conflictApi = makeApi({
          async send() {
            this.calls.push(["send"]);
            throw {
              status: 409,
              code: "opencode_session_busy",
              body: { error: "opencode_session_busy", status: { type: "busy", active: true } },
            };
          },
        });
        const conflictController = new OpenCodeChatController({ agentId: "agent-1", rootElement: null, api: conflictApi });
        conflictController.store.conversationId = "conversation-1";
        await conflictController.send("still draft");
        assert.equal(conflictController.store.localSubmit, null);
        assert.equal(conflictController.draftText, "still draft");
        assert.equal(conflictController.store.sessionStatus, "busy");

        const abortApi = makeApi();
        const abortController = new OpenCodeChatController({ agentId: "agent-1", rootElement: null, api: abortApi });
        abortController.store.conversationId = "conversation-1";
        await abortController.abort();
        assert.equal(abortApi.calls.some((call) => call[0] === "abort"), true);
        assert.equal(abortApi.calls.filter((call) => call[0] === "getMessages").length > 0, true);

        const stillActiveApi = makeApi({
          async abort() {
            this.calls.push(["abort"]);
            throw { status: 409, code: "opencode_abort_still_active", body: { error: "opencode_abort_still_active" } };
          },
        });
        const stillActiveController = new OpenCodeChatController({ agentId: "agent-1", rootElement: null, api: stillActiveApi });
        stillActiveController.store.conversationId = "conversation-1";
        await stillActiveController.abort();
        assert.match(stillActiveController.store.errors.at(-1).message, /still reports/);
        assert.equal(stillActiveApi.calls.some((call) => call[0] === "createConversation"), false);

        const permissionApi = makeApi();
        const permissionController = new OpenCodeChatController({ agentId: "agent-1", rootElement: null, api: permissionApi });
        permissionController.store.conversationId = "conversation-1";
        await permissionController.respondPermission("perm-1", "allow", true);
        assert.deepEqual(permissionApi.calls.at(-1), ["respondPermission", "perm-1", { decision: "allow", remember: true }]);
        """
    )

    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
