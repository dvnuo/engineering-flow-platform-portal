"""The starter-card picker: its pure parts, and the click that uses it.

card_picker.js exports the logic that decides what a card sends -- the prompt
template, which rows match what was typed, what is remembered -- so it can be
run under node without a page. The dialogs themselves are checked in a
browser; what is pinned here is everything that could send the wrong prompt.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from _js_extract_helpers import I18N_STUB

PICKER = Path("app/static/js/card_picker.js")
PERSONALIZATION = Path("app/static/js/assistant_personalization.js")


def _node(script: str):
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping card picker tests")
    # A file, not `node -e`: the bundle is longer than a Windows command line.
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "case.js"
        path.write_text(script, encoding="utf-8")
        result = subprocess.run([node_bin, str(path)], check=False, text=True, capture_output=True, encoding="utf-8")
    if result.returncode != 0:
        raise AssertionError(f"node failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def _picker(body: str):
    """Run ``body`` with the module's exports as ``api``; it must set ``out``."""
    return _node(
        f"""
{I18N_STUB}
{PICKER.read_text(encoding="utf-8")}
const api = module.exports;
let out = null;
{body}
console.log(JSON.stringify(out));
"""
    )


# ------------------------------------------------------------ prompt template


def test_a_template_takes_each_fields_value_and_drops_the_sentence_of_an_empty_one():
    out = _picker(
        """
const template = "Troubleshoot {{service}} in {{ environment }}.\\nSymptom: {{symptom}}\\n\\n\\n\\nStay read-only.";
out = {
  full: api.composeTemplate(template, { service: "payments-api", environment: "cps-dev", symptom: "pods restart" }),
  optional: api.composeTemplate(template, { service: "payments-api", environment: "cps-dev", symptom: "  " }),
  twice: api.composeTemplate("Link {{a}} and close {{a}}.", { a: "EFP-9" }),
  mixed: api.composeTemplate("Use {{a}} with {{b}}.", { a: "x", b: "" }),
  unknown: api.composeTemplate("Keep {{Not_A_Field}} as is.", {}),
};
"""
    )

    assert out["full"] == "Troubleshoot payments-api in cps-dev.\nSymptom: pods restart\n\nStay read-only."
    # The line that was only about the symptom goes with it.
    assert out["optional"] == "Troubleshoot payments-api in cps-dev.\n\nStay read-only."
    assert out["twice"] == "Link EFP-9 and close EFP-9."
    # A line with one value filled stays; only an all-empty line is dropped.
    assert out["mixed"] == "Use x with ."
    assert out["unknown"] == "Keep {{Not_A_Field}} as is."


def test_source_parameters_can_read_another_field_and_know_which_ones_they_wait_for():
    out = _picker(
        """
const params = { repo: "{{repo}}", types: ["Epic", " ", "{{kind}}"], flag: true, off: false, none: null };
out = {
  filled: api.resolveParams(params, { repo: "acme/portal", kind: "Story" }),
  empty: api.resolveParams(params, {}),
  depends: api.paramDependencies(params).sort(),
  nothing: api.paramDependencies({ types: ["Epic"] }),
};
"""
    )

    assert out["filled"] == {"repo": ["acme/portal"], "types": ["Epic", "Story"], "flag": ["true"]}
    # An unanswered field leaves no empty parameter behind.
    assert out["empty"] == {"types": ["Epic"], "flag": ["true"]}
    assert out["depends"] == ["kind", "repo"]
    assert out["nothing"] == []


# --------------------------------------------------------------- list content


