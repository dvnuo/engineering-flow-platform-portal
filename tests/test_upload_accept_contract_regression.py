"""The composer's upload allowlist has one source: Settings.chat_upload_extensions.

The template renders it into the file picker, chat_ui.js reads it back from
the DOM, the upload proxy enforces it and agent pods receive it as
EFP_CHAT_UPLOAD_EXTENSIONS. These tests pin that the JS fallback and the
default accept list still equal the historical hardcoded contract.
"""

from pathlib import Path

from _js_extract_helpers import _extract_js_set_values

from app.utils.chat_upload_policy import (
    DEFAULT_CHAT_UPLOAD_EXTENSIONS,
    FORWARDED_EXTENSION_MIME_TYPES,
    IMAGE_EXTENSION_MIME_TYPES,
    build_chat_upload_policy,
)


def _chat_ui_source() -> str:
    return Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")


def _app_template_source() -> str:
    return Path("app/templates/app.html").read_text(encoding="utf-8")


def test_js_fallback_allowlist_matches_the_portal_default():
    js = _chat_ui_source()

    assert _extract_js_set_values(js, "DEFAULT_UPLOAD_EXTENSIONS") == set(DEFAULT_CHAT_UPLOAD_EXTENSIONS)
    assert _extract_js_set_values(js, "IMAGE_UPLOAD_EXTENSIONS") == set(IMAGE_EXTENSION_MIME_TYPES)
    assert _extract_js_set_values(js, "FORWARDED_UPLOAD_EXTENSIONS") == set(FORWARDED_EXTENSION_MIME_TYPES)
    # The old per-file hardcoded sets are gone: one source of truth.
    for stale in ("SUPPORTED_UPLOAD_MIME_TYPES", "SUPPORTED_UPLOAD_EXTENSIONS", "AUTO_PARSE_EXTENSIONS", "AUTO_PARSE_MIME_TYPES"):
        assert stale not in js


def test_js_reads_the_policy_rendered_into_the_upload_input():
    js = _chat_ui_source()

    assert 'document.getElementById("upload-input")?.dataset?.chatUploadPolicy' in js
    assert "function getChatUploadPolicy()" in js
    assert "getChatUploadPolicy().extensions.has(ext)" in js
    assert "describeChatUploadPolicy()" in js
    assert "uploadTooLargeMessage(file)" in js
    assert "reject(new Error(uploadErrorMessageFromXhr(xhr)))" in js
    assert "Supported: images, pdf, docx, xlsx, csv, txt." not in js


def test_template_renders_accept_and_policy_from_settings():
    html = _app_template_source()

    assert 'accept="{{ chat_upload_policy.accept }}"' in html
    assert 'data-chat-upload-policy="{{ chat_upload_policy_json }}"' in html
    assert 'accept="image/jpeg' not in html


def test_default_accept_is_the_productivity_suite_list():
    # The default picker offers what Productivity Suite accepts plus the text
    # formats: the parsed documents, the forwarded office formats, and the
    # images, whose MIME entries let mobile pickers offer the camera roll.
    policy = build_chat_upload_policy(None, 25)
    accept_tokens = {token.strip() for token in policy.accept.split(",") if token.strip()}

    assert accept_tokens == {
        ".pdf", ".docx", ".xlsx", ".csv", ".txt", ".log", ".pptx", ".zip",
        ".md", ".yaml", ".yml", ".json", ".xml",
        ".doc", ".dot", ".xls", ".png", ".jpg", ".jpeg", ".jpe", ".jfif", ".tiff", ".webp", ".bmp",
        "image/png", "image/jpeg", "image/tiff", "image/webp", "image/bmp",
    }
    assert "*" not in accept_tokens
    assert policy.env_value == (
        "pdf,docx,xlsx,csv,txt,log,pptx,zip,md,yaml,yml,json,xml,doc,dot,xls,png,jpg,jpeg,jpe,jfif,tiff,webp,bmp"
    )
    assert policy.image_extensions == ("png", "jpg", "jpeg", "jpe", "jfif", "tiff", "webp", "bmp")
    assert policy.forwarded_extensions == ("doc", "dot", "xls")
    assert "doc" not in policy.document_extensions and "png" not in policy.document_extensions

    # A deployment without Productivity Suite trims the list to the parsed formats.
    documents_only = build_chat_upload_policy("pdf,docx", 25)
    assert documents_only.accept == ".pdf,.docx"
    assert documents_only.image_extensions == () and documents_only.forwarded_extensions == ()

    with_images = build_chat_upload_policy("png,pdf", 25)
    assert with_images.accept == ".png,.pdf,image/png"
