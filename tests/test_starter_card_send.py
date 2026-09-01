"""Clicking a starter card starts the run.

The card names what it does, and a card that needs a value asks for it in a
dialog the member confirms, so the trip to the Send button only added a step
between deciding and starting.

These run the real module under node against a small DOM shim, because what
matters is the whole click -- dialog, composed prompt, send -- and not any one
function in it.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


MODULE = Path("app/static/js/assistant_personalization.js")

# Only what assistant_personalization.js actually touches on this path. A fuller
# fake would start passing for reasons a browser would not.
DOM_SHIM = """
const calls = { submits: 0, fills: [], prompts: [] };
const handlers = {};

function element(extra) {
  return Object.assign({
    value: "",
    className: "",
    innerHTML: "",
    focus: () => {},
    setSelectionRange: () => {},
    dispatchEvent: () => true,
    addEventListener: (type, fn) => { (handlers[type] = handlers[type] || []).push(fn); },
    querySelector: () => null,
    append: () => {},
    remove: () => {},
  }, extra || {});
}

const input = element({});
Object.defineProperty(input, "value", {
  get() { return this._text || ""; },
  set(next) { this._text = next; calls.fills.push(next); },
});
const form = element({ requestSubmit: () => { calls.submits += 1; } });
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
const clickCard = (index) => {
  const button = { dataset: { starterCard: String(index) } };
  button.closest = (selector) => (selector === "[data-starter-card]" ? button : null);
  return (handlers.click || []).reduce(
    (chain, fn) => chain.then(() => fn({ target: button })),
    Promise.resolve()
  );
};
const settle = () => new Promise((resolve) => setImmediate(resolve));
"""


def _run_node(script: str):
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping starter card behaviour tests")
    result = subprocess.run([node_bin, "-e", script], check=False, text=True, capture_output=True)
    if result.returncode != 0:
        raise AssertionError(f"node failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return json.loads(result.stdout.strip())


def _click(card: dict, *, answer="ABC-1", drop_form=False):
    """Select an assistant, let its cards load, then click the first one."""
    script = f"""
{DOM_SHIM}
{"delete elements['chat-form'];" if drop_form else ""}
globalThis.window = {{
  showPrompt: (options) => {{ calls.prompts.push(options); return Promise.resolve({json.dumps(answer)}); }},
}};
globalThis.fetch = () => Promise.resolve({{
  ok: true,
  json: () => Promise.resolve({{ welcome: null, cards: [{json.dumps(card)}] }}),
}});

{MODULE.read_text(encoding="utf-8")}

fireDocument("portal:agent-selected", {{ agentId: "a1" }});
settle()
  .then(() => clickCard(0))
  .then(settle)
  .then(() => console.log(JSON.stringify(calls)))
  .catch((error) => {{ console.error(error); process.exit(1); }});
"""
    return _run_node(script)


PLAIN_CARD = {"title": "Summarise the sprint", "prompt": "Summarise the current sprint."}
INPUT_CARD = {
    "title": "Draft test cases",
    "prompt": "Design test cases for {{input}}.",
    "input": {"label": "Ticket", "placeholder": "ABC-1"},
}


def test_a_card_that_needs_no_value_sends_on_the_click():
    result = _click(PLAIN_CARD)

    assert result["fills"] == ["Summarise the current sprint."]
    # Through the form the Send button uses, so every guard on that path -- an
    # upload in flight, a run already going -- still refuses the click.
    assert result["submits"] == 1
    assert result["prompts"] == []


def test_a_card_that_needs_a_value_asks_once_and_then_sends():
    result = _click(INPUT_CARD, answer="EFP-42")

    assert len(result["prompts"]) == 1
    assert result["prompts"][0]["title"] == "Draft test cases"
    # The dialog is the confirmation step; a second click on Send added nothing.
    assert result["fills"] == ["Design test cases for EFP-42."]
    assert result["submits"] == 1


def test_cancelling_the_dialog_sends_nothing_and_leaves_the_composer_alone():
    result = _click(INPUT_CARD, answer=None)

    assert result["fills"] == []
    assert result["submits"] == 0


def test_the_placeholder_is_substituted_everywhere_it_appears():
    card = {"title": "Two holes", "prompt": "Link {{input}} and close {{input}}.", "input": {"label": "Ticket"}}

    assert _click(card, answer="EFP-9")["fills"] == ["Link EFP-9 and close EFP-9."]


def test_the_prompt_still_lands_in_the_composer_when_there_is_no_form_to_submit():
    # Cards are painted into the welcome row, which can outlive a composer that
    # a partial render has not put back yet.
    result = _click(PLAIN_CARD, drop_form=True)

    assert result["fills"] == ["Summarise the current sprint."]
    assert result["submits"] == 0
