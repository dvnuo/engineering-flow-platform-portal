from pathlib import Path
import json
import shutil
import subprocess
import re

import pytest

from _js_extract_helpers import _extract_js_function


def _source() -> str:
    return Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")


def test_chat_ui_upload_path_includes_session_and_parse_hooks():
    js = _source()
    assert "session_id=${encodeURIComponent(sessionId)}" in js
    assert "/api/files/parse?session_id=${encodeURIComponent(sessionId)}" in js


def test_chat_ui_send_guard_checks_uploading_and_parsing():
    js = _source()
    assert 'pf.status === "uploading"' in js
    assert 'pf.status === "parsing"' in js


def test_parse_uploaded_pending_file_treats_success_false_as_failure():
    js = _source()
    parse_fn = _extract_js_function(js, "parseUploadedPendingFile")
    node_bin = shutil.which("node")
    if not node_bin:
      pytest.skip("node is not installed")
    script = f"""
{parse_fn}
globalThis.handleErrorResponse = async () => "unused";
globalThis.fetch = async () => ({{
  ok: true,
  json: async () => ({{ success:false, error:"unsupported_file_type" }})
}});
(async () => {{
  try {{
    await parseUploadedPendingFile({{ file_id:"f1" }}, "agent", "s1");
    console.log("NO_ERROR");
  }} catch (err) {{
    console.log(String(err && err.message ? err.message : err));
  }}
}})();
"""
    out = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    assert "unsupported_file_type" in out.stdout.strip()


def test_removed_sources_and_mentions_contracts():
    js = _source()
    assert "function insertFileReference(fileIdOrRef)" not in js
    assert "openMyUploads" not in js
    assert "cachedMentionFiles" not in js
    assert "cachedMentionFilesByAgent" not in js
    assert 'agentApi("/api/files/list")' not in js
    assert "@file_${" not in js
    assert "attachmentHistory" not in js
    assert "didAppendAttachmentHistoryForPendingSend" not in js
    assert "draftAttachmentsValue" not in js
    assert "backupFiles" not in js
    assert "getUserArticleAttachments" not in js
    panel_block = js[js.index("const ALLOWED_UTILITY_PANEL_KEYS"):js.index("function normalizeUtilityPanelKey")]
    assert '"uploads"' not in panel_block


def test_upload_and_attachments_pipeline_still_present():
    js = _source()
    assert "async function uploadPendingFile" in js
    assert "async function parseUploadedPendingFile" in js
    assert "function buildAttachmentsFromChatState" in js
    assert "attachments: attachmentsAtSend" in js


def test_build_attachments_from_pending_uploaded_only_and_no_hidden_fallback():
    js = _source()
    build_attachments = _extract_js_function(js, "buildAttachmentsFromChatState")
    node_bin = shutil.which("node")
    if not node_bin:
      pytest.skip("node is not installed")
    script = f"""
{build_attachments}
globalThis.document = {{ getElementById: (id) => id === "chat-attachments" ? {{ value: '[\"old-file\"]' }} : null }};
const a = buildAttachmentsFromChatState('agent', {{ pendingFiles:[{{file_id:'f1',status:'uploaded'}},{{file_id:'f2',status:'parsing'}}] }});
const b = buildAttachmentsFromChatState('agent', {{ pendingFiles:[] }});
console.log(JSON.stringify({{a,b}}));
"""
    out = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    payload = json.loads(out.stdout.strip())
    assert payload["a"] == [
        {
            "file_id": "f1",
            "id": "f1",
            "name": "f1",
            "filename": "f1",
            "content_type": "",
            "mime": "",
            "size": None,
            "type": "file",
            "parsed": False,
            "parse_error": "",
        }
    ]
    assert payload["b"] == []


