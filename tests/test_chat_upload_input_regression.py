from pathlib import Path
import re


def test_chat_upload_input_allows_multiple_files():
    html = Path("app/templates/app.html").read_text(encoding="utf-8")

    match = re.search(r'<input[^>]*id="upload-input"[^>]*>', html)
    assert match, "Expected upload file input in app template"

    upload_input = match.group(0)
    assert 'id="upload-input"' in upload_input
    assert 'type="file"' in upload_input
    assert "multiple" in upload_input
    assert "accept=" in upload_input