def test_typing_narrows_the_rows_and_empty_groups_disappear():
    out = _picker(
        """
const groups = [
  { id: "viewed", items: [{ value: "ABC-1", title: "ABC-1", subtitle: "Login page redesign" }, { value: "ABC-2", title: "ABC-2", subtitle: "Refund flow" }] },
  { id: "assigned", items: [{ value: "PAY-7", title: "PAY-7", subtitle: "Retry on timeout", badge: "In Review" }] },
];
const shape = (list) => list.map((group) => [group.id, group.items.map((item) => item.value)]);
out = {
  all: shape(api.filterGroups(groups, "")),
  word: shape(api.filterGroups(groups, "REFUND")),
  both: shape(api.filterGroups(groups, "login abc")),
  badge: shape(api.filterGroups(groups, "review")),
  none: shape(api.filterGroups(groups, "zzz")),
  flat: api.flatItems(groups).map((item) => item.value),
};
"""
    )

    assert out["all"] == [["viewed", ["ABC-1", "ABC-2"]], ["assigned", ["PAY-7"]]]
    assert out["word"] == [["viewed", ["ABC-2"]]]
    assert out["both"] == [["viewed", ["ABC-1"]]]
    assert out["badge"] == [["assigned", ["PAY-7"]]]
    assert out["none"] == []
    assert out["flat"] == ["ABC-1", "ABC-2", "PAY-7"]


def test_used_before_leaves_out_what_the_live_list_already_shows():
    out = _picker(
        """
const groups = [{ id: "viewed", items: [{ value: "ABC-1", title: "ABC-1" }] }];
out = {
  merged: api.historyGroup(["ABC-1", "ABC-9", ""], groups),
  none: api.historyGroup(["ABC-1"], groups),
  alone: api.historyGroup(["payments-api in cps-dev"], []),
};
"""
    )

    assert out["merged"] == {"id": "history", "items": [{"value": "ABC-9", "title": "ABC-9"}]}
    assert out["none"] is None
    assert out["alone"]["items"][0]["value"] == "payments-api in cps-dev"


def test_the_request_names_the_source_and_carries_parameters_as_p_keys():
    out = _picker(
        """
out = {
  plain: api.suggestionUrl("jira.issues", {}),
  full: api.suggestionUrl("github.paths", { q: "client py", agentId: "a 1", params: { repo: ["acme/portal"], types: ["Epic", "Story"] }, recent: ["https://ci.corp/job/app/4/"] }),
};
"""
    )

    assert out["plain"] == "/api/suggestions/jira.issues"
    assert out["full"] == (
        "/api/suggestions/github.paths?q=client+py&agent_id=a+1&p.repo=acme%2Fportal"
        "&p.types=Epic&p.types=Story&recent=https%3A%2F%2Fci.corp%2Fjob%2Fapp%2F4%2F"
    )


# -------------------------------------------------------------------- history


def test_history_keeps_the_last_five_values_per_field_newest_first():
    out = _picker(
        """
const store = { data: {}, getItem(key) { return this.data[key] || null; }, setItem(key, value) { this.data[key] = value; } };
const history = api.createHistory(store);
["a", "b", "c", "d", "e", "f", "b", "  ", "x".repeat(501)].forEach((value) => history.add("Card::input", value));
history.add("", "ignored");
const broken = api.createHistory({ getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } });
broken.add("Card::input", "a");
const garbage = api.createHistory({ getItem() { return "[1,2"; }, setItem() {} });
out = {
  values: history.get("Card::input"),
  other: history.get("Other::input"),
  keys: Object.keys(JSON.parse(store.data["efp.cards.recent:v1"])),
  broken: broken.get("Card::input"),
  garbage: garbage.get("Card::input"),
  none: api.createHistory(null).get("Card::input"),
  key: api.historyKeyFor({ title: " Open a pull request " }, "branch"),
};
"""
    )

    assert out["values"] == ["b", "f", "e", "d", "c"]
    assert out["other"] == []
    assert out["keys"] == ["Card::input"]
    # A browser that refuses storage just has no history.
    assert out["broken"] == [] and out["garbage"] == [] and out["none"] == []
    assert out["key"] == "Open a pull request::branch"


