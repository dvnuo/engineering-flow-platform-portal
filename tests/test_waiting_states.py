"""Waiting: every action that asks the server shows that it is running, where the member pressed.

Deleting an app took several seconds (the host stops it and removes what it
has) and the page showed nothing in that time: the dialog that asked "are you
sure" had closed, no button had changed, and the member could not tell
whether the click had been taken. The same held for most actions of the Apps
pages.

The rule since then, in three shapes:

    a dialog that asked    stays open while its action runs: the confirm
                           button is busy, nothing closes the dialog, and a
                           failure is said in it (dialogs.js, ``action``)
    a button               turns busy and keeps exactly its size
                           (``portalBusyButton``; buttons drawn from state
                           carry the same classes)
    a switch or a choice   is held, with a spinner beside its name

What is pinned here is the code that makes those hold. The sizes and the
flows themselves were measured in a browser against a slow app host.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from _js_extract_helpers import I18N_STUB, _extract_js_function

ROOT = Path(__file__).resolve().parents[1]
DIALOGS = (ROOT / "app/static/js/dialogs.js").read_text(encoding="utf-8")
APPS = (ROOT / "app/static/js/apps.js").read_text(encoding="utf-8")
CSS = re.sub(r"/\*.*?\*/", "", (ROOT / "app/static/css/app.css").read_text(encoding="utf-8"), flags=re.S)


def _node(script: str):
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping the waiting-state tests that run code")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "case.js"
        path.write_text(script, encoding="utf-8")
        result = subprocess.run([node_bin, str(path)], check=False, text=True, capture_output=True, encoding="utf-8")
    if result.returncode != 0:
        raise AssertionError(f"node failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def _function(source: str, name: str) -> str:
    return _extract_js_function(source, name)


def _calls(source: str, opener: str) -> list[str]:
    """The text of every call that starts with ``opener``, to its closing bracket."""
    found = []
    at = source.find(opener)
    while at >= 0:
        depth = 0
        index = at + len(opener) - 1
        for index in range(at + len(opener) - 1, len(source)):
            if source[index] == "(":
                depth += 1
            elif source[index] == ")":
                depth -= 1
                if depth == 0:
                    break
        found.append(source[at : index + 1])
        at = source.find(opener, index)
    return found


def _rule(selector: str) -> str:
    """The declarations of the first rule whose selector list holds ``selector``."""
    for match in re.finditer(r"(?m)^([^{}@]+)\{([^{}]*)\}", CSS):
        if selector in [" ".join(part.split()) for part in match.group(1).split(",")]:
            return match.group(2)
    raise AssertionError(f"no rule for {selector}")


# ------------------------------------------------------------- the dialog waits


def test_a_dialog_with_an_action_stays_open_until_the_action_is_done():
    confirm = DIALOGS.split("function confirmResult() {", 1)[1].split("function cancelResult()", 1)[0]
    # Without an action the dialog closes at once, as it always did.
    assert "if (!hasAction) { cleanup(result); return; }" in confirm
    assert confirm.index("setBusy(true);") < confirm.index("opts.action(") < confirm.index("cleanup(result);\n          }, function (error)")
    # A failure is said in the dialog, and the member can try again.
    assert 'setStatus((error && error.message) || t("Request failed"), true);' in confirm
    assert confirm.index("}, function (error) {") < confirm.index("setBusy(false);")
    # What was typed is checked in the dialog too, not in a second one.
    assert "var problem = opts.validate(val);" in confirm and "setStatus(problem, true);" in confirm


def test_nothing_closes_or_restarts_a_dialog_that_is_working():
    assert "function cancelResult() {\n        if (busy) return;" in DIALOGS
    assert "function confirmResult() {\n        if (busy) return;" in DIALOGS
    assert 'if (busy && (e.key === "Escape" || e.key === "Enter")) { e.preventDefault(); return; }' in DIALOGS
    busy = DIALOGS.split("function setBusy(on) {", 1)[1].split("function cleanup(", 1)[0]
    assert "if (cancelEl) cancelEl.disabled = on;" in busy and "if (input) input.disabled = on;" in busy
    assert "busyButton(confirmEl, opts.busyText" in busy


def test_the_line_for_what_is_happening_is_in_the_dialog_from_the_start():
    assert "'<p class=\"portal-dialog-status\" data-dialog-status aria-live=\"polite\"></p>'" in DIALOGS
    assert "statusHtml +" in DIALOGS
    # Never taken out, never hidden: empty until there is something to say.
    assert "statusEl.hidden" not in DIALOGS and 'statusEl.classList.toggle("hidden"' not in DIALOGS
    status = _rule(".portal-dialog-status")
    assert "min-height: 42px" in status and "-webkit-line-clamp: 2" in status and "overflow: hidden" in status
    # The confirm button is as wide as its busy label before anything runs.
    assert 'if (hasAction) reserveBusyWidth(confirmEl, opts.busyText || t("Working\\u2026"));' in DIALOGS


# ----------------------------------------------------------- a button that works

FAKE_BUTTON = """
function fakeButton(width, fits) {
  const classes = new Set();
  const attrs = {};
  const button = {
    innerHTML: '<i></i><span>Save</span>', disabled: false, style: { width: '' },
    classList: { add: (...names) => names.forEach((n) => classes.add(n)), remove: (...names) => names.forEach((n) => classes.delete(n)), contains: (n) => classes.has(n) },
    setAttribute: (name, value) => { attrs[name] = String(value); },
    getAttribute: (name) => (name in attrs ? attrs[name] : null),
    removeAttribute: (name) => { delete attrs[name]; },
    getBoundingClientRect: () => ({ width: width }),
    clientWidth: width,
    get scrollWidth() { return fits ? width : width + 30; },
    set textContent(value) { this.innerHTML = String(value); },
    get textContent() { return this.innerHTML.replace(/<[^>]+>/g, ''); },
  };
  button.look = () => ({ html: button.innerHTML, disabled: button.disabled, width: button.style.width, classes: Array.from(classes).sort(), attrs: Object.assign({}, attrs) });
  return button;
}
"""


def test_a_busy_button_keeps_its_size_and_comes_back_as_it_was():
    out = _node(
        I18N_STUB
        + FAKE_BUTTON
        + _function(DIALOGS, "busyButton")
        + """
