from pathlib import Path


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
