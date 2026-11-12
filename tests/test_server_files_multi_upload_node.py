"""Node harness: uploads from the Files panel.

The Upload button opens a ``multiple`` file picker, and files or folders can
be dropped on the panel. ``startServerFilesUpload`` first asks about names
that are already in the folder, then sends one request per file, a few at a
time, keeps going past a failed file, reloads the folder and reports the
outcome in a toast. The list stays on screen meanwhile; a batch can be
cancelled, and a file over the size cap is refused before it is sent.
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
    "trimServerFilePath",
    "joinServerFilePath",
    "normalizeServerFileItem",
    "serverFilesPanelShows",
    "parseRuntimeErrorResponse",
    "describeServerFileUpload",
    "uploadOneServerFile",
    "serverFileTooLargeReason",
    "serverFileUploadTopName",
    "serverFilesUploadConflicts",
    "uniqueServerFileName",
    "applyServerFilesConflictChoice",
    "resolveServerFilesUploadConflicts",
    "serverFilesUploadProgress",
    "renderServerFilesUploadStrip",
    "queueServerFilesUploadStrip",
    "cancelServerFilesUpload",
    "runServerFilesUploadBatch",
    "summarizeServerFileFailures",
    "describeServerFilesUploadOutcome",
    "startServerFilesUpload",
    "uploadFilesToServerFiles",
    "uploadToServerFiles",
]

HARNESS = r"""
const SERVER_FILES_UPLOAD_FAILURES_SHOWN = 5;
const SERVER_FILES_UPLOAD_PARALLEL = 3;
const serverFilesUploads = new Map();
let serverFilesUploadStripQueued = false;
// The panel is open on Server Files of agent-A, showing /workspace/docs.
const state = {
  selectedAgentId: "agent-A",
  toolPanelOpen: true,
  activeUtilityPanel: "server-files",
  serverFilesCurrentPath: "/workspace/docs",
  serverFilesItems: [],
  serverFilesRevealNames: [],
};
const out = { requests: [], panels: [], reloads: [], reveals: [], toasts: [], asked: [], listed: [], maxInFlight: 0, multiple: null };
let inFlight = 0;
let answers = [];
let delays = [];
let onRequest = null;
let choice = "keep";
let otherFolderNames = [];
function setToolPanel(title, html, kind) { out.panels.push(html); }
// The folder is redrawn where it stands, not replaced by a loading line.
function reloadServerFilesInPlace(path) { out.reloads.push([state.selectedAgentId, path]); out.reveals.push(state.serverFilesRevealNames); }
function showToast(message, opts) { out.toasts.push([message, (opts && opts.variant) || ""]); }
async function askServerFilesChoice(question) {
  out.asked.push([question.title, question.message, question.choices.map((c) => c.id), question.choices.filter((c) => c.primary).map((c) => c.id)]);
  return choice;
}
async function agentApiFor(agentId, path) {
  out.listed.push([agentId, path]);
  return { items: otherFolderNames.map((name) => ({ name, path: "/x/" + name })) };
}
class FormData {
  constructor() { this.fields = []; }
  append(name, value, filename) {
    this.fields.push([name, filename !== undefined ? filename : (value && value.name !== undefined ? value.name : value)]);
  }
}
// One request: answered after a delay with the next queued answer, unless aborted first.
class XMLHttpRequest {
  constructor() {
    this.listeners = {};
    this.upload = { listeners: {}, addEventListener: (type, fn) => { this.upload.listeners[type] = fn; } };
    this.status = 0;
    this.responseText = "";
  }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  open(method, url) { this.method = method; this.url = url; }
  send(body) {
    inFlight += 1;
    out.maxInFlight = Math.max(out.maxInFlight, inFlight);
    const count = out.requests.push([this.url, this.method, body.fields]);
    if (onRequest) onRequest(count);
    const answer = answers.shift();
    const delay = delays.length ? delays.shift() : 5;
    this.timer = setTimeout(() => {
      inFlight -= 1;
      this.done = true;
      if (this.upload.listeners.progress) this.upload.listeners.progress({ lengthComputable: true, loaded: 1, total: 2 });
      const [status, payload] = answer;
      this.status = status;
      // A string body is sent as-is (plain text or HTML); anything else as JSON.
      this.responseText = typeof payload === "string" ? payload : JSON.stringify(payload);
      this.listeners.load();
    }, delay);
  }
  abort() {
    if (this.done) return;
    this.done = true;
    clearTimeout(this.timer);
    inFlight -= 1;
    this.listeners.abort();
  }
}
const document = {
  getElementById() { return null; },
  createElement(tag) {
    return { tag, click() { out.multiple = this.multiple === true; this.onchange({ target: { files: picked } }); } };
  },
};
let picked = [];
const settle = async () => {
  for (let i = 0; i < 400 && out.toasts.length === 0; i += 1) await new Promise((r) => setTimeout(r, 5));
};
async function pick(files) {
  picked = files;
  uploadToServerFiles("/workspace/docs");
  await settle();
}
const ok = (name) => [200, { success: true, mode: "file_save", uploaded_filename: name }];
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


