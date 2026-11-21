"""Apps in the Portal page: the parts that decide what a member reads, run under node.

app/static/js/apps.js exports its plain functions (what state an app is in
and why, the order of a list, what a failed look at a repository asks the
member to do, the address a link names) so they can run without a page. The
pages themselves were looked at in a browser; what is pinned here is
everything that could tell a member the wrong thing.

The second half runs the router's own functions from chat_ui.js on the
addresses of the Apps section.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from _js_extract_helpers import I18N_STUB, _extract_js_function

APPS_JS = Path("app/static/js/apps.js")
CHAT_JS = Path("app/static/js/chat_ui.js")


def _node(script: str):
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping the Apps page tests")
    # A file, not `node -e`: the bundle is longer than a Windows command line.
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "case.js"
        path.write_text(script, encoding="utf-8")
        result = subprocess.run([node_bin, str(path)], check=False, text=True, capture_output=True, encoding="utf-8")
    if result.returncode != 0:
        raise AssertionError(f"node failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def _apps(body: str):
    """Run ``body`` with the module's exports as ``api``; it must set ``out``."""
    return _node(
        f"""
{I18N_STUB}
{APPS_JS.read_text(encoding="utf-8")}
const api = module.exports;
let out = null;
{body}
console.log(JSON.stringify(out));
"""
    )


def test_the_module_loads_without_a_page():
    out = _apps("out = Object.keys(api).sort();")
    for name in ("appState", "sortMine", "sortAvailable", "inspectProblem", "envFromRows", "createSseParser"):
        assert name in out


# ----------------------------------------------------- an app's state, in words


def test_an_apps_state_and_its_reason_are_said_in_plain_words():
    out = _apps(
        """
const app = (fields) => Object.assign({ enabled: true, deployed: true, state: "running", manage: {} }, fields);
const pick = (state) => [state.key, state.tone, state.label, state.reason];
out = {
  running: pick(api.appState(app({}), null)),
  idle: pick(api.appState(app({ state: "stopped", state_reason: "idle" }), null)),
  manual: pick(api.appState(app({ state: "stopped", state_reason: "manual" }), null)),
  crashed: pick(api.appState(app({ state: "crashed", state_reason: "exit:137" }), null)),
  memory: pick(api.appState(app({ state: "killed", state_reason: "memory" }), null)),
  failed: pick(api.appState(app({ state: "failed" }), null)),
  unknown: pick(api.appState(app({ state: null }), null)),
  undeployed: pick(api.appState(app({ deployed: false, state: null }), null)),
  firstFailed: pick(api.appState(app({ deployed: false, state: null, manage: { latest_release: { status: "failed" } } }), null)),
  deploying: pick(api.appState(app({ manage: { active_release: { id: "r1", status: "running" } } }), null)),
  takenDown: pick(api.appState(app({ enabled: false, disabled_reason: "Under review" }), null)),
  ownerGone: pick(api.appState(app({ enabled: false, disabled_reason: "owner_inactive" }), null)),
};
"""
    )

    assert out["running"] == ["running", "success", "Running", ""]
    assert out["idle"][:3] == ["stopped", "neutral", "Stopped"]
    assert "without visitors" in out["idle"][3] and "next visit starts it" in out["idle"][3]
    assert "stopped by hand" in out["manual"][3]
    assert out["crashed"][:3] == ["crashed", "error", "Crashed"] and "code 137" in out["crashed"][3]
    assert out["memory"][0] == "killed" and "more memory than an app may" in out["memory"][3]
    assert out["failed"][:2] == ["failed", "error"]
    assert out["unknown"][:2] == ["unknown", "neutral"]
    assert out["undeployed"][:3] == ["undeployed", "neutral", "Not deployed"]
    assert out["firstFailed"][:3] == ["deploy_failed", "error", "Deployment failed"]
    assert out["deploying"][:2] == ["deploying", "info"]
    assert out["takenDown"][:3] == ["disabled", "error", "Taken down"]
    assert out["takenDown"][3] == "An administrator took it down: Under review"
    assert "owner no longer has access" in out["ownerGone"][3]


def test_the_fresher_status_answer_wins_over_the_card():
    out = _apps(
        """
const app = { enabled: true, deployed: true, state: "running", manage: { latest_release: { status: "failed" } } };
out = {
  card: api.appState(app, null),
  stopped: api.appState(app, { enabled: true, deployed: true, deploying: false, state: "stopped", reason: "idle" }).key,
  deploying: api.appState(app, { enabled: true, deployed: true, deploying: true, state: "running" }).key,
  disabled: api.appState(app, { enabled: false, disabled_reason: "x", deployed: true, deploying: true, state: "running" }).key,
};
"""
    )
    # The app runs, but its newest code did not make it: said beside the state.
    assert out["card"]["key"] == "running" and out["card"]["lastDeployFailed"] is True
    assert out["card"]["attention"] is False
    assert out["stopped"] == "stopped"
    assert out["deploying"] == "deploying"
    # Taken down beats everything else that is going on.
    assert out["disabled"] == "disabled"


# ---------------------------------------------------------- the app center's lists


def test_my_apps_put_what_needs_me_first_and_the_rest_by_name():
    out = _apps(
        """
const app = (name, fields) => Object.assign({ name, enabled: true, deployed: true, state: "running", manage: {} }, fields);
const apps = [
  app("Zebra", {}),
  app("alpha", {}),
  app("Mango", { state: "crashed", state_reason: "exit:1" }),
  app("Kiwi", { enabled: false, disabled_reason: "x" }),
  app("Beta", { manage: { latest_release: { status: "failed" } } }),
  app("Delta", { deployed: false, state: null, manage: { latest_release: { status: "failed" } } }),
  app("Echo", { state: "stopped", state_reason: "idle" }),
];
out = { order: api.sortMine(apps).map((item) => item.name), input: apps.map((item) => item.name) };
"""
    )
    assert out["order"] == ["Beta", "Delta", "Kiwi", "Mango", "alpha", "Echo", "Zebra"]
    # The list handed in is left as it was.
    assert out["input"][0] == "Zebra"


def test_available_apps_put_what_was_used_last_first():
    out = _apps(
        """
const apps = [
  { name: "Never used B" },
  { name: "Old", last_visited_at: "2026-11-01T08:00:00" },
  { name: "never used a" },
  { name: "Recent", last_visited_at: "2026-11-14T08:00:00Z" },
];
out = api.sortAvailable(apps).map((item) => item.name);
"""
    )
    assert out == ["Recent", "Old", "never used a", "Never used B"]


def test_the_search_box_looks_in_the_name_the_address_the_description_and_the_owner():
    out = _apps(
        """
const app = { name: "Mind map", slug: "mind-map", description: "Draw and share", owner: { display_name: "Alice Chan", username: "achan" } };
out = ["", "  ", "mind", "MAP mind", "share", "alice", "achan", "mind-map", "mind zebra", "bob"].map((query) => api.matchesQuery(app, query));
"""
    )
    assert out == [True, True, True, True, True, True, True, True, False, False]


def test_with_no_list_named_the_center_opens_where_there_is_something():
    out = _apps(
        """
out = [
  api.chooseView({ mine: [{}], available: [{}] }),
  api.chooseView({ mine: [], available: [{}] }),
  api.chooseView({ mine: [], available: [] }),
  api.chooseView({ mine: null, available: null }),
];
"""
    )
    assert out == ["mine", "available", "mine", "mine"]


def test_who_may_open_an_app_reads_right_for_owner_and_administrator():
    out = _apps(
        """
out = {
  labels: ["private", "public", "platform", "anyone", "odd"].map(api.audienceLabel),
  widens: [["private", "public"], ["public", "platform"], ["platform", "anyone"], ["anyone", "private"], ["public", "public"], ["platform", "public"]]
    .map(([from, to]) => api.audienceWidens(from, to)),
};
"""
    )
    assert out["labels"] == ["Only the owner", "The owner's group", "Every member", "Anyone", "odd"]
    assert out["widens"] == [True, True, True, False, False, False]


