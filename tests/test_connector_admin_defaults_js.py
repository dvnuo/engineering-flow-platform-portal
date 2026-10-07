"""The administrator-defaults dialog's pure helpers in chat_ui.js, run under node."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._js_extract_helpers import _extract_js_function

CHAT_UI = Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "chat_ui.js"


def _const_block(source: str, name: str) -> str:
    match = re.search(rf"^const {name} = .*?;$", source, re.MULTILINE)
    assert match, name
    return match.group(0)


def _run(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    source = CHAT_UI.read_text(encoding="utf-8")
    prelude = "\n".join(
        [
            # chat_ui.js defines escapeHtml twice and the later, string-based
            # one wins in the browser; _extract_js_function would find the DOM
            # one, so the test supplies the same quote-escaping stub.
            "function escapeHtml(str) { if (str == null) return ''; return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;').replace(/'/g, '&#039;'); }",
            _const_block(source, "ADMIN_DEFAULTS_CHOICE_LABELS"),
            _const_block(source, "ADMIN_DEFAULTS_CHOICES"),
            _extract_js_function(source, "adminDefaultsConflicts"),
            _extract_js_function(source, "defaultAdminDefaultsDecisions"),
            _extract_js_function(source, "renderAdminDefaultsConflictHtml"),
            _extract_js_function(source, "renderAdminDefaultsPreviewHtml"),
            _extract_js_function(source, "collectAdminDefaultsDecisions"),
        ]
    )
    result = subprocess.run([node, "-e", prelude + "\n" + script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


PREVIEW = {
    "connector": {"type": "jira", "label": "Jira"},
    "available": True,
    "up_to_date": False,
    "fingerprint": "abc",
    "additions": [{"id": "jira.instances:url=https://sb", "label": "Jira instance Sandbox", "detail": "New: URL https://sb"}],
    "conflicts": [
        {
            "id": 'jira.instances:url=https://x/"]<weird>',
            "kind": "item",
            "label": "Jira instance <img src=x onerror=alert(1)>",
            "fields": [
                {"label": "Name", "mine": "Mine & co", "theirs": "<script>alert(1)</script>", "secret": False},
                {"label": "API token", "mine": "Set", "theirs": "Set", "secret": True},
            ],
            "options": ["admin", "mine", "both"],
        },
        {
            "id": "__proto__",
            "kind": "field",
            "label": "Jira thing",
            "fields": [{"label": "Thing", "mine": "a", "theirs": "b", "secret": False}],
            "options": ["admin", "mine", "nonsense"],
        },
    ],
}


def test_default_decisions_prefer_keeping_both_then_mine_and_survive_odd_ids():
    out = _run(
        f"const preview = {json.dumps(PREVIEW)};"
        "const d = defaultAdminDefaultsDecisions(preview);"
        "console.log(JSON.stringify({ decisions: d, proto: d['__proto__'], empty: defaultAdminDefaultsDecisions(null) }));"
    )
    assert out == {
        "decisions": {'jira.instances:url=https://x/"]<weird>': "both", "__proto__": "mine"},
        "proto": "mine",
        "empty": {},
    }


def test_preview_html_escapes_values_keys_radios_by_index_and_checks_the_default_choice():
    out = _run(
        f"const preview = {json.dumps(PREVIEW)};"
        "console.log(JSON.stringify({ html: renderAdminDefaultsPreviewHtml(preview), none: renderAdminDefaultsPreviewHtml({}) }));"
    )
    html = out["html"]
    assert out["none"] == ""
    assert "<script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<img" not in html and "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "Mine &amp; co" in html
    # Ids never reach the markup; radios are keyed by the conflict's index.
    assert "<weird>" not in html and 'url=https://x' not in html
    assert 'name="admin-defaults-conflict-0" value="both" data-defaults-index="0" checked' in html
    assert 'name="admin-defaults-conflict-1" value="mine" data-defaults-index="1" checked' in html
    assert 'value="nonsense"' not in html
    assert "Will be added" in html and "Jira instance Sandbox" in html and "New: URL https://sb" in html
    assert "Differs from yours" in html
    assert html.count('data-defaults-set-all="') == 3
    assert 'aria-label="Set all to Keep both"' in html
    assert "Use the administrator&#039;s" in html and "Keep mine" in html and "Keep both" in html
    assert "Administrator&#039;s" in html and 'role="radiogroup"' not in html


def test_set_all_shortcuts_only_appear_with_more_than_one_clash():
    single = dict(PREVIEW, conflicts=PREVIEW["conflicts"][:1])
    out = _run(
        f"const preview = {json.dumps(single)};"
        "console.log(JSON.stringify({ html: renderAdminDefaultsPreviewHtml(preview) }));"
    )
    assert "data-defaults-set-all" not in out["html"]


def test_collect_reads_the_checked_radio_per_index_and_ignores_unknown_values():
    out = _run(
        f"const preview = {json.dumps(PREVIEW)};"
        "const root = { querySelectorAll() { return ["
        "  { dataset: { defaultsIndex: '0' }, value: 'admin' },"
        "  { dataset: { defaultsIndex: '1' }, value: 'nonsense' },"
        "  { dataset: { defaultsIndex: '7' }, value: 'admin' },"
        "]; } };"
        "console.log(JSON.stringify(collectAdminDefaultsDecisions(root, preview)));"
    )
    assert out == {'jira.instances:url=https://x/"]<weird>': "admin", "__proto__": "mine"}
