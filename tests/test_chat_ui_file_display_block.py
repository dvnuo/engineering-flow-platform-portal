"""Runtime ``file`` display blocks render as download cards under the reply."""

from pathlib import Path
import shutil
import subprocess
import textwrap

import pytest

from tests._js_extract_helpers import _extract_js_function


SRC = Path("app/static/js/chat_ui.js")

FILE_BLOCK_HELPERS = [
    "parseWorkspaceLinkPath",
    "workspaceFileName",
    "workspaceFileDirectory",
    "currentWorkspaceAgentId",
    "buildWorkspaceFileDownloadUrl",
    "isMeaningfulText",
    "pickFirstMeaningfulBlockValue",
    "getDisplayBlockText",
    "hasRenderableDisplayBlock",
    "formatFileBlockSize",
    "formatFileBlockMeta",
    "buildWorkspaceFileContentUrl",
    "fileBlockPreviewHtml",
    "renderFileBlock",
]


def _source() -> str:
    return SRC.read_text(encoding="utf-8")


def _node_bin() -> str:
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping file block rendering test")
    return node_bin


def test_file_blocks_are_wired_into_the_display_block_renderer():
    js = _source()
    assert 'if (type === "file") return renderFileBlock(block);' in js
    has_renderable = _extract_js_function(js, "hasRenderableDisplayBlock")
    assert 'if (type === "file")' in has_renderable
    assert "const FILE_BLOCK_TYPE_LABELS" in js
    assert "message-file-open" in js and "data-server-path" in js


def test_message_list_opens_the_folder_of_a_file_card():
    js = _source()
    assert 'dom.messageList?.addEventListener("click"' in js
    assert 'closest(".message-file-open[data-server-path]")' in js
    assert "function openServerFilesAt(" in js


def test_file_card_css_is_present():
    css = Path("app/static/css/app.css").read_text(encoding="utf-8")
    for selector in (".message-file-card", ".message-file-actions", ".message-file-badge.is-created", ".message-workspace-link::after"):
        assert selector in css, selector