# ----------------------------------------------------------------------- New app


def test_the_repository_is_looked_at_only_once_the_fields_are_plausible():
    out = _apps(
        """
out = {
  repos: [
    "https://github.com/acme/tools", "https://github.com/acme/tools.git", "https://ghe.example.com/acme/tools/",
    "git@github.com:acme/tools.git", "http://127.0.0.1/acme/tools",
    "", "acme/tools", "https://github.com/acme", "https://github.com/acme/tools/tree/main", "https://github.com/ac me/tools",
  ].map(api.plausibleRepoUrl),
  refs: ["main", "release/1.2", "v1.0.0", "", "a b", "-x", "a..b", "a:b"].map(api.plausibleRef),
  dirs: ["", "apps/mindmap", "apps", "../x", "a/../b", "a b", "a\\\\b"].map(api.plausibleEntryDir),
  slugs: ["Mind map", "  Sprint Board 2  ", "---", "A", "Caf\\u00e9 notes"].map(api.slugify),
};
"""
    )
    assert out["repos"] == [True, True, True, True, True, False, False, False, False, False]
    assert out["refs"] == [True, True, True, False, False, False, False, False]
    assert out["dirs"] == [True, True, True, False, False, False, False]
    assert out["slugs"] == ["mind-map", "sprint-board-2", "", "a", "caf-notes"]


def test_an_unreadable_repository_names_the_account_to_invite():
    out = _apps(
        """
const error = {
  code: "repo_not_readable",
  message_key: "The service account {account} cannot read {repo}. Add {account} to the repository as a read-only collaborator, then try again.",
  params: { account: "efp-bot", repo: "acme/private" },
};
out = {
  named: api.inspectProblem(error, null, 422),
  // The error did not name the account: the settings do.
  fromSettings: api.inspectProblem({ code: "repo_not_readable", params: { repo: "acme/private" } }, { service_account: "efp-reader" }, 422),
  // Nobody knows the account: the member is sent to an administrator.
  unknown: api.inspectProblem({ code: "repo_not_readable", detail: "The platform's service account cannot read acme/private.", params: {} }, { service_account: null }, 422),
};
"""
    )
    named = out["named"]
    assert named["account"] == "efp-bot"
    assert "efp-bot" in named["text"] and "acme/private" in named["text"]
    assert len(named["steps"]) == 3 and "efp-bot" in named["steps"][1] and "Read" in named["steps"][1]
    assert out["fromSettings"]["account"] == "efp-reader"
    assert out["unknown"]["account"] == "" and "administrator" in out["unknown"]["steps"][0]
    assert out["unknown"]["text"] == "The platform's service account cannot read acme/private."


def test_a_manifest_whose_owners_leave_the_member_out_shows_the_username_to_add():
    out = _apps(
        """
out = api.inspectProblem({ code: "not_listed_as_owner", detail: "x", params: { username: "alice" } }, null, 403);
"""
    )
    assert out["snippet"] == '"alice"'
    assert "owners" in out["text"] and "owners" in out["steps"][0] and "efp-app.json" in out["steps"][0]


def test_an_invalid_manifest_lists_every_problem():
    out = _apps(
        """
out = api.inspectProblem({
  code: "manifest_invalid",
  detail: "efp-app.json is not valid: a b",
  message_key: "efp-app.json is not valid: {problems}",
  params: { problems: "a b" },
  problems: [
    { detail: "\\"name\\" is required.", message_key: "\\"name\\" is required.", params: {} },
    { detail: "x", message_key: "\\"type\\" must be one of: {types}.", params: { types: "fastapi, flask" } },
  ],
}, null, 422);
"""
    )
    assert out["list"] == ['"name" is required.', '"type" must be one of: fastapi, flask.']
    # The joined sentence is not repeated above the list.
    assert out["text"] == ""
    assert out["steps"]


def test_other_failures_keep_the_servers_own_sentence():
    out = _apps(
        """
out = {
  ref: api.inspectProblem({ code: "ref_not_found", message_key: "No branch or tag named {ref} was found in {repo}.", params: { ref: "nope", repo: "acme/tools" } }, null, 422),
  missing: api.inspectProblem({ code: "manifest_missing", detail: "No efp-app.json was found." }, null, 422),
  plain: api.errorText({ detail: [{ msg: "field required" }, { msg: "too long" }] }, 422),
  status: api.errorText(null, 502),
  none: api.errorText(null, 0),
};
"""
    )
    assert out["ref"]["text"] == "No branch or tag named nope was found in acme/tools."
    assert out["ref"]["title"] == "No such branch or tag"
    assert "entry directory" in out["missing"]["steps"][0]
    assert out["plain"] == "field required; too long"
    assert out["status"] == "Request failed (HTTP 502)"
    assert out["none"] == "Request failed"


# ------------------------------------------------------------- an app's own page


def test_a_deployments_steps_have_names_and_say_why_one_failed():
    out = _apps(
        """
out = {
  labels: [
    { source: "portal", name: "resolve" }, { source: "portal", name: "manifest" }, { source: "host", name: "manifest" },
    { source: "host", name: "dependencies" }, { source: "host", name: "activate" }, { source: "host", name: "brand-new" },
  ].map(api.stepLabel),
  failed: api.stepNote({ status: "failed", error: { message: "pip failed", message_key: "The deployment failed at the step {step}: {reason}", params: { step: "dependencies", reason: "no wheel" } } }),
  plain: api.stepNote({ status: "failed", error: { message: "pip failed" } }),
  note: api.stepNote({ status: "succeeded", note: "x", note_key: "The icon {path} was not found and was left out.", note_params: { path: "icon.png" } }),
  none: api.stepNote({ status: "succeeded" }),
};
"""
    )
    assert out["labels"] == [
        "Find the commit",
        "Read efp-app.json",
        "Check the manifest",
        "Install the dependencies",
        "Switch to this release",
        "brand-new",
    ]
    assert out["failed"] == "The deployment failed at the step dependencies: no wheel"
    assert out["plain"] == "pip failed"
    assert out["note"] == "The icon icon.png was not found and was left out."
    assert out["none"] == ""


def test_environment_variables_are_checked_before_they_are_sent():
    out = _apps(
        """
out = {
  ok: api.envFromRows([{ name: " API_KEY ", value: "s3cret" }, { name: "", value: "" }, { name: "MODE", value: "" }]),
  badName: api.envFromRows([{ name: "1ST", value: "x" }]),
  empty: api.envFromRows([{ name: "", value: "orphan" }]),
  reserved: [api.envFromRows([{ name: "EFP_APP_TOKEN", value: "x" }]), api.envFromRows([{ name: "port", value: "1" }]), api.envFromRows([{ name: "Path", value: "1" }])],
  twice: api.envFromRows([{ name: "A", value: "1" }, { name: "A", value: "2" }]),
  none: api.envFromRows([]),
};
"""
    )
    assert out["ok"] == {"env": {"API_KEY": "s3cret", "MODE": ""}}
    assert "not a valid variable name" in out["badName"]["error"]
    assert "(empty)" in out["empty"]["error"]
    assert all("set by the platform" in item["error"] for item in out["reserved"])
    assert out["twice"]["error"] == "A is listed twice."
    assert out["none"] == {"env": {}}


def test_the_live_log_is_read_event_by_event_however_it_is_cut():
    out = _apps(
        """
const lines = [];
const feed = api.createSseParser((line) => lines.push(line));
feed('data: {"line": "one"}\\n\\n: keepalive\\n\\nda');
feed('ta: {"line": "two \\\\"quoted\\\\""}\\r\\n\\r\\ndata: {"li');
feed('ne": "three"}\\n');
feed('\\nevent: other\\ndata: {"note": "no line here"}\\n\\n');
out = lines;
"""
    )
    assert out == ["one", 'two "quoted"', "three"]


