"""Node harness: the Files panel uploads several picked files.

The Upload button opens a ``multiple`` file picker; ``uploadFilesToServerFiles``
sends the picked files one request each, in order, to the same folder, keeps
going past a failed file, reloads the folder and reports the outcome in a toast.
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests._js_extract_helpers import _extract_js_function


SRC = Path("app/static/js/chat_ui.js")

FUNCTIONS = [
    "parseRuntimeErrorResponse",
    "describeServerFileUpload",
    "uploadOneServerFile",
    "uploadFilesToServerFiles",
    "uploadToServerFiles",
]

HARNESS = r"""
const state = { selectedAgentId: "agent-A" };
const out = { requests: [], panels: [], reloads: [], toasts: [], maxInFlight: 0, multiple: null };
let inFlight = 0;
let answers = [];
let onRequest = null;
function escapeHtml(v) { return String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;"); }
function setToolPanel(title, html, kind) { out.panels.push(html); }
function loadServerFiles(path) { out.reloads.push([state.selectedAgentId, path]); }
function showToast(message, opts) { out.toasts.push([message, (opts && opts.variant) || ""]); }
class FormData {
  constructor() { this.fields = []; }
  append(name, value) { this.fields.push([name, value && value.name !== undefined ? value.name : value]); }
}
async function fetch(url, options) {
  inFlight += 1;
  out.maxInFlight = Math.max(out.maxInFlight, inFlight);
  out.requests.push([url, options.method, options.body.fields]);
  if (onRequest) onRequest(out.requests.length);
  await new Promise((resolve) => setTimeout(resolve, 5));
  inFlight -= 1;
  const [status, body] = answers.shift();
  let used = false;
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => { used = true; return body; },
    text: async () => { if (used) throw new TypeError("body used"); return JSON.stringify(body); },
  };
}
const document = {
  createElement(tag) {
    const input = { tag, click() { out.multiple = this.multiple === true; this.onchange({ target: { files: picked } }); } };
    return input;
  },
};
let picked = [];
async function pick(files) {
  picked = files;
  uploadToServerFiles("/workspace/docs");
  for (let i = 0; i < 200 && out.toasts.length === 0; i += 1) await new Promise((r) => setTimeout(r, 5));
}
"""


def _run_case(body: str) -> dict:
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping Files multi-upload test")
    js = SRC.read_text(encoding="utf-8")
    bundle = "\n".join(_extract_js_function(js, name) for name in FUNCTIONS)
    script = HARNESS + bundle + "\n(async () => {\n" + body + "\nconsole.log(JSON.stringify(out));\n})();\n"
    with tempfile.TemporaryDirectory() as tmp:
        script_path = Path(tmp) / "server_files_multi_upload_case.js"
        script_path.write_text(script, encoding="utf-8")
        result = subprocess.run(
            [node_bin, str(script_path)], check=False, capture_output=True, text=True, encoding="utf-8"
        )
    if result.returncode != 0:
        raise AssertionError(f"node failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_picker_allows_several_files_and_uploads_each_in_order():
    out = _run_case(
        """
        answers = [
          [200, { success: true, mode: "file_save", uploaded_filename: "a.txt", target_path: "/workspace/docs/a.txt" }],
          [200, { success: true, mode: "file_save", uploaded_filename: "b.pdf", target_path: "/workspace/docs/b.pdf" }],
          [200, { success: true, mode: "zip_extract", extracted_count: 4, target_path: "/workspace/docs" }],
        ];
        await pick([{ name: "a.txt" }, { name: "b.pdf" }, { name: "c.zip" }]);
        """
    )

    assert out["multiple"] is True
    assert out["requests"] == [
        ["/a/agent-A/api/server-files/upload", "POST", [["path", "/workspace/docs"], ["file", "a.txt"]]],
        ["/a/agent-A/api/server-files/upload", "POST", [["path", "/workspace/docs"], ["file", "b.pdf"]]],
        ["/a/agent-A/api/server-files/upload", "POST", [["path", "/workspace/docs"], ["file", "c.zip"]]],
    ]
    assert out["maxInFlight"] == 1
    assert [p for p in out["panels"] if "Uploading" in p] == [
        '<div class="portal-inline-state">Uploading 1 of 3: a.txt\u2026</div>',
        '<div class="portal-inline-state">Uploading 2 of 3: b.pdf\u2026</div>',
        '<div class="portal-inline-state">Uploading 3 of 3: c.zip\u2026</div>',
    ]
    assert out["reloads"] == [["agent-A", "/workspace/docs"]]
    assert out["toasts"] == [["Uploaded 3 files", ""]]


def test_failed_file_does_not_stop_the_rest_and_is_named_with_its_reason():
    out = _run_case(
        """
        answers = [
          [200, { success: true, mode: "file_save", uploaded_filename: "a.txt" }],
          [413, { detail: "File too large. Maximum size is 50MB." }],
          [200, { success: false, error: "Permission denied" }],
          [200, { success: true, mode: "file_save", uploaded_filename: "d.md" }],
        ];
        await pick([{ name: "a.txt" }, { name: "big.iso" }, { name: "locked.txt" }, { name: "d.md" }]);
        """
    )

    assert len(out["requests"]) == 4
    assert out["reloads"] == [["agent-A", "/workspace/docs"]]
    assert out["toasts"] == [[
        "Uploaded 2 of 4 files. Failed: big.iso: File too large. Maximum size is 50MB.; locked.txt: Permission denied",
        "error",
    ]]


def test_single_file_keeps_its_own_message_and_failure_text():
    ok = _run_case(
        """
        answers = [[200, { success: true, mode: "file_save", uploaded_filename: "a<b>.txt", target_path: "/workspace/docs/a<b>.txt" }]];
        await pick([{ name: "a<b>.txt" }]);
        """
    )
    assert '<div class="portal-inline-state">Uploading a&lt;b>.txt\u2026</div>' in ok["panels"]
    # Toasts render text, so the name is not HTML-escaped there.
    assert ok["toasts"] == [["Uploaded a<b>.txt to /workspace/docs/a<b>.txt", ""]]

    failed = _run_case(
        """
        answers = [[502, { error: "Runtime unavailable" }]];
        await pick([{ name: "a.txt" }]);
        """
    )
    assert failed["toasts"] == [["Upload failed: Runtime unavailable", "error"]]
    assert failed["reloads"] == [["agent-A", "/workspace/docs"]]


def test_switching_assistant_mid_batch_keeps_uploads_on_the_original_one():
    out = _run_case(
        """
        answers = [
          [200, { success: true, mode: "file_save", uploaded_filename: "a.txt" }],
          [200, { success: true, mode: "file_save", uploaded_filename: "b.txt" }],
        ];
        onRequest = (n) => { if (n === 1) state.selectedAgentId = "agent-B"; };
        await pick([{ name: "a.txt" }, { name: "b.txt" }]);
        """
    )

    assert [r[0] for r in out["requests"]] == [
        "/a/agent-A/api/server-files/upload",
        "/a/agent-A/api/server-files/upload",
    ]
    # The other assistant's panel is neither overwritten with progress nor reloaded.
    assert not any("2 of 2" in p for p in out["panels"])
    assert out["reloads"] == []
    assert out["toasts"] == [["Uploaded 2 files", ""]]
