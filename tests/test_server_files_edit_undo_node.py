"""The Files panel: editing a text file, Undo for a move or a rename, and a
delete that leaves the list on screen.

Editing writes through the upload call, which replaces a file of the same
name, so the cases here are mostly about what must not be lost: text that is
not UTF-8 or was cut short is never offered for editing, line endings and a
byte order mark survive a save, the runtime's copy is compared before it is
replaced, and text typed and not saved outlives the panel showing something
else.
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests._js_extract_helpers import _extract_js_function


SRC = Path("app/static/js/chat_ui.js")
CSS = Path("app/static/css/app.css")

FUNCTIONS = [
    "trimServerFilePath",
    "serverFileBaseName",
    "joinServerFilePath",
    "serverFileParentPath",
    "describeServerFileError",
    "parseRuntimeErrorResponse",
    "serverFilesPanelShows",
    "serverFilesPost",
    "moveServerFilesTo",
    "describeServerFilesMove",
    "undoServerFilesMoves",
    "showServerFilesChangeToast",
    "showServerFilesMoveToast",
    "runServerFilesMove",
    "runServerFilesUndo",
    "serverFileDraftKey",
    "carryServerFileDrafts",
    "serverFileEditBlocker",
    "splitServerFileText",
    "joinServerFileText",
    "readServerFileText",
    "saveServerFileText",
    "noteServerFileEditorLeft",
    "serverFilePreviewHeader",
    "renderServerFileText",
    "openServerFileEditor",
    "deleteSelectedServerFiles",
    "serverFileNameProblem",
    "bindServerFileNameEditor",
]

HARNESS = r"""
const SERVER_FILES_UPLOAD_FAILURES_SHOWN = 5;
const TOAST_DEFAULT_DURATION_MS = 2600;
const TOAST_ERROR_DURATION_MS = 8000;
const TOAST_ACTION_DURATION_MS = 8000;
const TOAST_MAX_VISIBLE = 4;
const SERVER_FILE_BOM = String.fromCharCode(0xFEFF);
const SERVER_FILE_REPLACEMENT_CHAR = String.fromCharCode(0xFFFD);
const serverFileDrafts = new Map();
let serverFilesUndo = null;
// The panel is open on Server Files of agent-A, showing /workspace/docs.
const state = {
  selectedAgentId: "agent-A",
  toolPanelOpen: true,
  activeUtilityPanel: "server-files",
  serverFilesCurrentPath: "/workspace/docs",
  serverFilesRootPath: "/workspace",
  serverFilesItems: [],
  serverFilesRevealNames: [],
};
const out = { requests: [], toasts: [], reloads: [], confirms: [], panels: [], uploads: [], previews: [], downloads: [] };
let answers = [];
let onFetch = null;
async function fetch(url, options) {
  const opts = options || {};
  out.requests.push([url, opts.method || "GET", opts.body ? JSON.parse(opts.body) : null]);
  if (onFetch) onFetch(url);
  const answer = answers.shift();
  if (answer === "network") throw new Error("Failed to fetch");
  const [status, body] = answer;
  const raw = typeof body === "string" ? body : JSON.stringify(body);
  return { ok: status >= 200 && status < 300, status, json: async () => JSON.parse(raw), text: async () => raw };
}
let confirmAnswers = [];
async function showConfirm(opts) {
  out.confirms.push([opts.title, opts.message, opts.confirmText, Boolean(opts.danger)]);
  return confirmAnswers.shift();
}
function escapeHtml(text) { return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;"); }
function escapeHtmlAttr(text) { return escapeHtml(text).replace(/"/g, "&quot;"); }
function safe(text) { return escapeHtml(text); }
function buildServerFilesBreadcrumb(dir, root) { return "[" + root + "|" + dir + "]"; }
function downloadSelectedFiles(paths) { out.downloads.push(paths); }
function serverFileTooLargeReason(file) { return tooLarge; }
let tooLarge = "";
let uploadAnswers = [];
let uploadGate = null;
// The upload call as the editor uses it: where, under which name, which
// bytes. It waits on `uploadGate` when a case wants to act while it runs.
async function uploadOneServerFile(agentId, dir, blob, hooks) {
  const bytes = Array.from(new Uint8Array(await blob.arrayBuffer()));
  out.uploads.push([agentId, dir, hooks && hooks.name, await blob.text(), bytes.slice(0, 3)]);
  if (uploadGate) await uploadGate;
  return uploadAnswers.length ? uploadAnswers.shift() : { ok: true };
}

// ---- the screen, as far as these functions touch it ---------------------------
function fakeEl(extra) {
  const handlers = {};
  const classes = new Set();
  return Object.assign({
    dataset: {},
    disabled: false,
    textContent: "",
    focused: 0,
    classes,
    classList: {
      add(name) { classes.add(name); },
      remove(name) { classes.delete(name); },
      toggle(name, on) { if (on) classes.add(name); else classes.delete(name); },
      contains(name) { return classes.has(name); },
    },
    addEventListener(type, fn) { handlers[type] = fn; },
    fire(type, event) {
      const e = Object.assign({ key: "", prevented: false, preventDefault() { this.prevented = true; }, stopPropagation() {} }, event || {});
      const result = handlers[type](e);
      return result && typeof result.then === "function" ? result.then(() => e) : e;
    },
    focus() { this.focused += 1; document.activeElement = this; },
  }, extra || {});
}
let screen = { title: "", html: "", editor: null, parts: {} };
const PARTS = ["textarea", ".portal-file-editor-status", ".sf-edit-save", ".sf-edit-close", ".sf-edit-btn", ".sf-preview-download-btn"];
function setToolPanel(title, html, key) {
  // As the page's own setToolPanel does before it replaces the panel.
  noteServerFileEditorLeft();
  state.activeUtilityPanel = key || null;
  out.panels.push(title);
  const parts = {};
  PARTS.forEach((sel) => {
    const marker = sel === "textarea" ? "<textarea" : sel.slice(1);
    if (html.includes(marker)) parts[sel] = fakeEl(sel === "textarea" ? { value: "", readOnly: false, scrollTop: 9, setSelectionRange() {} } : {});
  });
  const editor = html.includes('id="server-file-editor"') ? fakeEl({ querySelector: (sel) => parts[sel] || null }) : null;
  screen = { title, html, editor, parts };
}
const dom = { toolPanelBody: { querySelector: (sel) => screen.parts[sel] || null } };
let listPanel = null;
const document = {
  body: { isBody: true },
  activeElement: null,
  getElementById(id) {
    if (id === "server-file-editor") return screen.editor;
    if (id === "server-files-panel") return listPanel;
    return null;
  },
};
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const settle = async () => { for (let i = 0; i < 6; i += 1) await tick(); };
const editorOf = () => ({
  area: screen.parts["textarea"],
  status: screen.parts[".portal-file-editor-status"],
  save: screen.parts[".sf-edit-save"],
  close: screen.parts[".sf-edit-close"],
});
const type = (text) => { const area = screen.parts["textarea"]; area.value = text; area.fire("input"); };
const textAnswer = (content, extra) => [200, Object.assign({ success: true, content, is_binary: false, truncated: false }, extra || {})];
const viewOf = (content, path) => ({
  agentId: "agent-A",
  filePath: path || "/workspace/docs/plan.md",
  dir: "/workspace/docs",
  rootPath: "/workspace",
  read: { content, is_binary: false, truncated: false },
});
"""

# What a case does not load from the page is stood in for by one of these.
STUBS = {
    "showToast": r"""
const toasts = [];
function showToast(message, opts) {
  const o = typeof opts === "number" ? { duration: opts } : (opts || {});
  const toast = { isConnected: true, dataset: {}, action: o.action || null };
  out.toasts.push([message, o.variant || "", o.action ? o.action.label : ""]);
  toasts.push(toast);
  return toast;
}
""",
    "dismissToast": r"""
function dismissToast(toast) { toast.dataset.leaving = "1"; }
""",
    "reloadServerFilesInPlace": r"""
let onReload = null;
async function reloadServerFilesInPlace(path) {
  out.reloads.push([path, state.serverFilesRevealNames.slice()]);
  if (onReload) onReload(path);
}
""",
    "previewServerFile": r"""
function previewServerFile(path, dir, root, options) { out.previews.push([path, dir, root, options || null]); }
""",
}


def _run_case(body: str, extra: tuple[str, ...] = ()) -> dict:
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping the Files edit and undo test")
    js = SRC.read_text(encoding="utf-8")
    names = FUNCTIONS + list(extra)
    stubs = "".join(text for name, text in STUBS.items() if name not in names)
    bundle = "\n".join(_extract_js_function(js, name) for name in names)
    script = HARNESS + stubs + bundle + "\n(async () => {\n" + body + "\nconsole.log(JSON.stringify(out));\n})();\n"
    with tempfile.TemporaryDirectory() as tmp:
        script_path = Path(tmp) / "server_files_edit_undo_case.js"
        script_path.write_text(script, encoding="utf-8")
        result = subprocess.run(
            [node_bin, str(script_path)], check=False, capture_output=True, text=True, encoding="utf-8"
        )
    if result.returncode != 0:
        raise AssertionError(f"node failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


# ----- what may be edited, and what a save puts back ---------------------------

def test_only_whole_utf8_text_is_offered_for_editing():
    out = _run_case(
        r"""
        const bad = "caf" + String.fromCharCode(0xFFFD);
        out.blockers = [
          serverFileEditBlocker("plan.md", { content: "hello" }),
          serverFileEditBlocker("empty.txt", { content: "" }),
          serverFileEditBlocker(".env", { content: "A=1" }),
          serverFileEditBlocker("data.bin", { content: "", is_binary: true }),
          serverFileEditBlocker("big.log", { content: "first part", truncated: true }),
          serverFileEditBlocker("latin1.txt", { content: bad }),
          serverFileEditBlocker("notes.ZIP", { content: "text" }),
          serverFileEditBlocker(" padded.txt", { content: "text" }),
          serverFileEditBlocker("plan.md", null),
        ];
        """
    )

    assert out["blockers"] == [
        "",
        "",
        "",
        "This file is not text, so it cannot be shown here.",
        "Only the first part of this file is shown. It is too large to edit here.",
        "This file is not UTF-8 text, so it cannot be edited here.",
        # The runtime unpacks a .zip and stores an upload under its trimmed name.
        "This file cannot be edited here.",
        "This file cannot be edited here.",
        "",
    ]


def test_line_endings_and_the_byte_order_mark_survive_a_save():
    out = _run_case(
        r"""
        const BOM = String.fromCharCode(0xFEFF);
        const round = (content, edit) => {
          const shape = splitServerFileText(content);
          const typed = edit ? edit(shape.text) : shape.text;
          return [shape.text, shape.bom, shape.eol === "\r\n" ? "crlf" : "lf", joinServerFileText(typed, shape) === content, joinServerFileText(typed, shape)];
        };
        out.unix = round("a\nb\n");
        out.windows = round("a\r\nb\r\n");
        out.marked = round(BOM + "a\r\nb");
        out.empty = round("");
        // Mostly one kind: every line is saved with it.
        out.mixedMostlyCrlf = round("a\r\nb\r\nc\n");
        out.mixedMostlyLf = round("a\nb\nc\r\n");
        // Typed text only ever holds "\n"; a pasted "\r\n" does not double up.
        out.typed = round("a\r\nb\r\n", (text) => text + "c\r\nd\n");
        out.markCode = joinServerFileText("x", { bom: true, eol: "\n" }).charCodeAt(0);
        """
    )

    assert out["unix"][:4] == ["a\nb\n", False, "lf", True]
    assert out["windows"][:4] == ["a\nb\n", False, "crlf", True]
    assert out["marked"][:4] == ["a\nb", True, "crlf", True]
    assert out["empty"][:4] == ["", False, "lf", True]
    assert out["mixedMostlyCrlf"][2] == "crlf" and out["mixedMostlyCrlf"][4] == "a\r\nb\r\nc\r\n"
    assert out["mixedMostlyLf"][2] == "lf" and out["mixedMostlyLf"][4] == "a\nb\nc\n"
    assert out["typed"][4] == "a\r\nb\r\nc\r\nd\r\n"
    assert out["markCode"] == 0xFEFF


def test_reading_a_file_reports_gone_apart_from_other_failures():
    out = _run_case(
        r"""
        answers = [
          textAnswer("hello"),
          [404, { success: false, error: "not_found" }],
          [403, { success: false, error: "path outside workspace" }],
          [200, { success: false, error: "not_found" }],
          "network",
        ];
        out.results = [];
        for (let i = 0; i < 5; i += 1) out.results.push(await readServerFileText("agent-A", "/workspace/docs/a b.md"));
        """
    )

    assert out["requests"][0] == ["/a/agent-A/api/server-files/read?path=%2Fworkspace%2Fdocs%2Fa%20b.md", "GET", None]
    assert out["results"][0]["ok"] is True and out["results"][0]["read"]["content"] == "hello"
    assert out["results"][1] == {"ok": False, "gone": True, "error": "It is no longer there. Reload the folder."}
    assert out["results"][2] == {"ok": False, "gone": False, "error": "path outside workspace"}
    assert out["results"][3] == {"ok": False, "gone": False, "error": "It is no longer there. Reload the folder."}
    assert out["results"][4] == {"ok": False, "gone": False, "error": "Failed to fetch"}


def test_a_save_goes_through_the_upload_call_under_the_file_s_own_name():
    out = _run_case(
        r"""
        uploadAnswers = [{ ok: true }, { ok: false, error: "disk full" }, { ok: false }];
        out.results = [
          await saveServerFileText("agent-A", "/workspace/docs/plan.md", "a\r\nb"),
          await saveServerFileText("agent-A", "/workspace/top.txt", "x"),
          await saveServerFileText("agent-A", "/workspace/top.txt", "x"),
        ];
        tooLarge = "Larger than the 50 MB limit";
        out.results.push(await saveServerFileText("agent-A", "/workspace/top.txt", "x"));
        """
    )

    assert out["uploads"] == [
        ["agent-A", "/workspace/docs", "plan.md", "a\r\nb", [97, 13, 10]],
        ["agent-A", "/workspace", "top.txt", "x", [120]],
        ["agent-A", "/workspace", "top.txt", "x", [120]],
    ]
    # A file over the cap is refused before it is sent.
    assert out["results"] == ["", "disk full", "Upload failed", "Larger than the 50 MB limit"]


# ----- the editor ---------------------------------------------------------------

def test_typing_is_kept_as_a_draft_and_a_save_writes_it_back():
    out = _run_case(
        r"""
        openServerFileEditor(viewOf("a\r\nb\r\n"), null);
        const ed = editorOf();
        out.opened = [screen.title, ed.area.value, ed.area.focused, ed.status.textContent, serverFileDrafts.size, ed.save.disabled];
        out.hasPath = screen.html.includes("[/workspace|/workspace/docs]");

        type("a\nB\n");
        out.typed = [ed.status.textContent, Array.from(serverFileDrafts.keys()), serverFileDrafts.get("agent-A\n/workspace/docs/plan.md"), ed.save.disabled];
        // Back to what the file holds: nothing to keep, nothing to save.
        type("a\nb\n");
        out.backToStart = [ed.status.textContent, serverFileDrafts.size, ed.save.disabled];

        type("a\nB\n");
        answers = [textAnswer("a\r\nb\r\n")];
        const saving = ed.save.fire("click");
        out.whileSaving = [ed.save.disabled, ed.close.disabled, screen.editor.dataset.saving];
        await saving;
        // Still in the editor, with nothing left to save.
        out.after = [serverFileDrafts.size, ed.status.textContent, ed.save.disabled, ed.close.disabled, screen.editor.dataset.saving, out.panels.length];

        // The next save compares with what this one wrote: no question.
        type("a\nB\nc\n");
        answers = [textAnswer("a\r\nB\r\n")];
        await ed.save.fire("click");
        out.second = [out.confirms.length, ed.status.textContent];
        """
    )

    assert out["opened"] == ["File: plan.md", "a\nb\n", 1, "", 0, True]
    assert out["hasPath"] is True
    assert out["typed"] == [
        "Unsaved changes",
        ["agent-A\n/workspace/docs/plan.md"],
        {"text": "a\nB\n", "base": "a\r\nb\r\n"},
        False,
    ]
    assert out["backToStart"] == ["", 0, True]
    assert out["whileSaving"] == [True, True, "1"]
    # The runtime's copy is read first; it had not changed, so nothing is asked.
    assert out["requests"][0] == ["/a/agent-A/api/server-files/read?path=%2Fworkspace%2Fdocs%2Fplan.md", "GET", None]
    # Written with the line ending the file had.
    assert out["uploads"] == [
        ["agent-A", "/workspace/docs", "plan.md", "a\r\nB\r\n", [97, 13, 10]],
        ["agent-A", "/workspace/docs", "plan.md", "a\r\nB\r\nc\r\n", [97, 13, 10]],
    ]
    assert out["after"] == [0, "Saved", True, False, "", 1]
    assert out["second"] == [0, "Saved"]
    # On screen the status line says it; no toast, and the editor stays.
    assert out["toasts"] == [] and out["previews"] == []


def test_what_is_typed_while_a_save_runs_is_kept_as_a_change_not_saved_yet():
    out = _run_case(
        r"""
        openServerFileEditor(viewOf("one"), null);
        const ed = editorOf();
        type("mine");
        answers = [textAnswer("one")];
        let release;
        uploadGate = new Promise((resolve) => { release = resolve; });
        const saving = ed.save.fire("click");
        await settle();
        // The upload is on its way; the box was never locked.
        type("mine and more");
        release();
        await saving;
        out.result = [out.uploads[0][3], ed.area.value, ed.status.textContent, ed.save.disabled, serverFileDrafts.get("agent-A\n/workspace/docs/plan.md")];
        """
    )

    assert out["result"] == ["mine", "mine and more", "Unsaved changes", False, {"text": "mine and more", "base": "mine"}]


def test_save_does_nothing_until_something_was_typed():
    out = _run_case(
        r"""
        openServerFileEditor(viewOf("same"), null);
        const ed = editorOf();
        await ed.save.fire("click");
        await screen.editor.fire("keydown", { key: "s", ctrlKey: true });
        await settle();
        out.disabled = ed.save.disabled;
        """
    )

    assert out["disabled"] is True
    assert out["requests"] == [] and out["uploads"] == [] and out["toasts"] == [] and out["previews"] == []


def test_a_file_changed_in_the_meantime_is_not_replaced_without_asking():
    out = _run_case(
        r"""
        openServerFileEditor(viewOf("one"), null);
        const ed = editorOf();
        type("mine");

        // The assistant wrote to it: the member says no.
        answers = [textAnswer("theirs")];
        confirmAnswers = [false];
        await ed.save.fire("click");
        out.declined = [out.uploads.length, ed.status.textContent, ed.status.classes.has("is-error"), ed.save.disabled, serverFileDrafts.size];

        // Asked again on the next save: yes.
        answers = [textAnswer("theirs")];
        confirmAnswers = [true];
        await ed.save.fire("click");
        out.accepted = [out.uploads.length, serverFileDrafts.size, ed.status.textContent];

        // It became a file that is not text.
        openServerFileEditor(viewOf(""), null);
        type("mine");
        answers = [textAnswer("", { is_binary: true })];
        confirmAnswers = [false];
        await editorOf().save.fire("click");

        // It was deleted.
        answers = [[404, { success: false, error: "not_found" }]];
        confirmAnswers = [true];
        await editorOf().save.fire("click");
        out.recreated = out.uploads.length;
        """
    )

    assert out["declined"] == [0, "Not saved", True, False, 1]
    assert out["accepted"] == [1, 0, "Saved"]
    changed = [
        "The file changed",
        "plan.md was changed after you opened it, perhaps by the assistant. Save your text over it?",
        "Save my text",
        False,
    ]
    gone = ["The file is gone", "plan.md is no longer there. Save your text as a new file?", "Save my text", False]
    assert out["confirms"] == [changed, changed, changed, gone]
    assert out["recreated"] == 2


def test_a_save_that_fails_keeps_the_text_and_says_why():
    out = _run_case(
        r"""
        openServerFileEditor(viewOf("one"), null);
        const ed = editorOf();
        type("mine");

        // The runtime could not be asked what it holds: nothing is written.
        answers = ["network"];
        await ed.save.fire("click");
        out.unchecked = [ed.status.textContent, out.uploads.length, ed.save.disabled, ed.area.value, serverFileDrafts.size];

        answers = [textAnswer("one")];
        uploadAnswers = [{ ok: false, error: "disk full" }];
        await ed.save.fire("click");
        out.failed = [ed.status.textContent, ed.status.classes.has("is-error"), ed.area.value, serverFileDrafts.size, ed.save.disabled];
        out.toastsSoFar = out.toasts.length;

        // The member went to another panel while it was being saved: the
        // failure is a toast, and the text is still there to come back to.
        answers = [textAnswer("one")];
        uploadAnswers = [{ ok: false, error: "disk full" }];
        let saving = ed.save.fire("click");
        setToolPanel("Sessions", "<p>sessions</p>", "sessions");
        await saving;
        out.draftKept = serverFileDrafts.get("agent-A\n/workspace/docs/plan.md");

        // The same, but the save went through: said in a toast, nothing kept.
        answers = [textAnswer("one")];
        saving = ed.save.fire("click");
        await saving;
        out.draftAfterSave = serverFileDrafts.size;
        """
    )

    assert out["unchecked"] == ["Could not check the file: Failed to fetch", 0, False, "mine", 1]
    assert out["failed"] == ["Could not save: disk full", True, "mine", 1, False]
    assert out["toastsSoFar"] == 0
    # Not the "kept" notice (it was being saved), then the failure.
    assert out["toasts"] == [
        ["plan.md was not saved (Could not save: disk full). Your text is kept: open the file again to continue.", "error", ""],
        ["Saved plan.md", "", ""],
    ]
    assert out["draftKept"] == {"text": "mine", "base": "one"}
    assert out["draftAfterSave"] == 0


def test_close_asks_before_dropping_typed_text():
    out = _run_case(
        r"""
        openServerFileEditor(viewOf("one"), null);
        let ed = editorOf();
        // Nothing typed: straight back to the file.
        await ed.close.fire("click");
        out.clean = [out.confirms.length, out.previews.length];

        openServerFileEditor(viewOf("one"), null);
        ed = editorOf();
        type("mine");
        confirmAnswers = [false];
        await ed.close.fire("click");
        out.kept = [serverFileDrafts.size, out.previews.length, ed.area.value];
        confirmAnswers = [true];
        await ed.close.fire("click");
        out.dropped = [serverFileDrafts.size, out.previews.length];
        """
    )

    assert out["clean"] == [0, 1]
    assert out["kept"] == [1, 1, "mine"]
    assert out["dropped"] == [0, 2]
    assert out["confirms"] == [["Discard changes?", "Your changes to plan.md will be lost.", "Discard", True]] * 2
    assert out["previews"][0] == ["/workspace/docs/plan.md", "/workspace/docs", "/workspace", None]


def test_keys_in_the_editor():
    out = _run_case(
        r"""
        openServerFileEditor(viewOf("one"), null);
        const ed = editorOf();
        type("mine");
        answers = [textAnswer("one")];
        // From the box or from a button: the keys are the editor's.
        const save = await screen.editor.fire("keydown", { key: "s", ctrlKey: true });
        await settle();
        out.saved = [save.prevented, out.uploads.length, ed.status.textContent];

        // A key that ends an input method's composition is not a command.
        const composing = await screen.editor.fire("keydown", { key: "Escape", isComposing: true });
        out.composing = [composing.prevented, out.previews.length];
        const other = await screen.editor.fire("keydown", { key: "a" });
        out.plain = other.prevented;
        const esc = await screen.editor.fire("keydown", { key: "Escape" });
        await settle();
        out.escaped = [esc.prevented, out.previews.length];
        """
    )

    assert out["saved"] == [True, 1, "Saved"]
    assert out["composing"] == [False, 0]
    assert out["plain"] is False
    assert out["escaped"] == [True, 1]


def test_unsaved_text_outlives_the_panel_and_comes_back_with_the_file():
    out = _run_case(
        r"""
        answers = [textAnswer("one")];
        await previewServerFile("/workspace/docs/plan.md", "/workspace/docs", "/workspace");
        screen.parts[".sf-edit-btn"].fire("click");
        type("mine");

        // The panel shows something else: the member is told once.
        setToolPanel("Server Files", "<div>list</div>", "server-files");
        setToolPanel("Server Files", "<div>list</div>", "server-files");
        out.left = out.toasts.slice();

        // The assistant rewrote the file meanwhile; the draft still knows
        // what it started from, so the save asks.
        answers = [textAnswer("theirs")];
        await previewServerFile("/workspace/docs/plan.md", "/workspace/docs", "/workspace");
        const ed = editorOf();
        out.restored = [ed.area.value, ed.status.textContent];
        answers = [textAnswer("theirs")];
        confirmAnswers = [false];
        await ed.save.fire("click");
        out.asked = out.confirms.length;

        // The file can no longer be read: the text is still reachable.
        answers = [[404, { success: false, error: "not_found" }]];
        await previewServerFile("/workspace/docs/plan.md", "/workspace/docs", "/workspace");
        out.unreadable = [editorOf().area.value, editorOf().status.textContent];
        """,
        extra=("previewServerFile",),
    )

    assert out["left"] == [["Your unsaved changes to plan.md are kept. Open the file again to continue.", "info", ""]]
    assert out["restored"] == ["mine", "Your unsaved changes were restored"]
    assert out["asked"] == 1
    assert out["unreadable"] == ["mine", "The file could not be read: It is no longer there. Reload the folder."]


def test_a_draft_follows_its_file_and_goes_with_it():
    out = _run_case(
        r"""
        const keys = () => Array.from(serverFileDrafts.keys()).sort();
        const fill = () => {
          serverFileDrafts.clear();
          ["/workspace/docs/plan.md", "/workspace/docs/sub/deep.txt", "/workspace/docs-old/plan.md", "/workspace/top.txt"].forEach((path) => {
            serverFileDrafts.set("agent-A\n" + path, { text: path, base: "" });
          });
          serverFileDrafts.set("agent-B\n/workspace/docs/plan.md", { text: "other assistant", base: "" });
        };

        fill();
        carryServerFileDrafts("agent-A", "/workspace/docs/plan.md", "/workspace/docs/renamed.md");
        out.renamed = keys();

        // A folder: everything under it, and nothing that merely starts alike.
        fill();
        carryServerFileDrafts("agent-A", "/workspace/docs/", "/workspace/archive/docs");
        out.folderMoved = keys();
        out.text = serverFileDrafts.get("agent-A\n/workspace/archive/docs/sub/deep.txt").text;

        fill();
        carryServerFileDrafts("agent-A", "/workspace/docs", "");
        out.deleted = keys();

        // Through the calls themselves.
        fill();
        answers = [[200, { success: true }], [200, { success: true }], [400, { success: false, error: "destination already exists" }]];
        await serverFilesPost("agent-A", "move", { source: "/workspace/top.txt", destination: "/workspace/docs/top.txt" });
        await serverFilesPost("agent-A", "delete", { paths: ["/workspace/docs-old", "/workspace/docs/sub"] });
        await serverFilesPost("agent-A", "move", { source: "/workspace/docs/plan.md", destination: "/workspace/x.md" });
        out.viaCalls = keys();
        """
    )

    other = "agent-B\n/workspace/docs/plan.md"
    assert out["renamed"] == sorted([
        "agent-A\n/workspace/docs/renamed.md",
        "agent-A\n/workspace/docs/sub/deep.txt",
        "agent-A\n/workspace/docs-old/plan.md",
        "agent-A\n/workspace/top.txt",
        other,
    ])
    assert out["folderMoved"] == sorted([
        "agent-A\n/workspace/archive/docs/plan.md",
        "agent-A\n/workspace/archive/docs/sub/deep.txt",
        "agent-A\n/workspace/docs-old/plan.md",
        "agent-A\n/workspace/top.txt",
        other,
    ])
    assert out["text"] == "/workspace/docs/sub/deep.txt"
    assert out["deleted"] == sorted(["agent-A\n/workspace/docs-old/plan.md", "agent-A\n/workspace/top.txt", other])
    # A failed move carries nothing.
    assert out["viaCalls"] == sorted(["agent-A\n/workspace/docs/plan.md", "agent-A\n/workspace/docs/top.txt", other])


# ----- the page a text file is shown on -----------------------------------------

def test_a_text_file_offers_edit_and_anything_else_a_download_with_the_reason():
    out = _run_case(
        r"""
        const show = async (answer, options) => {
          answers = [answer];
          await previewServerFile("/workspace/docs/plan.md", "/workspace/docs", "/workspace", options);
          return {
            title: screen.title,
            edit: Boolean(screen.parts[".sf-edit-btn"]),
            download: Boolean(screen.parts[".sf-preview-download-btn"]),
            editor: Boolean(screen.editor),
            pre: screen.html.includes('<pre class="portal-panel-pre">'),
            note: (screen.html.match(/portal-file-preview-note">([^<]*)</) || [])[1] || "",
            path: screen.html.includes("[/workspace|/workspace/docs]"),
          };
        };
        out.text = await show(textAnswer("a <b> c"));
        out.escaped = screen.html.includes("a &lt;b> c");
        screen.parts[".sf-edit-btn"].fire("click");
        out.editing = [Boolean(screen.editor), editorOf().area.value];

        out.empty = await show(textAnswer(""));
        out.emptyText = screen.html.includes("(empty file)");
        out.binary = await show(textAnswer("", { is_binary: true }));
        screen.parts[".sf-preview-download-btn"].fire("click");
        out.cut = await show(textAnswer("first part", { truncated: true }));

        // A new file is opened for its text straight away, if it can be.
        out.fresh = await show(textAnswer(""), { edit: true });
        out.freshBinary = await show(textAnswer("", { is_binary: true }), { edit: true });

        out.missing = await show([404, { success: false, error: "not_found" }]);
        out.missingText = screen.html.includes("Unable to preview this file: It is no longer there. Reload the folder.");

        // The member went to another panel while the runtime answered.
        const panelsBefore = out.panels.length;
        onFetch = () => { state.activeUtilityPanel = "sessions"; };
        answers = [textAnswer("late")];
        await previewServerFile("/workspace/docs/plan.md", "/workspace/docs", "/workspace");
        out.late = out.panels.length - panelsBefore;
        """,
        extra=("previewServerFile",),
    )

    assert out["text"] == {"title": "File: plan.md", "edit": True, "download": False, "editor": False, "pre": True, "note": "", "path": True}
    assert out["escaped"] is True
    assert out["editing"] == [True, "a <b> c"]
    assert out["empty"]["edit"] is True and out["emptyText"] is True
    assert out["binary"] == {
        "title": "File: plan.md", "edit": False, "download": True, "editor": False, "pre": False,
        "note": "This file is not text, so it cannot be shown here.", "path": True,
    }
    assert out["downloads"] == [["/workspace/docs/plan.md"]]
    assert out["cut"]["edit"] is False and out["cut"]["download"] is True and out["cut"]["pre"] is True
    assert out["cut"]["note"] == "Only the first part of this file is shown. It is too large to edit here."
    assert out["fresh"]["editor"] is True
    assert out["freshBinary"]["editor"] is False and out["freshBinary"]["download"] is True
    # With the path above the reason, so there is a way back to the folder.
    assert out["missing"]["path"] is True and out["missingText"] is True
    assert out["late"] == 0


def test_a_file_named_with_enter_is_opened_and_one_named_by_a_click_elsewhere_is_not():
    out = _run_case(
        r"""
        const box = (value) => {
          const handlers = {};
          const input = {
            value, disabled: false,
            addEventListener(type, fn) { handlers[type] = fn; },
            setAttribute() {}, focus() {},
          };
          const error = { textContent: "", classList: { toggle() {} } };
          const saved = [];
          bindServerFileNameEditor(input, error, { initial: "", siblings: [], cancel: () => {}, save: async (name, fromBlur) => { saved.push([name, fromBlur]); return ""; } });
          return { handlers, saved };
        };
        const byEnter = box("notes.md");
        byEnter.handlers.keydown({ key: "Enter", preventDefault() {} });
        await tick();
        const byBlur = box("notes.md");
        byBlur.handlers.blur();
        await tick();
        out.saved = [byEnter.saved, byBlur.saved];
        """
    )

    assert out["saved"] == [[["notes.md", False]], [["notes.md", True]]]

    create = _extract_js_function(SRC.read_text(encoding="utf-8"), "startServerFileCreate")
    assert "if (!isFolder && !fromBlur) {" in create
    assert "previewServerFile(path, currentPath, state.serverFilesRootPath, { edit: true });" in create


def test_the_panel_tells_the_editor_before_it_shows_something_else():
    js = SRC.read_text(encoding="utf-8")
    hook = 'if (typeof noteServerFileEditorLeft === "function") noteServerFileEditorLeft();'
    assert hook in _extract_js_function(js, "setToolPanel")
    assert hook in _extract_js_function(js, "closeToolPanel")
    # Unsaved text does not outlive the page, which asks first.
    bound = _extract_js_function(js, "bindServerFilesPanelOnce")
    assert 'window.addEventListener("beforeunload"' in bound
    assert "if (serverFileDrafts.size === 0) return;" in bound

    css = CSS.read_text(encoding="utf-8")
    for selector in (".portal-file-editor-text {", ".portal-file-editor-status.is-error {", ".portal-file-preview-note {", ".portal-file-row.is-deleting {"):
        assert selector in css


# ----- undo ---------------------------------------------------------------------

def test_a_move_can_be_taken_back_from_its_toast():
    out = _run_case(
        r"""
        answers = [
          [200, { success: true }],
          [400, { success: false, error: "destination already exists" }],
          [200, { success: true }],
        ];
        await runServerFilesMove("agent-A", ["/workspace/docs/a.txt", "/workspace/docs/reports", "/workspace/docs/c.md"], "/workspace/archive");
        out.offered = out.toasts.slice();
        out.reloadsAfterMove = out.reloads.length;

        // Undo: only what moved goes back, each to where it was.
        answers = [[200, { success: true }], [200, { success: true }]];
        const before = out.requests.length;
        toasts[0].action.run();
        // Pressed once: a second press does nothing.
        toasts[0].action.run();
        await settle();
        out.undoRequests = out.requests.slice(before);
        out.afterUndo = out.toasts.slice(1);
        out.cleared = serverFilesUndo === null;
        """
    )

    assert out["offered"] == [["Moved 2 of 3 items. Failed: reports: Something with that name already exists", "error", "Undo"]]
    assert out["reloadsAfterMove"] == 1
    assert out["undoRequests"] == [
        ["/a/agent-A/api/server-files/move", "POST", {"source": "/workspace/archive/a.txt", "destination": "/workspace/docs/a.txt"}],
        ["/a/agent-A/api/server-files/move", "POST", {"source": "/workspace/archive/c.md", "destination": "/workspace/docs/c.md"}],
    ]
    assert out["afterUndo"] == [["Moved 2 items back", "", ""]]
    # What came back into the folder on screen is pointed out.
    assert out["reloads"][-1] == ["/workspace/docs", ["a.txt", "c.md"]]
    assert out["cleared"] is True


def test_nothing_moved_offers_no_undo_and_a_failed_undo_says_what_stayed():
    out = _run_case(
        r"""
        answers = [[400, { success: false, error: "invalid destination" }]];
        await runServerFilesMove("agent-A", ["/workspace/docs/a.txt"], "/workspace/docs/a.txt");
        out.nothing = [out.toasts[0], serverFilesUndo === null];

        answers = [[200, { success: true }], [200, { success: true }]];
        await runServerFilesMove("agent-A", ["/workspace/docs/a.txt", "/workspace/docs/b.txt"], "/workspace/archive");
        // Something took a.txt's old name in the meantime.
        answers = [[400, { success: false, error: "destination already exists" }], [200, { success: true }]];
        toasts[1].action.run();
        await settle();
        out.partial = out.toasts[2];
        out.revealed = out.reloads[out.reloads.length - 1];
        """
    )

    assert out["nothing"] == [["Moved 0 of 1 items. Failed: a.txt: It cannot be moved there", "error", ""], True]
    assert out["partial"] == ["Could not undo: a.txt: Something with that name already exists", "error", ""]
    assert out["revealed"] == ["/workspace/docs", ["b.txt"]]


def test_ctrl_z_presses_undo_only_while_its_toast_is_on_screen():
    out = _run_case(
        r"""
        const offer = () => {
          showServerFilesChangeToast("Renamed a.txt to b.txt", "agent-A", [{ from: "/workspace/docs/a.txt", to: "/workspace/docs/b.txt" }], "Renamed back to a.txt");
          return toasts[toasts.length - 1];
        };
        out.none = runServerFilesUndo();

        // The toast has gone.
        let toast = offer();
        toast.isConnected = false;
        out.gone = [runServerFilesUndo(), serverFilesUndo === null];

        // It is on its way out.
        toast = offer();
        toast.dataset.leaving = "1";
        out.leaving = runServerFilesUndo();

        // Another assistant is on screen.
        toast = offer();
        state.selectedAgentId = "agent-B";
        out.otherAssistant = runServerFilesUndo();
        state.selectedAgentId = "agent-A";

        answers = [[200, { success: true }]];
        out.pressed = runServerFilesUndo();
        out.dismissed = toast.dataset.leaving;
        await settle();
        out.again = runServerFilesUndo();
        """
    )

    assert out["none"] is False
    assert out["gone"] == [False, True]
    assert out["leaving"] is False
    assert out["otherAssistant"] is False
    assert out["pressed"] is True and out["dismissed"] == "1"
    assert out["requests"] == [
        ["/a/agent-A/api/server-files/move", "POST", {"source": "/workspace/docs/b.txt", "destination": "/workspace/docs/a.txt"}],
    ]
    assert out["toasts"][-1] == ["Renamed back to a.txt", "", ""]
    assert out["again"] is False


def test_a_rename_reports_itself_with_undo():
    rename = _extract_js_function(SRC.read_text(encoding="utf-8"), "startServerFileRename")
    assert 't("Renamed {from} to {to}", { from: original, to: name })' in rename
    assert "[{ from: path, to: destination }]," in rename
    assert 't("Renamed back to {name}", { name: original })' in rename


def test_a_toast_with_an_action_stays_longer_and_runs_it_once():
    out = _run_case(
        r"""
        const timers = [];
        globalThis.setTimeout = (fn, ms) => { timers.push(ms); return timers.length; };
        globalThis.clearTimeout = () => {};
        const make = (tag) => {
          const handlers = {};
          const classes = new Set();
          const node = {
            tag, children: [], dataset: {}, textContent: "", className: "", offsetWidth: 0,
            classList: { add(name) { classes.add(name); }, remove(name) { classes.delete(name); }, contains(name) { return classes.has(name); } },
            append(child) { node.children.push(child); },
            addEventListener(type, fn) { handlers[type] = fn; },
            setAttribute() {},
            remove() { node.removed = true; },
            click() { handlers.click(); },
            get childElementCount() { return node.children.length; },
            get firstElementChild() { return node.children[0]; },
          };
          return node;
        };
        const stack = make("div");
        document.getElementById = (id) => (id === "global-toast" ? stack : null);
        document.createElement = make;

        let ran = 0;
        const toast = showToast("Moved 1 item to /workspace/archive", { action: { label: "Undo", run: () => { ran += 1; } } });
        const button = toast.children.find((child) => child.className === "portal-toast-action");
        out.button = button.textContent;
        out.durations = [timers[0]];
        button.click();
        button.click();
        out.ran = ran;
        out.leaving = toast.dataset.leaving;

        timers.length = 0;
        showToast("Uploaded 2 files");
        showToast("Failed", { variant: "error" });
        out.durations.push(timers[0], timers[1]);
        out.plainHasNoAction = stack.children[1].children.every((child) => child.className !== "portal-toast-action");
        """,
        extra=("dismissToast", "showToast"),
    )

    assert out["button"] == "Undo"
    assert out["durations"] == [8000, 2600, 8000]
    assert out["ran"] == 1
    assert out["leaving"] == "1"
    assert out["plainHasNoAction"] is True


# ----- delete -------------------------------------------------------------------

def test_delete_leaves_the_list_on_screen_and_moves_the_keyboard_on():
    out = _run_case(
        r"""
        const makeRows = (paths) => paths.map((path) => fakeEl({ dataset: { path } }));
        let rows = makeRows(["/workspace/docs/a", "/workspace/docs/b", "/workspace/docs/c", "/workspace/docs/d"]);
        listPanel = { querySelectorAll: () => rows };
        state.serverFilesItems = [{ path: "/workspace/docs/b", name: "b", is_dir: false }];
        confirmAnswers = [true];
        answers = [[200, { success: true }]];
        // The redraw: the deleted rows are gone, and so is the focus they held.
        onReload = () => {
          out.dimmed = rows.filter((row) => row.classes.has("is-deleting")).map((row) => row.dataset.path);
          rows = makeRows(["/workspace/docs/a", "/workspace/docs/d"]);
          document.activeElement = document.body;
        };
        await deleteSelectedServerFiles(["/workspace/docs/b", "/workspace/docs/c"], "/workspace/docs");
        out.focus = rows.map((row) => row.focused);

        // The last rows went: the keyboard lands on what is now the last one.
        rows = makeRows(["/workspace/docs/a", "/workspace/docs/d"]);
        confirmAnswers = [true];
        answers = [[200, { success: true }]];
        onReload = () => { rows = makeRows(["/workspace/docs/a"]); document.activeElement = document.body; };
        await deleteSelectedServerFiles(["/workspace/docs/d"], "/workspace/docs");
        out.focusLast = rows.map((row) => row.focused);

        // Focus that went elsewhere meanwhile is left where it is.
        rows = makeRows(["/workspace/docs/a", "/workspace/docs/d"]);
        confirmAnswers = [true];
        answers = [[200, { success: true }]];
        onReload = () => { rows = makeRows(["/workspace/docs/a"]); document.activeElement = { composer: true }; };
        await deleteSelectedServerFiles(["/workspace/docs/d"], "/workspace/docs");
        out.focusElsewhere = rows.map((row) => row.focused);
        """
    )

    assert out["confirms"][0][1] == "Delete 2 selected items? This can't be undone."
    assert out["requests"][0] == ["/a/agent-A/api/server-files/delete", "POST", {"paths": ["/workspace/docs/b", "/workspace/docs/c"]}]
    assert out["dimmed"] == ["/workspace/docs/b", "/workspace/docs/c"]
    # The panel was never replaced by a "Deleting" line.
    assert out["panels"] == []
    assert out["toasts"] == [["Deleted 2 items", "", ""], ["Deleted 1 item", "", ""], ["Deleted 1 item", "", ""]]
    assert out["reloads"][0] == ["/workspace/docs", []]
    assert out["focus"] == [0, 1]
    assert out["focusLast"] == [1]
    assert out["focusElsewhere"] == [0]


def test_a_failed_delete_says_why_and_still_reads_the_folder_again():
    out = _run_case(
        r"""
        listPanel = { querySelectorAll: () => [] };
        confirmAnswers = [true, false, true];
        answers = [[403, { success: false, error: "path outside workspace" }]];
        await deleteSelectedServerFiles(["/workspace/docs/a"], "/workspace/docs");
        // Not confirmed: nothing is asked of the runtime.
        await deleteSelectedServerFiles(["/workspace/docs/a"], "/workspace/docs");
        out.afterDecline = out.requests.length;

        // The member left the Files panel while it was being deleted.
        answers = [[200, { success: true }]];
        onFetch = () => { state.activeUtilityPanel = "sessions"; };
        await deleteSelectedServerFiles(["/workspace/docs/a"], "/workspace/docs");
        """
    )

    assert out["toasts"] == [["Delete failed: path outside workspace", "error", ""], ["Deleted 1 item", "", ""]]
    assert out["afterDecline"] == 1
    # Some of the items may be gone even after a failure; away from the panel
    # there is no list to redraw.
    assert out["reloads"] == [["/workspace/docs", []]]
    assert out["panels"] == []
