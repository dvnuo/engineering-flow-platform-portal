"""Behaviour of the pure Mermaid helpers in chat_ui.js, run under Node."""

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests._js_extract_helpers import _extract_js_function


SRC = Path("app/static/js/chat_ui.js")

HELPERS = (
    "normalizeFenceLanguage",
    "isMermaidFenceLanguage",
    "isMermaidCodeElement",
    "mermaidCacheKey",
    "diagramErrorSummary",
    "buildDiagramToolbarHtml",
)


def _node_bin() -> str:
    """Skip rather than error where node is absent, like the other node tests."""
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping mermaid helper behaviour test")
    return node_bin


def test_mermaid_helpers_classify_fences_and_summarise_errors():
    src = SRC.read_text(encoding="utf-8")
    helpers_js = "\n".join(_extract_js_function(src, name) for name in HELPERS)
    script = (
        helpers_js
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");

            // The fence info string is model output and lands in a class attribute.
            assert.equal(normalizeFenceLanguage(" JS "), "js");
            assert.equal(normalizeFenceLanguage('js" onload="alert(1)"'), "js");
            assert.equal(normalizeFenceLanguage("c++"), "c++");
            assert.equal(normalizeFenceLanguage("objective-c"), "objective-c");
            assert.equal(normalizeFenceLanguage(""), "");
            assert.equal(normalizeFenceLanguage(undefined), "");

            assert.equal(isMermaidFenceLanguage("Mermaid"), true);
            assert.equal(isMermaidFenceLanguage("mmd"), true);
            assert.equal(isMermaidFenceLanguage("mermaid title=x"), true);
            assert.equal(isMermaidFenceLanguage("mermaidjs"), false);
            assert.equal(isMermaidFenceLanguage(""), false);

            assert.equal(isMermaidCodeElement({ classList: ["hljs", "language-mermaid"] }), true);
            assert.equal(isMermaidCodeElement({ classList: ["language-MMD"] }), true);
            assert.equal(isMermaidCodeElement({ classList: ["hljs", "language-js"] }), false);
            assert.equal(isMermaidCodeElement({ classList: [] }), false);
            assert.equal(isMermaidCodeElement(null), false);

            assert.notEqual(mermaidCacheKey("dark", "graph TD"), mermaidCacheKey("default", "graph TD"));
            assert.equal(mermaidCacheKey("dark", "graph TD"), mermaidCacheKey("dark", "graph TD"));

            // mermaid parse errors carry the message in .str with the caret lines after it.
            assert.equal(
              diagramErrorSummary({ str: "Parse error on line 2:\n...B --> \n---^\nExpecting 'SEMI'", message: "x" }),
              "Parse error on line 2:",
            );
            assert.equal(diagramErrorSummary(new Error("  \n boom ")), "boom");
            assert.equal(diagramErrorSummary(""), "unknown error");
            const long = diagramErrorSummary("x".repeat(400));
            assert.equal(long.length, 160);
            assert.ok(long.endsWith("..."));

            const toolbar = buildDiagramToolbarHtml();
            assert.match(toolbar, /data-diagram-view="diagram"[^>]*disabled/);
            assert.match(toolbar, /data-diagram-view="code"[^>]*aria-pressed="true"/);
            assert.ok(toolbar.includes("message-diagram-copy"));
            assert.ok(toolbar.includes('class="message-diagram-status" role="status" hidden'));
            """
        )
    )

    result = subprocess.run(
        [_node_bin(), "-e", script],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
