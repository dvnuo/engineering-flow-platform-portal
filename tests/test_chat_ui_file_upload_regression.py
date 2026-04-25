from pathlib import Path
import json
import shutil
import subprocess

import pytest

from _js_extract_helpers import _extract_js_function


def _source() -> str:
    return Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")


def test_chat_ui_upload_path_includes_session_and_parse_hooks():
    js = _source()

    assert "function generateClientWebchatSessionId()" in js
    assert "function ensureChatSessionId(agentId = state.selectedAgentId)" in js
    assert "session_id=${encodeURIComponent(sessionId)}" in js
    assert "/api/files/parse?session_id=${encodeURIComponent(sessionId)}" in js


def test_chat_ui_send_guard_checks_uploading_and_parsing():
    js = _source()

    assert 'pf.status === "uploading"' in js
    assert 'pf.status === "parsing"' in js
    assert "Waiting for" in js and "finish processing" in js


def test_chat_ui_insert_file_reference_inserts_token_into_input():
    js = _source()

    assert "function insertFileReference(fileIdOrRef)" in js
    assert 'raw.startsWith("@file_")' in js
    assert '`@file_${raw.slice(0, 8)}`' in js
    assert "dom.chatInput.setRangeText" in js
    assert 'dispatchEvent(new Event("input", { bubbles: true }))' in js


def test_chat_ui_history_attachment_rendering_does_not_force_img_for_all_attachments():
    js = _source()

    assert "attachmentType === \"image\" && imageUrl" in js
    assert "message-attachment-file" in js
    assert "normalizedAttachments.forEach(fileId =>" not in js
    assert 'const imageUrl = isAttachmentObject ? (attachment.url || attachment.previewUrl || "") : ""' in js
    assert 'return `<img src="${safeUrl}" class="message-attachment-thumb"' in js


def test_chat_ui_upload_state_machine_keeps_documents_parsing_until_parse_completes():
    js = _source()

    upload_fn_start = js.index("async function uploadPendingFile(")
    upload_fn_end = js.index("window.removePendingFile", upload_fn_start)
    upload_fn = js[upload_fn_start:upload_fn_end]
    assert "pf.status = 'uploaded';" not in upload_fn

    add_fn_start = js.index("async function addPendingFilesAndUpload(")
    add_fn_end = js.index("function renderInputPreview()", add_fn_start)
    add_fn = js[add_fn_start:add_fn_end]
    assert 'pf.status = "parsing";' in add_fn
    assert 'pf.status = "uploaded";' in add_fn
    assert 'pf.status = "failed";' in add_fn
    assert 'pf.error = pf.parseError;' in add_fn
    assert 'pf.parseData = null;' in add_fn


def test_chat_ui_auto_parse_accepts_mime_or_extension():
    js = _source()

    assert "const AUTO_PARSE_MIME_TYPES = new Set([" in js
    assert '"application/pdf"' in js
    assert '"text/csv"' in js
    assert '"text/plain"' in js

    should_parse_start = js.index("function shouldAutoParseUploadedFile(")
    should_parse_end = js.index("async function parseUploadedPendingFile(", should_parse_start)
    should_parse_fn = js[should_parse_start:should_parse_end]
    assert "AUTO_PARSE_MIME_TYPES.has(mime) || AUTO_PARSE_EXTENSIONS.has(ext)" in should_parse_fn


def test_chat_ui_history_generic_file_chip_includes_optional_metadata():
    js = _source()

    assert "function formatAttachmentMetaText(attachment)" in js
    assert "attachment.content_type || attachment.contentType" in js
    assert "attachment.size" in js
    assert 'metaText ? `${baseText} · ${metaText}` : baseText' in js