def test_usage_bars_are_scaled_to_the_tallest_day_and_cover_every_day():
    out = _apps(
        """
const now = new Date(Date.UTC(2026, 10, 15, 12, 0, 0));
const period = api.daysOfPeriod([{ day: "2026-11-15", requests: 40 }, { day: "2026-11-13", requests: 10 }, { day: "2026-10-01", requests: 99 }], 4, now);
out = {
  days: period.map((item) => item.day),
  rows: period.map((item) => (item.row ? item.row.requests : null)),
  heights: api.barHeights(period, (item) => (item.row ? item.row.requests : 0)),
  flat: api.barHeights([{ v: 0 }, { v: null }, { v: -3 }], (item) => item.v),
};
"""
    )
    assert out["days"] == ["2026-11-12", "2026-11-13", "2026-11-14", "2026-11-15"]
    assert out["rows"] == [None, 10, None, 40]
    assert out["heights"] == [0, 25, 0, 100]
    assert out["flat"] == [0, 0, 0]


def test_the_activity_list_says_what_was_done():
    out = _apps(
        """
const say = (action, detail) => api.eventText({ action, detail: detail || {} });
out = [
  say("app.created"),
  say("app.updated", { fields: ["llm_enabled", "connectors_enabled"] }),
  say("app.visibility_changed", { from: "private", to: "platform" }),
  say("app.disabled", { reason: "Under review" }),
  say("app.owner_changed", { from: "alice", to: "bob" }),
  say("deploy.requested", { ref: "main" }),
  say("deploy.succeeded", { sha: "0123456789abcdef" }),
  say("deploy.failed", { error: "pip failed" }),
  say("rollback.succeeded", { sha: "abcdef0123456789" }),
  say("token.reset"),
  say("template.changed", { is_template: true }),
  say("template.changed", { is_template: false }),
  say("template.version_published", { tag: "v1.0.0" }),
  say("host.crashed"),
  say("something.new"),
];
"""
    )
    assert out == [
        "Registered the app",
        "Changed the model provider switch, the connectors",
        "Changed who may open it from Only the owner to Every member",
        "Took the app down: Under review",
        "Transferred the app from alice to bob",
        "Asked for a deployment of main",
        "Deployed 0123456",
        "A deployment failed: pip failed",
        "Rolled back to abcdef0",
        "Reset the app token",
        "Offered the app as a template",
        "Stopped offering the app as a template",
        "Published version v1.0.0",
        "The app crashed",
        "something.new",
    ]


def test_times_without_a_zone_are_utc_and_sizes_are_readable():
    out = _apps(
        """
out = {
  naive: api.parseTime("2026-11-15T08:30:00").toISOString(),
  spaced: api.parseTime("2026-11-15 08:30:00.123456").toISOString(),
  zoned: api.parseTime("2026-11-15T08:30:00+08:00").toISOString(),
  zulu: api.parseTime("2026-11-15T08:30:00Z").toISOString(),
  bad: [api.parseTime(""), api.parseTime(null), api.parseTime("soon")],
  sizes: [0, 512, 1024, 1536, 5 * 1024 * 1024, 123 * 1024 * 1024, 3 * 1024 * 1024 * 1024, -1, "x"].map(api.formatBytes),
  initials: ["mind map", "  Zed", "", null].map(api.appInitial),
};
"""
    )
    assert out["naive"] == "2026-11-15T08:30:00.000Z"
    assert out["spaced"].startswith("2026-11-15T08:30:00.123")
    assert out["zoned"] == "2026-11-15T00:30:00.000Z"
    assert out["zulu"] == "2026-11-15T08:30:00.000Z"
    assert out["bad"] == [None, None, None]
    assert out["sizes"] == ["0 B", "512 B", "1.0 KB", "1.5 KB", "5.0 MB", "123 MB", "3.0 GB", "", ""]
    assert out["initials"] == ["M", "Z", "?", "?"]


# ------------------------------------------------------- the section's addresses


def _router(body: str):
    """The router's own functions from chat_ui.js, with a page that has (or lacks) the Apps rail button."""
    source = CHAT_JS.read_text(encoding="utf-8")
    start = source.index("const PORTAL_ROUTE_SECTIONS = new Set([")
    end = source.index("function initialPortalRouteSectionFromHash(")
    functions = "\n".join(
        _extract_js_function(source, name) for name in ("safeDecodeRouteComponent", "parsePortalHashRoute", "portalHashForRoute")
    )
    return _node(
        f"""
const window = {{ location: {{ hash: "" }} }};
const dom = {{ appsMenuBtn: {{}}, usersMenuBtn: {{}} }};
{source[start:end]}
{functions}
let out = null;
{body}
console.log(JSON.stringify(out));
"""
    )


def test_the_apps_addresses_name_a_list_an_app_or_a_part_of_its_page():
    out = _router(
        """
const pick = (hash) => { const r = parsePortalHashRoute(hash); return [r.valid, r.section, r.appsView, r.appId, r.appTab]; };
out = {
  center: pick("#/apps"),
  list: pick("#/apps/available"),
  templates: pick("#/apps/templates"),
  app: pick("#/apps/0f6f2c1e-1111-4222-8333-444455556666"),
  part: pick("#/apps/0f6f2c1e-1111-4222-8333-444455556666/logs"),
  overview: pick("#/apps/0f6f2c1e-1111-4222-8333-444455556666/overview"),
  badPart: pick("#/apps/0f6f2c1e-1111-4222-8333-444455556666/nope"),
  listWithPart: pick("#/apps/mine/logs"),
  tooDeep: pick("#/apps/a/logs/more"),
  query: pick("#/apps?view=mine"),
  // The other sections still take one id at most.
  tasks: pick("#/tasks/abc/def"),
};
"""
    )
    app_id = "0f6f2c1e-1111-4222-8333-444455556666"
    assert out["center"] == [True, "apps", "", "", ""]
    assert out["list"] == [True, "apps", "available", "", ""]
    assert out["templates"] == [True, "apps", "templates", "", ""]
    assert out["app"] == [True, "apps", "", app_id, ""]
    assert out["part"] == [True, "apps", "", app_id, "logs"]
    assert out["overview"] == [True, "apps", "", app_id, "overview"]
    for name in ("badPart", "listWithPart", "tooDeep", "query", "tasks"):
        assert out[name][0] is False, name


def test_the_addresses_written_are_the_ones_read():
    out = _router(
        """
const id = "0f6f2c1e-1111-4222-8333-444455556666";
out = [
  portalHashForRoute({ section: "apps" }),
  portalHashForRoute({ section: "apps", appsView: "templates" }),
  portalHashForRoute({ section: "apps", appsView: "nonsense" }),
  portalHashForRoute({ section: "apps", appId: id }),
  // The overview is the page itself: its address carries no part.
  portalHashForRoute({ section: "apps", appId: id, appTab: "overview" }),
  portalHashForRoute({ section: "apps", appId: id, appTab: "deployments" }),
  portalHashForRoute({ section: "apps", appId: id, appTab: "nonsense" }),
  // An app's page wins over a list left selected.
  portalHashForRoute({ section: "apps", appsView: "mine", appId: id, appTab: "logs" }),
];
"""
    )
    app_id = "0f6f2c1e-1111-4222-8333-444455556666"
    assert out == [
        "#/apps",
        "#/apps/templates",
        "#/apps",
        f"#/apps/{app_id}",
        f"#/apps/{app_id}",
        f"#/apps/{app_id}/deployments",
        f"#/apps/{app_id}",
        f"#/apps/{app_id}/logs",
    ]