def test_history_forgets_the_fields_used_longest_ago():
    out = _picker(
        """
const store = { data: {}, getItem(key) { return this.data[key] || null; }, setItem(key, value) { this.data[key] = value; } };
const history = api.createHistory(store);
let clock = 1000;
Date.now = () => (clock += 10);
for (let index = 0; index < 65; index += 1) history.add(`Card ${index}::input`, "v");
const kept = Object.keys(JSON.parse(store.data["efp.cards.recent:v1"]));
out = { count: kept.length, oldest: kept.includes("Card 0::input"), newest: kept.includes("Card 64::input") };
"""
    )

    assert out == {"count": 60, "oldest": False, "newest": True}


# --------------------------------------------------------------------- words


def test_states_and_groups_have_words_and_a_missing_list_says_what_to_do():
    out = _picker(
        """
out = {
  ahead: api.statusLabel({ status: "ahead", count: 1 }),
  aheadMany: api.statusLabel({ status: "ahead", count: 4 }),
  failed: api.statusLabel({ status: "failed", count: 3 }),
  failedPlain: api.statusLabel({ status: "failed" }),
  scenarios: api.statusLabel({ status: "scenarios", count: 3 }),
  open: api.statusLabel({ status: "open" }),
  unknown: api.statusLabel({ status: "something_new" }),
  none: api.statusLabel({}),
  group: api.groupLabel("no_pr"),
  groupUnknown: api.groupLabel("nope"),
  off: api.stateNote({ state: "not_configured", connector: "github" }),
  refused: api.stateNote({ state: "unavailable", connector: "jira", reason: "unauthorized" }),
  slow: api.stateNote({ state: "unavailable", connector: "jira", reason: "timeout" }),
  down: api.stateNote({ state: "unavailable", connector: "confluence", reason: "unreachable" }),
  ok: api.stateNote({ state: "ok" }),
};
"""
    )

    assert out["ahead"] == "1 commit ahead" and out["aheadMany"] == "4 commits ahead"
    assert out["failed"] == "3 failed" and out["failedPlain"] == "Failed"
    assert out["scenarios"] == "3 scenarios"
    assert out["open"] == "Open" and out["unknown"] == "something_new" and out["none"] == ""
    assert out["group"] == "Your recent branches, no pull request yet" and out["groupUnknown"] == ""
    assert out["off"] == "Set up GitHub in Connectors to pick from a list here."
    assert out["refused"] == "Jira refused the connector's credentials. Type or paste the value instead."
    assert out["slow"] == "The list took too long to load. Type or paste the value instead."
    assert out["down"] == "Could not reach Confluence. Type or paste the value instead."
    assert out["ok"] == ""


def test_times_are_shown_relative_to_now():
    out = _picker(
        """
const now = Date.parse("2026-11-12T12:00:00Z");
out = {
  hours: api.relativeTime("2026-11-12T10:00:00Z", now),
  days: api.relativeTime("2026-11-09T12:00:00Z", now),
  jira: api.relativeTime("2026-11-12T08:00:00.000+0000", now),
  now: api.relativeTime("2026-11-12T11:59:50Z", now),
  bad: api.relativeTime("not a date", now),
  empty: api.relativeTime("", now),
};
"""
    )

    assert out["hours"] == "2 hours ago" and out["days"] == "3 days ago"
    assert out["jira"] == "4 hours ago"
    assert out["now"] == "this minute"
    assert out["bad"] == "" and out["empty"] == ""


def test_the_dialogs_do_not_close_on_a_click_outside_them():
    # A picker holds what the member typed; Cancel and Escape are the ways out.
    source = PICKER.read_text(encoding="utf-8")
    assert "event.target === shell.root" not in source
    assert 'shell.setEscape(() => finish(null));' in source
    assert 'shell.cancelEl.addEventListener("click", () => finish(null));' in source


