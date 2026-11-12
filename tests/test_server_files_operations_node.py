"""The Files panel's operations: new file, new folder, rename, move.

The toolbar keeps two buttons (Upload, New); what acts on ticked rows lives in
the "Select all" line and what acts on one row in that row's menu. The
runtimes already serve ``mkdir``, ``new-file`` and ``move``; these cases cover
the panel's side: the layout contract, the name rules, the requests, and the
name box that a new item and a rename share.
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

HELPERS = [
    "trimServerFilePath",
    "serverFileBaseName",
    "joinServerFilePath",
    "serverFilesCrumbs",
    "sortServerFileItems",
    "formatServerFileSize",
    "formatServerFileModified",
    "serverFileNameProblem",
    "describeServerFileError",
    "parseRuntimeErrorResponse",
    "serverFilesPost",
    "moveServerFilesTo",
    "describeServerFilesMove",
    "bindServerFileNameEditor",
    "serverFilesDragKind",
    "serverFilesMoveAllowed",
    "readServerFileEntries",
    "collectDroppedServerFiles",
    "serverFilesListingSignature",
]

HARNESS = r"""
const SERVER_FILES_UPLOAD_FAILURES_SHOWN = 5;
const SERVER_FILES_DRAG_TYPE = "application/x-efp-server-files";
const SERVER_FILES_DROP_LIMIT = 5;
const SERVER_FILES_IGNORED_NAMES = [".DS_Store", "Thumbs.db"];
const out = { requests: [] };
let answers = [];
async function fetch(url, options) {
  out.requests.push([url, options.method, JSON.parse(options.body)]);
  const [status, body] = answers.shift();
  const raw = typeof body === "string" ? body : JSON.stringify(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => JSON.parse(raw),
    text: async () => raw,
  };
}
// The name box and its error line, as far as the editor touches them.
function fakeInput(value) {
  const handlers = {};
  return {
    value,
    disabled: false,
    attrs: {},
    focused: 0,
    addEventListener(type, fn) { handlers[type] = fn; },
    setAttribute(name, v) { this.attrs[name] = v; },
    focus() { this.focused += 1; },
    fire(type, extra) {
      return handlers[type](Object.assign({ key: "", preventDefault() {}, stopPropagation() {} }, extra || {}));
    },
  };
}
function fakeError() {
  const classes = new Set(["hidden"]);
  return {
    textContent: "",
    classList: { toggle(name, on) { if (on) classes.add(name); else classes.delete(name); } },
    get hidden() { return classes.has("hidden"); },
  };
}
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
function editor(value, options) {
  const input = fakeInput(value);
  const error = fakeError();
  const log = { saved: [], cancelled: 0 };
  bindServerFileNameEditor(input, error, {
    initial: options.initial,
    siblings: options.siblings || [],
    save: async (name) => { log.saved.push(name); return options.failure || ""; },
    cancel: () => { log.cancelled += 1; },
  });
  return { input, error, log };
}
"""


def _run_case(body: str) -> dict:
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping the Files operations test")
    js = SRC.read_text(encoding="utf-8")
    bundle = "\n".join(_extract_js_function(js, name) for name in HELPERS)
    script = HARNESS + bundle + "\n(async () => {\n" + body + "\nconsole.log(JSON.stringify(out));\n})();\n"
    with tempfile.TemporaryDirectory() as tmp:
        script_path = Path(tmp) / "server_files_operations_case.js"
        script_path.write_text(script, encoding="utf-8")
        result = subprocess.run(
            [node_bin, str(script_path)], check=False, capture_output=True, text=True, encoding="utf-8"
        )
    if result.returncode != 0:
        raise AssertionError(f"node failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def _panel_block() -> str:
    js = SRC.read_text(encoding="utf-8")
    start = js.index("async function loadServerFiles(path) {")
    return js[start:js.index("function getSelectedFiles(panel) {", start)]


def test_toolbar_keeps_two_buttons_and_the_rest_moves_to_the_selection_line_and_row_menu():
    block = _panel_block()
    toolbar_start = block.index('class="portal-file-toolbar-actions"')
    select_start = block.index('class="portal-file-select-row"')
    list_start = block.index('class="portal-panel-stack portal-file-list"')
    toolbar = block[toolbar_start:select_start]
    selection = block[select_start:list_start]

    # The toolbar acts on the folder: Upload and New, nothing else.
    assert toolbar.count("<button") == 2
    assert "sf-upload-btn" in toolbar and "sf-new-btn" in toolbar
    assert "sf-download-btn" not in toolbar and "sf-delete-btn" not in toolbar

    # What acts on the ticked rows is hidden until a row is ticked.
    assert 'class="portal-file-selection-actions hidden"' in selection
    for name in ("sf-download-btn", "sf-move-btn", "sf-delete-btn"):
        assert name in selection

    # Every row carries its own menu button; one popover serves them all.
    assert "data-sf-row-menu" in block
    assert block.count('popover="manual"') == 1
    assert 'data-sf-menu></div>' in block
    assert 't("New file")' in block and 't("New folder")' in block

    js = SRC.read_text(encoding="utf-8")
    entries = _extract_js_function(js, "serverFileRowMenuEntries")
    labels = [label for label in ("Rename", "Move", "Download", "Copy path", "Delete") if f't("{label}")' in entries]
    assert labels == ["Rename", "Move", "Download", "Copy path", "Delete"]


def test_styles_keep_the_menu_above_the_list_and_the_row_at_its_height():
    css = CSS.read_text(encoding="utf-8")

    def block(selector: str) -> str:
        start = css.index(selector)
        return css[start:css.index("}", start)]

    menu = block(".portal-file-menu {")
    assert "position: fixed" in menu
    # Closed is the default; only an open menu gets a display.
    assert "display: none" in menu
    assert "display: grid" in block(".portal-file-menu:popover-open {")

    button = block(".portal-file-row-menu-button {")
    assert "opacity: 0" in button
    assert "margin: -4px -4px -4px 0" in button
    # A touch screen has no hover to reveal the button with.
    assert "@media (hover: none)" in css

    # A narrow panel keeps the name and drops the time, then the size.
    assert "container-type: inline-size" in block(".portal-file-browser {")
    assert "@container (max-width: 400px)" in css
    assert "@container (max-width: 300px)" in css


def test_names_paths_sizes_and_order():
    out = _run_case(
        """
        out.join = [joinServerFilePath("/workspace", "a.txt"), joinServerFilePath("/workspace/", "a.txt"), joinServerFilePath("/", "a"), joinServerFilePath("", "a")];
        out.base = [serverFileBaseName("/workspace/docs/plan.md"), serverFileBaseName("/workspace/docs/"), serverFileBaseName("")];
        out.crumbs = serverFilesCrumbs("/workspace/docs/2026", "/workspace");
        out.rootCrumbs = serverFilesCrumbs("/workspace/", "/workspace");
        out.outside = serverFilesCrumbs("/elsewhere/x", "/workspace");
        out.sorted = sortServerFileItems([
          { name: "file10.txt", is_dir: false },
          { name: "Zeta", is_dir: true },
          { name: "file2.txt", is_dir: false },
          { name: "alpha", is_dir: true },
          { name: "File1.txt", is_dir: false },
        ]).map((item) => item.name);
        out.sizes = [0, 1, 1023, 1024, 15360, 5 * 1024 * 1024, 250 * 1024 * 1024, 3 * 1024 * 1024 * 1024, null, undefined, "", -1, "x"].map(formatServerFileSize);
        """
    )

    assert out["join"] == ["/workspace/a.txt", "/workspace/a.txt", "/a", "a"]
    assert out["base"] == ["plan.md", "docs", ""]
    assert out["crumbs"] == [
        {"label": "/workspace", "path": "/workspace"},
        {"label": "docs", "path": "/workspace/docs"},
        {"label": "2026", "path": "/workspace/docs/2026"},
    ]
    assert out["rootCrumbs"] == [{"label": "/workspace", "path": "/workspace"}]
    assert out["outside"] == [{"label": "/workspace", "path": "/workspace"}]
    # Folders first; then by name, counting the way people do and ignoring case.
    assert out["sorted"] == ["alpha", "Zeta", "File1.txt", "file2.txt", "file10.txt"]
    assert out["sizes"] == ["0 B", "1 B", "1023 B", "1 KB", "15 KB", "5.0 MB", "250 MB", "3.00 GB", "", "", "", "", ""]


def test_modified_time_is_short_for_today_and_grows_with_age():
    out = _run_case(
        """
        const now = new Date(2026, 10, 11, 15, 30);
        const today = new Date(2026, 10, 11, 9, 5);
        const thisYear = new Date(2026, 2, 4, 9, 5);
        const older = new Date(2024, 2, 4, 9, 5);
        out.today = formatServerFileModified(today.toISOString(), now) === today.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        out.thisYear = formatServerFileModified(thisYear.toISOString(), now) === thisYear.toLocaleDateString([], { month: "short", day: "numeric" });
        out.older = formatServerFileModified(older.toISOString(), now);
        out.olderHasYear = out.older.includes("2024");
        out.thisYearText = formatServerFileModified(thisYear.toISOString(), now);
        out.blank = [formatServerFileModified("", now), formatServerFileModified(null, now), formatServerFileModified("not a date", now)];
        """
    )

    assert out["today"] is True
    assert out["thisYear"] is True
    assert "2026" not in out["thisYearText"]
    assert out["olderHasYear"] is True
    assert out["blank"] == ["", "", ""]


def test_name_rules():
    out = _run_case(
        """
        const siblings = ["docs", "plan.md"];
        out.problems = [
          serverFileNameProblem("", siblings, ""),
          serverFileNameProblem("   ", siblings, ""),
          serverFileNameProblem(".", siblings, ""),
          serverFileNameProblem("..", siblings, ""),
          serverFileNameProblem("a/b", siblings, ""),
          serverFileNameProblem("a\\\\b", siblings, ""),
          serverFileNameProblem("x".repeat(256), siblings, ""),
          serverFileNameProblem("docs", siblings, ""),
          serverFileNameProblem(" plan.md ", siblings, ""),
          // A rename may keep its own name, and names differ by case.
          serverFileNameProblem("plan.md", siblings, "plan.md"),
          serverFileNameProblem("Plan.md", siblings, ""),
          serverFileNameProblem("notes.txt", siblings, ""),
          serverFileNameProblem(".env", siblings, ""),
        ];
        out.errors = ["already exists", "destination already exists", "not_found", "invalid destination", "Permission denied", ""].map(describeServerFileError);
        """
    )

    assert out["problems"] == [
        "Enter a name",
        "Enter a name",
        "This name is not allowed",
        "This name is not allowed",
        "A name cannot contain a slash",
        "A name cannot contain a slash",
        "This name is too long",
        "docs already exists",
        "plan.md already exists",
        "",
        "",
        "",
        "",
    ]
    assert out["errors"] == [
        "Something with that name already exists",
        "Something with that name already exists",
        "It is no longer there. Reload the folder.",
        "It cannot be moved there",
        "Permission denied",
        "Request failed",
    ]


def test_create_and_rename_calls_report_the_runtime_reason():
    out = _run_case(
        """
        answers = [
          [200, { success: true, mode: "mkdir", path: "/workspace/docs/new" }],
          [400, { success: false, error: "already exists" }],
          [403, { detail: "Forbidden" }],
          [200, { success: false, error: "invalid destination" }],
        ];
        out.results = [
          await serverFilesPost("agent-A", "mkdir", { path: "/workspace/docs/new" }),
          await serverFilesPost("agent-A", "new-file", { path: "/workspace/docs/plan.md" }),
          await serverFilesPost("agent-A", "move", { source: "/workspace/a", destination: "/workspace/b" }),
          await serverFilesPost("agent-A", "move", { source: "/workspace/a", destination: "/workspace/a" }),
        ];
        """
    )

    assert out["requests"] == [
        ["/a/agent-A/api/server-files/mkdir", "POST", {"path": "/workspace/docs/new"}],
        ["/a/agent-A/api/server-files/new-file", "POST", {"path": "/workspace/docs/plan.md"}],
        ["/a/agent-A/api/server-files/move", "POST", {"source": "/workspace/a", "destination": "/workspace/b"}],
        ["/a/agent-A/api/server-files/move", "POST", {"source": "/workspace/a", "destination": "/workspace/a"}],
    ]
    assert out["results"][0] == {"ok": True, "data": {"success": True, "mode": "mkdir", "path": "/workspace/docs/new"}}
    assert out["results"][1] == {"ok": False, "error": "Something with that name already exists"}
    assert out["results"][2] == {"ok": False, "error": "Forbidden"}
    assert out["results"][3] == {"ok": False, "error": "It cannot be moved there"}


def test_move_sends_one_request_per_item_and_keeps_going_past_a_failure():
    out = _run_case(
        """
        answers = [
          [200, { success: true, mode: "move" }],
          [400, { success: false, error: "destination already exists" }],
          [200, { success: true, mode: "move" }],
        ];
        const paths = ["/workspace/docs/a.txt", "/workspace/docs/reports", "/workspace/docs/c.md"];
        const outcome = await moveServerFilesTo("agent-A", paths, "/workspace/archive");
        out.outcome = outcome;
        out.toast = describeServerFilesMove(outcome, paths.length, "/workspace/archive");
        out.allMoved = describeServerFilesMove({ moved: 2, failures: [] }, 2, "/workspace/archive");
        out.oneMoved = describeServerFilesMove({ moved: 1, failures: [] }, 1, "/workspace");
        out.capped = describeServerFilesMove(
          { moved: 0, failures: Array.from({ length: 7 }, (_, i) => ({ name: `f${i}`, error: "no" })) }, 7, "/workspace/x");
        """
    )

    assert out["requests"] == [
        ["/a/agent-A/api/server-files/move", "POST", {"source": "/workspace/docs/a.txt", "destination": "/workspace/archive/a.txt"}],
        ["/a/agent-A/api/server-files/move", "POST", {"source": "/workspace/docs/reports", "destination": "/workspace/archive/reports"}],
        ["/a/agent-A/api/server-files/move", "POST", {"source": "/workspace/docs/c.md", "destination": "/workspace/archive/c.md"}],
    ]
    assert out["outcome"] == {
        "moved": 2,
        "failures": [{"name": "reports", "error": "Something with that name already exists"}],
    }
    assert out["toast"] == "Moved 2 of 3 items. Failed: reports: Something with that name already exists"
    assert out["allMoved"] == "Moved 2 items to /workspace/archive"
    assert out["oneMoved"] == "Moved 1 item to /workspace"
    assert out["capped"] == "Moved 0 of 7 items. Failed: f0: no; f1: no; f2: no; f3: no; f4: no; and 2 more"


def test_name_box_enter_saves_a_usable_name_and_explains_an_unusable_one():
    out = _run_case(
        """
        // A new item: Enter on nothing asks for a name and keeps the box open.
        const empty = editor("", { initial: "" });
        empty.input.fire("keydown", { key: "Enter" });
        await tick();
        out.empty = [empty.error.textContent, empty.error.hidden, empty.input.attrs["aria-invalid"], empty.log];

        // Typing clears the reason.
        empty.input.value = "n";
        empty.input.fire("input");
        out.cleared = [empty.error.textContent, empty.error.hidden, empty.input.attrs["aria-invalid"]];

        const taken = editor("docs", { initial: "", siblings: ["docs"] });
        taken.input.fire("keydown", { key: "Enter" });
        await tick();
        out.taken = [taken.error.textContent, taken.log];

        const good = editor("  notes.md  ", { initial: "" });
        good.input.fire("keydown", { key: "Enter" });
        await tick();
        // A second Enter, or the blur that follows the redraw, does not save twice.
        good.input.fire("keydown", { key: "Enter" });
        good.input.fire("blur");
        await tick();
        out.good = [good.log, good.error.hidden];
        """
    )

    assert out["empty"] == ["Enter a name", False, "true", {"saved": [], "cancelled": 0}]
    assert out["cleared"] == ["", True, "false"]
    assert out["taken"] == ["docs already exists", {"saved": [], "cancelled": 0}]
    assert out["good"] == [{"saved": ["notes.md"], "cancelled": 0}, True]


def test_name_box_keeps_the_runtime_reason_and_cancels_cleanly():
    out = _run_case(
        """
        // The runtime refused: the reason shows, the box is usable again.
        const refused = editor("notes.md", { initial: "", failure: "Permission denied" });
        refused.input.fire("keydown", { key: "Enter" });
        await tick();
        out.refused = [refused.error.textContent, refused.input.disabled, refused.input.focused, refused.log];

        // Escape cancels once, and the blur that follows does nothing more.
        const escaped = editor("draft", { initial: "" });
        escaped.input.fire("keydown", { key: "Escape" });
        escaped.input.fire("blur");
        await tick();
        out.escaped = escaped.log;

        // Clicking elsewhere saves a usable new name...
        const blurGood = editor("draft.txt", { initial: "" });
        blurGood.input.fire("blur");
        await tick();
        out.blurGood = blurGood.log;

        // ...and drops an empty or unusable one without complaint.
        const blurEmpty = editor("", { initial: "" });
        blurEmpty.input.fire("blur");
        await tick();
        const blurBad = editor("a/b", { initial: "" });
        blurBad.input.fire("blur");
        await tick();
        out.blurDropped = [blurEmpty.log, blurBad.log, blurBad.error.hidden];

        // A rename left as it was is a cancel, on Enter and on blur alike.
        const sameEnter = editor("plan.md", { initial: "plan.md", siblings: ["plan.md"] });
        sameEnter.input.fire("keydown", { key: "Enter" });
        await tick();
        const sameBlur = editor("plan.md", { initial: "plan.md", siblings: ["plan.md"] });
        sameBlur.input.fire("blur");
        await tick();
        out.unchanged = [sameEnter.log, sameBlur.log];

        const renamed = editor("plan-v2.md", { initial: "plan.md", siblings: ["plan.md", "docs"] });
        renamed.input.fire("keydown", { key: "Enter" });
        await tick();
        out.renamed = renamed.log;
        """
    )

    assert out["refused"] == ["Permission denied", False, 1, {"saved": ["notes.md"], "cancelled": 0}]
    assert out["escaped"] == {"saved": [], "cancelled": 1}
    assert out["blurGood"] == {"saved": ["draft.txt"], "cancelled": 0}
    assert out["blurDropped"] == [{"saved": [], "cancelled": 1}, {"saved": [], "cancelled": 1}, True]
    assert out["unchanged"] == [{"saved": [], "cancelled": 1}, {"saved": [], "cancelled": 1}]
    assert out["renamed"] == {"saved": ["plan-v2.md"], "cancelled": 0}


def test_a_dropped_folder_is_walked_and_keeps_its_structure():
    out = _run_case(
        """
        // What the browser hands over for a drop: entries for files and folders.
        const fileEntry = (name) => ({ isFile: true, isDirectory: false, name, file: (done) => done({ name }) });
        const dirEntry = (name, children) => ({
          isFile: false, isDirectory: true, name,
          createReader() {
            // A reader hands its children over in batches, then an empty one.
            const batches = [children.slice(0, 2), children.slice(2), []];
            return { readEntries: (done) => done(batches.shift() || []) };
          },
        });
        const item = (entry) => ({ kind: "file", webkitGetAsEntry: () => entry, getAsFile: () => ({ name: entry.name }) });
        const flat = (result) => [result.entries.map((e) => [e.file.name, e.relativeDir]), result.tooMany];

        out.mixed = flat(await collectDroppedServerFiles({ items: [
          item(fileEntry("loose.txt")),
          { kind: "string" },
          item(dirEntry("reports", [
            fileEntry("q1.md"),
            fileEntry(".DS_Store"),
            dirEntry("2026", [fileEntry("jan.md")]),
            dirEntry("empty", []),
          ])),
        ] }));
        // No entry API (an older browser): the item's file is used as it is.
        out.noEntries = flat(await collectDroppedServerFiles({ items: [{ kind: "file", getAsFile: () => ({ name: "a.txt" }) }] }));
        // No item list at all: the plain file list.
        out.plain = flat(await collectDroppedServerFiles({ files: [{ name: "c.md" }] }));
        out.nothing = flat(await collectDroppedServerFiles(null));
        // A tree past the limit stops being read and says so.
        out.huge = flat(await collectDroppedServerFiles({ items: [
          item(dirEntry("big", Array.from({ length: 9 }, (_, i) => fileEntry(`f${i}`)))),
        ] }))[1];
        """
    )

    assert out["mixed"] == [[["loose.txt", ""], ["q1.md", "reports"], ["jan.md", "reports/2026"]], False]
    assert out["noEntries"] == [[["a.txt", ""]], False]
    assert out["plain"] == [[["c.md", ""]], False]
    assert out["nothing"] == [[], False]
    assert out["huge"] is True


def test_what_a_drag_carries_and_where_rows_may_be_moved():
    out = _run_case(
        """
        out.kinds = [
          serverFilesDragKind({ types: ["Files"] }),
          serverFilesDragKind({ types: ["text/plain", SERVER_FILES_DRAG_TYPE] }),
          serverFilesDragKind({ types: ["text/plain"] }),
          serverFilesDragKind(null),
        ];
        const moving = ["/workspace/docs/reports", "/workspace/docs/plan.md"];
        out.allowed = [
          serverFilesMoveAllowed(moving, "/workspace/docs", "/workspace/archive"),
          serverFilesMoveAllowed(moving, "/workspace/docs", "/workspace"),
          // The folder they are already in, with or without a trailing slash.
          serverFilesMoveAllowed(moving, "/workspace/docs", "/workspace/docs"),
          serverFilesMoveAllowed(moving, "/workspace/docs/", "/workspace/docs"),
          // A folder into itself, or into something inside it.
          serverFilesMoveAllowed(moving, "/workspace/docs", "/workspace/docs/reports"),
          serverFilesMoveAllowed(moving, "/workspace/docs", "/workspace/docs/reports/2026"),
          // A sibling whose name merely starts the same is fine.
          serverFilesMoveAllowed(moving, "/workspace/docs", "/workspace/docs/reports-old"),
          serverFilesMoveAllowed(moving, "/workspace/docs", ""),
        ];
        """
    )

    assert out["kinds"] == ["upload", "move", "", ""]
    assert out["allowed"] == [True, True, False, False, False, False, True, False]


def test_sort_orders_and_change_detection():
    out = _run_case(
        """
        const items = [
          { name: "old-big.bin", is_dir: false, size: 900, modified_at: "2026-01-01T00:00:00+00:00" },
          { name: "new-small.txt", is_dir: false, size: 10, modified_at: "2026-11-11T00:00:00+00:00" },
          { name: "mid.md", is_dir: false, size: 10, modified_at: "2026-06-01T00:00:00+00:00" },
          { name: "b-dir", is_dir: true, size: 0, modified_at: "2026-02-01T00:00:00+00:00" },
          { name: "a-dir", is_dir: true, size: 0, modified_at: "2026-10-01T00:00:00+00:00" },
          { name: "undated", is_dir: false, size: 5 },
        ];
        const names = (by) => sortServerFileItems(items, by).map((item) => item.name);
        out.byName = names("name");
        out.byDefault = names();
        out.byModified = names("modified");
        out.bySize = names("size");

        const listing = { items: [{ path: "/w/a", is_dir: false, size: 1, modified_at: "t1" }, { path: "/w/b", is_dir: true, size: 0, modified_at: "t1" }] };
        const same = { items: listing.items.slice().reverse() };
        const touched = { items: [{ path: "/w/a", is_dir: false, size: 2, modified_at: "t2" }, listing.items[1]] };
        const added = { items: listing.items.concat([{ path: "/w/c", is_dir: false, size: 1, modified_at: "t1" }]) };
        const sig = serverFilesListingSignature;
        out.changes = [sig(listing) === sig(same), sig(listing) === sig(touched), sig(listing) === sig(added), sig(null) === sig({ items: [] })];
        """
    )

    # Folders always come first; within them the same order applies.
    assert out["byName"] == ["a-dir", "b-dir", "mid.md", "new-small.txt", "old-big.bin", "undated"]
    assert out["byDefault"] == out["byName"]
    assert out["byModified"] == ["a-dir", "b-dir", "new-small.txt", "mid.md", "old-big.bin", "undated"]
    # Largest first; equal sizes fall back to the name, and folders (no size) to theirs.
    assert out["bySize"] == ["a-dir", "b-dir", "old-big.bin", "mid.md", "new-small.txt", "undated"]
    # The order the runtime lists in is not a change; a new size, time or file is.
    assert out["changes"] == [True, False, False, True]


def test_rows_take_the_keyboard_a_drag_and_the_sort_choice():
    js = SRC.read_text(encoding="utf-8")
    block = _panel_block()

    # One row is in the tab order and the arrows walk the rest; rows can be dragged.
    assert 'tabindex="${index === 0 ? 0 : -1}"' in block
    assert "const dragAttr = itemPath ? ' draggable=\"true\"' : '';" in block
    # The sort choice shares the selection line and hides while rows are ticked.
    select_start = block.index('class="portal-file-select-row"')
    selection = block[select_start:block.index('class="portal-panel-stack portal-file-list"')]
    assert "sf-sort-btn" in selection
    assert "panel.querySelector('.sf-sort-btn')?.classList.toggle('hidden', selected.length > 0);" in js

    keys = _extract_js_function(js, "handleServerFilesKeydown")
    for key in ("'F2'", "'Delete'", "'Escape'", "'ArrowDown'", "'ArrowUp'", "'Enter'"):
        assert f"key === {key}" in keys
    # A name being typed keeps its own keys.
    assert "target.closest('.portal-file-name-edit')" in keys

    # The panel is read again when a run ends and when the member comes back,
    # and never while they are in the middle of something.
    refresh = _extract_js_function(js, "refreshServerFilesQuietly")
    assert refresh.count("serverFilesMemberIsBusy(panel)") == 2
    bound = _extract_js_function(js, "bindServerFilesPanelOnce")
    assert '"portal:runtime-event"' in bound and '"visibilitychange"' in bound

    css = CSS.read_text(encoding="utf-8")
    for selector in (".portal-file-upload-strip {", ".portal-file-drop-label {", ".portal-file-row.is-drop-into {", ".portal-file-sort-btn {"):
        assert selector in css
    # The conversation says what a file dropped on it becomes.
    assert ".portal-message-list.drag-over::after {" in css
    assert 'messageList.dataset.dropLabel = t("Drop to attach to your message");' in js


def test_loading_empty_and_progress_states_are_visible():
    js = SRC.read_text(encoding="utf-8")
    start = js.index("async function loadServerFiles(path) {")
    region = js[start:js.index("async function previewServerFile(", start)]

    # A bare .portal-inline-state is hidden (app.css); the panel's loading,
    # empty-folder and progress lines have to say they are to be shown.
    assert 'class="portal-inline-state">' not in region
    assert '<div class="portal-inline-state is-visible">${t("Empty directory")}</div>' in region
    assert '<div class="portal-inline-state is-visible">${t("No folders here")}</div>' in region

    css = CSS.read_text(encoding="utf-8")
    base = css[css.index(".portal-inline-state {"):]
    assert "display: none" in base[:base.index("}")]
    shown = css[css.index(".portal-inline-state.is-visible {"):]
    assert "display: block" in shown[:shown.index("}")]