def test_build_attachments_metadata_shape_and_parse_flags():
    js = _source()
    build_attachments = _extract_js_function(js, "buildAttachmentsFromChatState")
    node_bin = shutil.which("node")
    if not node_bin:
      pytest.skip("node is not installed")
    script = f"""
{build_attachments}
const textResult = buildAttachmentsFromChatState('agent', {{
  pendingFiles:[{{
    file_id:'f1',
    status:'uploaded',
    isImage:false,
    uploadedData:{{ name:'notes.txt', content_type:'text/plain', size:12 }}
  }}]
}});
const imageResult = buildAttachmentsFromChatState('agent', {{
  pendingFiles:[{{
    file_id:'img1',
    status:'uploaded',
    isImage:true,
    uploadedData:{{ name:'cat.png', content_type:'image/png', size:99 }},
    previewUrl:'blob:http://local/abc'
  }}]
}});
const parsedOkResult = buildAttachmentsFromChatState('agent', {{
  pendingFiles:[{{
    file_id:'f2',
    status:'uploaded',
    uploadedData:{{ name:'a.csv', content_type:'text/csv', size:5 }},
    parseData:{{ success:true }}
  }}]
}});
const parseFailedResult = buildAttachmentsFromChatState('agent', {{
  pendingFiles:[{{
    file_id:'pdf1',
    status:'uploaded',
    uploadedData:{{ name:'a.pdf', content_type:'application/pdf', size:123 }},
    parseError:'unsupported_file_type',
    parseData:null
  }}]
}});
const filtered = buildAttachmentsFromChatState('agent', {{
  pendingFiles:[
    {{ file_id:'u1', status:'uploading' }},
    {{ file_id:'p1', status:'parsing' }},
    {{ file_id:'f1', status:'failed' }},
    {{ status:'uploaded', uploadedData:{{ name:'nofileid.txt' }} }}
  ]
}});
console.log(JSON.stringify({{
  textResult,
  imageResult,
  parsedOkResult,
  parseFailedResult,
  filtered,
  imageStringified: JSON.stringify(imageResult),
}}));
"""
    out = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    payload = json.loads(out.stdout.strip())
    assert payload["textResult"] == [
        {
            "file_id": "f1",
            "id": "f1",
            "name": "notes.txt",
            "filename": "notes.txt",
            "content_type": "text/plain",
            "mime": "text/plain",
            "size": 12,
            "type": "file",
            "parsed": False,
            "parse_error": "",
        }
    ]
    image_attachment = payload["imageResult"][0]
    assert image_attachment["type"] == "image"
    assert image_attachment["content_type"] == "image/png"
    assert image_attachment["mime"] == "image/png"
    assert image_attachment["name"] == "cat.png"
    assert "previewUrl" not in image_attachment
    assert "url" not in image_attachment
    assert "dataUrl" not in image_attachment
    assert "blob:" not in payload["imageStringified"]
    assert payload["parsedOkResult"][0]["parsed"] is True
    assert len(payload["parseFailedResult"]) == 1
    assert payload["parseFailedResult"][0]["parsed"] is False
    assert payload["parseFailedResult"][0]["parse_error"] == "unsupported_file_type"
    assert payload["filtered"] == []


def test_build_attachments_handles_non_string_content_type_safely():
    js = _source()
    build_attachments = _extract_js_function(js, "buildAttachmentsFromChatState")
    node_bin = shutil.which("node")
    if not node_bin:
      pytest.skip("node is not installed")
    script = f"""
{build_attachments}
const result = buildAttachmentsFromChatState('agent', {{
  pendingFiles:[{{
    file_id:'x1',
    status:'uploaded',
    uploadedData:{{ name:'weird.bin', content_type:123, size:1 }}
  }}]
}});
console.log(JSON.stringify({{ result, serialized: JSON.stringify(result) }}));
"""
    out = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    payload = json.loads(out.stdout.strip())
    item = payload["result"][0]
    assert item["content_type"] == "123"
    assert item["type"] == "file"
    assert "url" not in item
    assert "previewUrl" not in item
    assert "blob:" not in payload["serialized"]
    assert "base64" not in payload["serialized"]


def test_parse_failure_catch_keeps_uploaded_and_no_continue():
    js = _source()
    start = js.index("if (shouldAutoParseUploadedFile(pf, data))")
    end = js.index("} else {", start)
    parse_block = js[start:end]
    catch_start = parse_block.index("} catch (parseError) {")
    parse_catch_block = parse_block[catch_start:]
    assert 'pf.status = "uploaded"' in parse_catch_block
    assert "pf.parseError =" in parse_catch_block
    assert 'pf.error = ""' in parse_catch_block
    assert "pf.parseData = null" in parse_catch_block
    assert 'pf.status = "failed"' not in parse_catch_block
    assert "continue;" not in parse_catch_block


def test_retry_and_edit_do_not_restore_history_attachments_hidden_input():
    js = _source()
    assert "edit-attachments" not in js
    assert "attachmentsInput.value = JSON.stringify(attachments)" not in js


def test_attachment_only_send_contract_and_textarea_not_required():
    js = _source()
    app_html = Path("app/templates/app.html").read_text(encoding="utf-8")
    assert 'id="quick-uploads-btn"' not in app_html
    assert 'id="upload-input"' in app_html
    assert 'id="composer-attach-btn"' in app_html
    assert 'id="send-chat-btn"' in app_html
    textarea_block = re.search(r'<textarea[^>]*id="chat-input"[\s\S]*?</textarea>', app_html).group(0)
    assert "required" not in textarea_block
    start = js.index("async function submitChatForSelectedAgent()")
    end = js.index("function handleAgentChatSuccess", start)
    submit_block = js[start:end]
    assert "attachmentsAtSend.length === 0" in submit_block
    assert "message: requestMessage" in submit_block
    assert "[attachment]" in submit_block
    assert "attachments: attachmentsAtSend" in submit_block
