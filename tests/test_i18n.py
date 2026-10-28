"""The localisation infrastructure: catalogues keyed by the English text, a request-bound t().

English is the source language. ``app/i18n.py`` binds the request's locale
(the middleware from Accept-Language, the web routes from the member's saved
preference) and ``t()``/``tn()`` read it; ``base.html`` inlines the page's
catalogue for ``app/static/js/i18n.js``, which exposes the same helpers to the
browser; ``scripts/i18n_check.py`` keeps the catalogues and the source in step
and runs here so CI fails on a missing translation.
"""
from __future__ import annotations

import importlib.util
import io
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from _js_extract_helpers import I18N_STUB, _extract_js_function
from app import i18n
from test_language_preference import _app_page

REPO = Path(__file__).resolve().parents[1]
CHAT_JS = (REPO / "app" / "static" / "js" / "chat_ui.js").read_text(encoding="utf-8")
BASE_HTML = (REPO / "app" / "templates" / "base.html").read_text(encoding="utf-8")

PSEUDO = {
    "zh-Hans": {
        "Sign out": "[hans] Sign out",
        "Updated {time}": "[hans] Updated {time}",
        "one file|{n} files": "[hans] {n} file(s)",
        "Blank": "   ",
    },
    "zh-Hant-HK": {"Sign out": "[hk] Sign out"},
}


@pytest.fixture
def pseudo_catalogues(monkeypatch):
    monkeypatch.setattr(i18n, "_catalogues", {locale: dict(entries) for locale, entries in PSEUDO.items()})
    yield
    i18n.reload_catalogues()


# --- translate -----------------------------------------------------------------

def test_translate_falls_back_to_the_english_key_and_fills_placeholders(pseudo_catalogues):
    assert i18n.translate("zh-Hans", "Sign out") == "[hans] Sign out"
    assert i18n.translate("zh-Hant-HK", "Sign out") == "[hk] Sign out"
    assert i18n.translate("en", "Sign out") == "Sign out"
    assert i18n.translate("zh-Hans", "Not translated") == "Not translated"
    # A blank translation is as good as none.
    assert i18n.translate("zh-Hans", "Blank") == "Blank"
    assert i18n.translate("zh-Hans", "Updated {time}", time="10:00") == "[hans] Updated 10:00"
    # A placeholder nobody filled stays visible rather than raising.
    assert i18n.translate("en", "Updated {time}") == "Updated {time}"
    assert i18n.translate("fr", "Sign out") == "Sign out"


def test_plural_forms(pseudo_catalogues):
    assert i18n.translate_plural("en", 1, "one file", "{n} files") == "one file"
    assert i18n.translate_plural("en", 3, "one file", "{n} files") == "3 files"
    assert i18n.translate_plural("en", 0, "one file", "{n} files") == "0 files"
    # Chinese keeps one form for every count.
    assert i18n.translate_plural("zh-Hans", 1, "one file", "{n} files") == "[hans] 1 file(s)"
    assert i18n.translate_plural("zh-Hans", 3, "one file", "{n} files") == "[hans] 3 file(s)"
    assert i18n.translate_plural("zh-Hant-HK", 3, "one file", "{n} files") == "3 files"


def test_t_reads_the_bound_locale(pseudo_catalogues):
    assert i18n.t("Sign out") == "Sign out"
    token = i18n.bind_locale("zh-Hant-HK")
    try:
        assert i18n.current_locale() == "zh-Hant-HK"
        assert i18n.t("Sign out") == "[hk] Sign out"
        assert i18n.tn(2, "one file", "{n} files") == "2 files"
    finally:
        i18n.reset_locale(token)
    assert i18n.current_locale() == "en"
    # An unknown locale binds as English rather than failing the request.
    token = i18n.bind_locale("fr")
    try:
        assert i18n.current_locale() == "en"
    finally:
        i18n.reset_locale(token)


def test_marked_keys_are_translated_when_shown(pseudo_catalogues):
    # A table built at import time marks its strings with N_(), which changes
    # nothing; the code that shows them translates with tv() in the request.
    table = {"title": i18n.N_("Sign out"), "id": "logout"}
    assert table == {"title": "Sign out", "id": "logout"}
    token = i18n.bind_locale("zh-Hans")
    try:
        assert i18n.tv(table["title"]) == "[hans] Sign out"
        assert i18n.tv("Updated {time}", time="now") == "[hans] Updated now"
        # Ids, URLs and names are not keys: they pass through unchanged.
        assert i18n.tv(table["id"]) == "logout"
        assert i18n.tv(None) == ""
        assert i18n.tv(42) == "42"
    finally:
        i18n.reset_locale(token)
    assert i18n.tv(table["title"]) == "Sign out"


