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
    async text() {{ return JSON.stringify(body); }},
  }};
}}

function makeBrokenJsonResponse(status, textBody) {{
  return {{
    status,
    headers: {{ get(name) {{ return name === "content-type" ? "application/json" : ""; }} }},
    async json() {{ throw new Error("invalid json payload"); }},
    async text() {{ return textBody; }},
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
  if (typeof Response !== "function") {{
    throw new Error("Global Response is not available in this Node runtime");
  }}

  const caseA = await handleErrorResponse(makeJsonResponse(400, {{
    error: "Model output was truncated because max_output_tokens was reached.",
    error_type: "truncated_response",
    code: "max_output_tokens_exceeded",
  }}));

  const caseB = await handleErrorResponse(makeJsonResponse(422, {{
    detail: "legacy detail message",
  }}));

  const caseC = await handleErrorResponse(makeJsonResponse(422, {{
    detail: ["first detail", {{ msg: "second detail" }}, {{ nested: "shape" }}],
  }}));

  const caseD = await handleErrorResponse(makeJsonResponse(400, {{
    error: {{ message: "structured nested message" }},
  }}));

  const caseE = await handleErrorResponse(makeJsonResponse(409, {{
    message: "top-level message",
  }}));

  const caseF = await handleErrorResponse(makeJsonResponse(500, {{
    error_type: "truncated_response",
    code: "max_output_tokens_exceeded",
  }}));

  const caseG = await handleErrorResponse(makeBrokenJsonResponse(502, "raw upstream body"));

  const caseH = await handleErrorResponse(makeBrokenJsonResponse(500, "   "));

  const caseI = await handleErrorResponse(makeTextResponse(502, "Proxy upstream failure"));

  const caseJ = await handleErrorResponse(makeTextResponse(500, "   "));

  const caseK = await handleErrorResponse(new Response("raw upstream body", {{
    status: 502,
    headers: {{ "content-type": "application/json" }},
  }}));

  const caseL = await handleErrorResponse(new Response("   ", {{
    status: 500,
    headers: {{ "content-type": "application/json" }},
  }}));

  console.log(JSON.stringify({{ caseA, caseB, caseC, caseD, caseE, caseF, caseG, caseH, caseI, caseJ, caseK, caseL }}));
}})().catch((err) => {{
  console.error(err);
  process.exit(1);
}});
"""

    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout.strip())

    assert data["caseA"] == "Model output was truncated because max_output_tokens was reached."
    assert data["caseB"] == "legacy detail message"
    assert data["caseC"] == 'first detail, second detail, {"nested":"shape"}'
    assert data["caseD"] == "structured nested message"
    assert data["caseE"] == "top-level message"
    assert data["caseF"] == "Request failed (HTTP 500): truncated_response / max_output_tokens_exceeded"
    assert data["caseG"] == "raw upstream body"
    assert data["caseH"] == "Request failed (HTTP 500)"
    assert data["caseI"] == "Proxy upstream failure"
    assert data["caseJ"] == "Request failed (HTTP 500)"
    assert data["caseK"] == "raw upstream body"
    assert data["caseL"] == "Request failed (HTTP 500)"