def test_a_picked_row_fills_another_field_only_while_that_field_is_not_the_members_own():
    out = _picker(
        """
out = {
  empty: api.mayFill({ touched: false, current: "", filled: "" }, "master"),
  // Filled by an earlier pick: a branch of another repository brings its own default.
  refilled: api.mayFill({ touched: false, current: "master", filled: "master" }, "develop"),
  typed: api.mayFill({ touched: true, current: "release/1.2", filled: "master" }, "develop"),
  // A default the card came with was never filled by a pick.
  cardDefault: api.mayFill({ touched: false, current: "cps-dev", filled: "" }, "cps-uat"),
  nothingOffered: api.mayFill({ touched: false, current: "", filled: "" }, ""),
};
"""
    )
    assert out == {"empty": True, "refilled": True, "typed": False, "cardDefault": False, "nothingOffered": False}
    # The form applies exactly this rule, and remembers what it filled.
    source = PICKER.read_text(encoding="utf-8")
    assert "if (!mayFill({ touched: control.touched, current: control.input.value, filled: control.filled }, offered)) return;" in source
    assert "control.filled = String(offered);" in source


def _css_rule(selector: str) -> str:
    sheet = Path("app/static/css/app.css").read_text(encoding="utf-8")
    start = sheet.index(f"\n{selector} {{") + 1
    return " ".join(sheet[start : sheet.index("}", start)].split())


def test_long_rows_are_cut_short_instead_of_scrolling_the_list_or_the_form_sideways():
    # A form is a grid. Left to size its one column, the grid makes it as
    # wide as the longest issue summary in a list and the dialog scrolls
    # sideways; the column is pinned to the dialog's width instead.
    form = _css_rule(".portal-picker-form")
    assert "grid-template-columns: minmax(0, 1fr);" in form
    assert "overflow-x: hidden;" in form
    assert "min-width: 0;" in _css_rule(".portal-picker-field")
    assert "overflow-x: hidden;" in _css_rule(".portal-picker-list")
    assert "min-width: 0;" in _css_rule(".portal-picker-row-main")
    # Text wraps to a second line and is then cut; text without spaces (a
    # pasted link) breaks anywhere rather than pushing the row wider.
    text = _css_rule(".portal-picker-row-title,\n.portal-picker-row-sub")
    assert "-webkit-line-clamp: 2;" in text and "overflow-wrap: anywhere;" in text and "nowrap" not in text
    # The full text is one hover away.
    source = PICKER.read_text(encoding="utf-8")
    assert 'class="portal-picker-row-title" title="${esc(item.title || item.value)}"' in source
    assert 'class="portal-picker-row-sub" title="${esc(item.subtitle)}"' in source


def test_the_picker_script_loads_before_the_scripts_that_use_it():
    page = Path("app/templates/app.html").read_text(encoding="utf-8")
    picker = page.index("js/card_picker.js")
    assert picker < page.index("js/interactive_input.js")
    assert picker < page.index("js/assistant_personalization.js")


# ------------------------------------------------- the click that uses it all

DOM_SHIM = """
const calls = { submits: 0, fills: [], pickers: [], forms: [], uploads: [], fetches: [], prefetch: [], order: [] };
const handlers = {};

function element(extra) {
  return Object.assign({
    value: "", className: "", innerHTML: "",
    focus: () => {}, setSelectionRange: () => {}, dispatchEvent: () => true,
    addEventListener: (type, fn) => { (handlers[type] = handlers[type] || []).push(fn); },
    querySelector: () => null, append: () => {}, remove: () => {},
  }, extra || {});
}

const input = element({});
Object.defineProperty(input, "value", {
  get() { return this._text || ""; },
  set(next) { this._text = next; calls.fills.push(next); calls.order.push("fill"); },
});
const form = element({ requestSubmit: () => { calls.submits += 1; calls.order.push("submit"); } });
const list = element({});
const elements = { "chat-input": input, "chat-form": form, "message-list": list };

const documentHandlers = {};
globalThis.document = {
  readyState: "complete",
  getElementById: (id) => elements[id] || null,
  createElement: () => element({}),
  addEventListener: (type, fn) => { (documentHandlers[type] = documentHandlers[type] || []).push(fn); },
};
const fireDocument = (type, detail) => (documentHandlers[type] || []).forEach((fn) => fn({ detail }));
const cardButton = (index) => {
  const button = { dataset: { starterCard: String(index) } };
  button.closest = (selector) => (selector === "[data-starter-card]" ? button : null);
  return button;
};
const fire = (type, index) => (handlers[type] || []).reduce(
  (chain, fn) => chain.then(() => fn({ target: cardButton(index) })),
  Promise.resolve()
);
const settle = () => new Promise((resolve) => setImmediate(resolve));
"""