def test_bootstrap_json_is_safe_inside_a_script_element(monkeypatch):
    monkeypatch.setattr(i18n, "_catalogues", {"zh-Hans": {"</script>": "</script> [hans]", "Sign out": "[hans] Sign out"}})
    try:
        payload = i18n.i18n_bootstrap("zh-Hans")
        assert "</" not in payload
        assert json.loads(payload.replace("<\\/", "</")) == {
            "locale": "zh-Hans",
            "messages": {"</script>": "</script> [hans]", "Sign out": "[hans] Sign out"},
        }
        assert i18n.i18n_bootstrap("en") == '{"locale":"en","messages":{}}'
        assert i18n.i18n_bootstrap("fr") == '{"locale":"en","messages":{}}'
    finally:
        i18n.reload_catalogues()


# --- the pages ------------------------------------------------------------------

def test_pages_inline_the_catalogue_of_the_request_locale():
    from app.main import app

    client = TestClient(app)
    chinese = client.get("/login", headers={"Accept-Language": "zh-CN,zh;q=0.9"}).text
    assert 'window.EFP_I18N = {"locale":"zh-Hans","messages":' in chinese
    assert '<script src="/static/js/i18n.js?v=' in chinese
    english = client.get("/login").text
    assert 'window.EFP_I18N = {"locale":"en","messages":{}};' in english
    # The helpers load before dialogs.js, the first script that may call t().
    assert english.index("js/i18n.js") < english.index("js/dialogs.js")


def test_the_member_preference_binds_the_locale_for_the_page(monkeypatch):
    html = _app_page(monkeypatch, accept_language="en-US", preferences_json=json.dumps({"ui_locale": "zh-Hant-HK"}))
    assert '<html lang="zh-Hant-HK" data-locale="zh-Hant-HK"' in html
    assert 'window.EFP_I18N = {"locale":"zh-Hant-HK","messages":' in html


def test_base_html_renders_the_language_from_the_request():
    assert "{{ i18n_bootstrap(page_locale)|safe }}" in BASE_HTML
    assert "current_locale()" in BASE_HTML
    assert BASE_HTML.index("js/i18n.js") < BASE_HTML.index("{% block head_scripts %}")


# --- the check script ------------------------------------------------------------

