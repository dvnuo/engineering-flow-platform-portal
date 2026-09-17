"""Node harness: chat_ui.js applies the server-rendered upload policy."""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests._js_extract_helpers import _extract_js_function, _extract_js_set_values


SRC = Path("app/static/js/chat_ui.js")

POLICY_FUNCTIONS = [
    "normalizeUploadPolicyList",
    "readChatUploadPolicyFromDom",
    "getChatUploadPolicy",
    "describeChatUploadPolicy",
    "fileExtensionFromName",
    "isRuntimeSupportedUpload",
    "uploadTooLargeMessage",
    "shouldAutoParseUploadedFile",
    "uploadErrorMessageFromXhr",
]


def _node_bin() -> str:
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping chat upload policy behaviour test")
    return node_bin


def _policy_js(src: str) -> str:
    defaults = sorted(_extract_js_set_values(src, "DEFAULT_UPLOAD_EXTENSIONS"))
    images = sorted(_extract_js_set_values(src, "IMAGE_UPLOAD_EXTENSIONS"))
    prelude = (
        f"const DEFAULT_UPLOAD_EXTENSIONS = new Set({json.dumps(defaults)});\n"
        f"const IMAGE_UPLOAD_EXTENSIONS = new Set({json.dumps(images)});\n"
        "let cachedChatUploadPolicy = null;\n"
    )
    return prelude + "\n".join(_extract_js_function(src, name) for name in POLICY_FUNCTIONS)


def test_upload_policy_comes_from_the_dom_with_a_default_fallback():
    src = SRC.read_text(encoding="utf-8")
    script = (
        _policy_js(src)
        + "\n"
        + textwrap.dedent(
            r"""
            const assert = require("node:assert/strict");

            // 1) No policy on the page: the default (non-visual) allowlist applies, by extension only.
            globalThis.document = { getElementById: () => null };
            assert.equal(isRuntimeSupportedUpload({ name: "report.PDF", type: "" }), true);
            assert.equal(isRuntimeSupportedUpload({ name: "deck.pptx", type: "" }), true);
            assert.equal(isRuntimeSupportedUpload({ name: "bundle.zip", type: "application/zip" }), true);
            assert.equal(isRuntimeSupportedUpload({ name: "notes.md", type: "text/markdown" }), true);
            assert.equal(isRuntimeSupportedUpload({ name: "shot.png", type: "image/png" }), false);
            assert.equal(isRuntimeSupportedUpload({ name: "README", type: "text/plain" }), false);
            assert.equal(isRuntimeSupportedUpload(null), false);
            assert.ok(describeChatUploadPolicy().split(", ").includes("pdf"));
            assert.equal(uploadTooLargeMessage({ name: "huge.pdf", size: 10 ** 9 }), "");

            // 2) The server-rendered policy wins over the fallback (here it adds png for a vision model).
            cachedChatUploadPolicy = null;
            const rendered = { extensions: [".MD", "txt", "Json", "png"], accept: ".md,.txt,.json,.png,image/png", max_upload_mb: 2 };
            globalThis.document = {
              getElementById: (id) => (id === "upload-input" ? { dataset: { chatUploadPolicy: JSON.stringify(rendered) } } : null),
            };
            assert.equal(isRuntimeSupportedUpload({ name: "notes.MD", type: "" }), true);
            assert.equal(isRuntimeSupportedUpload({ name: "config.json", type: "application/json" }), true);
            assert.equal(isRuntimeSupportedUpload({ name: "shot.png", type: "image/png" }), true);
            assert.equal(isRuntimeSupportedUpload({ name: "report.pdf", type: "application/pdf" }), false);
            assert.equal(describeChatUploadPolicy(), "md, txt, json, png");
            assert.equal(
              uploadTooLargeMessage({ name: "big.txt", size: 3 * 1024 * 1024 }),
              "File too large: big.txt (3.0 MB). Maximum size is 2MB."
            );
            assert.equal(uploadTooLargeMessage({ name: "ok.txt", size: 2 * 1024 * 1024 }), "");

            // 3) A malformed or empty policy falls back to the defaults.
            for (const raw of ["{not json", JSON.stringify({ extensions: [] }), ""]) {
              cachedChatUploadPolicy = null;
              globalThis.document = { getElementById: () => ({ dataset: { chatUploadPolicy: raw } }) };
              assert.equal(isRuntimeSupportedUpload({ name: "sheet.xlsx" }), true, raw);
              assert.equal(isRuntimeSupportedUpload({ name: "shot.png" }), false, raw);
            }

            // 4) Auto-parse: never images, every other accepted file.
            assert.equal(shouldAutoParseUploadedFile({ file: { type: "image/png", name: "a.png" } }, { content_type: "image/png" }), false);
            assert.equal(shouldAutoParseUploadedFile({ file: { type: "", name: "a.png" }, name: "a.png" }, {}), false);
            assert.equal(shouldAutoParseUploadedFile({ file: { type: "text/markdown", name: "a.md" } }, { content_type: "text/markdown", filename: "a.md" }), true);
            assert.equal(shouldAutoParseUploadedFile({ file: { type: "", name: "a.pdf" } }, { content_type: "application/pdf" }), true);
            assert.equal(shouldAutoParseUploadedFile({ file: { type: "", name: "a.log" }, name: "a.log" }, {}), true);

            // 5) Upload failures surface the server's words, not only the status.
            assert.equal(
              uploadErrorMessageFromXhr({ status: 415, responseText: JSON.stringify({ detail: "File type .exe is not allowed. Allowed: pdf" }) }),
              "File type .exe is not allowed. Allowed: pdf"
            );
            assert.equal(
              uploadErrorMessageFromXhr({ status: 415, responseText: JSON.stringify({ success: false, error: "runtime says no" }) }),
              "runtime says no"
            );
            assert.equal(uploadErrorMessageFromXhr({ status: 422, responseText: JSON.stringify({ detail: [{ msg: "field required" }] }) }), "field required");
            assert.equal(uploadErrorMessageFromXhr({ status: 502, responseText: "<html>bad gateway</html>" }), "HTTP 502");
            assert.equal(uploadErrorMessageFromXhr({ status: 500, responseText: "" }), "HTTP 500");

            console.log("ok");
            """
        )
    )
    result = subprocess.run([_node_bin(), "-e", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "ok" in result.stdout
