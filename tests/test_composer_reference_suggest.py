"""References in the composer: "#" offers recent issues, pull requests and pages.

The list is an offer the member did not ask for by name, so it must never get
between them and the Send key: "fix #12" followed by Enter sends the message.
"""
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from _js_extract_helpers import I18N_STUB, _extract_js_function

CHAT_UI = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
PICKER = Path("app/static/js/card_picker.js").read_text(encoding="utf-8")


def _const(name: str) -> str:
    match = re.search(rf"^const {name} = .*;$", CHAT_UI, flags=re.MULTILINE)
    assert match, name
    return match.group(0)


def _node(script: str):
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping composer reference tests")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "case.js"
        path.write_text(script, encoding="utf-8")
        result = subprocess.run([node_bin, str(path)], check=False, text=True, capture_output=True, encoding="utf-8")
    if result.returncode != 0:
        raise AssertionError(f"node failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_a_hash_at_the_start_of_a_word_is_a_reference_and_nothing_else_is():
    out = _node(
        f"""
{_const("COMPOSER_REFERENCE_PATTERN")}
{_extract_js_function(CHAT_UI, "composerReferenceToken")}
const at = (text) => composerReferenceToken(text, text.length);
console.log(JSON.stringify({{
  bare: at("#"),
  typed: at("please review #retry"),
  key: at("see #ABC-12"),
  midWord: at("issue#12"),
  heading: at("# Title"),
  finished: at("see #ABC-12 and"),
  doubled: at("##"),
  cursor: composerReferenceToken("#abc def", 4),
  none: at("no reference here"),
}}));
"""
    )

    assert out["bare"] == {"start": 0, "query": ""}
    assert out["typed"] == {"start": 14, "query": "retry"}
    assert out["key"] == {"start": 4, "query": "ABC-12"}
    # Not at the start of a word, a Markdown heading, or already finished.
    assert out["midWord"] is None and out["heading"] is None and out["finished"] is None
    assert out["doubled"] is None and out["none"] is None
    # Only the text before the caret counts.
    assert out["cursor"] == {"start": 0, "query": "abc"}


def test_items_come_from_every_source_that_answered_a_few_from_each():
    out = _node(
        f"""
{I18N_STUB}
{PICKER}
const picker = module.exports;
{_const("COMPOSER_REFERENCE_PER_SOURCE")}
{_extract_js_function(CHAT_UI, "composerReferenceItems")}
const issues = {{ state: "ok", groups: [{{ id: "viewed", items: Array.from({{ length: 8 }}, (_, i) => ({{ value: `ABC-${{i}}`, title: `ABC-${{i}}`, subtitle: `Summary ${{i}}`, badge: "To Do" }})) }}] }};
const pulls = {{ state: "ok", groups: [{{ id: "mine", items: [{{ value: "https://github.com/a/b/pull/7", title: "Add retry", subtitle: "a/b#7" }}] }}] }};
const off = {{ state: "not_configured", groups: [] }};
const recent = composerReferenceItems([issues, pulls, off, null], "", picker);
const narrowed = composerReferenceItems([issues, pulls], "7", picker);
const searched = composerReferenceItems([pulls], "retry on timeout", picker);
console.log(JSON.stringify({{ recent, narrowed: narrowed.map((item) => item.value), searched: searched.map((item) => item.value) }}));
"""
    )

    assert [item["value"] for item in out["recent"]] == ["ABC-0", "ABC-1", "ABC-2", "ABC-3", "ABC-4", "https://github.com/a/b/pull/7"]
    assert out["recent"][0] == {"label": "ABC-0", "desc": "Summary 0 - To Do", "title": "ABC-0", "value": "ABC-0"}
    # One character narrows the recent lists here; from two on, the server did.
    assert out["narrowed"] == ["ABC-7", "https://github.com/a/b/pull/7"]
    assert out["searched"] == ["https://github.com/a/b/pull/7"]


LIST_SHIM = """
const buttons = [];
const state = { selectedSuggestionIndex: -1, suggestRequestSeq: 0 };
const picked = [];
const safe = (value) => String(value);
const escapeHtmlAttr = (value) => String(value);
function makeButton(index) {
  const classes = new Set();
  return {
    dataset: { i: String(index) }, _click: null,
    classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c) },
    addEventListener(type, fn) { this._click = fn; },
    click() { this._click(); },
    scrollIntoView() {},
    active: () => classes.has("is-active"),
  };
}
const hidden = new Set(["hidden"]);
const dom = {
  chatSuggest: {
    _html: "",
    set innerHTML(value) { this._html = value; buttons.length = 0; const n = (value.match(/<button/g) || []).length; for (let i = 0; i < n; i += 1) buttons.push(makeButton(i)); },
    get innerHTML() { return this._html; },
    classList: { add: (c) => hidden.add(c), remove: (c) => hidden.delete(c), contains: (c) => hidden.has(c) },
    querySelectorAll: () => buttons,
  },
};
const ITEMS = [{ label: "ABC-1", value: "ABC-1" }, { label: "ABC-2", value: "ABC-2" }, { label: "ABC-3", value: "ABC-3" }];
const active = () => buttons.findIndex((button) => button.active());
"""


def _list(body: str):
    functions = "\n".join(
        _extract_js_function(CHAT_UI, name)
        for name in ("hideSuggest", "showSuggest", "moveSuggestionSelection", "pickCurrentSuggestion")
    )
    return _node(f"{I18N_STUB}{LIST_SHIM}\n{functions}\n{body}")


def test_an_offered_list_leaves_enter_to_send_until_an_arrow_key_enters_it():
    out = _list(
        """
showSuggest(ITEMS, (item) => picked.push(item.value), { preselect: false });
const opened = { index: state.selectedSuggestionIndex, active: active(), enterPicks: pickCurrentSuggestion() };
moveSuggestionSelection(1);
const down = active();
moveSuggestionSelection(1);
const picks = pickCurrentSuggestion();
showSuggest(ITEMS, (item) => picked.push(item.value), { preselect: false });
moveSuggestionSelection(-1);
console.log(JSON.stringify({ opened, down, picks, picked, upFromNothing: active() }));
"""
    )

    # Enter with nothing highlighted is not a pick, so the composer sends.
    assert out["opened"] == {"index": -1, "active": -1, "enterPicks": False}
    assert out["down"] == 0
    assert out["picks"] is True and out["picked"] == ["ABC-2"]
    assert out["upFromNothing"] == 2


def test_a_list_the_member_asked_for_still_opens_on_its_first_row():
    out = _list(
        """
showSuggest(ITEMS, (item) => picked.push(item.value));
const opened = { index: state.selectedSuggestionIndex, active: active() };
const picks = pickCurrentSuggestion();
showSuggest(ITEMS, (item) => picked.push(item.value));
moveSuggestionSelection(-1);
console.log(JSON.stringify({ opened, picks, picked, wrapped: active() }));
"""
    )

    assert out["opened"] == {"index": 0, "active": 0}
    assert out["picks"] is True and out["picked"] == ["ABC-1"]
    assert out["wrapped"] == 2


def test_the_reference_list_is_opened_without_a_preselected_row():
    body = _extract_js_function(CHAT_UI, "maybeShowSuggest")
    assert "{ preselect: false }" in body
    assert "composerReferenceToken(text, cursor)" in body
    # The slash list is unchanged: it is asked for by name and opens selected.
    assert body.count("showSuggest(") == 2