def _load_check_script():
    spec = importlib.util.spec_from_file_location("i18n_check", REPO / "scripts" / "i18n_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_repository_passes_the_catalogue_check():
    check = _load_check_script()
    report = io.StringIO()
    code = check.run([], repo=REPO, out=report)
    assert code == 0, report.getvalue()


def test_the_check_script_reads_keys_and_reports_what_is_wrong(tmp_path):
    check = _load_check_script()
    templates = tmp_path / "app" / "templates"
    templates.mkdir(parents=True)
    (templates / "page.html").write_text(
        '<a title="{{ t(\'Open it\') }}">{{ t("Hello {name}", name=user) }}</a>\n'
        '{{ t(variable) }}\n'
        "{{ tn(count, 'one file', '{n} files') }}\n",
        encoding="utf-8",
    )
    js_dir = tmp_path / "app" / "static" / "js"
    js_dir.mkdir(parents=True)
    (js_dir / "ui.js").write_text(
        'showToast(t("Copied"));\n'
        "const label = t(`Template ${x}`);\n"
        'const count = tn(items.length, "one item", "{n} items", { extra: 1 });\n'
        '// a method called t() is not a key: obj.t("nope"); and neither is at("nope")\n',
        encoding="utf-8",
    )
    (tmp_path / "app" / "ui.py").write_text('label = t("Member")\nmessage = "中文"\n', encoding="utf-8")
    locale_dir = tmp_path / "app" / "locale"
    locale_dir.mkdir()
    (locale_dir / "zh-Hans.json").write_text(
        json.dumps({"Hello {name}": "Hi {nmae}", "Copied": "", "Gone": "x", "one item|{n} items": "{n} items"}),
        encoding="utf-8",
    )

    keys, errors = check.extract_keys(tmp_path)
    assert sorted(key.text for key in keys) == [
        "Copied",
        "Hello {name}",
        "Member",
        "Open it",
        "one file|{n} files",
        "one item|{n} items",
    ]
    assert [error.split(": ", 1)[1] for error in errors] == [
        "t() needs a plain string literal as its key",
        "t() needs a plain string literal as its key",
    ]

    report = io.StringIO()
    assert check.run([], repo=tmp_path, out=report) == 1
    text = report.getvalue()
    assert "ERROR zh-Hans: translation of 'Hello {name}' uses placeholders the key has not: ['nmae']" in text
    assert "ERROR zh-Hans: missing translation for 'Copied'" in text
    assert "ERROR zh-Hant-HK: missing translation for 'Member'" in text
    assert "WARN zh-Hans: orphan entry 'Gone'" in text
    assert "ERROR app/ui.py:2: Chinese characters belong in app/locale" in text
    # {n} is implied for a plural key, so the translated pair is not flagged.
    assert "translation of 'one item|{n} items' uses placeholders" not in text

    report = io.StringIO()
    check.run(["--fill"], repo=tmp_path, out=report)
    filled = json.loads((locale_dir / "zh-Hant-HK.json").read_text(encoding="utf-8"))
    assert filled == {
        "Copied": "",
        "Hello {name}": "",
        "Member": "",
        "one file|{n} files": "",
        "one item|{n} items": "",
        "Open it": "",
    }
    assert list(filled) == ["Copied", "Hello {name}", "Member", "one file|{n} files", "one item|{n} items", "Open it"]
    hans = json.loads((locale_dir / "zh-Hans.json").read_text(encoding="utf-8"))
    assert hans["Gone"] == "x" and hans["Member"] == ""


def test_the_check_script_decodes_escapes_like_the_page_does(tmp_path):
    check = _load_check_script()
    js_dir = tmp_path / "app" / "static" / "js"
    js_dir.mkdir(parents=True)
    (js_dir / "escapes.js").write_text(
        'a = t("Loading\\u2026");\nb = t("Size \\u00d7 2");\nc = t("Smile \\u{1F600}");\nd = t("Tab\\there");\ne = t("x\\x41y");\n',
        encoding="utf-8",
    )
    keys, errors = check.extract_keys(tmp_path)
    assert errors == []
    assert [key.text for key in keys] == ["Loading\u2026", "Size \u00d7 2", "Smile \U0001F600", "Tab\there", "xAy"]


def test_the_check_script_reads_marked_keys_and_leaves_tv_alone(tmp_path):
    check = _load_check_script()
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "tables.py").write_text(
        'LABELS = {"aws": N_("AWS accounts"), "id": "aws"}\n'
        'shown = tv(LABELS["aws"])\n'
        "bad = N_(name)\n",
        encoding="utf-8",
    )
    js_dir = tmp_path / "app" / "static" / "js"
    js_dir.mkdir(parents=True)
    (js_dir / "tables.js").write_text(
        'const GROUPS = { proxy: N_("Proxies") };\nnode.textContent = tv(GROUPS[kind]);\n',
        encoding="utf-8",
    )
    keys, errors = check.extract_keys(tmp_path)
    assert sorted(key.text for key in keys) == ["AWS accounts", "Proxies"]
    assert [error.split(": ", 1)[1] for error in errors] == ["N_() needs a plain string literal as its key"]


# --- API error details ----------------------------------------------------------------

def test_api_error_details_stay_english_and_are_catalogue_keys():
    # Raise sites mark the detail with N_(): the API answers in English
    # whatever the browser asks for, and the page translates what it shows.
    from test_agents_api import _build_agents_client_with_overrides

    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        response = client.get("/api/agents/missing-agent", headers={"Accept-Language": "zh-CN"})
    finally:
        cleanup()
    assert response.status_code == 404
    assert response.json() == {"detail": "Agent not found"}
    check = _load_check_script()
    keys = {key.text for key in check.extract_keys(REPO)[0]}
    assert {"Agent not found", "Forbidden", "Unauthorized", "Invalid credentials"} <= keys
    hans = json.loads((REPO / "app" / "locale" / "zh-Hans.json").read_text(encoding="utf-8"))
    assert hans["Agent not found"].strip()


def test_the_page_translates_a_known_error_detail():
    script = "\n".join(
        [
            # The page's tv(): a catalogue that knows one detail.
            "globalThis.tv = (k) => (k === 'Task not found' ? '[hans] Task not found' : String(k));",
            _extract_js_function(CHAT_JS, "getRuntimeMutationErrorMessage"),
            "console.log(JSON.stringify({",
            "  known: getRuntimeMutationErrorMessage({ status: 404 }, { detail: 'Task not found' }),",
            "  unknown: getRuntimeMutationErrorMessage({ status: 500 }, { error: 'runtime exploded' }),",
            "  fallback: getRuntimeMutationErrorMessage({ status: 500 }, {}, 'Failed'),",
            "}));",
        ]
    )
    assert _run_node(script) == {"known": "[hans] Task not found", "unknown": "runtime exploded", "fallback": "Failed"}


# --- the browser helpers -----------------------------------------------------------

def _node():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping the i18n.js test")
    return node_bin


