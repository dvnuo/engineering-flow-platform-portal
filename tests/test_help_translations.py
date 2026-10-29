"""Translated help topics: app/help/<locale>/<topic>.md beside the English.

help_center.localized_documents swaps a translation in topic by topic for the
request's language; the group, icon and order always come from the English
file. These tests pin that behaviour and keep every translation in step with
its English topic: the translation must still have an English original, keep
its code blocks, inline code and {mod} placeholders, and exist for every topic
in each language the Portal ships, as the catalogues do.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.i18n import bind_locale, reset_locale
from app.services import help_center
from app.services.help_center import HELP_DIR, NON_TOPIC_FILES, load_documents, localized_documents, split_front_matter

TRANSLATED_LOCALES = ("zh-Hans", "zh-Hant-HK")
FENCE_RE = re.compile(r"^\s*```", re.M)
INLINE_CODE_RE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
CJK_RE = re.compile("[\u4e00-\u9fff]")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _english_topics() -> list[str]:
    return sorted(p.stem for p in HELP_DIR.glob("*.md") if p.name not in NON_TOPIC_FILES)


def _translations() -> list[tuple[str, str]]:
    found = []
    for locale in TRANSLATED_LOCALES:
        for path in sorted((HELP_DIR / locale).glob("*.md")):
            found.append((locale, path.stem))
    return found


def _strip_fenced_blocks(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.S)


# --- the loader -----------------------------------------------------------------------------

def test_a_translation_replaces_title_summary_and_body_but_not_the_listing(tmp_path):
    _write(
        tmp_path / "guide.md",
        "---\ntitle: The guide\nsummary: English summary\ngroup: Working\nicon: book\norder: 20\n---\nEnglish body",
    )
    _write(tmp_path / "other.md", "---\ntitle: Other\ngroup: Working\n---\nOther body")
    _write(
        tmp_path / "zh-Hans" / "guide.md",
        "---\ntitle: [hans] The guide\nsummary: [hans] summary\ngroup: Moved\nicon: x\norder: 1\n---\n[hans] body",
    )
    _write(tmp_path / "zh-Hans" / "gone.md", "---\ntitle: [hans] Gone\ngroup: Working\n---\nno English topic")

    documents = localized_documents("zh-Hans", tmp_path)
    guide = documents["guide"]
    assert guide.meta["title"] == "[hans] The guide"
    assert guide.meta["summary"] == "[hans] summary"
    assert guide.body == "[hans] body"
    assert guide.locale == "zh-Hans"
    # The listing stays the English file's: a translation cannot move a topic.
    assert (guide.meta["group"], guide.meta["icon"], guide.meta["order"]) == ("Working", "book", "20")
    # A topic without a translation stays English; a translation without a topic is ignored.
    assert documents["other"].body == "Other body"
    assert documents["other"].locale == "en"
    assert "gone" not in documents

    assert localized_documents("en", tmp_path)["guide"].body == "English body"
    assert localized_documents("fr", tmp_path)["guide"].body == "English body"
    assert localized_documents("zh-Hant-HK", tmp_path)["guide"].locale == "en"


def test_a_translation_without_a_body_keeps_the_english_body(tmp_path):
    _write(tmp_path / "guide.md", "---\ntitle: The guide\ngroup: Working\n---\nEnglish body")
    _write(tmp_path / "zh-Hans" / "guide.md", "---\ntitle: [hans] The guide\n---\n")
    guide = localized_documents("zh-Hans", tmp_path)["guide"]
    assert guide.meta["title"] == "[hans] The guide"
    assert guide.body == "English body"
    assert guide.locale == "en"


def test_topics_follow_the_request_locale(tmp_path, monkeypatch):
    _write(tmp_path / "guide.md", "---\ntitle: The guide\ngroup: Working\n---\nEnglish body")
    _write(tmp_path / "zh-Hant-HK" / "guide.md", "---\ntitle: [hk] The guide\n---\n[hk] body")
    monkeypatch.setattr(help_center, "HELP_DIR", tmp_path)

    english = help_center.get_topic("guide")
    assert (english.title, english.body, english.body_locale) == ("The guide", "English body", "en")
    token = bind_locale("zh-Hant-HK")
    try:
        translated = help_center.get_topic("guide")
    finally:
        reset_locale(token)
    assert (translated.title, translated.body, translated.body_locale) == ("[hk] The guide", "[hk] body", "zh-Hant-HK")


def _help_panel(monkeypatch, tmp_path, accept_language):
    from app.main import app
    import app.web as web_module

    monkeypatch.setattr(help_center, "HELP_DIR", tmp_path)
    member = SimpleNamespace(id=7, username="jdoe", nickname="Jane", role="user", preferences_json=None)
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: member)
    client = TestClient(app)
    response = client.get("/app/help/panel", params={"topic": "guide"}, headers={"Accept-Language": accept_language})
    assert response.status_code == 200
    return response.text


def test_the_panel_says_when_a_guide_is_not_translated(monkeypatch, tmp_path):
    _write(tmp_path / "guide.md", "---\ntitle: The guide\ngroup: Working\n---\nEnglish body")
    html = _help_panel(monkeypatch, tmp_path, "zh-CN")
    assert 'data-help-markdown="English body"' in html
    assert "portal-help-untranslated" in html
    # In English there is nothing to say.
    assert "portal-help-untranslated" not in _help_panel(monkeypatch, tmp_path, "en-US")


def test_the_panel_shows_the_translation(monkeypatch, tmp_path):
    _write(tmp_path / "guide.md", "---\ntitle: The guide\ngroup: Working\n---\nEnglish body")
    _write(tmp_path / "zh-Hans" / "guide.md", "---\ntitle: [hans] The guide\n---\n[hans] body")
    html = _help_panel(monkeypatch, tmp_path, "zh-CN")
    assert "[hans] The guide" in html
    assert 'data-help-markdown="[hans] body"' in html
    assert "portal-help-untranslated" not in html


# --- the shipped translations ----------------------------------------------------------------

@pytest.mark.parametrize("locale", TRANSLATED_LOCALES)
def test_every_topic_is_translated(locale):
    translated = {path.stem for path in (HELP_DIR / locale).glob("*.md")}
    missing = [topic for topic in _english_topics() if topic not in translated]
    assert not missing, f"{locale} has no translation of: {missing}"


@pytest.mark.parametrize(("locale", "topic"), _translations())
def test_a_translation_keeps_the_structure_of_its_english_topic(locale, topic):
    english_path = HELP_DIR / f"{topic}.md"
    assert english_path.is_file(), f"{locale}/{topic}.md has no English topic"
    english_text = english_path.read_text(encoding="utf-8-sig")
    translated_text = (HELP_DIR / locale / f"{topic}.md").read_text(encoding="utf-8")
    assert not translated_text.startswith("\ufeff"), "write translations without a BOM"

    english_meta, english_body = split_front_matter(english_text)
    translated_meta, translated_body = split_front_matter(translated_text)
    # Connector topics take their title from the catalogue (their English front
    # matter is only an icon); a topic that lists itself keeps a translated title.
    if english_meta.get("title"):
        assert translated_meta.get("title"), "a translation keeps the title in its front matter"
    for key in ("group", "icon", "order"):
        if key in translated_meta:
            assert translated_meta[key] == english_meta.get(key), f"{key} must stay the English file's"

    assert len(FENCE_RE.findall(translated_body)) == len(FENCE_RE.findall(english_body)), "code fences"
    english_spans = set(INLINE_CODE_RE.findall(_strip_fenced_blocks(english_body)))
    translated_spans = set(INLINE_CODE_RE.findall(_strip_fenced_blocks(translated_body)))
    assert not english_spans - translated_spans, f"inline code changed or lost: {sorted(english_spans - translated_spans)}"
    assert translated_body.count("{mod}") == english_body.count("{mod}")
    # It is a translation, not a copy.
    assert CJK_RE.search(translated_body) or not english_body.strip()


def test_the_english_topics_stay_english():
    for path in HELP_DIR.glob("*.md"):
        assert not CJK_RE.search(path.read_text(encoding="utf-8-sig")), path.name


def test_the_loader_skips_the_language_folders():
    assert set(load_documents()) == set(_english_topics())