def test_without_the_rail_button_there_is_no_apps_section():
    out = _router(
        """
dom.appsMenuBtn = null;
const route = parsePortalHashRoute("#/apps/mine");
out = [route.valid, route.section, isPortalRouteSectionAvailable("apps"), isPortalRouteSectionAvailable("tasks")];
"""
    )
    # A saved #/apps link on a Portal without Apps falls back to the default section.
    assert out == [False, "assistants", False, True]


# ------------------------------------------------- the links to the two guides


def _help_loader(body: str):
    """openHelpTopic from chat_ui.js against an htmx that queues like the real one.

    The real htmx sends one request at a time from the same source: a request
    made while another is in flight waits for it, and its promise resolves at
    once. ``landed`` is what is in the panel: "loading", "raw:<topic>" (the
    topic's markdown, not rendered) or "rendered:<topic>".
    """
    source = CHAT_JS.read_text(encoding="utf-8")
    return _node(
        f"""
{I18N_STUB}
let landed = "none";
let inFlight = false;
let failNext = false;
const queued = [];
const requests = [];
let renders = 0;
const content = {{
  dataset: {{}},
  set innerHTML(value) {{ landed = "loading"; }},
  get innerHTML() {{ return landed; }},
  querySelector() {{ return null; }},
}};
const dom = {{ workspaceDetailContent: content }};
const state = {{ activeNavSection: "help" }};
const document = {{ querySelectorAll: () => [] }};
let isApplyingPortalRoute = true;
const setMainView = () => {{}};
const syncMainHeader = () => {{}};
const renderIcons = () => {{}};
const commitPortalRoute = () => {{}};
const setActiveNavSection = async () => {{}};
const safe = (text) => String(text);
const renderHelpMarkdown = () => {{
  if (landed.startsWith("raw:")) {{ landed = "rendered:" + landed.slice(4); renders += 1; }}
}};
const send = (url) => new Promise((resolve, reject) => {{
  inFlight = true;
  requests.push(url);
  setTimeout(() => {{
    inFlight = false;
    if (failNext) {{ failNext = false; reject(new Error("offline")); }} else {{ landed = "raw:" + url.split("topic=")[1]; resolve(); }}
    const next = queued.shift();
    if (next) send(next).catch(() => {{}});
  }}, 5);
}});
const htmx = {{
  ajax(verb, url) {{
    if (inFlight) {{ queued.push(url); return Promise.resolve(); }}
    return send(url);
  }},
}};
let activeHelpTopicId = "";
let helpTopicLoad = null;
{_extract_js_function(source, "openHelpTopic")}
const settle = () => new Promise((resolve) => setTimeout(resolve, 40));
let out = null;
(async () => {{
{body}
  console.log(JSON.stringify(out));
}})();
"""
    )


def test_a_guide_opened_from_a_link_is_fetched_once_and_rendered():
    # A link to a guide reaches the router twice (popstate, then hashchange).
    out = _help_loader(
        """
  const route = { updateRoute: false, ensureSection: false };
  await Promise.all([openHelpTopic("building-apps", route), openHelpTopic("building-apps", route)]);
  await settle();
  const first = { requests: requests.length, renders, landed };
  await openHelpTopic("using-apps", route);
  await settle();
  out = { first, then: { requests: requests.length, renders, landed } };
"""
    )
    assert out["first"] == {"requests": 1, "renders": 1, "landed": "rendered:building-apps"}
    # The next guide is a load of its own.
    assert out["then"] == {"requests": 2, "renders": 2, "landed": "rendered:using-apps"}


def test_a_guide_that_failed_to_load_can_be_opened_again():
    out = _help_loader(
        """
  const route = { updateRoute: false, ensureSection: false };
  failNext = true;
  await Promise.all([openHelpTopic("building-apps", route), openHelpTopic("building-apps", route)]);
  await settle();
  const failed = { requests: requests.length, state: content.dataset.workspaceState };
  await openHelpTopic("building-apps", route);
  await settle();
  out = { failed, again: { requests: requests.length, landed, state: content.dataset.workspaceState } };
"""
    )
    assert out["failed"] == {"requests": 1, "state": "help-error"}
    assert out["again"] == {"requests": 2, "landed": "rendered:building-apps", "state": "help-topic"}


# ------------------------------------------------ the pages themselves, on a page
#
# Everything above runs functions that need no page. What follows runs the
# pages: apps.js on a page that is just enough (nodes that keep what is
# written to them, listeners the test fires, timers the test runs, requests
# the test answers by hand), so that an answer can be made to arrive late,
# a dialog can be confirmed after the page under it changed, and two things
# can be pressed at once.

