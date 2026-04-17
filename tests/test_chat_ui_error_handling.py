import json
import shutil
import subprocess
from pathlib import Path

import pytest

from _js_extract_helpers import _extract_js_function


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _chat_ui_js_source() -> str:
    chat_ui_path = _repo_root() / "app" / "static" / "js" / "chat_ui.js"
    return chat_ui_path.read_text(encoding="utf-8")


def test_handle_error_response_supports_runtime_error_shapes():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping handleErrorResponse behavior test")

    js_file = _chat_ui_js_source()
    handle_error_response = _extract_js_function(js_file, "handleErrorResponse")

    script = f"""
{handle_error_response}

function makeJsonResponse(status, body) {{
  return {{
    status,
    headers: {{ get(name) {{ return name === "content-type" ? "application/json" : ""; }} }},
    async json() {{ return body; }},
    async text() {{ return ""; }},
  }};
}}

function makeTextResponse(status, textBody) {{
  return {{
    status,
    headers: {{ get(name) {{ return name === "content-type" ? "text/plain" : ""; }} }},
    async json() {{ throw new Error("not json"); }},
    async text() {{ return textBody; }},
  }};
}}

(async () => {{
  const caseA = await handleErrorResponse(makeJsonResponse(400, {{
    error: "Model output was truncated because max_output_tokens was reached.",
    error_type: "truncated_response",
    code: "max_output_tokens_exceeded",
  }}));

  const caseB = await handleErrorResponse(makeJsonResponse(422, {{
    detail: "legacy detail message",
  }}));

  const caseC = await handleErrorResponse(makeTextResponse(502, "Proxy upstream failure"));

  const caseD = await handleErrorResponse(makeTextResponse(500, "   "));

  console.log(JSON.stringify({{ caseA, caseB, caseC, caseD }}));
}})().catch((err) => {{
  console.error(err);
  process.exit(1);
}});
"""

    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout.strip())

    assert data["caseA"] == "Model output was truncated because max_output_tokens was reached."
    assert data["caseB"] == "legacy detail message"
    assert data["caseC"] == "Proxy upstream failure"
    assert data["caseD"] == "Request failed (HTTP 500)"
