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
    script = "\n".join(
        [
            labels,
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

                const updated = renderFileBlock({ type: "file", path: "notes.md", action: "updated", size: 512, text: "Meeting notes" });
                assert.match(updated, /Updated<\/span>/);
                assert.match(updated, /notes.md · Markdown · 512 B/);
                assert.match(updated, /data-server-path=""/);
                assert.match(updated, /class="message-file-description">Meeting notes</);

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
