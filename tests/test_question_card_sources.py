"""A question the assistant asks by naming a picker source.

Instead of looking candidates up and listing them as options, the assistant can
ask "which issue?" with ``source: jira.issues``; the runtime carries that in the
question's metadata and the card shows the member's own recent items. These
drive the real module under node and read the card it builds.
"""
import json

from test_interactive_card_lifecycle import _run_node

PICKER = """
const created = [];
window.portalCardPicker = {
  resolveParams: (params) => Object.fromEntries(Object.entries(params || {}).map(([k, v]) => [k, [].concat(v)])),
  createSuggestionList: (config) => { created.push(config.source); return { load() {}, setQuery() {}, move() {}, activeItem() { return null; }, clearActive() {} }; },
};
"""

ASK = """
const ask = (question) => {
  const request = { request_id: "q-9", questions: [question] };
  setPending({ question_request: request });
  runtimeEvent("question.requested", { question_request: request });
  const row = list.children.find((c) => c.id === "portal-interactive-input");
  return row ? row.innerHTML : "";
};
"""


def _card(question: dict, *, picker: bool = True) -> str:
    body = f"""
{PICKER if picker else ""}
{ASK}
console.log(JSON.stringify({{ html: ask({json.dumps(question)}) }}));
"""
    return _run_node(body)["html"]


SOURCED = {
    "header": "Ticket",
    "question": "Which ticket should I implement?",
    "metadata": {"source": "jira.issues", "source_params": {"types": ["Story"]}},
}


def test_a_question_with_a_source_shows_a_list_and_an_answer_box_instead_of_options():
    html = _card({**SOURCED, "options": [{"label": "ABC-1"}, {"label": "ABC-2"}]})

    assert 'data-question-source="jira.issues"' in html
    assert "data-question-params=\"{&quot;types&quot;:[&quot;Story&quot;]}\"" in html
    # The list replaces the options the assistant would otherwise have had to
    # look up; the box takes the pick, or anything typed instead.
    assert 'type="radio"' not in html
    assert "data-question-custom-input" in html and "disabled" not in html
    assert "Pick from the list, or type your answer" in html
    assert "Which ticket should I implement?" in html and "Ticket" in html
    assert "Something else" not in html


def test_without_the_picker_script_the_question_is_asked_the_way_it_always_was():
    html = _card({**SOURCED, "options": [{"label": "ABC-1"}, {"label": "ABC-2"}]}, picker=False)

    assert "data-question-source" not in html
    assert html.count('type="radio"') == 2


def test_a_question_that_refuses_free_text_keeps_its_options():
    # The pick lands in the answer box, and this question has none.
    html = _card({**SOURCED, "custom": False, "options": [{"label": "ABC-1"}, {"label": "ABC-2"}]})

    assert "data-question-source" not in html
    assert html.count('type="radio"') == 2


def test_a_source_that_is_not_an_id_is_ignored():
    for bad in ('jira.issues" onmouseover="x', "Jira.Issues", "", "a" * 70, 7, None):
        question = {"question": "Which?", "options": [{"label": "A"}, {"label": "B"}], "metadata": {"source": bad}}
        html = _card(question)
        assert "data-question-source" not in html, bad
        assert html.count('type="radio"') == 2


def test_a_plain_question_is_untouched_by_all_this():
    html = _card({"question": "Which project?", "options": [{"label": "EFP"}, {"label": "OPS"}]})

    assert "data-question-source" not in html
    assert html.count('type="radio"') == 2 and "Something else" in html
