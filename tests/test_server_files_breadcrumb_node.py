"""The Files panel breadcrumb starts with the root directory's real path.

Members read a "Workspace" crumb as a directory name and send the assistant
looking for `Workspace/...`, which a case-sensitive filesystem does not have.
The first crumb is the runtime's root_path ("/workspace" on the pods), and
the crumbs below it are the directories under that root.
"""

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests._js_extract_helpers import _extract_js_function, _with_i18n_stub


SRC = Path("app/static/js/chat_ui.js")


def test_breadcrumb_source_labels_the_root_with_its_path():
    body = _extract_js_function(SRC.read_text(encoding="utf-8"), "buildServerFilesBreadcrumb")
    assert 't("Workspace")' not in body
    assert "escapeHtml(normalizedRoot)" in body


def test_breadcrumb_root_crumb_is_the_root_path():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping breadcrumb behaviour test")
    src = SRC.read_text(encoding="utf-8")
    # The page's escapeHtml goes through a DOM element and escapeHtmlAttr's regex
    # literals defeat the extractor; text stand-ins do for the labels here.
    prelude = (
        'globalThis.escapeHtml = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");\n'
        'globalThis.escapeHtmlAttr = (s) => globalThis.escapeHtml(s).replace(/"/g, "&quot;");\n'
    )
    script = _with_i18n_stub(prelude + _extract_js_function(src, "buildServerFilesBreadcrumb")) + textwrap.dedent(
        r"""
        const assert = require("node:assert/strict");
        const crumbs = (html) => Array.from(html.matchAll(/data-server-path="([^"]*)">([^<]*)<\/a>/g)).map((m) => [m[1], m[2]]);

        // At the root: one crumb, labelled with the path itself.
        assert.deepEqual(crumbs(buildServerFilesBreadcrumb("/workspace", "/workspace")), [["/workspace", "/workspace"]]);
        // Below the root: the path crumb, then one crumb per directory.
        assert.deepEqual(
          crumbs(buildServerFilesBreadcrumb("/workspace/uploads/q3", "/workspace/")),
          [["/workspace", "/workspace"], ["/workspace/uploads", "uploads"], ["/workspace/uploads/q3", "q3"]],
        );
        // No root reported yet: "/" stands in.
        assert.deepEqual(crumbs(buildServerFilesBreadcrumb("", "")), [["/", "/"]]);
        // The label is never the word "Workspace".
        assert.ok(!buildServerFilesBreadcrumb("/workspace/src", "/workspace").includes(">Workspace<"));
        console.log("ok");
        """
    )
    result = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
