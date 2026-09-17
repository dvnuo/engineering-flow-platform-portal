"""Node harness: the attachment chip shared by the composer bubble and the transcript."""

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests._js_extract_helpers import _extract_js_function


SRC = Path("app/static/js/chat_ui.js")

CHIP_FUNCTIONS = [
    "fileExtensionFromName",
    "attachmentKind",
    "attachmentSizeText",
    "attachmentFileUrl",
    "formatAttachmentMetaText",
    "attachmentChipHtml",
    "buildAttachmentChipNode",
]


def _node_bin() -> str:
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping attachment chip behaviour test")
    return node_bin


def test_chips_link_to_the_runtime_file_when_it_is_still_held():
    src = SRC.read_text(encoding="utf-8")
    script = (
        "\n".join(_extract_js_function(src, name) for name in CHIP_FUNCTIONS)
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");
            function escapeHtml(v) { return String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }
            function escapeHtmlAttr(v) { return escapeHtml(v); }
            const state = { selectedAgentId: "agent-A" };
            function currentSessionIdForSelectedAgent() { return "s-1"; }
            globalThis.document = {
              createElement(tag) {
                return { tag, className: "", dataset: {}, textContent: "", children: [], appendChild(child) { this.children.push(child); } };
              },
            };

            // A file the runtime still holds becomes a link that opens it.
            const held = { file_id: "f-log", name: "app.log", content_type: "text/plain", size: 233, type: "file" };
            assert.equal(attachmentFileUrl(held), "/a/agent-A/api/files/f-log?session_id=s-1");
            const html = attachmentChipHtml(held, "app.log");
            assert.match(html, /^<a class="message-attachment-file is-clickable" href="\/a\/agent-A\/api\/files\/f-log\?session_id=s-1" target="_blank" rel="noopener noreferrer" title="Open app.log">/);
            assert.match(html, /data-lucide="file-text"/);
            assert.match(html, /<span class="message-attachment-name">app.log<\/span><span class="message-attachment-meta">LOG · 233 B<\/span>/);
            const node = buildAttachmentChipNode(held, "app.log");
            assert.equal(node.tag, "a");
            assert.equal(node.href, "/a/agent-A/api/files/f-log?session_id=s-1");
            assert.equal(node.className, "message-attachment-file is-clickable");
            assert.equal(node.children[1].children[0].textContent, "app.log");
            assert.equal(node.children[1].children[1].textContent, "LOG · 233 B");

            // Without a file id there is nothing to open: a plain chip.
            const gone = { name: "spec.pdf", content_type: "application/pdf", size: 2048 };
            assert.equal(attachmentFileUrl(gone), "");
            assert.match(attachmentChipHtml(gone, "spec.pdf"), /^<div class="message-attachment-file" title="spec.pdf">/);
            assert.equal(buildAttachmentChipNode(gone, "spec.pdf").tag, "div");

            // Names are escaped in markup and ids are URL-encoded.
            const odd = { file_id: "id with/slash", name: "a<b>&\"c\".txt" };
            assert.equal(attachmentFileUrl(odd), "/a/agent-A/api/files/id%20with%2Fslash?session_id=s-1");
            const oddHtml = attachmentChipHtml(odd, "a<b>&\"c\".txt");
            assert.ok(!oddHtml.includes("<b>"));
            assert.ok(oddHtml.includes("a&lt;b&gt;&amp;&quot;c&quot;.txt"));

            // No selected agent: never a link.
            state.selectedAgentId = "";
            assert.equal(attachmentFileUrl(held), "");
            console.log("ok");
            """
        )
    )
    result = subprocess.run([_node_bin(), "-e", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "ok" in result.stdout