def test_chat_ui_upload_helpers_behavior_cases():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js = _source()
    upload_helpers_start = js.index("const SUPPORTED_UPLOAD_MIME_TYPES = new Set([")
    upload_helpers_end = js.index("async function parseUploadedPendingFile(", upload_helpers_start)
    upload_helpers_block = js[upload_helpers_start:upload_helpers_end]
    insert_file_reference = _extract_js_function(js, "insertFileReference")

    script = f"""
{upload_helpers_block}
{insert_file_reference}

const parseCases = {{
  mimePdfNoExt: shouldAutoParseUploadedFile(
    {{ file: {{ type: "application/pdf", name: "spec-without-ext" }}, name: "spec-without-ext" }},
    {{ content_type: "application/pdf", filename: "spec-without-ext" }}
  ),
  mimePlainNoExt: shouldAutoParseUploadedFile(
    {{ file: {{ type: "text/plain", name: "notes" }}, name: "notes" }},
    {{ content_type: "text/plain", filename: "notes" }}
  ),
  imageMime: shouldAutoParseUploadedFile(
    {{ file: {{ type: "image/png", name: "diagram" }}, name: "diagram" }},
    {{ content_type: "image/png", filename: "diagram" }}
  ),
  imageMimeWithMisleadingDocExt: shouldAutoParseUploadedFile(
    {{ file: {{ type: "image/png", name: "looks-like-doc.pdf" }}, name: "looks-like-doc.pdf" }},
    {{ content_type: "image/png", filename: "looks-like-doc.pdf" }}
  ),
  docxExtNoMime: shouldAutoParseUploadedFile(
    {{ file: {{ type: "", name: "doc.docx" }}, name: "doc.docx" }},
    {{ content_type: "", filename: "doc.docx" }}
  ),
  xlsxExtNoMime: shouldAutoParseUploadedFile(
    {{ file: {{ type: "", name: "sheet.xlsx" }}, name: "sheet.xlsx" }},
    {{ content_type: "", filename: "sheet.xlsx" }}
  ),
  csvExtNoMime: shouldAutoParseUploadedFile(
    {{ file: {{ type: "", name: "rows.csv" }}, name: "rows.csv" }},
    {{ content_type: "", filename: "rows.csv" }}
  ),
  txtExtNoMime: shouldAutoParseUploadedFile(
    {{ file: {{ type: "", name: "notes.txt" }}, name: "notes.txt" }},
    {{ content_type: "", filename: "notes.txt" }}
  ),
  unsupportedBoth: shouldAutoParseUploadedFile(
    {{ file: {{ type: "application/octet-stream", name: "blob.bin" }}, name: "blob.bin" }},
    {{ content_type: "application/octet-stream", filename: "blob.bin" }}
  ),
}};

const supportedUploadCases = {{
  supportedMime: isRuntimeSupportedUpload({{ type: "text/plain", name: "notes-without-ext" }}),
  supportedExt: isRuntimeSupportedUpload({{ type: "", name: "table.csv" }}),
  unsupported: isRuntimeSupportedUpload({{ type: "application/x-msdownload", name: "run.exe" }}),
}};

function makeInput(value, cursorStart, cursorEnd) {{
  return {{
    value,
    selectionStart: cursorStart,
    selectionEnd: cursorEnd,
    focused: false,
    inputEventCount: 0,
    setRangeText(text, start, end, mode) {{
      this.value = this.value.slice(0, start) + text + this.value.slice(end);
      const nextPos = start + text.length;
      this.selectionStart = nextPos;
      this.selectionEnd = nextPos;
    }},
    dispatchEvent(evt) {{
      if (evt && evt.type === "input") this.inputEventCount += 1;
      return true;
    }},
    focus() {{
      this.focused = true;
    }},
  }};
}}

globalThis.syncChatInputHeight = function () {{}};
globalThis.maybeShowSuggest = function () {{}};
globalThis.Event = function Event(type, init) {{
  this.type = type;
  this.bubbles = Boolean(init && init.bubbles);
}};

const inputA = makeInput("", 0, 0);
globalThis.dom = {{ chatInput: inputA }};
insertFileReference("@file_12345678");

const inputB = makeInput("", 0, 0);
globalThis.dom = {{ chatInput: inputB }};
insertFileReference("1234567890abcdef");

const inputC = makeInput("hello world", 5, 5);
globalThis.dom = {{ chatInput: inputC }};
insertFileReference("1234567890abcdef");

console.log(JSON.stringify({{
  parseCases,
  supportedUploadCases,
  insertCases: {{
    keepRawToken: inputA.value,
    fullIdNormalized: inputB.value,
    spacingAroundToken: inputC.value,
    inputEvents: [inputA.inputEventCount, inputB.inputEventCount, inputC.inputEventCount],
  }},
}}));
"""

    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    payload = json.loads(completed.stdout.strip())

    assert payload["parseCases"] == {
        "mimePdfNoExt": True,
        "mimePlainNoExt": True,
        "imageMime": False,
        "imageMimeWithMisleadingDocExt": False,
        "docxExtNoMime": True,
        "xlsxExtNoMime": True,
        "csvExtNoMime": True,
        "txtExtNoMime": True,
        "unsupportedBoth": False,
    }
    assert payload["supportedUploadCases"] == {
        "supportedMime": True,
        "supportedExt": True,
        "unsupported": False,
    }
    assert payload["insertCases"]["keepRawToken"] == "@file_12345678"
    assert payload["insertCases"]["fullIdNormalized"] == "@file_12345678"
    assert payload["insertCases"]["spacingAroundToken"] == "hello @file_12345678 world"
    assert payload["insertCases"]["inputEvents"] == [1, 1, 1]
    assert "chat-attachments" not in insert_file_reference


