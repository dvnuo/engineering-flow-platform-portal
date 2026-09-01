"""The composer chip that names, explains, and links a typed slash command.

The behavioural tests run the real module under node against a small DOM shim,
so what is exercised is the whole path a member takes -- text in the composer,
resolved against the assistant's skills, rendered as a chip -- rather than a
string formatter in isolation. The wiring assertions are plain reads, because a
chip nothing mounts is the failure most likely to ship unnoticed.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from _js_extract_helpers import _extract_js_function


CHAT_UI = Path("app/static/js/chat_ui.js")
CHIP = Path("app/static/js/composer_skill_chip.js")
APP_HTML = Path("app/templates/app.html")
CSS = Path("app/static/css/app.css")
CI = Path(".github/workflows/ci.yml")

RESOLVER_FUNCTIONS = (
    "normalizeSkillCommand",
    "parseSkillSlashInput",
    "getCachedSkillsForAgent",
    "findCachedSkillForSlash",
    "skillSourceUrl",
    "resolvePortalSkillCommand",
)

# Only what composer_skill_chip.js actually touches. Kept deliberately small: a
# fuller fake would start passing for reasons a browser would not.
DOM_SHIM = """
function makeElement(id) {
  const classes = new Set();
  const handlers = {};
  const element = {
    id,
    value: "",
    innerHTML: "",
    classList: {
      add: (name) => classes.add(name),
      remove: (name) => classes.delete(name),
      contains: (name) => classes.has(name),
    },
    addEventListener: (type, fn) => { (handlers[type] = handlers[type] || []).push(fn); },
    replaceChildren: () => { element.innerHTML = ""; },
    fire: (type) => (handlers[type] || []).forEach((fn) => fn({ target: element })),
    hidden: () => classes.has("hidden"),
  };
  classes.add("hidden");
  return element;
}

const elements = {
  "composer-skill-chip": makeElement("composer-skill-chip"),
  "chat-input": makeElement("chat-input"),
};
const documentHandlers = {};
globalThis.document = {
  readyState: "complete",
  getElementById: (id) => elements[id] || null,
  addEventListener: (type, fn) => { (documentHandlers[type] = documentHandlers[type] || []).push(fn); },
};
const fireDocument = (type) => (documentHandlers[type] || []).forEach((fn) => fn({}));
const chip = elements["composer-skill-chip"];
const field = elements["chat-input"];
const type = (text) => { field.value = text; field.fire("input"); };
"""


def _run_node(script: str):
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping composer skill chip behaviour tests")
    result = subprocess.run([node_bin, "-e", script], check=False, text=True, capture_output=True)
    if result.returncode != 0:
        raise AssertionError(f"node failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return json.loads(result.stdout.strip())


def _resolver_bundle() -> str:
    js = CHAT_UI.read_text(encoding="utf-8")
    return "\n".join(_extract_js_function(js, name) for name in RESOLVER_FUNCTIONS)


DEFAULT_SKILL = {
    "name": "create-pull-request",
    "description": "Open a pull request from the current branch.",
    "callable": True,
    "blocked_reason": "",
    "repo_path": "create-pull-request/skill.md",
}

DEFAULT_AGENT = {
    "id": "a1",
    "effective_skill_repo_url": "https://github.com/org/skills",
    "effective_skill_branch": "dev",
}


def _chip_after_typing(text, *, skills=None, agent=None, load=None):
    """Load the real chip module over the shim, type `text`, return what shows."""
    skills = [DEFAULT_SKILL] if skills is None else skills
    agent = DEFAULT_AGENT if agent is None else agent
    script = f"""
{DOM_SHIM}

const state = {{
  selectedAgentId: "a1",
  mineAgents: {json.dumps([agent] if agent else [])},
  agentDefaults: null,
  cachedSkills: [],
  cachedSkillsByAgent: new Map([["a1", {json.dumps(skills)}]]),
}};
{_resolver_bundle()}

let loadCalls = 0;
globalThis.window = {{
  currentPortalAgentId: () => state.selectedAgentId,
  resolvePortalSkillCommand: (text) => resolvePortalSkillCommand(text),
  ensurePortalSkillsLoaded: (agentId) => {{ loadCalls += 1; return {json.dumps(load or "resolved")} === "reject" ? Promise.reject(new Error("nope")) : Promise.resolve([]); }},
}};

{CHIP.read_text(encoding="utf-8")}