FAKE_PAGE = r"""
const markup = [];
class FakeNode {
  constructor(label, attrs) {
    this.label = label;
    this._q = new Map();
    this._lists = {};
    this._attrs = {};
    this._fields = {};
    this._html = "";
    this.writes = 0;
    this.dataset = {};
    this.style = {};
    this.textContent = "";
    this.value = "";
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.isConnected = true;
    this.options = [];
    this.scrollHeight = 0;
    this.scrollTop = 0;
    this.clientHeight = 0;
    this.parentElement = null;
    const classes = new Set();
    this.classList = {
      add: (...names) => names.forEach((name) => classes.add(name)),
      remove: (...names) => names.forEach((name) => classes.delete(name)),
      contains: (name) => classes.has(name),
      toggle: (name, on) => {
        const want = on === undefined ? !classes.has(name) : Boolean(on);
        if (want) classes.add(name); else classes.delete(name);
        return want;
      },
    };
    Object.keys(attrs || {}).forEach((name) => this.setAttribute(name, attrs[name]));
  }
  set innerHTML(value) { this._html = String(value); this.writes += 1; if (this._html) markup.push(this._html); }
  get innerHTML() { return this._html; }
  // A form's fields by name, made when first asked for.
  get elements() {
    return new Proxy(this._fields, {
      get: (fields, name) => {
        if (typeof name !== "string") return undefined;
        if (!(name in fields)) fields[name] = new FakeNode(this.label + "." + name);
        return fields[name];
      },
    });
  }
  reset() { Object.keys(this._fields).forEach((name) => { this._fields[name].value = ""; }); }
  // One node per selector, the same one each time; lists are what the test put there.
  querySelector(selector) {
    if (!this._q.has(selector)) this._q.set(selector, new FakeNode(this.label + " " + selector));
    return this._q.get(selector);
  }
  querySelectorAll(selector) { return this._lists[selector] || []; }
  // By the node's own id and attributes; the selector's ancestors are taken on trust.
  matches(selector) {
    return String(selector).split(",").some((part) => {
      const last = part.trim().split(/\s+/).pop();
      const id = /#([\w-]+)/.exec(last);
      const wanted = last.match(/\[[^\]]+\]/g) || [];
      if (!id && !wanted.length) return false;
      if (id && this._attrs.id !== id[1]) return false;
      return wanted.every((item) => {
        const found = /^\[([\w-]+)(?:="([^"]*)")?\]$/.exec(item);
        return Boolean(found) && found[1] in this._attrs && (found[2] === undefined || this._attrs[found[1]] === found[2]);
      });
    });
  }
  closest(selector) { return this.matches(selector) ? this : null; }
  setAttribute(name, value) {
    this._attrs[name] = String(value);
    if (name === "id") this.id = String(value);
    if (name.indexOf("data-") === 0) this.dataset[name.slice(5).replace(/-([a-z])/g, (whole, letter) => letter.toUpperCase())] = String(value);
  }
  getAttribute(name) { return name in this._attrs ? this._attrs[name] : null; }
  removeAttribute(name) { delete this._attrs[name]; }
  hasAttribute(name) { return name in this._attrs; }
  addEventListener() {}
  removeEventListener() {}
  appendChild(node) { return node; }
  remove() {}
  focus() {}
  select() {}
  click() {}
  contains() { return true; }
  insertAdjacentHTML() {}
  getBoundingClientRect() { return { width: 0 }; }
}

const byId = new Map();
const found = {};
const listeners = {};
const document = {
  hidden: false,
  body: new FakeNode("body"),
  activeElement: null,
  addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
  getElementById(id) {
    if (!byId.has(id)) byId.set(id, new FakeNode("#" + id, { id: id }));
    return byId.get(id);
  },
  querySelector(selector) { return found[selector] || null; },
  querySelectorAll() { return []; },
  createElement(tag) { return new FakeNode("<" + tag + ">"); },
  createTextNode(text) { return { text: String(text) }; },
  contains() { return true; },
};

const timers = { next: 1, intervals: new Map(), timeouts: new Map() };
const toasts = [];
const opened = [];
const copied = [];
const dialogs = [];
// showConfirm and showPrompt: the question is kept, and answered when the test says so.
function ask(opts) {
  const entry = { opts: opts };
  entry.promise = new Promise((resolve) => { entry.resolve = resolve; });
  dialogs.push(entry);
  return entry.promise;
}
const window = {
  location: { origin: "http://portal.test", href: "http://portal.test/app#/apps", hash: "#/apps" },
  setInterval(fn) { const id = timers.next++; timers.intervals.set(id, fn); return id; },
  clearInterval(id) { timers.intervals.delete(id); },
  setTimeout(fn) { const id = timers.next++; timers.timeouts.set(id, fn); return id; },
  clearTimeout(id) { timers.timeouts.delete(id); },
  showToast(message, options) { toasts.push([(options && options.variant) || "", String(message)]); },
  showConfirm: ask,
  showPrompt: ask,
  open(url) { opened.push(url); },
};
const navigator = { clipboard: { writeText: async (text) => { copied.push(text); } } };

// What the server holds, as far as these pages ask: a test changes it before or between reads.
const world = { apps: {}, list: [], status: {}, releases: [], lines: [], stream: null };
const calls = [];
function fetch(url, init) {
  const call = { url: String(url), method: (init && init.method) || "GET", body: init && init.body ? JSON.parse(init.body) : undefined, done: false };
  call.promise = new Promise((resolve) => { call.resolve = resolve; });
  calls.push(call);
  return call.promise;
}
function answer(call, status, json) {
  call.done = true;
  call.resolve({
    ok: status >= 200 && status < 300,
    status: status,
    headers: { get: () => "application/json" },
    json: async () => json,
    text: async () => JSON.stringify(json),
    body: null,
  });
}
// The live log: an answer that stays open, to which the test sends lines.
function openStream(call) {
  const stream = { queue: [], waiting: null };
  stream.send = (lines) => {
    const text = lines.map((line) => "data: " + JSON.stringify({ line: line }) + "\n\n").join("");
    const chunk = { done: false, value: new TextEncoder().encode(text) };
    if (stream.waiting) { const give = stream.waiting; stream.waiting = null; give(chunk); } else stream.queue.push(chunk);
  };
  const reader = { read: () => new Promise((resolve) => { if (stream.queue.length) resolve(stream.queue.shift()); else stream.waiting = resolve; }) };
  call.done = true;
  call.resolve({ ok: true, status: 200, headers: { get: () => "text/event-stream" }, body: { getReader: () => reader } });
  world.stream = stream;
}
function app(id, extra) {
  const base = {
    id: id, slug: "slug-" + id.toLowerCase(), name: "App " + id, description: "", url: "http://apps.test/slug-" + id.toLowerCase() + "/",
    visibility: "private", effective_visibility: "private", owner: { username: "me", display_name: "Me" }, is_owner: true, can_manage: true,
    enabled: true, deployed: true, state: "running",
  };
  const manage = {
    repo_url: "https://github.com/acme/" + id, repo_web_url: "https://github.com/acme/" + id, fork_url: "https://github.com/acme/" + id + "/fork",
    repo_ref: "main", entry_dir: "", env: { OWNER: id }, llm_enabled: false, connectors_enabled: ["jira", "github"], connectors_write: [],
    default_daily_credits: 5, default_daily_tokens: 100000,
  };
  const over = Object.assign({}, world.apps[id] || {}, extra || {});
  return Object.assign(base, over, { manage: Object.assign(manage, over.manage || {}) });
}
// Answers what the pages read in the background, so a test holds only the calls it is about.
function route(call) {
  const url = call.url;
  let hit = null;
  if (call.method !== "GET") return false;
  if (/\/api\/apps\/settings$/.test(url)) answer(call, 200, { public_base_url: "http://apps.test", visibilities: ["private", "public", "platform"], connector_types: ["jira", "github", "jenkins"] });
  else if (/\/api\/apps\?scope=/.test(url)) answer(call, 200, { apps: world.list });
  else if (/\/api\/apps\/templates$/.test(url)) answer(call, 200, { templates: [] });
  else if (/\/repo-refs\?/.test(url)) answer(call, 200, { branches: ["main"], tags: [], default_branch: "main" });
  else if ((hit = /\/api\/apps\/([A-Z])$/.exec(url))) answer(call, 200, app(hit[1]));
  else if (/\/status$/.test(url)) answer(call, 200, Object.assign({ host_reachable: true, enabled: true, deployed: true, state: "running", deploying: false, active_release_id: null, rollback_available: true, commits_behind: 0 }, world.status));
  else if (/\/releases$/.test(url)) answer(call, 200, { releases: world.releases, rollback_release_id: null });
  else if ((hit = /\/releases\/(R\d)$/.exec(url))) answer(call, 200, world.releases.filter((item) => item.id === hit[1])[0] || { id: hit[1], status: "succeeded", steps: [] });
  else if (/\/template\/(tags|versions)$/.test(url)) answer(call, 200, { tags: [], versions: [] });
  else if (/\/logs\?tail=/.test(url)) answer(call, 200, { lines: world.lines });
  else if (/\/logs\/stream$/.test(url)) openStream(call);
  else if (/\/data$/.test(url)) answer(call, 200, { data_bytes: 2048, host_reachable: true });
  else if (/\/(events|usage)/.test(url)) answer(call, 200, { events: [] });
  else return false;
  return true;
}
const settle = async () => { for (let round = 0; round < 25; round += 1) await new Promise((resolve) => setImmediate(resolve)); };
async function drain(hold) {
  for (let round = 0; round < 12; round += 1) {
    await settle();
    const moved = calls.filter((call) => !call.done && !(hold && hold(call))).filter(route).length;
    if (!moved) break;
  }
  await settle();
}
// Calls by method and by how their address ends: all of them, or the ones not answered yet.
const sent = (method, tail) => calls.filter((call) => call.method === method && call.url.endsWith(tail));
const pending = (method, tail) => sent(method, tail).filter((call) => !call.done);
function fire(type, target, extra) {
  const event = Object.assign({ type: type, target: target, key: "", prevented: false, preventDefault() { this.prevented = true; } }, extra || {});
  (listeners[type] || []).forEach((fn) => fn(event));
  return event;
}
const el = (attrs, props) => Object.assign(new FakeNode("target", attrs), props || {});
const click = (attrs, props) => fire("click", el(attrs, props));
// Every timer that is waiting fires once (the status refresh, a followed deployment, a look at a repository).
async function runTimers() {
  Array.from(timers.timeouts.entries()).forEach(([id, fn]) => { timers.timeouts.delete(id); fn(); });
  await drain();
}
async function open(box, id, tab, hold) {
  const shown = window.EfpApps.showApp(box, { appId: id, tab: tab || "overview" });
  await drain(hold);
  await shown;
  await drain(hold);
}
"""


def _page(body: str):
    """Run ``body`` (the inside of an async function) against the pages; it must set ``out``."""
    return _node(
        I18N_STUB
        + FAKE_PAGE
        + APPS_JS.read_text(encoding="utf-8")
        + "\nconst Apps = window.EfpApps;\nlet out = null;\n(async () => {\n"
        + body
        + "\n  console.log(JSON.stringify(out));\n})().catch((error) => { console.error(error); process.exit(1); });\n"
    )