def test_picker_allows_several_files_and_uploads_each():
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
    # The list is not replaced by a progress line while the files go up.
    assert out["panels"] == []
    assert out["reloads"] == [["agent-A", "/workspace/docs"]]
    # What arrived is pointed out after the reload; a zip's contents are not known by name.
    assert out["reveals"] == [["a.txt", "b.pdf"]]
    # The zip was unpacked, not saved, and the toast says so.
    assert out["toasts"] == [["Uploaded 3 files. Extracted 4 files to /workspace/docs", ""]]


def test_files_go_up_a_few_at_a_time():
    out = _run_case(
        """
        const names = Array.from({ length: 8 }, (_, i) => `f${i}.txt`);
        answers = names.map((name) => ok(name));
        await pick(names.map((name) => ({ name })));
        """
    )

    assert len(out["requests"]) == 8
    assert [r[2][1][1] for r in out["requests"][:3]] == ["f0.txt", "f1.txt", "f2.txt"]
    assert out["maxInFlight"] == 3
    assert out["toasts"] == [["Uploaded 8 files", ""]]


def test_failed_file_does_not_stop_the_rest_and_is_named_with_its_reason():
    out = _run_case(
        """
        answers = [
          ok("a.txt"),
          [413, { detail: "File too large. Maximum size is 50MB." }],
          [200, { success: false, error: "Permission denied" }],
          ok("d.md"),
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
        answers = [ok("a.txt"), ok("b.txt"), ok("c.txt"), ok("d.txt")];
        onRequest = (n) => { if (n === 1) state.selectedAgentId = "agent-B"; };
        await pick([{ name: "a.txt" }, { name: "b.txt" }, { name: "c.txt" }, { name: "d.txt" }]);
        """
    )

    assert [r[0] for r in out["requests"]] == ["/a/agent-A/api/server-files/upload"] * 4
    # The other assistant's panel is neither redrawn nor reloaded.
    assert out["panels"] == []
    assert out["reloads"] == []
    assert out["toasts"] == [["Uploaded 4 files", ""]]


def test_closing_the_panel_mid_batch_does_not_reopen_it():
    out = _run_case(
        """
        answers = [ok("a.txt"), ok("b.txt"), ok("c.txt")];
        onRequest = (n) => { if (n === 1) { state.toolPanelOpen = false; state.activeUtilityPanel = null; } };
        await pick([{ name: "a.txt" }, { name: "b.txt" }, { name: "c.txt" }]);
        """
    )

    assert len(out["requests"]) == 3
    assert out["panels"] == []
    assert out["reloads"] == []
    assert out["toasts"] == [["Uploaded 3 files", ""]]


def test_the_folder_on_screen_is_reloaded_even_after_the_member_moved():
    out = _run_case(
        """
        answers = [ok("a.txt"), ok("b.txt")];
        onRequest = (n) => { if (n === 1) state.serverFilesCurrentPath = "/workspace/other"; };
        await pick([{ name: "a.txt" }, { name: "b.txt" }]);
        """
    )

    # Not pulled back to where the upload went, and nothing to point out here.
    assert out["reloads"] == [["agent-A", "/workspace/other"]]
    assert out["reveals"] == [[]]


def test_failure_list_is_capped():
    out = _run_case(
        """
        answers = Array.from({ length: 7 }, () => [200, { success: false }]);
        await pick(Array.from({ length: 7 }, (_, i) => ({ name: `f${i}.txt` })));
        """
    )

    assert out["toasts"] == [[
        "Uploaded 0 of 7 files. Failed: f0.txt: Upload failed; f1.txt: Upload failed; f2.txt: Upload failed; "
        "f3.txt: Upload failed; f4.txt: Upload failed; and 2 more",
        "error",
    ]]


def test_plain_text_and_html_error_bodies_keep_their_reason():
    out = _run_case(
        """
        answers = [
          [502, "upstream connect error"],
          [413, "<html><head><title>413 Request Entity Too Large</title></head><body>nginx</body></html>"],
          [500, "{}"],
        ];
        await pick([{ name: "a.txt" }, { name: "b.iso" }, { name: "c.txt" }]);
        """
    )

    assert out["toasts"] == [[
        "Uploaded 0 of 3 files. Failed: a.txt: upstream connect error; b.iso: 413 Request Entity Too Large; c.txt: HTTP 500",
        "error",
    ]]


