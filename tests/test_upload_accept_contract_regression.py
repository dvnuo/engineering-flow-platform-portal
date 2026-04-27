import re
from pathlib import Path

from _js_extract_helpers import _extract_js_set_values


def _chat_ui_source() -> str:
    return Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")


def _app_template_source() -> str:
    return Path("app/templates/app.html").read_text(encoding="utf-8")


def test_upload_accept_contract_stays_aligned_with_portal_supported_types():
    js = _chat_ui_source()
    html = _app_template_source()
    supported_mime_types = _extract_js_set_values(js, "SUPPORTED_UPLOAD_MIME_TYPES")
    supported_extensions = _extract_js_set_values(js, "SUPPORTED_UPLOAD_EXTENSIONS")

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

    js_supported_accept_tokens = supported_mime_types | {f".{ext}" for ext in supported_extensions}
    expected_accept_tokens = required_mime_types | required_extensions
    assert accept_tokens == expected_accept_tokens
    assert accept_tokens.issubset(js_supported_accept_tokens)
    assert "*" not in accept_tokens


def test_upload_and_auto_parse_sets_stay_aligned_for_document_types():
    js = _chat_ui_source()
    supported_mime_types = _extract_js_set_values(js, "SUPPORTED_UPLOAD_MIME_TYPES")
    supported_extensions = _extract_js_set_values(js, "SUPPORTED_UPLOAD_EXTENSIONS")
    auto_parse_mime_types = _extract_js_set_values(js, "AUTO_PARSE_MIME_TYPES")
    auto_parse_extensions = _extract_js_set_values(js, "AUTO_PARSE_EXTENSIONS")

    assert auto_parse_mime_types.issubset(supported_mime_types)
    assert auto_parse_extensions.issubset(supported_extensions)
    assert all(not mime.startswith("image/") for mime in auto_parse_mime_types)
    assert all(ext not in {"jpg", "jpeg", "png", "webp", "gif"} for ext in auto_parse_extensions)