def test_an_answer_that_arrives_after_another_app_was_opened_stays_with_its_own_app():
    # The variables of A are saved (the host restarts the app, which takes a
    # while), the member opens B, and then the save answers.
    out = _page(
        r"""
  world.apps.B = { manage: { connectors_enabled: ["github", "jenkins"] } };
  const box = new FakeNode("detail");
  await open(box, "A", "settings");
  click({ "data-app-env-save": "" });
  await settle();
  const save = pending("PATCH", "/api/apps/A")[0];
  await open(box, "B", "settings", (call) => call === save);
  answer(save, 200, app("A", { name: "App A as saved" }));
  await settle();
  // What B's page does next is B's: its name over the page, its own list in what it sends.
  fire("change", el({ "data-app-connector": "github" }, { checked: false }));
  await settle();
  const next = pending("PATCH", "")[0];
  out = { heading: Apps.heading().title, next: [next.url, next.body] };
"""
    )
    assert out == {"heading": "App B", "next": ["/api/apps/B", {"connectors_enabled": ["jenkins"]}]}


def test_the_token_of_one_app_is_never_kept_on_another_apps_page():
    out = _page(
        r"""
  const box = new FakeNode("detail");
  await open(box, "A", "settings");
  click({ "data-app-token-reveal": "" });
  await settle();
  const first = pending("GET", "/api/apps/A/token")[0];
  await open(box, "B", "settings", (call) => call === first);
  answer(first, 200, { token: "efpa_token_of_A" });
  await settle();
  // Copy on B's page asks for B's token: A's is not lying there to be taken.
  click({ "data-app-token-copy": "" });
  await settle();
  answer(pending("GET", "/token")[0], 200, { token: "efpa_token_of_B" });
  await settle();
  out = { asked: sent("GET", "/token").map((call) => call.url), copied: copied };
"""
    )
    assert out == {"asked": ["/api/apps/A/token", "/api/apps/B/token"], "copied": ["efpa_token_of_B"]}


# What the member presses, on which part of the page, and what the dialog's action must send.
QUESTIONS = {
    "clear the data": ("data", 'click({ "data-app-data-clear": "" });', "DELETE /api/apps/A/data"),
    "reset the token": ("settings", 'click({ "data-app-token-reset": "" });', "POST /api/apps/A/token/reset"),
    "roll back": ("deployments", 'click({ "data-app-rollback": "" });', "POST /api/apps/A/rollback"),
    "remove a version": ("settings", 'click({ "data-app-template-remove": "V1", "data-tag": "v1" });', "DELETE /api/apps/A/template/versions/V1"),
    "turn the model provider on": ("settings", 'fire("change", el({ "data-app-llm": "" }, { checked: true }));', "PATCH /api/apps/A"),
    "let a connector write": ("settings", 'fire("change", el({ "data-app-connector-write": "jira" }, { checked: true }));', "PATCH /api/apps/A"),
    "open it to more people": ("settings", 'fire("change", el({ name: "app-audience" }, { value: "platform" }));', "PATCH /api/apps/A"),
    "upgrade": ("overview", 'click({ "data-app-upgrade": "V2" });', "POST /api/apps/A/upgrade"),
}


@pytest.mark.parametrize(("part", "press", "sends"), list(QUESTIONS.values()), ids=list(QUESTIONS))
def test_a_dialog_acts_on_the_app_it_asked_about_whatever_page_is_open_by_then(part, press, sends):
    # The dialog asks about A; Back or Forward puts B's page under it; then it is confirmed.
    out = _page(
        r"""
  world.apps.A = { template: { app_name: "Kit", version: { tag: "v1" }, update_available: { id: "V2", tag: "v2" } } };
  const box = new FakeNode("detail");
  await open(box, "A", "PART");
  PRESS
  await settle();
  const asked = dialogs[0].opts.title;
  await open(box, "B", "PART");
  const before = calls.length;
  const running = Promise.resolve().then(() => dialogs[0].opts.action(true)).then(() => "done", (error) => "failed: " + error.message);
  await settle();
  const made = calls.slice(before).filter((call) => call.method !== "GET");
  made.forEach((call) => answer(call, 200, call.method === "PATCH" ? app("A") : { id: "R9", status: "succeeded", steps: [], token: "efpa_new", ok: true }));
  await drain();
  out = { asked: asked, sent: made.map((call) => call.method + " " + call.url), result: await running, heading: Apps.heading().title };
""".replace("PART", part).replace("PRESS", press)
    )
    assert "App A" in out["asked"] or "v1" in out["asked"]
    assert out["sent"] == [sends]
    assert out["result"] == "done"
    # Nothing of A is drawn over B's page afterwards.
    assert out["heading"] == "App B"


def test_two_switches_pressed_one_after_the_other_are_two_saves_and_not_one_undone():
    out = _page(
        r"""
  const box = new FakeNode("detail");
  await open(box, "A", "settings");
  const section = box.querySelector('[data-app-settings="capabilities"]');
  const jira = el({ "data-app-connector": "jira" }, { checked: false });
  const github = el({ "data-app-connector": "github" }, { checked: false });
  // Its connector is off, so it is switched off for a reason of its own.
  const write = el({ "data-app-connector-write": "jenkins" }, { disabled: true });
  section._lists.input = [jira, github, write];
  fire("change", jira);
  await settle();
  const during = { github: [github.disabled, github.dataset.appHeld || ""], write: [write.disabled, write.dataset.appHeld || ""] };
  fire("change", github);   // a press that got through all the same
  await settle();
  const whileSaving = sent("PATCH", "/api/apps/A").length;
  answer(pending("PATCH", "/api/apps/A")[0], 200, app("A", { manage: { connectors_enabled: ["github"] } }));
  await settle();
  const after = { github: [github.disabled, github.dataset.appHeld || ""], write: write.disabled };
  fire("change", github);
  await settle();
  out = { during: during, whileSaving: whileSaving, after: after, bodies: sent("PATCH", "/api/apps/A").map((call) => call.body) };
"""
    )
    # While Jira is saved the other switches wait, marked as held by that wait.
    assert out["during"] == {"github": [True, "1"], "write": [True, ""]}
    assert out["whileSaving"] == 1
    # Free again, and the one that was off for its own reason is still off.
    assert out["after"] == {"github": [False, ""], "write": True}
    # The second save is made of what the first one left: both connectors end up off.
    assert out["bodies"] == [{"connectors_enabled": ["github"]}, {"connectors_enabled": []}]


def test_a_choice_that_cannot_be_saved_is_held_and_put_back():
    out = _page(
        r"""
  const box = new FakeNode("detail");
  await open(box, "A", "settings");
  const audience = box.querySelector('[data-app-settings="audience"]');
  const chosen = el({ name: "app-audience" }, { value: "platform", checked: true });
  audience._lists.input = [chosen];
  click({ "data-app-env-save": "" });   // a save holds the page
  await settle();
  const held = chosen.disabled;
  const drawn = audience.writes;
  fire("change", chosen);               // pressed all the same
  await settle();
  const whileHeld = sent("PATCH", "/api/apps/A").map((call) => call.body);
  answer(pending("PATCH", "/api/apps/A")[0], 200, app("A"));
  await settle();
  out = { held: held, putBack: audience.writes - drawn, whileHeld: whileHeld, free: !chosen.disabled, dialogs: dialogs.length };
"""
    )
    assert out == {"held": True, "putBack": 1, "whileHeld": [{"env": {}}], "free": True, "dialogs": 0}


