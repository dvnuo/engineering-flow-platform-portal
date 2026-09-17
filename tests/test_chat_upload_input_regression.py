from pathlib import Path
import re


def test_chat_upload_input_allows_multiple_files_and_renders_the_configured_policy():
    html = Path("app/templates/app.html").read_text(encoding="utf-8")

    match = re.search(r'<input[^>]*id="upload-input"[^>]*>', html)
    assert match, "Expected upload file input in app template"

    upload_input = match.group(0)
    assert 'id="upload-input"' in upload_input
    assert 'type="file"' in upload_input
    assert "multiple" in upload_input
    # Both the picker's accept list and the JSON chat_ui.js reads come from
    # Settings.chat_upload_extensions (EFP_CHAT_UPLOAD_EXTENSIONS), so the
    # template must not carry its own hardcoded list any more.
    assert 'accept="{{ chat_upload_policy.accept }}"' in upload_input
    assert 'data-chat-upload-policy="{{ chat_upload_policy_json }}"' in upload_input
    assert "image/jpeg" not in upload_input
