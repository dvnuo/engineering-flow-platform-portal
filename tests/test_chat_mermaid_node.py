"""Behaviour of the pure Mermaid helpers in chat_ui.js, run under Node."""

import re
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
    "buildDiagramZoomControlsHtml",
    "composeDiagramFixRequest",
    "parseSvgNaturalSize",
    "clampDiagramScale",
    "formatZoomPercent",
    "defaultDiagramScale",
    "withFreshSvgId",
    "diagramExportFilename",
    "diagramTitle",
)

# Module constants the sizing helpers read; extracted so the test tracks the real values.
CONSTANT_RE = re.compile(r"^const (DIAGRAM_FIT_THRESHOLD|DIAGRAM_ZOOM_MIN|DIAGRAM_ZOOM_MAX) = [^;]+;$", re.M)


def _node_bin() -> str:
    """Skip rather than error where node is absent, like the other node tests."""
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping mermaid helper behaviour test")
    return node_bin


def test_mermaid_helpers_classify_fences_and_summarise_errors():
    src = SRC.read_text(encoding="utf-8")
    constants = CONSTANT_RE.findall(src)
    assert len(constants) == 3, "DIAGRAM_FIT_THRESHOLD / DIAGRAM_ZOOM_MIN / DIAGRAM_ZOOM_MAX must stay top-level consts"
    helpers_js = "\n".join(match.group(0) for match in CONSTANT_RE.finditer(src)) + "\n" + "\n".join(_extract_js_function(src, name) for name in HELPERS)
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
              "Parse error on line 2",
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

            const request = composeDiagramFixRequest("Parse error on line 4", "flowchart LR\n  A[Unclosed --> B\n");
            assert.ok(request.includes('(the block starting with "flowchart LR")'));
            assert.ok(request.includes("did not render in Portal: Parse error on line 4. Fix"));
            assert.ok(request.includes("resend only the corrected mermaid code block"));
            assert.equal(composeDiagramFixRequest("boom", "").includes("the block starting with"), false);

            // Sizing: the viewBox is the natural size; mermaid's width="100%" is not.
            assert.deepEqual(parseSvgNaturalSize("-50 -10 1234.5 567", "100%", "100%"), { width: 1234.5, height: 567 });
            assert.deepEqual(parseSvgNaturalSize("", "640px", "480"), { width: 640, height: 480 });
            assert.equal(parseSvgNaturalSize("", "100%", "100%"), null);
            assert.equal(parseSvgNaturalSize("0 0 0 10", "", ""), null);
            assert.equal(clampDiagramScale(0.01), DIAGRAM_ZOOM_MIN);
            assert.equal(clampDiagramScale(99), DIAGRAM_ZOOM_MAX);
            assert.equal(clampDiagramScale("nope"), 1);
            assert.equal(formatZoomPercent(0.6789), "68%");
            // A column that shows 90% of the drawing keeps Fit; 40% is unreadable, so 100% + scroll.
            assert.deepEqual(defaultDiagramScale(0.9), { mode: "fit", scale: 0.9 });
            assert.deepEqual(defaultDiagramScale(DIAGRAM_FIT_THRESHOLD - 0.01), { mode: "custom", scale: 1 });
            assert.deepEqual(defaultDiagramScale(1.7), { mode: "fit", scale: 1 });
            assert.deepEqual(defaultDiagramScale(NaN), { mode: "fit", scale: 1 });

            const entry = { id: "portal-mermaid-3", svg: '<svg id="portal-mermaid-3"><style>#portal-mermaid-3 .node{}</style><path marker-end="url(#portal-mermaid-3_flowchart-pointEnd)"/></svg>' };
            const fresh = withFreshSvgId(entry, "portal-mermaid-view-9");
            assert.equal(fresh.includes("portal-mermaid-3"), false);
            assert.equal((fresh.match(/portal-mermaid-view-9/g) || []).length, 3);
            assert.equal(withFreshSvgId({ id: "", svg: "<svg/>" }, "x"), "<svg/>");

            assert.equal(diagramExportFilename("flowchart LR\n  A --> B", "svg"), "mermaid-flowchart.svg");
            assert.equal(diagramExportFilename("  sequenceDiagram\n A->>B: hi", "png"), "mermaid-sequencediagram.png");
            assert.equal(diagramExportFilename("", "svg"), "mermaid-diagram.svg");
            assert.equal(diagramTitle("\n  stateDiagram-v2\n  [*] --> A"), "stateDiagram-v2");
            assert.equal(diagramTitle(""), "Diagram");
            const zoom = buildDiagramZoomControlsHtml();
            for (const action of ["out", "reset", "in", "fit"]) assert.ok(zoom.includes(`data-diagram-zoom="${action}"`), action);
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
