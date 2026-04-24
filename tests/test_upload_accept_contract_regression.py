from pathlib import Path
import re


def _chat_ui_source() -> str:
    return Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")


def _app_template_source() -> str:
    return Path("app/templates/app.html").read_text(encoding="utf-8")


def test_upload_accept_contract_stays_aligned_with_portal_supported_types():
    js = _chat_ui_source()
    html = _app_template_source()

    required_mime_types = {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
    }
    required_extensions = {".pdf", ".docx", ".xlsx", ".csv", ".txt"}

    for mime in required_mime_types:
        assert f'"{mime}"' in js
    for ext in required_extensions:
        assert f'"{ext[1:]}"' in js

    accept_match = re.search(r'id="upload-input"[^>]*accept="([^"]+)"', html)
    assert accept_match, "Expected upload-input accept contract in app template"
    accept_tokens = {token.strip() for token in accept_match.group(1).split(",") if token.strip()}

    expected_accept_tokens = required_mime_types | required_extensions
    assert accept_tokens == expected_accept_tokens
    assert "*" not in accept_tokens