def test_the_app_center_opened_twice_at_once_keeps_one_refresh_timer():
    # Back to #/apps fires popstate and hashchange, and the shell applies the address for both.
    out = _page(
        r"""
  const box = new FakeNode("detail");
  const first = Apps.showCenter(box, { view: "" });
  const second = Apps.showCenter(box, { view: "" });
  await drain();
  await Promise.all([first, second]);
  const running = timers.intervals.size;
  Apps.leave();
  out = { running: running, afterLeaving: timers.intervals.size };
"""
    )
    assert out == {"running": 1, "afterLeaving": 0}


def test_the_live_log_is_cut_a_batch_at_a_time_and_stays_where_it_is_read():
    out = _page(
        r"""
  world.lines = Array.from({ length: 4900 }, (unused, index) => "line " + index);
  const box = new FakeNode("detail");
  await open(box, "A", "logs");
  const log = box.querySelector("[data-app-log]");
  let shown = 4900;
  let rewrites = 0;
  let appended = 0;
  Object.defineProperty(log, "textContent", { configurable: true, get: () => "", set(value) { shown = String(value).split("\n").length - 1; rewrites += 1; } });
  log.appendChild = (node) => { shown += node.text.split("\n").length - 1; appended += 1; };
  Object.defineProperty(log, "scrollHeight", { configurable: true, get: () => shown * 18 });
  log.clientHeight = 600;
  const feed = async (count, top) => {
    const before = { rewrites: rewrites, appended: appended };
    log.scrollTop = top;
    world.stream.send(Array.from({ length: count }, (unused, index) => "streamed " + index));
    await settle();
    return { rewrites: rewrites - before.rewrites, appended: appended - before.appended, shown: shown, top: log.scrollTop };
  };
  const reading = 18 * 1000;   // the member is reading line 1000
  out = {
    toTheCap: await feed(100, reading),
    pastIt: await feed(499, reading),
    theCut: await feed(1, reading),
    on: await feed(499, 18 * 501),
    cutAtTheEnd: await feed(1, 5499 * 18 - 600),
  };
"""
    )
    # Up to the cap and for a while past it, a line is one more piece of text.
    assert out["toTheCap"] == {"rewrites": 0, "appended": 100, "shown": 5000, "top": 18000}
    assert out["pastIt"] == {"rewrites": 0, "appended": 499, "shown": 5499, "top": 18000}
    # Then the oldest go in one cut: written anew once, and the member is on the same lines as before.
    assert out["theCut"] == {"rewrites": 1, "appended": 0, "shown": 5000, "top": 18 * 1000 - 18 * 499}
    assert out["on"] == {"rewrites": 0, "appended": 499, "shown": 5499, "top": 18 * 501}
    # A member who was at the end is at the end after the cut too.
    assert out["cutAtTheEnd"] == {"rewrites": 1, "appended": 0, "shown": 5000, "top": 5000 * 18}


def test_a_release_the_member_picked_is_not_replaced_by_the_one_that_runs():
    out = _page(
        r"""
  world.status = { deploying: true, active_release_id: "R2" };
  world.releases = [{ id: "R2", status: "running", kind: "deploy", steps: [] }, { id: "R1", status: "succeeded", kind: "deploy", steps: [] }];
  const box = new FakeNode("detail");
  await open(box, "A", "deployments");
  const panel = box.querySelector("[data-app-release]");
  const followed = panel.dataset.releaseId;
  click({ "data-app-release-show": "R1" });
  await drain();
  const picked = panel.dataset.releaseId;
  await runTimers();   // the status is read again, and again
  await runTimers();
  const stays = panel.dataset.releaseId;
  // The deployment ends meanwhile: it is said and the history is read again, with R1 still shown.
  world.status = { deploying: false, active_release_id: null };
  world.releases[0].status = "succeeded";
  const reads = sent("GET", "/releases").length;
  await runTimers();
  const ended = { shown: panel.dataset.releaseId, said: toasts.map((item) => item[1]), historyReadAgain: sent("GET", "/releases").length > reads };
  // Picking a release that runs follows it to its end.
  world.status = { deploying: true, active_release_id: "R3" };
  world.releases.unshift({ id: "R3", status: "running", kind: "deploy", steps: [] });
  click({ "data-app-release-show": "R3" });
  await drain();
  world.releases[0].status = "failed";
  world.releases[0].error_summary = "The deployment failed at the step start.";
  world.status = { deploying: false, active_release_id: null };
  await runTimers();
  out = { followed: followed, picked: picked, stays: stays, ended: ended, then: { shown: panel.dataset.releaseId, said: toasts[toasts.length - 1] } };
"""
    )
    assert out["followed"] == "R2" and out["picked"] == "R1"
    assert out["stays"] == "R1"
    assert out["ended"] == {"shown": "R1", "said": ["App A is deployed."], "historyReadAgain": True}
    assert out["then"] == {"shown": "R3", "said": ["error", "The deployment failed at the step start."]}


def test_new_app_cannot_be_closed_or_started_over_while_the_app_is_being_created():
    out = _page(
        r"""
  const modal = document.getElementById("app-create-modal");
  modal.classList.add("hidden");
  const closers = [el({ "data-app-create-close": "" }), el({ "data-app-create-close": "" })];
  modal._lists["[data-app-create-close]"] = closers;
  const form = document.getElementById("app-create-form");
  Apps.openCreateDialog();
  await drain();
  form.elements.repo_url.value = "https://github.com/acme/tools";
  form.elements.ref.value = "main";
  form.elements.name.value = "Tools";
  fire("input", document.getElementById("app-create-repo"));
  await runTimers();   // the look at the repository
  answer(pending("POST", "/api/apps/inspect")[0], 200, { manifest: { name: "Tools", type: "fastapi" }, suggested_name: "Tools", suggested_slug: "tools", commit_sha: "0123456789abcdef", repo: "acme/tools", ref: "main", entry_dir: "" });
  await settle();
  fire("submit", form);
  await settle();
  const creating = pending("POST", "/api/apps")[0];
  const held = closers.map((item) => item.disabled);
  fire("keydown", document.body, { key: "Escape" });
  click({ "data-app-create-close": "" });
  fire("mousedown", modal);
  Apps.leave();
  const open = !modal.classList.contains("hidden");
  Apps.openCreateDialog();   // would start the dialog over
  fire("submit", form);
  await settle();
  const posts = sent("POST", "/api/apps").length;
  answer(creating, 201, { app: app("N") });
  await settle();
  out = { held: held, open: open, body: creating.body, posts: posts, closed: modal.classList.contains("hidden"), free: closers.map((item) => !item.disabled), said: toasts[toasts.length - 1] };
"""
    )
    assert out["held"] == [True, True]
    assert out["open"] is True
    assert out["body"] == {"repo_url": "https://github.com/acme/tools", "ref": "main", "entry_dir": "", "name": "Tools", "deploy": True}
    # One app was asked for, once.
    assert out["posts"] == 1
    # Created: now it closes by itself, and its ways out work again.
    assert out["closed"] is True and out["free"] == [True, True]
    assert out["said"] == ["success", "App N was created. Its first deployment is running."]


def test_a_refresh_of_the_administration_list_that_fails_says_so():
    out = _page(
        r"""
  const root = new FakeNode("admin", { "data-admin-apps-panel": "" });
  found["[data-admin-apps-panel]"] = root;
  fire("htmx:afterSwap", root);
  await settle();
  answer(pending("GET", "/admin/overview")[0], 200, { apps: [app("A")], host: null });
  await settle();
  const rows = root.querySelector("[data-admin-apps-rows]");
  click({ "data-admin-apps-refresh": "" });
  await settle();
  answer(pending("GET", "/admin/overview")[0], 502, { detail: "Bad gateway" });
  await settle();
  const refresh = { said: toasts.slice(), rowsKept: rows.innerHTML.indexOf("App A") >= 0 };
  // The first read has the list's own place to say that it failed, and needs no second voice.
  const fresh = new FakeNode("admin again", { "data-admin-apps-panel": "" });
  found["[data-admin-apps-panel]"] = fresh;
  fire("htmx:afterSwap", fresh);
  await settle();
  answer(pending("GET", "/admin/overview")[0], 500, { detail: "The database is away." });
  await settle();
  out = { refresh: refresh, first: { inPlace: fresh.querySelector("[data-admin-apps-state]").textContent, toasts: toasts.length } };
"""
    )
    assert out["refresh"] == {"said": [["error", "The list of apps could not be refreshed: Bad gateway"]], "rowsKept": True}
    assert out["first"] == {"inPlace": "The database is away.", "toasts": 1}


