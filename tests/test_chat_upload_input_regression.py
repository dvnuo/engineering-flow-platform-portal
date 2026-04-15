from pathlib import Path


def test_chat_upload_input_allows_multiple_files():
    html = Path("app/templates/app.html").read_text(encoding="utf-8")
    assert '<input id="upload-input" type="file" class="hidden" multiple />' in html