type({json.dumps(text)});
console.log(JSON.stringify({{ html: chip.innerHTML, hidden: chip.hidden(), loadCalls }}));
"""
    return _run_node(script)


# ------------------------------------------------------- what the chip shows


def test_a_recognised_command_becomes_a_chip_linking_to_its_source():
    result = _chip_after_typing("/create-pull-request")

    assert result["hidden"] is False
    assert 'href="https://github.com/org/skills/blob/dev/create-pull-request/skill.md"' in result["html"]
    assert "/create-pull-request" in result["html"]


def test_the_link_opens_a_new_tab_and_severs_the_opener():
    html = _chip_after_typing("/create-pull-request")["html"]

    assert 'target="_blank"' in html
    # The target is a repository the member is signed in to.
    assert 'rel="noopener noreferrer"' in html


def test_the_skill_description_is_what_hovering_shows():
    assert "Open a pull request from the current branch." in _chip_after_typing("/create-pull-request")["html"]


def test_arguments_after_the_command_do_not_dismiss_the_chip():
    # The send path treats `/skill args` as an invocation, so the chip has to
    # agree, or it would vanish exactly when the member finishes typing.
    assert _chip_after_typing("/create-pull-request fix the flaky test")["hidden"] is False


def test_plain_prose_shows_nothing():
    result = _chip_after_typing("what does this repository do?")

    assert result["hidden"] is True
    assert result["html"] == ""


def test_a_slash_command_that_is_not_a_skill_shows_nothing():
    assert _chip_after_typing("/not-a-real-skill")["hidden"] is True


def test_a_command_mid_sentence_is_not_treated_as_an_invocation():
    # `parseSkillSlashInput` anchors at the start, and so does the send path.
    assert _chip_after_typing("mention /create-pull-request in the docs")["hidden"] is True


def test_the_chip_clears_when_the_command_is_deleted():
    script = f"""
{DOM_SHIM}
const state = {{
  selectedAgentId: "a1",
  mineAgents: {json.dumps([DEFAULT_AGENT])},
  agentDefaults: null,
  cachedSkills: [],
  cachedSkillsByAgent: new Map([["a1", {json.dumps([DEFAULT_SKILL])}]]),
}};
{_resolver_bundle()}
globalThis.window = {{
  currentPortalAgentId: () => state.selectedAgentId,
  resolvePortalSkillCommand: (text) => resolvePortalSkillCommand(text),
  ensurePortalSkillsLoaded: () => Promise.resolve([]),
}};
{CHIP.read_text(encoding="utf-8")}
type("/create-pull-request");
const shown = {{ html: chip.innerHTML, hidden: chip.hidden() }};
type("");
console.log(JSON.stringify({{ shown, cleared: {{ html: chip.innerHTML, hidden: chip.hidden() }} }}));
"""
    result = _run_node(script)

    assert result["shown"]["hidden"] is False
    assert result["cleared"]["hidden"] is True
    assert result["cleared"]["html"] == ""


def test_switching_assistant_retires_a_chip_drawn_for_the_previous_one():
    # Skills differ per assistant, so a chip left over is a claim about the
    # wrong runtime.
    script = f"""
{DOM_SHIM}
const state = {{
  selectedAgentId: "a1",
  mineAgents: {json.dumps([DEFAULT_AGENT])},
  agentDefaults: null,
  cachedSkills: [],
  cachedSkillsByAgent: new Map([["a1", {json.dumps([DEFAULT_SKILL])}]]),
}};
{_resolver_bundle()}
globalThis.window = {{
  currentPortalAgentId: () => state.selectedAgentId,
  resolvePortalSkillCommand: (text) => resolvePortalSkillCommand(text),
  ensurePortalSkillsLoaded: () => Promise.resolve([]),
}};
{CHIP.read_text(encoding="utf-8")}
type("/create-pull-request");
const before = chip.hidden();
state.selectedAgentId = "a2";
state.cachedSkillsByAgent.set("a2", []);
fireDocument("portal:agent-selected");
console.log(JSON.stringify({{ before, afterHidden: chip.hidden(), afterHtml: chip.innerHTML }}));
"""
    result = _run_node(script)

    assert result["before"] is False
    assert result["afterHidden"] is True
    assert result["afterHtml"] == ""


def test_a_skill_with_no_repo_path_renders_without_a_link():
    html = _chip_after_typing(
        "/create-pull-request", skills=[{**DEFAULT_SKILL, "repo_path": ""}]
    )["html"]

    assert "portal-skill-chip" in html
    assert "href" not in html
    # Says why there is nothing to click rather than looking broken.
    assert "unavailable" in html


def test_a_blocked_skill_says_so_and_says_why():
    html = _chip_after_typing(
        "/create-pull-request",
        skills=[{
            **DEFAULT_SKILL,
            "callable": False,
            "blocked_reason": "skill denied by current OpenCode permission profile",
        }],
    )["html"]

    assert "is-blocked" in html
    assert "denied by current OpenCode permission profile" in html


def test_a_blocked_skill_with_no_stated_reason_still_explains_itself():
    html = _chip_after_typing(
        "/create-pull-request", skills=[{**DEFAULT_SKILL, "callable": False}]
    )["html"]

    assert "not callable" in html


def test_markup_in_a_skill_description_is_escaped():
    # Descriptions come from the skills repository, a separate repo with a
    # separate review path.
    html = _chip_after_typing(
        "/create-pull-request",
        skills=[{**DEFAULT_SKILL, "description": '<img src=x onerror="alert(1)">'}],
    )["html"]

    assert "<img" not in html
    assert "&lt;img" in html


def test_a_quote_in_a_description_cannot_break_out_of_the_tooltip_attribute():
    html = _chip_after_typing(
        "/create-pull-request",
        skills=[{**DEFAULT_SKILL, "description": '" onmouseover="alert(1)'}],
    )["html"]

    assert 'onmouseover="alert(1)"' not in html
    assert "&quot;" in html


def test_a_leading_slash_with_nothing_cached_asks_for_the_skill_list():
    # A member who types a command before the list has loaded would otherwise
    # see nothing and have no way to make the chip appear.
    result = _chip_after_typing("/create-pull-request", skills=[])

    assert result["hidden"] is True
    assert result["loadCalls"] == 1


def test_prose_does_not_trigger_a_skill_fetch():
    assert _chip_after_typing("just a question")["loadCalls"] == 0


# ------------------------------------------------------------------ the link


def _urls(cases: list[dict]):
    script = f"""
{_extract_js_function(CHAT_UI.read_text(encoding="utf-8"), "skillSourceUrl")}
const cases = {json.dumps(cases)};
console.log(JSON.stringify(cases.map((c) => skillSourceUrl(c["repo"], c["branch"], c["path"]))));
"""
    return _run_node(script)


def test_the_git_suffix_and_trailing_slashes_are_dropped():
    # Both spellings come back from `git remote get-url`, and neither is a page.
    assert _urls([
        {"repo": "https://github.com/org/skills.git", "branch": "main", "path": "a/skill.md"},
        {"repo": "https://github.com/org/skills/", "branch": "main", "path": "a/skill.md"},
    ]) == [
        "https://github.com/org/skills/blob/main/a/skill.md",
        "https://github.com/org/skills/blob/main/a/skill.md",
    ]


def test_an_ssh_remote_is_translated_rather_than_dropped():
    assert _urls([
        {"repo": "git@github.com:org/skills.git", "branch": "ops", "path": "runbook/skill.md"}
    ]) == ["https://github.com/org/skills/blob/ops/runbook/skill.md"]


def test_a_branch_containing_slashes_keeps_them():
    # `feature/x` is a legal branch and GitHub reads the slash as a path
    # separator, so percent-encoding the whole ref would 404.
    assert _urls([
        {"repo": "https://github.com/org/skills", "branch": "feature/role-branches", "path": "a/skill.md"}
    ]) == ["https://github.com/org/skills/blob/feature/role-branches/a/skill.md"]


def test_a_credentialed_remote_never_reaches_the_link():
    # Portal stores a token in the clone URL for private repositories, and the
    # chip renders the link into the page where it can be copied.
    links = _urls([
        {"repo": "https://x-access-token:ghpSECRET@github.com/org/skills.git", "branch": "main", "path": "a/skill.md"},
        {"repo": "https://someone@github.com/org/skills", "branch": "main", "path": "a/skill.md"},
    ])

    assert links == [
        "https://github.com/org/skills/blob/main/a/skill.md",
        "https://github.com/org/skills/blob/main/a/skill.md",
    ]
    for link in links:
        assert "@" not in link
        assert "SECRET" not in link


def test_an_enterprise_host_and_port_are_preserved():
    assert _urls([
        {"repo": "https://git.internal.example:8443/team/skills.git", "branch": "main", "path": "a/skill.md"}
    ]) == ["https://git.internal.example:8443/team/skills/blob/main/a/skill.md"]


def test_a_path_segment_with_a_space_is_encoded():
    assert _urls([
        {"repo": "https://github.com/org/skills", "branch": "main", "path": "my skill/skill.md"}
    ]) == ["https://github.com/org/skills/blob/main/my%20skill/skill.md"]


@pytest.mark.parametrize(
    "case",
    [
        {"repo": "", "branch": "main", "path": "a/skill.md"},
        {"repo": "https://github.com/org/skills", "branch": "", "path": "a/skill.md"},
        {"repo": "https://github.com/org/skills", "branch": "main", "path": ""},
        {"repo": "https://github.com", "branch": "main", "path": "a/skill.md"},
        {"repo": "file:///srv/skills", "branch": "main", "path": "a/skill.md"},
        {"repo": "not a url at all", "branch": "main", "path": "a/skill.md"},
    ],
    ids=["no-repo", "no-branch", "no-path", "no-repo-path", "not-browsable", "unparseable"],
)
def test_anything_that_cannot_make_a_real_link_makes_none(case):
    # An empty string tells the chip to render without a link. A guess here
    # would send the member to a 404 that reads as a broken deployment.
    assert _urls([case]) == [""]


# --------------------------------------------------------------- the wiring


def test_the_chip_is_mounted_in_the_composer_and_starts_hidden():
    html = APP_HTML.read_text(encoding="utf-8")
    # Between the wrapper opening and the textarea: the chip belongs above the
    # field it describes, inside the composer's own flex column.
    wrap = html.split('class="portal-composer-input-wrap"', 1)[1].split("<textarea", 1)[0]

    assert 'id="composer-skill-chip"' in wrap, "the chip must sit inside the composer, not elsewhere"
    assert 'class="portal-skill-chip-row hidden"' in html


def test_the_chip_script_is_loaded_after_chat_ui_publishes_its_helpers():
    html = APP_HTML.read_text(encoding="utf-8")

    assert "js/composer_skill_chip.js" in html
    assert html.index("js/chat_ui.js") < html.index("js/composer_skill_chip.js")


def test_the_helpers_the_chip_reads_are_published():
    js = CHAT_UI.read_text(encoding="utf-8")

    assert "window.resolvePortalSkillCommand = " in js
    assert "window.ensurePortalSkillsLoaded = " in js


def test_the_skill_suggestion_shape_carries_what_the_chip_needs():
    suggestion = _extract_js_function(CHAT_UI.read_text(encoding="utf-8"), "toSkillSuggestion")

    # Without the path there is nothing to link to, and `desc` folds status text
    # into the description, which is not what belongs in a hover.
    assert "repo_path" in suggestion
    assert "description:" in suggestion


def test_picking_a_suggestion_announces_the_change():
    # setRangeText fires no event, so the chip, the autosize, and the draft save
    # would all miss a command picked with the mouse.
    js = CHAT_UI.read_text(encoding="utf-8")
    pick = js.split("dom.chatInput.setRangeText(", 1)[1].split("hideSuggest();", 1)[0]

    assert 'dispatchEvent(new Event("input"' in pick


def test_clearing_the_composer_on_send_announces_the_change():
    # The chip is drawn from what was typed; without an event it would outlive
    # the message it described and sit beside an empty composer.
    js = CHAT_UI.read_text(encoding="utf-8")
    clearing = js.split('dom.chatInput.value = "";', 1)[1].split("clearDraftForAgent", 1)[0]

    assert 'dispatchEvent(new Event("input"' in clearing


def test_the_chip_takes_its_colours_from_theme_tokens():
    css = CSS.read_text(encoding="utf-8")

    assert ".portal-skill-chip {" in css
    # A literal here would render one theme's chip on the other theme's ground.
    chip_block = css.split(".portal-skill-chip {", 1)[1].split("}", 1)[0]
    assert "var(--portal-" in chip_block
    assert "#" not in chip_block


def test_ci_syntax_checks_the_new_script():
    # chat_ui.js has had a `node --check` gate since a syntax error shipped; a
    # second composer script deserves the same one.
    assert "node --check app/static/js/composer_skill_chip.js" in CI.read_text(encoding="utf-8")
