"""Chat composer attachment policy (EFP_CHAT_UPLOAD_EXTENSIONS / EFP_MAX_UPLOAD_MB)."""

import html
import json
import re
from types import SimpleNamespace

import pytest

from app.utils.chat_upload_policy import (
    DEFAULT_CHAT_UPLOAD_EXTENSIONS,
    build_chat_upload_policy,
    file_extension,
    get_chat_upload_policy,
    parse_chat_upload_extensions,
)


def test_parse_normalizes_dots_case_separators_and_duplicates():
    assert parse_chat_upload_extensions(" .PDF, md ;txt\n.md  Yml") == ["pdf", "md", "txt", "yml"]


def test_parse_falls_back_to_defaults_when_empty_or_garbage():
    defaults = list(DEFAULT_CHAT_UPLOAD_EXTENSIONS)
    assert parse_chat_upload_extensions("") == defaults
    assert parse_chat_upload_extensions(None) == defaults
    assert parse_chat_upload_extensions("*.*, ???") == defaults


def test_file_extension_ignores_paths_and_case():
    assert file_extension("Notes.TXT") == "txt"
    assert file_extension("dir/sub/report.PDF") == "pdf"
    assert file_extension("C:\\Users\\me\\a.tar.gz") == "gz"
    assert file_extension("README") == ""
    assert file_extension("") == ""
    assert file_extension(None) == ""


def test_policy_accept_summary_env_and_mime_types():
    policy = build_chat_upload_policy("png, pdf, md, log", 25)

    assert policy.extensions == ("png", "pdf", "md", "log")
    assert policy.image_extensions == ("png",)
    assert policy.document_extensions == ("pdf", "md", "log")
    assert policy.accept == ".png,.pdf,.md,.log,image/png"
    assert policy.summary == "png, pdf, md, log"
    assert policy.env_value == "png,pdf,md,log"
    assert policy.mime_types == ("image/png", "application/pdf", "text/markdown", "text/plain")
    assert policy.max_upload_bytes == 25 * 1024 * 1024


def test_policy_is_allowed_and_rejection_detail():
    policy = build_chat_upload_policy("txt,md", 25)

    assert policy.is_allowed("notes.TXT") is True
    assert policy.is_allowed("a/b/readme.md") is True
    assert policy.is_allowed("doc.pdf") is False
    assert policy.is_allowed("README") is False
    assert policy.is_allowed(None) is False
    assert policy.rejection_detail("doc.pdf") == "File type .pdf is not allowed. Allowed: txt, md"
    assert policy.rejection_detail("README") == "Files without an extension are not allowed. Allowed: txt, md"


def test_build_policy_max_upload_fallbacks():
    assert build_chat_upload_policy("txt", 40).max_upload_mb == 40
    assert build_chat_upload_policy("txt", "12").max_upload_mb == 12
    assert build_chat_upload_policy("txt", 0).max_upload_mb == 25
    assert build_chat_upload_policy("txt", "lots").max_upload_mb == 25
    assert build_chat_upload_policy("txt", None).max_upload_mb == 25


def test_client_json_roundtrips_and_is_attribute_safe():
    policy = build_chat_upload_policy("txt,md", 30)
    payload = json.loads(policy.to_client_json())

    assert payload == {"extensions": ["txt", "md"], "accept": ".txt,.md", "max_upload_mb": 30}
    assert "<" not in policy.to_client_json()


def test_settings_default_and_env_override(monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("EFP_CHAT_UPLOAD_EXTENSIONS", raising=False)
    assert Settings().chat_upload_extensions == "jpg,jpeg,png,webp,gif,pdf,docx,xlsx,csv,txt"

    monkeypatch.setenv("EFP_CHAT_UPLOAD_EXTENSIONS", " md; TXT ")
    assert parse_chat_upload_extensions(Settings().chat_upload_extensions) == ["md", "txt"]


def test_get_chat_upload_policy_reads_the_running_settings(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "chat_upload_extensions", " .PDF; md")
    monkeypatch.setattr(get_settings(), "max_upload_mb", 40)

    policy = get_chat_upload_policy()
    assert policy.extensions == ("pdf", "md")
    assert policy.max_upload_mb == 40


def test_app_page_renders_the_policy_into_the_upload_input(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    import app.web as web_module

    user = SimpleNamespace(id=1, username="alice", nickname="Alice", role="user")
    monkeypatch.setattr(web_module, "_authorized_web_user", lambda _request: (user, None))
    monkeypatch.setattr(web_module.get_settings(), "chat_upload_extensions", "md, txt, png")
    monkeypatch.setattr(web_module.get_settings(), "max_upload_mb", 12)

    response = TestClient(app).get("/app")
    assert response.status_code == 200

    match = re.search(r'<input[^>]*id="upload-input"[^>]*>', response.text)
    assert match, "upload input missing from rendered page"
    tag = match.group(0)

    accept = re.search(r'accept="([^"]+)"', tag)
    assert accept and accept.group(1) == ".md,.txt,.png,image/png"

    policy_attr = re.search(r'data-chat-upload-policy="([^"]+)"', tag)
    assert policy_attr, "policy JSON missing from upload input"
    policy = json.loads(html.unescape(policy_attr.group(1)))
    assert policy == {"extensions": ["md", "txt", "png"], "accept": ".md,.txt,.png,image/png", "max_upload_mb": 12}