def _click(cards: list[dict], *, setup: str = "", after_select: str = "", index: int = 0, event: str = "click"):
    script = f"""
{I18N_STUB}
{DOM_SHIM}
globalThis.window = {{}};
{setup}
globalThis.fetch = (url) => {{
  calls.fetches.push(String(url));
  return Promise.resolve({{ ok: true, json: () => Promise.resolve({{ welcome: null, cards: {json.dumps(cards)} }}) }});
}};

{PERSONALIZATION.read_text(encoding="utf-8")}

fireDocument("portal:agent-selected", {{ agentId: "a1" }});
settle()
  .then(() => {{ {after_select} }})
  .then(() => fire("{event}", {index}))
  .then(settle)
  .then(() => console.log(JSON.stringify(calls)))
  .catch((error) => {{ console.error(error); process.exit(1); }});
"""
    return _node(script)


PICKER_STUB = """
window.portalCardPicker = {
  historyKeyFor: (card, name) => `${card.title}::${name}`,
  prefetchCard: (card, agentId) => { calls.prefetch.push([card.title, agentId]); },
};
window.showPicker = (options) => { calls.pickers.push(options); return Promise.resolve(ANSWER); };
"""

SOURCE_CARD = {
    "title": "Summarize a requirement",
    "prompt": "Summarize the requirement in {{input}}.",
    "input": {"label": "Jira epic", "placeholder": "ABC-1000", "source": "jira.issues", "params": {"types": ["Epic"]}},
}


def test_a_card_with_a_source_opens_the_picker_and_sends_the_picked_value():
    result = _click([SOURCE_CARD], setup='const ANSWER = { value: "ABC-1000", item: { value: "ABC-1000", title: "ABC-1000", subtitle: "Do not send me" } };' + PICKER_STUB)

    assert result["pickers"] == [
        {
            "title": "Summarize a requirement",
            "label": "Jira epic",
            "placeholder": "ABC-1000",
            "source": "jira.issues",
            "params": {"types": ["Epic"]},
            "multiline": False,
            "agentId": "a1",
            "historyKey": "Summarize a requirement::input",
        }
    ]
    # The identifier goes into the prompt; the row's title and summary do not.
    assert result["fills"] == ["Summarize the requirement in ABC-1000."]
    assert result["submits"] == 1
    # v=2 asks the runtime for cards with several fields as well.
    assert result["fetches"] == ["/a/a1/api/personalization?v=2"]


def test_cancelling_the_picker_sends_nothing():
    result = _click([SOURCE_CARD], setup="const ANSWER = null;" + PICKER_STUB)

    assert len(result["pickers"]) == 1
    assert result["fills"] == [] and result["submits"] == 0


def test_a_plain_input_card_uses_the_picker_too_for_its_history_and_long_text():
    card = {"title": "Draw a diagram", "prompt": "Draw {{input}} as a Mermaid diagram.", "input": {"label": "What to draw", "multiline": True}}
    result = _click([card], setup='const ANSWER = { value: "the release process", item: null };' + PICKER_STUB)

    assert result["pickers"][0]["source"] == "" and result["pickers"][0]["multiline"] is True
    assert result["fills"] == ["Draw the release process as a Mermaid diagram."]


FORM_CARD = {
    "title": "Bulk create Jira issues",
    "prompt": "I want to bulk create Jira issues in {{input}} from a CSV.",
    "input": {"label": "Jira project key", "source": "jira.projects"},
    "fields": [{"name": "project", "label": "Jira project", "source": "jira.projects"}, {"name": "csv", "type": "file", "required": False}],
    "template": "Bulk create in {{project}}.\nThe CSV is attached: {{csv}}",
}

