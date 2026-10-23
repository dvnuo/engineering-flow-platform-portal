"""Node harness: the chat page stages workspace files on the local bridge.

A ``page.upload`` or ``workflow.run`` request names workspace files; the page
pulls each one through the Server files download, POSTs it to the bridge's
``/browser/files``, and runs the command with the staged ids. These tests
drive ``stageRequestFiles`` and ``runLocalBridge`` with stubbed fetches.
"""

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests._js_extract_helpers import _extract_js_function


BRIDGE_SRC = Path("app/static/js/connectors/local_bridge.js")
CHAT_SRC = Path("app/static/js/chat_ui.js")

BRIDGE_FUNCTIONS = [
    "workspaceFileUrl",
    "bridgeFailure",
    "fetchWorkspaceFile",
    "stageFile",
    "withoutFiles",
    "stageRequestFiles",
    "runLocalBridge",
]


def _node_bin() -> str:
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping local bridge staging test")
    return node_bin


def _run(script: str) -> None:
    result = subprocess.run([_node_bin(), "-e", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "ok" in result.stdout


HARNESS = r"""
const assert = require("node:assert/strict");
const RUN_TIMEOUT_MS = 65000;
const RUN_TIMEOUT_GRACE_MS = 5000;
const STAGE_TIMEOUT_MS = 120000;
const state = { localBridge: { alive: true, port: 8765, browserFiles: true, workflow: true, maxFileBytes: 100 * 1024 * 1024, probedAt: 0 } };
const calls = [];
let probeResult = { alive: true };
async function probeLocalBridge() { calls.push(["probe"]); return probeResult; }
function currentAgentId() { return "agent-A"; }
let stagedCount = 0;
let workspaceStatus = 200;
let runAnswer = null;
async function fetchWithTimeout(url, options, timeoutMs) {
  calls.push([options && options.method || "GET", url, timeoutMs]);
  if (url.startsWith("/a/")) {
    return { ok: workspaceStatus === 200, status: workspaceStatus, blob: async () => new Blob(["bytes of " + url]) };
  }
  if (url.endsWith("/browser/files")) {
    const part = options.body.get("file");
    calls.push(["staged", part.name, await part.text()]);
    stagedCount += 1;
    return { ok: true, status: 200, json: async () => ({ ok: true, data: { file: { id: `id-${stagedCount}`, name: part.name, bytes: 1 } } }) };
  }
  if (url.endsWith("/run")) {
    calls.push(["run-body", JSON.parse(options.body)]);
    return { ok: true, status: 412, json: async () => runAnswer };
  }
  throw new Error("unexpected url " + url);
}
"""


def test_page_upload_and_playbook_requests_stage_files_then_run():
    src = BRIDGE_SRC.read_text(encoding="utf-8")
    script = (
        HARNESS
        + "\n".join(_extract_js_function(src, name) for name in BRIDGE_FUNCTIONS)
        + textwrap.dedent(
            r"""
            (async () => {
              // page.upload: every file is fetched from the workspace by path,
              // posted under its own name, and replaced by file_ids.
              let out = await stageRequestFiles("agent-A", "page.upload", {
                ref: "r12",
                files: [
                  { path: "output/report.pdf", name: "report.pdf", bytes: 10 },
                  { path: "uploads/a b.png", name: "a b.png", bytes: 20 },
                ],
              });
              assert.equal(out.ok, true);
              assert.deepEqual(out.params, { ref: "r12", file_ids: ["id-1", "id-2"] });
              assert.deepEqual(calls.filter((c) => c[0] === "GET").map((c) => c[1]), [
                "/a/agent-A/api/server-files/download?paths=output%2Freport.pdf",
                "/a/agent-A/api/server-files/download?paths=uploads%2Fa%20b.png",
              ]);
              assert.deepEqual(calls.filter((c) => c[0] === "staged"), [
                ["staged", "report.pdf", "bytes of /a/agent-A/api/server-files/download?paths=output%2Freport.pdf"],
                ["staged", "a b.png", "bytes of /a/agent-A/api/server-files/download?paths=uploads%2Fa%20b.png"],
              ]);
              assert.ok(calls.filter((c) => c[0] === "POST").every((c) => c[1] === "http://127.0.0.1:8765/browser/files" && c[2] === STAGE_TIMEOUT_MS));

              // workflow.run: staged ids land in vars under the file's variable.
              calls.length = 0;
              out = await stageRequestFiles("agent-A", "workflow.run", {
                workflow_yaml: "steps: []",
                vars: { instruction: "Summarize" },
                files: [{ var: "file", path: "uploads/contract.pdf", name: "contract.pdf", bytes: 5 }],
                yes: true,
              });
              assert.equal(out.ok, true);
              assert.deepEqual(out.params, { workflow_yaml: "steps: []", vars: { instruction: "Summarize", file: "id-3" }, yes: true });
              assert.equal("files" in out.params, false);

              // Several files for one variable: their ids are joined with commas,
              // which the bridge splits; another variable keeps its own id.
              calls.length = 0;
              out = await stageRequestFiles("agent-A", "workflow.run", {
                workflow_yaml: "steps: []",
                files: [
                  { var: "files", path: "uploads/a.pdf", name: "a.pdf", bytes: 1 },
                  { var: "files", path: "uploads/b.png", name: "b.png", bytes: 1 },
                  { var: "cover", path: "uploads/c.jpg", name: "c.jpg", bytes: 1 },
                ],
              });
              assert.deepEqual(out.params.vars, { files: "id-4,id-5", cover: "id-6" });

              // No files: nothing is fetched, the params pass through.
              calls.length = 0;
              out = await stageRequestFiles("agent-A", "workflow.run", { workflow_yaml: "steps: []", files: [] });
              assert.deepEqual(out.params, { workflow_yaml: "steps: []" });
              assert.deepEqual(calls, []);
              console.log("ok");
            })().catch((error) => { console.error(error); process.exit(1); });
            """
        )
    )
    _run(script)


def test_staging_stops_on_outdated_bridges_and_fetch_failures():
    src = BRIDGE_SRC.read_text(encoding="utf-8")
    script = (
        HARNESS
        + "\n".join(_extract_js_function(src, name) for name in BRIDGE_FUNCTIONS)
        + textwrap.dedent(
            r"""
            (async () => {
              const files = [{ path: "output/report.pdf", name: "report.pdf", bytes: 10 }];
              // An older bridge advertises neither capability: the assistant is
              // told the bridge is outdated before any file leaves the workspace.
              state.localBridge.browserFiles = false;
              let out = await stageRequestFiles("agent-A", "page.upload", { ref: "r1", files });
              assert.equal(out.ok, false);
              assert.equal(out.error.code, "bridge_outdated");
              assert.equal(calls.filter((c) => c[0] === "GET").length, 0);
              state.localBridge.browserFiles = true;
              state.localBridge.workflow = false;
              out = await stageRequestFiles("agent-A", "workflow.run", { workflow_yaml: "steps: []", files: [] });
              assert.equal(out.error.code, "bridge_outdated");
              state.localBridge.workflow = true;

              // A file the workspace no longer has stops the request; nothing
              // is staged.
              workspaceStatus = 404;
              out = await stageRequestFiles("agent-A", "page.upload", { ref: "r1", files });
              assert.equal(out.error.code, "file_fetch_failed");
              assert.match(out.error.message, /HTTP 404/);
              assert.equal(calls.filter((c) => c[0] === "staged").length, 0);
              workspaceStatus = 200;

              // A file over the bridge's limit is refused before it is fetched.
              state.localBridge.maxFileBytes = 5;
              out = await stageRequestFiles("agent-A", "page.upload", { ref: "r1", files });
              assert.equal(out.error.code, "payload_too_large");
              state.localBridge.maxFileBytes = 0;

              // No bridge: the usual unreachable answer.
              state.localBridge.alive = false;
              probeResult = { alive: false };
              out = await stageRequestFiles("agent-A", "page.upload", { ref: "r1", files });
              assert.equal(out.error.code, "bridge_unreachable");
              console.log("ok");
            })().catch((error) => { console.error(error); process.exit(1); });
            """
        )
    )
    _run(script)


def test_run_keeps_a_failed_playbooks_steps_and_waits_for_the_requests_timeout():
    src = BRIDGE_SRC.read_text(encoding="utf-8")
    script = (
        HARNESS
        + "\n".join(_extract_js_function(src, name) for name in BRIDGE_FUNCTIONS)
        + textwrap.dedent(
            r"""
            (async () => {
              runAnswer = {
                ok: false,
                error: { code: "workflow_failed", message: "Browser workflow failed.", hint: "Inspect data.steps.", status: 412 },
                data: { pass: false, failed_step: 1, steps: [{ index: 0, status: "passed" }, { index: 1, status: "failed" }] },
              };
              const out = await runLocalBridge("workflow.run", { workflow_yaml: "steps: []" }, 120);
              assert.equal(out.ok, false);
              assert.equal(out.error.code, "workflow_failed");
              assert.equal(out.error.data.failed_step, 1);
              assert.equal(out.error.data.steps.length, 2);
              const run = calls.find((c) => c[0] === "POST" && c[1].endsWith("/run"));
              // The page waits for the request's own timeout plus a grace, not
              // the fixed 65 s that would cut a long playbook off.
              assert.equal(run[2], 125000);
              assert.equal(calls.find((c) => c[0] === "run-body")[1].timeout_seconds, 120);
              const quick = await runLocalBridge("tab.list", {}, 30);
              assert.equal(calls.filter((c) => c[0] === "POST" && c[1].endsWith("/run"))[1][2], 65000);
              assert.equal(quick.error.code, "workflow_failed");
              console.log("ok");
            })().catch((error) => { console.error(error); process.exit(1); });
            """
        )
    )
    _run(script)


def test_timeline_rows_name_the_files_and_the_playbook():
    src = CHAT_SRC.read_text(encoding="utf-8")
    script = (
        _extract_js_function(src, "connectorRequestDetail")
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            // The request event carries the prepared params (file entries).
            assert.equal(connectorRequestDetail("page.upload", { connector_request: { params: { ref: "r1", files: [{ path: "a" }, { path: "b" }] } } }), " (2 files)");
            assert.equal(connectorRequestDetail("page.upload", { connector_request: { params: { ref: "r1", files: [{ path: "a" }] } } }), " (1 file)");
            assert.equal(connectorRequestDetail("page.upload", { connector_request: { params: { ref: "r1", clear: true } } }), "");
            // The response event carries only the model's arguments.
            assert.equal(connectorRequestDetail("page.upload", { arguments: { params: { files: ["output/a.pdf"] } } }), " (1 file)");
            assert.equal(connectorRequestDetail("workflow.run", { arguments: { params: { workflow: "productivity-suite-analyze-file/workflow.yaml", files: { file: "uploads/c.pdf" } } } }), " (productivity-suite-analyze-file/workflow.yaml, 1 file)");
            assert.equal(connectorRequestDetail("workflow.run", { connector_request: { params: { workflow_yaml: "steps: []" } } }), "");
            assert.equal(connectorRequestDetail("page.click", { connector_request: { params: { ref: "r1" } } }), "");
            assert.equal(connectorRequestDetail("page.upload", {}), "");
            console.log("ok");
            """
        )
    )
    _run(script)