def test_render_file_block_builds_a_download_card():
    node_bin = _node_bin()
    js = _source()
    helpers = "\n".join(_extract_js_function(js, name) for name in FILE_BLOCK_HELPERS)
    # The card uses the module-level label table; the escape helpers hold regex
    # literals with quotes that the extractor cannot follow, so they are stubbed.
    labels_start = js.index("const FILE_BLOCK_TYPE_LABELS")
    labels = js[labels_start: js.index("};", labels_start) + 2]
    icon_start = js.index("const FILE_BLOCK_ICON")
    icon = js[icon_start: js.index(";", icon_start) + 1]
    script = "\n".join(
        [
            labels,
            icon,
            helpers,
            textwrap.dedent(
                r"""
                const escapeHtml = (value) => String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
                const escapeHtmlAttr = escapeHtml;
                const assert = require("node:assert/strict");
                const state = { selectedAgentId: "agent-1" };

                const block = {
                  type: "file",
                  path: "output/Q3 review.pptx",
                  name: "Q3 review.pptx",
                  size: 60794,
                  content_type: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                  action: "created",
                };
                assert.equal(hasRenderableDisplayBlock(block), true);
                assert.equal(hasRenderableDisplayBlock({ type: "file", name: "no-path.pptx" }), false);
                assert.equal(hasRenderableDisplayBlock({ type: "file", path: "../escape.pptx" }), false);

                const html = renderFileBlock(block);
                assert.match(html, /class="message-block message-block-file"/);
                assert.match(html, /href="\/a\/agent-1\/api\/server-files\/download\?paths=output%2FQ3%20review.pptx"/);
                assert.match(html, /download="Q3 review.pptx"/);
                assert.match(html, /Q3 review.pptx <span class="message-file-badge is-created">New<\/span>/);
                assert.match(html, /output\/Q3 review.pptx · PowerPoint · 59.4 KB/);
                assert.match(html, /class="portal-btn is-secondary message-file-open" data-server-path="output"/);
                // Nothing to expand (no description, no preview): a plain row, no disclosure.
                assert.match(html, /^<section class="message-block message-block-file"><div class="message-file-card"/);
                assert.doesNotMatch(html, /<details|message-file-chevron/);

                const updated = renderFileBlock({ type: "file", path: "notes.md", action: "updated", size: 512, text: "Meeting notes" });
                assert.match(updated, /Updated<\/span>/);
                assert.match(updated, /notes.md · Markdown · 512 B/);
                assert.match(updated, /data-server-path=""/);
                assert.match(updated, /class="message-file-description">Meeting notes</);
                // A description makes the card a closed disclosure whose summary is the row.
                assert.match(updated, /^<details class="message-block message-block-file"><summary class="message-file-card" data-workspace-file="notes.md">/);
                assert.doesNotMatch(updated, /<details[^>]*\sopen/);
                assert.match(updated, /message-file-chevron/);
                assert.doesNotMatch(updated, /message-file-expanded/);

                // An image preview lives in the body, hidden until the row is opened.
                const shot = renderFileBlock({ type: "file", path: "runs/shot-1.png", size: 2048 });
                assert.match(shot, /^<details class="message-block message-block-file"><summary class="message-file-card"/);
                assert.match(shot, /<\/summary><div class="message-file-expanded"><a class="message-file-preview"/);
                assert.match(shot, /<img src="\/a\/agent-1\/api\/server-files\/content\?path=runs%2Fshot-1.png"/);

                // Without an assistant there is nothing to download from, so no Download button.
                state.selectedAgentId = null;
                const inert = renderFileBlock(block);
                assert.doesNotMatch(inert, /message-file-download/);
                assert.match(inert, /message-file-open/);

                assert.equal(formatFileBlockSize(5 * 1024 * 1024), "5 MB");
                assert.equal(formatFileBlockMeta({ size: 10 }, "README"), "10 B");
                console.log("ok");
                """
            ),
        ]
    )
    result = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise AssertionError(f"node failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    assert result.stdout.strip() == "ok"


def test_four_or_more_files_in_a_row_fold_into_one_closed_group():
    """A run that reports every screenshot shows one "N files" row, not N cards."""
    node_bin = _node_bin()
    js = _source()
    helpers = "\n".join(
        _extract_js_function(js, name)
        for name in FILE_BLOCK_HELPERS + ["fileBlockDisplayName", "renderFileBlockGroup", "renderDisplayBlockSequence", "renderDisplayBlocksToHtml"]
    )
    labels_start = js.index("const FILE_BLOCK_TYPE_LABELS")
    labels = js[labels_start: js.index("};", labels_start) + 2]
    icon_start = js.index("const FILE_BLOCK_ICON")
    icon = js[icon_start: js.index(";", icon_start) + 1]
    group_min_start = js.index("const FILE_BLOCK_GROUP_MIN")
    group_min = js[group_min_start: js.index(";", group_min_start) + 1]
    script = "\n".join(
        [
            labels,
            icon,
            group_min,
            helpers,
            textwrap.dedent(
                r"""
                const escapeHtml = (value) => String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
                const escapeHtmlAttr = escapeHtml;
                const fill = (text, params) => String(text).replace(/\{(\w+)\}/g, (m, k) => (params && k in params ? String(params[k]) : m));
                const t = (key, params) => fill(key, params);
                const tn = (count, one, other, params) => fill(count === 1 ? one : other, Object.assign({ n: count }, params || {}));
                const md = { render: (text) => `<p>${text}</p>` };
                const normalizeMarkdownText = (text) => String(text || "");
                // Only file and markdown blocks take part here.
                const renderSingleDisplayBlock = (block) => (String(block.type) === "file" ? renderFileBlock(block) : md.render(getDisplayBlockText(block)));
                const assert = require("node:assert/strict");
                const state = { selectedAgentId: "agent-1" };

                const shots = [1, 2, 3, 4, 5].map((n) => ({ type: "file", path: `runs/shot-${n}.png`, size: 1024 }));
                const grouped = renderDisplayBlocksToHtml([{ type: "markdown", text: "Done." }, ...shots]);
                assert.match(grouped, /^<p>Done.<\/p><details class="message-block message-file-group"><summary class="message-file-group-summary">/);
                assert.doesNotMatch(grouped, /<details class="message-block message-file-group"[^>]*\sopen/);
                assert.match(grouped, /<span class="message-file-group-count">5 files<\/span>/);
                assert.match(grouped, /<span class="message-file-group-names">shot-1.png, shot-2.png, shot-3.png \+2 more<\/span>/);
                assert.equal((grouped.match(/message-file-card/g) || []).length, 5, "every file keeps its own row inside the group");
                assert.equal((grouped.match(/message-file-preview/g) || []).length, 5, "and its preview, still closed");

                // Three files stay as three rows: a short list reads better than a fold.
                const three = renderDisplayBlocksToHtml(shots.slice(0, 3));
                assert.doesNotMatch(three, /message-file-group/);
                assert.equal((three.match(/<details class="message-block message-block-file">/g) || []).length, 3);

                // Only consecutive files fold; a markdown block in between splits the run.
                const split = renderDisplayBlocksToHtml([...shots.slice(0, 2), { type: "markdown", text: "and" }, ...shots.slice(2)]);
                assert.doesNotMatch(split, /message-file-group/);
                console.log("ok");
                """
            ),
        ]
    )
    result = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise AssertionError(f"node failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    assert result.stdout.strip() == "ok"