FORM_STUB = """
window.showCardForm = (card, options) => { calls.forms.push([card.title, options]); return Promise.resolve(FORM_ANSWER); };
window.addPendingFilesAndUpload = (files) => { calls.uploads.push(files.map((file) => file.name)); calls.order.push("upload"); return Promise.resolve(); };
"""


def test_a_card_with_fields_opens_the_form_and_attaches_its_file_before_sending():
    setup = 'const FORM_ANSWER = { prompt: "Bulk create in ABC.\\nThe CSV is attached: issues.csv", values: {}, files: [{ name: "issues.csv" }] };' + FORM_STUB
    result = _click([FORM_CARD], setup=setup + "const ANSWER = null;" + PICKER_STUB)

    assert result["forms"] == [["Bulk create Jira issues", {"agentId": "a1"}]]
    assert result["pickers"] == []
    assert result["uploads"] == [["issues.csv"]]
    assert result["fills"] == ["Bulk create in ABC.\nThe CSV is attached: issues.csv"]
    # The file is in the composer before the message goes.
    assert result["order"] == ["upload", "fill", "submit"]


def test_a_card_with_fields_falls_back_to_its_single_input_where_there_is_no_form():
    result = _click([FORM_CARD], setup='const ANSWER = { value: "ABC", item: null };' + PICKER_STUB)

    assert result["forms"] == [] and len(result["pickers"]) == 1
    assert result["fills"] == ["I want to bulk create Jira issues in ABC from a CSV."]


def test_a_card_that_only_has_fields_does_nothing_where_there_is_no_form():
    card = {"title": "Fields only", "prompt": "", "fields": [{"name": "a"}], "template": "Do {{a}}."}
    result = _click([card], setup="const ANSWER = null;" + PICKER_STUB)

    assert result["pickers"] == [] and result["fills"] == [] and result["submits"] == 0


def test_cancelling_the_form_sends_nothing():
    result = _click([FORM_CARD], setup="const FORM_ANSWER = null;" + FORM_STUB)

    assert result["uploads"] == [] and result["fills"] == [] and result["submits"] == 0


def test_an_answer_given_after_switching_assistants_is_not_sent_to_the_new_one():
    setup = """
window.portalCardPicker = { historyKeyFor: () => "k", prefetchCard: () => {} };
window.showPicker = (options) => {
  calls.pickers.push(options.title);
  // The member picks another assistant while the dialog is still open.
  fireDocument("portal:agent-selected", { agentId: "a2" });
  return Promise.resolve({ value: "ABC-1", item: null });
};
"""
    result = _click([SOURCE_CARD], setup=setup)

    assert result["pickers"] == ["Summarize a requirement"]
    assert result["fills"] == [] and result["submits"] == 0


def test_hovering_a_card_warms_its_list_and_sends_nothing():
    result = _click([SOURCE_CARD], setup="const ANSWER = null;" + PICKER_STUB, event="mouseover")

    assert result["prefetch"] == [["Summarize a requirement", "a1"]]
    assert result["pickers"] == [] and result["submits"] == 0


# ------------------------------------------------------- counts on the cards

HINT_FETCH = """
const asked = [];
globalThis.fetch = (url) => {
  asked.push(String(url));
  const body = String(url) === "/api/suggestions"
    ? { sources: [{ id: "github.pull_requests", uses_agent: false }, { id: "ci.failed_builds", uses_agent: false }, { id: "jira.issues", uses_agent: false }] }
    : ANSWERS[String(url).split("?")[0]] || { state: "ok", groups: [] };
  return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
};
"""