def test_cancel_stops_what_is_in_flight_and_keeps_what_arrived():
    out = _run_case(
        """
        const names = ["a.txt", "b.txt", "c.txt", "d.txt", "e.txt"];
        answers = names.map((name) => ok(name));
        // a.txt arrives at once, which frees a slot for d.txt; the member
        // cancels while b, c and d are still on their way.
        delays = [1, 1000, 1000, 1000, 1000];
        onRequest = (n) => { if (n === 4) setTimeout(() => cancelServerFilesUpload("agent-A"), 0); };
        await pick(names.map((name) => ({ name })));
        out.left = serverFilesUploads.size;
        """
    )

    # e.txt was never sent.
    assert [r[2][1][1] for r in out["requests"]] == ["a.txt", "b.txt", "c.txt", "d.txt"]
    assert out["toasts"] == [[
        "Upload cancelled: 1 of 5 files had finished. A file that was being sent may still have arrived.",
        "error",
    ]]
    assert out["reloads"] == [["agent-A", "/workspace/docs"]]
    assert out["reveals"] == [["a.txt"]]
    assert out["left"] == 0


def test_a_file_over_the_size_cap_is_refused_before_it_is_sent():
    out = _run_case(
        """
        globalThis.getChatUploadPolicy = () => ({ maxUploadMb: 1 });
        answers = [ok("small.txt")];
        await pick([{ name: "small.txt", size: 10 }, { name: "big.iso", size: 3 * 1024 * 1024 }]);
        """
    )

    assert [r[2][1][1] for r in out["requests"]] == ["small.txt"]
    assert out["toasts"] == [["Uploaded 1 of 2 files. Failed: big.iso: Larger than the 1 MB limit", "error"]]


def test_a_name_already_in_the_folder_is_asked_about_first():
    kept = _run_case(
        """
        state.serverFilesItems = [{ name: "a.txt" }, { name: "notes.md" }];
        answers = [ok("a (1).txt"), ok("b.txt")];
        choice = "keep";
        await pick([{ name: "a.txt" }, { name: "b.txt" }]);
        """
    )
    # Skip is offered because b.txt does not clash; Keep both is the default.
    assert kept["asked"] == [["1 name is already taken", "Already in /workspace/docs: a.txt", ["skip", "replace", "keep"], ["keep"]]]
    # The folder on screen is the one uploaded to: its names are already known.
    assert kept["listed"] == []
    assert [r[2][1][1] for r in kept["requests"]] == ["a (1).txt", "b.txt"]
    assert kept["reveals"] == [["a (1).txt", "b.txt"]]

    skipped = _run_case(
        """
        state.serverFilesItems = [{ name: "a.txt" }];
        answers = [ok("b.txt")];
        choice = "skip";
        await pick([{ name: "a.txt" }, { name: "b.txt" }]);
        """
    )
    assert [r[2][1][1] for r in skipped["requests"]] == ["b.txt"]

    replaced = _run_case(
        """
        state.serverFilesItems = [{ name: "a.txt" }];
        answers = [ok("a.txt")];
        choice = "replace";
        await pick([{ name: "a.txt" }]);
        """
    )
    # Everything clashes, so there is nothing a Skip would leave.
    assert replaced["asked"][0][2] == ["replace", "keep"]
    assert [r[2][1][1] for r in replaced["requests"]] == ["a.txt"]

    called_off = _run_case(
        """
        state.serverFilesItems = [{ name: "a.txt" }];
        choice = null;
        picked = [{ name: "a.txt" }];
        await uploadFilesToServerFiles("/workspace/docs", picked);
        """
    )
    assert called_off["requests"] == []
    assert called_off["toasts"] == []
    assert called_off["reloads"] == []


def test_a_zip_is_unpacked_so_its_own_name_never_clashes():
    out = _run_case(
        """
        state.serverFilesItems = [{ name: "bundle.zip" }];
        answers = [[200, { success: true, mode: "zip_extract", extracted_count: 2, target_path: "/workspace/docs" }]];
        await pick([{ name: "bundle.zip" }]);
        """
    )

    assert out["asked"] == []
    assert out["toasts"] == [["Extracted 2 files to /workspace/docs", ""]]


