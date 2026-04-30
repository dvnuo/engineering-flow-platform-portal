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
    assert "session_id=${encodeURIComponent(sessionId)}" in js
    assert "/api/files/parse?session_id=${encodeURIComponent(sessionId)}" in js


def test_chat_ui_send_guard_checks_uploading_and_parsing():
    js = _source()
    assert 'pf.status === "uploading"' in js
    assert 'pf.status === "parsing"' in js


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
    assert payload["a"] == ["f1"]
    assert payload["b"] == []


def test_retry_and_edit_do_not_restore_history_attachments_hidden_input():
    js = _source()
    assert "edit-attachments" not in js
    assert "attachmentsInput.value = JSON.stringify(attachments)" not in js


def test_attachment_only_send_contract_and_textarea_not_required():
    js = _source()
    app_html = Path("app/templates/app.html").read_text(encoding="utf-8")
    textarea_block = app_html[app_html.index('<textarea id="chat-input"'):app_html.index('</textarea>', app_html.index('<textarea id="chat-input"'))]
    assert "required" not in textarea_block
    start = js.index("async function submitChatForSelectedAgent()")
    end = js.index("function handleAgentChatSuccess", start)
    submit_block = js[start:end]
    assert "attachmentsAtSend.length === 0" in submit_block
    assert "message: requestMessage" in submit_block
    assert "[attachment]" in submit_block
    assert "attachments: attachmentsAtSend" in submit_block