def test_a_card_shows_a_count_only_for_work_that_is_waiting_on_the_member():
    out = _node(
        f"""
{I18N_STUB}
const ANSWERS = {{
  "/api/suggestions/github.pull_requests": {{ state: "ok", groups: [
    {{ id: "review_requested", items: [{{ value: "a" }}, {{ value: "b" }}] }},
    {{ id: "mine", items: [{{ value: "c" }}] }},
  ] }},
  "/api/suggestions/ci.failed_builds": {{ state: "ok", groups: [{{ id: "pr_checks", items: [{{ value: "x" }}] }}] }},
  "/api/suggestions/jira.issues": {{ state: "ok", groups: [{{ id: "assigned", items: [{{ value: "ABC-1" }}] }}] }},
}};
{HINT_FETCH}
{PICKER.read_text(encoding="utf-8")}
const api = module.exports;
(async () => {{
  const review = await api.cardHint({{ title: "Review", input: {{ source: "github.pull_requests" }} }}, "a1");
  const builds = await api.cardHint({{ title: "Builds", input: {{ source: "ci.failed_builds" }} }}, "a1");
  const issues = await api.cardHint({{ title: "Issues", input: {{ source: "jira.issues" }} }}, "a1");
  const plain = await api.cardHint({{ title: "Plain", input: {{ label: "x" }} }}, "a1");
  const before = asked.length;
  await api.fetchSuggestions("github.pull_requests", {{ params: {{}}, agentId: "", recent: [] }});
  console.log(JSON.stringify({{ review, builds, issues, plain, asked, reused: asked.length === before }}));
}})();
"""
    )

    assert out["review"] == {"text": "2 to review", "tone": "accent"}
    assert out["builds"] == {"text": "1 failed", "tone": "danger"}
    # Assigned issues are not news, and a card without a source has nothing to count.
    assert out["issues"] is None and out["plain"] is None
    # Two lists were asked, and nothing for the cards that show no count.
    assert out["asked"] == ["/api/suggestions", "/api/suggestions/github.pull_requests", "/api/suggestions/ci.failed_builds"]
    # Opening the picker right after reuses the answer the count was made from.
    assert out["reused"] is True


def test_no_count_when_nothing_is_waiting_or_the_list_could_not_be_read():
    out = _node(
        f"""
{I18N_STUB}
const ANSWERS = {{
  "/api/suggestions/github.pull_requests": {{ state: "ok", groups: [{{ id: "mine", items: [{{ value: "c" }}] }}] }},
  "/api/suggestions/ci.failed_builds": {{ state: "not_configured", groups: [], connector: "jenkins" }},
}};
{HINT_FETCH}
{PICKER.read_text(encoding="utf-8")}
const api = module.exports;
(async () => {{
  const review = await api.cardHint({{ title: "Review", input: {{ source: "github.pull_requests" }} }}, "a1");
  const builds = await api.cardHint({{ title: "Builds", input: {{ source: "ci.failed_builds" }} }}, "a1");
  console.log(JSON.stringify({{ review, builds }}));
}})();
"""
    )

    assert out == {"review": None, "builds": None}


def test_the_count_is_put_on_its_card_once_the_cards_are_painted():
    setup = """
const badges = [];
const cardEl = { querySelector: () => null, append: (node) => badges.push([node.className, node.textContent]) };
list.querySelector = (selector) => (selector === '[data-starter-card="0"]' ? cardEl : null);
window.portalCardPicker = {
  historyKeyFor: () => "k",
  prefetchCard: () => {},
  cardHint: (card, agentId) => Promise.resolve(card.input && card.input.source === "github.pull_requests" ? { text: "2 waiting for your review", tone: "accent" } : null),
};
window.showPicker = () => Promise.resolve(null);
"""
    review = {"title": "Review a pull request", "prompt": "Review {{input}}.", "input": {"label": "Pull request", "source": "github.pull_requests"}}
    script = f"""
{I18N_STUB}
{DOM_SHIM}
globalThis.window = {{}};
{setup}
globalThis.fetch = () => Promise.resolve({{ ok: true, json: () => Promise.resolve({{ welcome: null, cards: {json.dumps([review, SOURCE_CARD])} }}) }});
{PERSONALIZATION.read_text(encoding="utf-8")}
fireDocument("portal:agent-selected", {{ agentId: "a1" }});
settle().then(settle).then(() => console.log(JSON.stringify({{ badges, submits: calls.submits }})));
"""
    out = _node(script)

    assert out["badges"] == [["portal-starter-card-hint is-accent", "2 waiting for your review"]]
    assert out["submits"] == 0