def test_a_dropped_folder_keeps_its_structure():
    out = _run_case(
        """
        // Dropped onto another folder: that folder is listed to compare names.
        otherFolderNames = ["reports"];
        choice = "keep";
        answers = [ok("q1.md"), ok("q2.md"), [200, { success: false, error: "Permission denied" }], ok("loose.txt")];
        startServerFilesUpload("/workspace/archive", [
          { file: { name: "q1.md" }, relativeDir: "reports" },
          { file: { name: "q2.md" }, relativeDir: "reports/2026" },
          { file: { name: "locked.md" }, relativeDir: "reports/2026" },
          { file: { name: "loose.txt" }, relativeDir: "" },
        ]);
        await settle();
        """
    )

    assert out["listed"] == [["agent-A", "/api/server-files?path=%2Fworkspace%2Farchive"]]
    assert out["asked"][0][:2] == ["1 name is already taken", "Already in /workspace/archive: reports"]
    # "Keep both" renames the folder once; everything under it follows.
    assert [[r[2][0][1], r[2][1][1]] for r in out["requests"]] == [
        ["/workspace/archive/reports (1)", "q1.md"],
        ["/workspace/archive/reports (1)/2026", "q2.md"],
        ["/workspace/archive/reports (1)/2026", "locked.md"],
        ["/workspace/archive", "loose.txt"],
    ]
    # A failure is named with the folder it was going to.
    assert out["toasts"] == [["Uploaded 3 of 4 files. Failed: reports (1)/2026/locked.md: Permission denied", "error"]]
    # The panel shows /workspace/docs, not the folder dropped on: nothing to point out.
    assert out["reloads"] == [["agent-A", "/workspace/docs"]]
    assert out["reveals"] == [[]]


def test_a_second_drop_joins_the_batch_that_is_running():
    out = _run_case(
        """
        answers = [ok("a.txt"), ok("b.txt"), ok("c.txt")];
        delays = [300, 300, 5];
        uploadFilesToServerFiles("/workspace/docs", [{ name: "a.txt" }, { name: "b.txt" }]);
        await new Promise((r) => setTimeout(r, 10));
        out.midway = serverFilesUploadProgress(serverFilesUploads.get("agent-A")).text.split(":")[0];
        uploadFilesToServerFiles("/workspace/docs", [{ name: "c.txt" }]);
        await settle();
        """
    )

    assert out["midway"] == "Uploading 1 of 2"
    assert len(out["requests"]) == 3
    # One batch, one reload, one toast.
    assert out["reloads"] == [["agent-A", "/workspace/docs"]]
    assert out["toasts"] == [["Uploaded 3 files", ""]]


def test_a_name_still_on_its_way_counts_as_taken():
    out = _run_case(
        """
        answers = [ok("report.pdf"), ok("report (1).pdf")];
        delays = [300, 5];
        choice = "keep";
        uploadFilesToServerFiles("/workspace/docs", [{ name: "report.pdf" }]);
        await new Promise((r) => setTimeout(r, 10));
        // The folder listing does not have it yet; the batch does.
        uploadFilesToServerFiles("/workspace/docs", [{ name: "report.pdf" }]);
        await settle();
        """
    )

    assert out["asked"] == [["1 name is already taken", "Already in /workspace/docs: report.pdf", ["replace", "keep"], ["keep"]]]
    assert [r[2][1][1] for r in out["requests"]] == ["report.pdf", "report (1).pdf"]
    assert out["toasts"] == [["Uploaded 2 files", ""]]


def test_conflict_helpers():
    out = _run_case(
        """
        out.unique = [
          uniqueServerFileName("plan.md", ["plan.md"], false),
          uniqueServerFileName("plan.md", ["plan.md", "plan (1).md"], false),
          uniqueServerFileName("reports", ["reports"], true),
          uniqueServerFileName("v1.2", ["v1.2"], true),
          uniqueServerFileName(".env", [".env"], false),
          uniqueServerFileName("archive.tar.gz", ["archive.tar.gz"], false),
        ];
        const entries = [
          { file: { name: "a.txt" }, relativeDir: "" },
          { file: { name: "x.md" }, relativeDir: "docs/deep" },
          { file: { name: "y.md" }, relativeDir: "docs" },
          { file: { name: "b.txt" }, relativeDir: "" },
        ];
        out.tops = entries.map(serverFileUploadTopName);
        out.clashes = serverFilesUploadConflicts(entries, ["a.txt", "docs", "zzz"]);
        const kept = applyServerFilesConflictChoice(entries, ["a.txt", "docs", "a (1).txt"], "keep");
        out.kept = kept.map((e) => [e.name || e.file.name, e.relativeDir]);
        out.skipped = applyServerFilesConflictChoice(entries, ["a.txt", "docs"], "skip").map((e) => e.file.name);
        out.replaced = applyServerFilesConflictChoice(entries, ["a.txt", "docs"], "replace").length;
        """
    )

    assert out["unique"] == ["plan (1).md", "plan (2).md", "reports (1)", "v1.2 (1)", ".env (1)", "archive.tar (1).gz"]
    assert out["tops"] == ["a.txt", "docs", "docs", "b.txt"]
    assert out["clashes"] == ["a.txt", "docs"]
    assert out["kept"] == [["a (2).txt", ""], ["x.md", "docs (1)/deep"], ["y.md", "docs (1)"], ["b.txt", ""]]
    assert out["skipped"] == ["b.txt"]
    assert out["replaced"] == 4