def test_chat_ui_history_attachment_rendering_branches_image_vs_generic_chip_behavior():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js = _source()
    format_attachment_meta_text = _extract_js_function(js, "formatAttachmentMetaText")
    render_chat_history = _extract_js_function(js, "renderChatHistory")

    script = f"""
{format_attachment_meta_text}
{render_chat_history}

class FakeElement {{
  constructor(tagName) {{
    this.tagName = String(tagName || "").toUpperCase();
    this.children = [];
    this.dataset = {{}};
    this.className = "";
    this.textContent = "";
    this.innerHTML = "";
  }}
  appendChild(child) {{
    this.children.push(child);
    return child;
  }}
}}

const messageList = new FakeElement("div");
const dom = {{ messageList }};
const chatState = {{ attachmentHistory: [] }};

globalThis.document = {{
  createElement(tag) {{
    return new FakeElement(tag);
  }},
}};
globalThis.getChatState = function () {{ return chatState; }};
globalThis.clearMessageListToWelcome = function () {{}};
globalThis.getHistoryMessageDisplayName = function () {{ return "User"; }};
globalThis.renderMarkdown = function () {{}};
globalThis.decorateToolMessages = function () {{}};
globalThis.scrollToBottom = function () {{}};

const userMessage = {{
  role: "user",
  content: "hello",
  attachments: [
    "legacy_file_ref",
    {{ type: "file", filename: "spec.pdf", content_type: "application/pdf", size: 12 }},
    {{ type: "image", url: "https://cdn.example.com/img.png", name: "img.png", file_id: "img-1" }},
  ],
}};

renderChatHistory([userMessage], {{}});

function collectByClass(root, className, acc = []) {{
  if (!root || typeof root !== "object") return acc;
  const classes = String(root.className || "").split(/\\s+/).filter(Boolean);
  if (classes.includes(className)) acc.push(root);
  for (const child of root.children || []) collectByClass(child, className, acc);
  return acc;
}}

const imageNodes = collectByClass(messageList, "message-attachment-thumb");
const fileNodes = collectByClass(messageList, "message-attachment-file");

console.log(JSON.stringify({{
  imageNodeCount: imageNodes.length,
  fileNodeCount: fileNodes.length,
  fileTexts: fileNodes.map(node => node.textContent),
}}));
"""

    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    payload = json.loads(completed.stdout.strip())

    assert payload["imageNodeCount"] == 1
    assert payload["fileNodeCount"] == 2
    assert any(text.startswith("📄 legacy_file_ref") for text in payload["fileTexts"])
    assert any(text.startswith("📄 spec.pdf") for text in payload["fileTexts"])


def test_chat_ui_upload_and_first_send_reuse_same_preallocated_session_id():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js = _source()
    create_default_chat_state = _extract_js_function(js, "createDefaultChatState")
    ensure_chat_state = _extract_js_function(js, "ensureChatState")
    current_session_for_agent = _extract_js_function(js, "currentSessionIdForAgent")
    sync_hidden_session = _extract_js_function(js, "syncHiddenSessionInputFromState")
    update_agent_session = _extract_js_function(js, "updateAgentSession")
    generate_session_id = _extract_js_function(js, "generateClientWebchatSessionId")
    ensure_session_id = _extract_js_function(js, "ensureChatSessionId")

    script = f"""
const state = {{
  selectedAgentId: "agent-1",
  chatStatesByAgent: new Map(),
  agentSessionIds: new Map(),
}};
const dom = {{}};
let hiddenValue = "";
const setLastSessionCalls = [];

globalThis.document = {{
  getElementById(id) {{
    if (id !== "chat-session-id") return null;
    return {{
      get value() {{ return hiddenValue; }},
      set value(v) {{ hiddenValue = String(v); }},
    }};
  }},
}};
globalThis.setLastSessionId = function (agentId, sessionId) {{
  setLastSessionCalls.push([agentId, sessionId]);
}};
globalThis.ensureEventSocketForSelectedAgent = function () {{}};
globalThis.currentSessionIdForSelectedAgent = function () {{
  return currentSessionIdForAgent(state.selectedAgentId);
}};

{create_default_chat_state}
{ensure_chat_state}
{current_session_for_agent}
{sync_hidden_session}
{update_agent_session}
{generate_session_id}
{ensure_session_id}

const uploadPhaseSessionId = ensureChatSessionId("agent-1");
const sendPhaseSessionId = ensureChatSessionId("agent-1");
const stateSessionId = currentSessionIdForAgent("agent-1");

console.log(JSON.stringify({{
  uploadPhaseSessionId,
  sendPhaseSessionId,
  stateSessionId,
  hiddenValue,
  setLastSessionCalls,
}}));
"""

    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    payload = json.loads(completed.stdout.strip())

    assert payload["uploadPhaseSessionId"].startswith("webchat_")
    assert payload["sendPhaseSessionId"] == payload["uploadPhaseSessionId"]
    assert payload["stateSessionId"] == payload["uploadPhaseSessionId"]
    assert payload["hiddenValue"] == payload["uploadPhaseSessionId"]
    assert payload["setLastSessionCalls"] == [["agent-1", payload["uploadPhaseSessionId"]]]