# ------------------------------------------------------- a dialog of one size

# The least of an element the list component touches.
FAKE_ELEMENT = """
function fakeElement() {
  const classes = new Set();
  return {
    innerHTML: "", style: {}, offsetHeight: 132,
    classList: {
      toggle(name, on) { if (on) classes.add(name); else classes.delete(name); },
      contains(name) { return classes.has(name); },
    },
    setAttribute() {}, removeAttribute() {}, addEventListener() {}, querySelectorAll() { return []; },
  };
}
"""


def test_a_list_that_keeps_its_place_says_so_when_it_has_nothing_to_list():
    out = _picker(
        FAKE_ELEMENT
        + """
const kept = fakeElement();
const list = api.createSuggestionList({ listEl: kept, inputEl: fakeElement(), idPrefix: "p", steady: true, onPick() {} });
list.load();
const idle = kept.innerHTML;
list.setQuery("zzz");
const typed = kept.innerHTML;

const plain = fakeElement();
api.createSuggestionList({ listEl: plain, inputEl: fakeElement(), idPrefix: "q", onPick() {} }).load();

out = {
  keptHidden: kept.classList.contains("hidden"),
  idle: idle.includes("portal-picker-empty") && idle.includes("Nothing to suggest yet."),
  typed: typed.includes("No matches. Press Enter to use what you typed."),
  plainHidden: plain.classList.contains("hidden"),
  plainHtml: plain.innerHTML,
};
"""
    )

    # In its dialog from the first moment to the last, whatever it holds.
    assert out["keptHidden"] is False and out["idle"] and out["typed"]
    # A list nobody asked to keep in place still steps aside when empty
    # (the lists of a question card, in the conversation).
    assert out["plainHidden"] is True and out["plainHtml"] == ""


def test_a_list_can_be_held_at_the_height_it_has():
    out = _picker(
        FAKE_ELEMENT
        + """
const el = fakeElement();
const list = api.createSuggestionList({ listEl: el, inputEl: fakeElement(), idPrefix: "p", onPick() {} });
list.load();
list.keepSize();
list.setQuery("nothing matches this");
out = { height: el.style.height, hidden: el.classList.contains("hidden"), empty: el.innerHTML.includes("portal-picker-empty") };
"""
    )

    assert out == {"height": "132px", "hidden": False, "empty": True}


def test_a_fields_list_opens_under_its_input_or_above_it_near_the_bottom_of_the_window():
    out = _picker(
        """
const rect = (top, bottom) => ({ top, bottom, left: 467.4, width: 506.2 });
out = {
  roomy: api.popPlacement(rect(350, 392), 900),
  tight: api.popPlacement(rect(350, 392), 560),
  bottom: api.popPlacement(rect(700, 742), 800),
  cramped: api.popPlacement(rect(40, 82), 180),
};
"""
    )

    # Plenty of room: under the input, as tall as a list gets.
    assert out["roomy"] == {"up": False, "left": 467, "width": 506, "maxHeight": 280, "top": 396, "bottom": None}
    # Less room below than a full list, still enough: under it, shorter.
    assert out["tight"]["up"] is False and out["tight"]["maxHeight"] == 152
    # Next to nothing below and more above: over the input, its foot on the input's top.
    assert out["bottom"]["up"] is True and out["bottom"]["top"] is None
    assert out["bottom"]["bottom"] == 104 and out["bottom"]["maxHeight"] == 280
    # No room either way: it opens where there is more, and never thinner than it can be read.
    assert out["cramped"]["up"] is False and out["cramped"]["maxHeight"] == 120
