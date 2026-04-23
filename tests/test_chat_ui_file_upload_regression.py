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


def test_chat_ui_history_attachment_rendering_does_not_force_img_for_all_attachments():
    js = _source()

    assert "attachmentType === \"image\" && imageUrl" in js
    assert "message-attachment-file" in js
    assert "normalizedAttachments.forEach(fileId =>" not in js
