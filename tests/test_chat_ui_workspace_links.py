"""workspace: links in assistant markdown become downloads through the Server Files proxy.

The runtime cannot build an absolute download URL (it knows neither the Portal
origin nor the assistant id), so the assistant writes
``[Download deck.pptx](workspace:output/deck.pptx)`` and the chat renderer
resolves it here. Behaviour tests run the extracted helpers with the real
markdown-it bundle under node and skip where node is absent, like the other
node-backed chat_ui tests.
"""

from pathlib import Path
import shutil
import subprocess
import textwrap

import pytest

from tests._js_extract_helpers import _extract_js_function


SRC = Path("app/static/js/chat_ui.js")
MARKDOWN_IT = Path("app/static/lib/markdown-it.min.js")

WORKSPACE_LINK_HELPERS = [
    "parseWorkspaceLinkPath",
    "workspaceFileName",
    "workspaceFileDirectory",
    "currentWorkspaceAgentId",
    "buildWorkspaceFileDownloadUrl",
    "buildWorkspaceFileContentUrl",
    "installWorkspaceLinkRenderer",
]


def _source() -> str:
    return SRC.read_text(encoding="utf-8")


def _node_bin() -> str:
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping workspace link behaviour test")
    return node_bin


def _helpers_js(src: str) -> str:
    return "\n".join(_extract_js_function(src, name) for name in WORKSPACE_LINK_HELPERS)


def _run_node(script: str) -> str:
    node_bin = _node_bin()
    result = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise AssertionError(f"node failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return result.stdout


def test_markdown_renderer_accepts_workspace_links_and_installs_the_rewriter():
    js = _source()
    assert "const md = window.markdownit({" in js
    assert 'return /^https?:\\/\\//i.test(text) || parseWorkspaceLinkPath(text) !== null;' in js
    assert "installWorkspaceLinkRenderer(md);" in js
    for name in WORKSPACE_LINK_HELPERS:
        assert f"function {name}(" in js, name


def test_workspace_download_anchor_is_not_forced_into_a_new_tab():
    enhance = _extract_js_function(_source(), "enhanceMarkdownBlock")
    assert 'if (anchor.hasAttribute("download")) return;' in enhance
    # The class is still applied first so the link keeps the message styling.
    assert enhance.index('anchor.classList.add("message-link")') < enhance.index('anchor.hasAttribute("download")')


def test_parse_workspace_link_path_normalizes_and_rejects_escapes():
    _node_bin()
    script = _helpers_js(_source()) + "\n" + textwrap.dedent(
        r"""
        const assert = require("node:assert/strict");
        const state = { selectedAgentId: "agent-1" };
        assert.equal(parseWorkspaceLinkPath("workspace:output/deck.pptx"), "output/deck.pptx");
        assert.equal(parseWorkspaceLinkPath("WORKSPACE:///output//deck.pptx/"), "output/deck.pptx");
        assert.equal(parseWorkspaceLinkPath("workspace:output\\q3%20review.pptx"), "output/q3 review.pptx");
        assert.equal(parseWorkspaceLinkPath("workspace:"), null);
        assert.equal(parseWorkspaceLinkPath("workspace:../etc/passwd"), null);
        assert.equal(parseWorkspaceLinkPath("workspace:output/./deck.pptx"), null);
        assert.equal(parseWorkspaceLinkPath("https://example.com/workspace:x"), null);
        assert.equal(parseWorkspaceLinkPath(null), null);
        assert.equal(workspaceFileName("output/decks/q3.pptx"), "q3.pptx");
        assert.equal(workspaceFileDirectory("output/decks/q3.pptx"), "output/decks");
        assert.equal(workspaceFileDirectory("q3.pptx"), "");
        assert.equal(
          buildWorkspaceFileDownloadUrl("agent 1", "output/q3 review.pptx"),
          "/a/agent%201/api/server-files/download?paths=output%2Fq3%20review.pptx"
        );
        assert.equal(buildWorkspaceFileDownloadUrl("", "output/x"), "");
        assert.equal(
          buildWorkspaceFileContentUrl("agent-1", "output/chart.png"),
          "/a/agent-1/api/server-files/content?path=output%2Fchart.png"
        );
        assert.equal(currentWorkspaceAgentId(), "agent-1");
        console.log("ok");
        """
    )
    assert _run_node(script).strip() == "ok"


def test_markdown_it_renders_workspace_links_as_downloads_and_images_inline():
    _node_bin()
    src = _source()
    script = _helpers_js(src) + "\n" + textwrap.dedent(
        rf"""
        const assert = require("node:assert/strict");
        const markdownit = require({str(MARKDOWN_IT.resolve())!r});
        const state = {{ selectedAgentId: "agent-1" }};
        const md = markdownit({{ html: false, linkify: true }});
        md.validateLink = function(text) {{
          return /^https?:\/\//i.test(text) || parseWorkspaceLinkPath(text) !== null;
        }};
        installWorkspaceLinkRenderer(md);

        const link = md.render("[Download deck.pptx](workspace:output/deck.pptx)");
        assert.match(link, /href="\/a\/agent-1\/api\/server-files\/download\?paths=output%2Fdeck.pptx"/);
        assert.match(link, /download="deck.pptx"/);
        assert.match(link, /data-workspace-file="output\/deck.pptx"/);
        assert.match(link, /class="message-workspace-link"/);
        assert.match(link, />Download deck.pptx<\/a>/);

        const image = md.render("![chart](workspace:output/chart.png)");
        assert.match(image, /<img src="\/a\/agent-1\/api\/server-files\/content\?path=output%2Fchart.png" alt="chart"/);

        // Escapes stay plain text, and ordinary links are untouched.
        const escape = md.render("[x](workspace:../secret)");
        assert.doesNotMatch(escape, /<a /);
        const https = md.render("[site](https://example.com/a)");
        assert.match(https, /href="https:\/\/example.com\/a"/);
        assert.doesNotMatch(https, /download=/);

        // Without a selected assistant the link stays inert instead of downloading the page.
        state.selectedAgentId = null;
        const inert = md.render("[x](workspace:output/deck.pptx)");
        assert.match(inert, /href="#"/);
        assert.doesNotMatch(inert, /download=/);
        console.log("ok");
        """
    )
    assert _run_node(script).strip() == "ok"