def _run_node(script: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        script_path = Path(tmp) / "i18n_case.js"
        script_path.write_text(script, encoding="utf-8")
        result = subprocess.run([_node(), str(script_path)], check=False, capture_output=True, encoding="utf-8", errors="replace")
    assert result.returncode == 0, f"node failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return json.loads(result.stdout.strip())


def test_i18n_js_translates_from_the_inlined_catalogue():
    i18n_js = (REPO / "app" / "static" / "js" / "i18n.js").as_posix()
    out = _run_node(
        "global.window = { EFP_I18N: { locale: 'zh-Hans', messages: { 'Sign out': '[hans] Sign out', "
        "'Updated {time}': '[hans] Updated {time}', 'one file|{n} files': '[hans] {n} file(s)', 'Blank': '  ' } } };\n"
        f"require({json.dumps(i18n_js)});\n"
        "console.log(JSON.stringify({\n"
        "  locale: window.EFP_I18N.locale,\n"
        "  translated: t('Sign out'),\n"
        "  missing: t('Not translated'),\n"
        "  blank: t('Blank'),\n"
        "  params: t('Updated {time}', { time: '10:00' }),\n"
        "  unfilled: t('Updated {time}'),\n"
        "  pluralOne: tn(1, 'one file', '{n} files'),\n"
        "  pluralMany: tn(3, 'one file', '{n} files'),\n"
        "  english: window.EFP_I18N.interpolate('{n} rows', { n: 2 }),\n"
        "}));\n"
    )
    assert out == {
        "locale": "zh-Hans",
        "translated": "[hans] Sign out",
        "missing": "Not translated",
        "blank": "Blank",
        "params": "[hans] Updated 10:00",
        # Translated, with the placeholder nobody filled left visible.
        "unfilled": "[hans] Updated {time}",
        "pluralOne": "[hans] 1 file(s)",
        "pluralMany": "[hans] 3 file(s)",
        "english": "2 rows",
    }


def test_i18n_js_marks_keys_and_translates_values():
    i18n_js = (REPO / "app" / "static" / "js" / "i18n.js").as_posix()
    out = _run_node(
        "global.window = { EFP_I18N: { locale: 'zh-Hant-HK', messages: { 'Proxies': '[hk] Proxies', 'Updated {time}': '[hk] Updated {time}' } } };\n"
        f"require({json.dumps(i18n_js)});\n"
        "const GROUPS = { proxy: N_('Proxies'), raw: 'proxy-1' };\n"
        "console.log(JSON.stringify({\n"
        "  marked: GROUPS.proxy,\n"
        "  shown: tv(GROUPS.proxy),\n"
        "  params: tv('Updated {time}', { time: 'now' }),\n"
        "  passthrough: tv(GROUPS.raw),\n"
        "  nullish: tv(null),\n"
        "}));\n"
    )
    assert out == {
        "marked": "Proxies",
        "shown": "[hk] Proxies",
        "params": "[hk] Updated now",
        "passthrough": "proxy-1",
        "nullish": "",
    }


def test_i18n_js_without_a_catalogue_is_english():
    i18n_js = (REPO / "app" / "static" / "js" / "i18n.js").as_posix()
    out = _run_node(
        "global.window = {}; global.document = { documentElement: { lang: 'en' } };\n"
        f"require({json.dumps(i18n_js)});\n"
        "console.log(JSON.stringify({ locale: window.EFP_I18N.locale, one: tn(1, 'one file', '{n} files'), many: tn(2, 'one file', '{n} files'), text: t('Sign out') }));\n"
    )
    assert out == {"locale": "en", "one": "one file", "many": "2 files", "text": "Sign out"}


def test_extracted_functions_carry_an_idempotent_stub():
    extracted = _extract_js_function(CHAT_JS, "normalizeThemePreference")
    assert extracted.startswith(I18N_STUB)
    assert "function normalizeThemePreference(" in extracted
    # Two extractions in one bundle must not redeclare anything, and the
    # stand-in behaves like the page's English t().
    out = _run_node(
        "\n".join(
            [
                "const THEME_ORDER = ['system', 'light', 'dark'];",
                extracted,
                _extract_js_function(CHAT_JS, "localeChangeNeedsReload"),
                "console.log(JSON.stringify({ theme: normalizeThemePreference('dark'), text: t('Updated {time}', { time: 'now' }), plural: tn(2, 'one file', '{n} files'), marked: tv(N_('Proxies')), nullish: tv(undefined) }));",
            ]
        )
    )
    assert out == {"theme": "dark", "text": "Updated now", "plural": "2 files", "marked": "Proxies", "nullish": ""}