def test_only_an_address_to_go_to_becomes_a_link():
    out = _apps(
        r"""
out = [
  "https://apps.example.com/mind-map/", "http://127.0.0.1:8000/x", "/api/apps/1/icon?v=2", "#/help/building-apps", "  https://a.example/b  ",
  "javascript:alert(1)", " JavaScript:alert(1)", "java\tscript:alert(1)", "data:text/html,<b>x</b>", "vbscript:x",
  "ssh://git@github.com/acme/tools", "file:///etc/passwd", "http://", "", null, undefined,
].map(api.safeUrl);
"""
    )
    assert out[:5] == ["https://apps.example.com/mind-map/", "http://127.0.0.1:8000/x", "/api/apps/1/icon?v=2", "#/help/building-apps", "https://a.example/b"]
    assert out[5:] == [""] * 11


def test_no_page_makes_a_link_an_image_or_a_new_tab_of_another_kind_of_address():
    out = _page(
        r"""
  const hostile = { url: "javascript:alert(1)", icon_url: "javascript:alert(2)", manage: { repo_web_url: "javascript:alert(3)", fork_url: "javascript:alert(4)", repo_url: "javascript:alert(5)" } };
  world.apps.A = hostile;
  world.list = [app("A"), app("B", Object.assign({ can_manage: false }, hostile))];
  const box = new FakeNode("detail");
  const center = Apps.showCenter(box, { view: "mine" });
  await drain();
  await center;
  await open(box, "A", "overview");
  await open(box, "A", "settings");
  click({ "data-app-open-current": "" });
  click({ "data-app-copy-current": "" });
  click({ "data-admin-app-open": "javascript:alert(6)" });
  const root = new FakeNode("admin", { "data-admin-apps-panel": "" });
  found["[data-admin-apps-panel]"] = root;
  fire("htmx:afterSwap", root);
  await settle();
  answer(pending("GET", "/admin/overview")[0], 200, { apps: [app("A")], host: null });
  await settle();
  const drawn = markup.join("\n");
  const hostileDrawn = { opened: opened.slice(), copied: copied.slice(), links: (drawn.match(/(href|src|data-app-copy-link|data-admin-app-open)="\s*javascript:/gi) || []).length, pages: markup.length };
  // The same pages with addresses to go to: the links are there, and Open opens.
  world.apps.A = {};
  markup.length = 0;
  await open(box, "C", "overview");
  click({ "data-app-open-current": "" });
  const good = markup.join("\n");
  out = { hostile: hostileDrawn, good: { opened: opened.slice(), address: good.indexOf('href="http://apps.test/slug-c/"') >= 0, code: good.indexOf('href="https://github.com/acme/C"') >= 0 } };
"""
    )
    assert out["hostile"]["pages"] > 5
    assert out["hostile"]["links"] == 0 and out["hostile"]["opened"] == [] and out["hostile"]["copied"] == []
    assert out["good"] == {"opened": ["http://apps.test/slug-c/"], "address": True, "code": True}


def test_the_variables_are_read_from_the_single_app_answer_and_from_no_other():
    out = _page(
        r"""
  // The lists carry no variables, and neither do the answers to what is saved here.
  const bare = (extra) => { const item = app("A", extra); delete item.manage.env; return item; };
  world.list = [bare()];
  const box = new FakeNode("detail");
  const center = Apps.showCenter(box, { view: "mine" });
  await drain();
  await center;
  await open(box, "A", "settings");
  const body = box.querySelector("[data-app-tab-body]");
  const fromTheRead = body.innerHTML.indexOf('value="OWNER"') >= 0;
  // A switch is saved, and the page is drawn again from what it has.
  fire("change", el({ "data-app-connector": "jira" }, { checked: false }));
  await settle();
  answer(pending("PATCH", "/api/apps/A")[0], 200, bare({ manage: { connectors_enabled: ["github"] } }));
  await settle();
  await open(box, "A", "overview");
  await open(box, "A", "settings");
  const keptAfterASave = body.innerHTML.indexOf('value="OWNER"') >= 0;
  // The variables themselves are saved: what was sent is what the page has from then on.
  const env = box.querySelector('[data-app-settings="env"]');
  const row = new FakeNode("row");
  row.querySelector("[data-env-name]").value = "NEW_KEY";
  row.querySelector("[data-env-value]").value = "1";
  env._lists["[data-env-row]"] = [row];
  click({ "data-app-env-save": "" });
  await settle();
  const save = pending("PATCH", "/api/apps/A")[0];
  answer(save, 200, bare());
  await settle();
  out = { fromTheRead: fromTheRead, keptAfterASave: keptAfterASave, sent: save.body, drawn: [env.innerHTML.indexOf('value="NEW_KEY"') >= 0, env.innerHTML.indexOf('value="OWNER"') >= 0] };
"""
    )
    assert out == {"fromTheRead": True, "keptAfterASave": True, "sent": {"env": {"NEW_KEY": "1"}}, "drawn": [True, False]}


def test_usage_shows_the_period_chosen_last_whatever_order_the_answers_come_in():
    out = _page(
        r"""
  const usage = (call) => call.url.indexOf("/usage") >= 0;
  const days = (call) => Number(call.url.split("days=")[1]);
  const box = new FakeNode("detail");
  await open(box, "A", "usage", usage);
  const body = box.querySelector("[data-app-tab-body]");
  fire("change", el({ "data-app-usage-days": "" }, { value: "90" }));
  fire("change", el({ "data-app-usage-days": "" }, { value: "7" }));
  await settle();
  const asked = pending("GET", "").filter(usage);
  asked.filter((call) => days(call) === 7).forEach((call) => answer(call, 200, { days: 7 }));
  await settle();
  const drawn = body.innerHTML.indexOf('<option value="7" selected>') >= 0;
  // The answers for the periods the member moved on from arrive afterwards.
  asked.filter((call) => days(call) !== 7).forEach((call) => answer(call, 200, { days: days(call) }));
  await settle();
  out = { asked: asked.map(days), drawn: drawn, stays: body.innerHTML.indexOf('<option value="7" selected>') >= 0 };
"""
    )
    assert out == {"asked": [30, 90, 7], "drawn": True, "stays": True}


def test_a_save_of_the_variables_that_fails_keeps_saying_why():
    out = _page(
        r"""
  const box = new FakeNode("detail");
  await open(box, "A", "settings");
  const message = box.querySelector('[data-app-settings="env"]').querySelector("[data-app-env-msg]");
  click({ "data-app-env-save": "" });
  await settle();
  answer(pending("PATCH", "/api/apps/A")[0], 502, { detail: "The app host could not be reached." });
  await settle();
  const said = [message.textContent, message.classList.contains("is-error")];
  await runTimers();   // the status is read again: the line is not wiped by it
  const later = message.textContent;
  fire("input", el({ "data-env-name": "" }));
  out = { said: said, later: later, afterTyping: [message.textContent, message.classList.contains("is-error")] };
"""
    )
    assert out["said"] == ["The app host could not be reached.", True]
    assert out["later"] == "The app host could not be reached."
    # The rows were changed: what the last save was told no longer applies.
    assert out["afterTyping"] == ["", False]