def test_chat_ui_build_attachments_only_includes_uploaded_files():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js = _source()
    build_attachments = _extract_js_function(js, "buildAttachmentsFromChatState")

    script = f"""
{build_attachments}

const chatState = {{
  pendingFiles: [
    {{ status: "uploaded", file_id: "img-1", name: "photo.png", type: "image/png", previewUrl: "blob:img-1" }},
    {{ status: "uploaded", file_id: "doc-1", name: "report.pdf", type: "application/pdf", previewUrl: "" }},
    {{ status: "uploading", file_id: "up-1", name: "uploading.txt", type: "text/plain", previewUrl: "" }},
    {{ status: "parsing", file_id: "parse-1", name: "parsing.docx", type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", previewUrl: "" }},
    {{ status: "failed", file_id: "fail-1", name: "failed.csv", type: "text/csv", previewUrl: "" }},
    {{ status: "uploaded", file_id: "", name: "missing-id.txt", type: "text/plain", previewUrl: "" }},
  ],
}};

globalThis.document = {{
  getElementById(id) {{
    if (id !== "chat-attachments") return null;
    return {{ value: "[]" }};
  }},
}};

const beforeSnapshot = JSON.stringify(chatState.pendingFiles);
const attachments = buildAttachmentsFromChatState("agent-1", chatState);
const afterSnapshot = JSON.stringify(chatState.pendingFiles);

console.log(JSON.stringify({{
  attachments,
  beforeSnapshot,
  afterSnapshot,
}}));
"""

    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    payload = json.loads(completed.stdout.strip())

    assert payload["attachments"] == ["img-1", "doc-1"]
    assert payload["afterSnapshot"] == payload["beforeSnapshot"]


def test_chat_ui_submit_guard_blocks_uploading_and_parsing_but_not_failed_only_state():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js = _source()
    submit_chat = _extract_js_function(js, "submitChatForSelectedAgent")

    script = f"""
{submit_chat}

const toasts = [];
let fetchCallCount = 0;
const state = {{
  selectedAgentId: "agent-1",
}};
const dom = {{
  chatInput: {{ value: "   " }},
}};

const chatStateByCase = new Map([
  ["uploading", {{ pendingFiles: [{{ status: "uploading", file_id: "up-1" }}] }}],
  ["parsing", {{ pendingFiles: [{{ status: "parsing", file_id: "parse-1" }}] }}],
  ["failed-only", {{ pendingFiles: [{{ status: "failed", file_id: "fail-1" }}] }}],
]);

let activeCase = "uploading";
globalThis.ensureChatState = function () {{ return chatStateByCase.get(activeCase); }};
globalThis.guardNoActiveChatRequestForAgent = function () {{ return true; }};
globalThis.showToast = function (message) {{ toasts.push(String(message)); }};
globalThis.fetch = async function () {{
  fetchCallCount += 1;
  throw new Error("fetch should not be called in this guard-focused test");
}};

async function runCase(caseName) {{
  activeCase = caseName;
  await submitChatForSelectedAgent();
}}

await runCase("uploading");
await runCase("parsing");
await runCase("failed-only");

console.log(JSON.stringify({{
  toasts,
  fetchCallCount,
}}));
"""

    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    payload = json.loads(completed.stdout.strip())

    assert payload["toasts"][0] == "Waiting for 1 file(s) to upload..."
    assert payload["toasts"][1] == "Waiting for 1 file(s) to finish processing..."
    assert len(payload["toasts"]) == 2
    assert payload["fetchCallCount"] == 0


def test_chat_ui_render_input_preview_badge_contract_contains_all_pending_states():
    js = _source()
    render_preview_start = js.index("function renderInputPreview() {")
    render_preview_end = js.index("async function uploadPendingFile(", render_preview_start)
    render_preview_fn = js[render_preview_start:render_preview_end]

    assert "pf.status === 'uploading'" in render_preview_fn
    assert "⏳" in render_preview_fn
    assert "pf.status === 'parsing'" in render_preview_fn
    assert "🧠" in render_preview_fn
    assert "pf.status === 'uploaded'" in render_preview_fn
    assert "✓" in render_preview_fn
    assert "pf.status === 'failed'" in render_preview_fn
    assert "✗" in render_preview_fn
