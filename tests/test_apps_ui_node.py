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