const out = {};
// The busy label fits: it is shown.
let button = fakeButton(112, true);
let done = busyButton(button, 'Deleting...');
out.fits = button.look();
out.twice = String(busyButton(button, 'again')) !== undefined && button.look().html;
done();
out.fitsAfter = button.look();
// It does not fit: the button keeps its own label under the spinner.
button = fakeButton(53.3, false);
button.setAttribute('aria-label', 'Save the variables');
done = busyButton(button, 'Saving...');
out.over = button.look();
done(); done();
out.overAfter = button.look();
// No label at all: the spinner alone.
button = fakeButton(40, true);
done = busyButton(button, '');
out.bare = button.look();
out.nothing = typeof busyButton(null, 'x');
console.log(JSON.stringify(out));
"""
    )
    assert out["fits"] == {
        "html": "Deleting...",
        "disabled": True,
        "width": "112px",
        "classes": ["is-busy"],
        "attrs": {"aria-busy": "true"},
    }
    assert out["twice"] == "Deleting..."  # a second press changes nothing
    assert out["fitsAfter"] == {"html": "<i></i><span>Save</span>", "disabled": False, "width": "", "classes": [], "attrs": {}}
    assert out["over"]["html"] == "<i></i><span>Save</span>" and out["over"]["width"] == "53.3px"
    assert out["over"]["classes"] == ["is-busy", "is-busy-over"]
    assert out["over"]["attrs"] == {"aria-busy": "true", "aria-label": "Saving..."}
    assert out["overAfter"]["attrs"] == {"aria-label": "Save the variables"} and out["overAfter"]["classes"] == []
    assert out["bare"]["classes"] == ["is-busy", "is-busy-over"]
    assert out["nothing"] == "function"


def test_the_busy_button_turns_and_does_not_move_anything():
    busy = _rule(".portal-btn.is-busy::before")
    assert "animation: portal-spin" in busy and "width: 14px" in busy and "box-sizing: border-box" in busy
    assert "cursor: progress" in _rule(".portal-btn.is-busy")
    # In the place of a 16px icon.
    assert "margin: 0 1px" in _rule(".portal-btn.is-busy.is-busy-icon::before")
    # Over the label: the label keeps its room, the spinner is taken out of the flow.
    assert "-webkit-text-fill-color: transparent" in _rule(".portal-btn.is-busy.is-busy-over")
    assert "visibility: hidden" in _rule(".portal-btn.is-busy.is-busy-over > *")
    assert "position: absolute" in _rule(".portal-btn.is-busy.is-busy-over::before")
    # People who asked for less motion get a slow turn, not a frozen ring.
    reduced = CSS.split("@media (prefers-reduced-motion: reduce) {\n  .portal-btn.is-busy::before,", 1)[1].split("}", 1)[0]
    assert "animation-duration: 2.4s" in reduced
    assert "window.portalBusyButton = busyButton;" in DIALOGS


def test_buttons_drawn_from_the_pages_state_are_busy_without_changing_size():
    out = _node(
        f"""
{I18N_STUB}
{APPS}
const api = module.exports;
console.log(JSON.stringify({{
  rest: api.lifecycleButtonHtml("stop", "square", "Stop", false, ""),
  pressed: api.lifecycleButtonHtml("stop", "square", "Stop", false, "stop"),
  other: api.lifecycleButtonHtml("start", "play", "Start", false, "stop"),
  deploy: api.stateButtonHtml("deploy", "is-primary", "data-app-deploy-latest", "Deploy latest", "Starting...", ""),
  deploying: api.stateButtonHtml("deploy", "is-primary", "data-app-deploy-latest", "Deploy latest", "Starting...", "deploy"),
  held: api.stateButtonHtml("upgrade", "is-primary", 'data-app-upgrade="7"', "Upgrade", "Starting...", "deploy"),
}}));
"""
    )
    assert out["rest"] == '<button type="button" class="portal-btn" data-app-lifecycle="stop"><i data-lucide="square" class="w-4 h-4"></i><span>Stop</span></button>'
    # The spinner takes the icon's place; the label is the same, so the width is.
    assert out["pressed"] == (
        '<button type="button" class="portal-btn is-busy is-busy-icon" data-app-lifecycle="stop" aria-busy="true" '
        'aria-label="Stopping\u2026" disabled><span>Stop</span></button>'
    )
    assert "disabled" not in out["other"]  # the caller says which are disabled; this one was not asked to be
    assert out["deploy"] == '<button type="button" class="portal-btn is-primary" data-app-deploy-latest><span>Deploy latest</span></button>'
    assert out["deploying"] == (
        '<button type="button" class="portal-btn is-primary is-busy is-busy-over" data-app-deploy-latest aria-busy="true" '
        'aria-label="Starting..." disabled><span>Deploy latest</span></button>'
    )
    # Another action runs: this button waits, without pretending to work.
    assert out["held"] == '<button type="button" class="portal-btn is-primary" data-app-upgrade="7" disabled><span>Upgrade</span></button>'


# --------------------------------------------------- the Apps pages use all three


def test_every_question_of_the_apps_pages_waits_for_its_answer():
    asked = _calls(APPS, "window.showConfirm(") + _calls(APPS, "window.showPrompt(")
    assert len(asked) >= 8
    for call in asked:
        assert "action:" in call, call[:120]
        assert "busyText:" in call, call[:120]
    # An administrator's questions go through one function, which adds the action.
    admin = _function(APPS, "adminAct")
    assert "action: function (value) {" in admin and "if (tried) await loadAdminApps();" in admin
    for call in _calls(APPS, "await adminAct("):
        assert call.startswith(("await adminAct(window.showPrompt", "await adminAct(window.showConfirm")), call[:80]
        assert call.count("busyText:") >= 1, call[:80]
    # No second dialog to say that the first one's action failed.
    assert "That did not work" not in APPS and "window.showAlert(" not in APPS


def test_deleting_an_app_shows_that_it_is_being_deleted():
    delete = _function(APPS, "deleteApp")
    for fragment in (
        'busyText: t("Deleting\\u2026")',
        'busyMessage: t("Stopping the app and removing what it has on the host. This can take a moment.")',
        "validate: function (typed) {",
        't("What you typed is not {slug}. Nothing was deleted.", { slug: app.slug })',
        "action: async function () {",
    ):
        assert fragment in delete, fragment
    action = delete.split("action: async function () {", 1)[1]
    # The request, then the list without the app, then the dialog closes: no gap in between.
    assert action.index('{ method: "DELETE" }') < action.index('shell().openCenter("mine")') < action.index('t("{name} is deleted."')
    # Nothing is sent outside the dialog any more.
    assert delete.count("await request(") == 1 and delete.index("action: async function") < delete.index("await request(")


def test_the_buttons_of_the_apps_pages_say_that_they_are_working():
    for name, label in (
        ("submitCreate", 't("Creating\\u2026")'),
        ("saveEnv", 't("Saving\\u2026")'),
        ("publishTemplateVersion", 't("Publishing\\u2026")'),
        ("downloadLog", 't("Preparing\\u2026")'),
        ("revealToken", 't("Loading\\u2026")'),
        ("copyToken", 't("Copying\\u2026")'),
        ("startDeploy", 't("Starting\\u2026")'),
    ):
        body = _function(APPS, name)
        assert "busy(" in body and label in body, name
    for name in ("changeAudience", "changeCapability", "toggleTemplate"):
        assert "savingControl(input)" in _function(APPS, name), name
    # Start, restart and stop say which of them was pressed.
    lifecycle = _function(APPS, "runLifecycle")
    assert "page.busyAction = verb;" in lifecycle and 'page.busyAction = "";' in lifecycle
    assert "page.busy, page.busyAction]" in _function(APPS, "drawOverview")
    # A button is never just switched off while something runs.
    assert "button.disabled = true" not in APPS


def test_a_switch_that_is_being_saved_is_held_with_a_spinner_beside_its_name():
    saving = _function(APPS, "savingControl")
    assert 'input.closest(".portal-app-switch-row") || input.closest("label")' in saving
    assert 'holder.setAttribute("aria-busy", "true");' in saving and "input.disabled = true;" in saving
    assert "animation: portal-spin" in _rule(".portal-app-switch-row.is-saving .portal-app-switch-copy strong::after")
    assert "cursor: progress" in _rule(".portal-share-option.is-saving")
