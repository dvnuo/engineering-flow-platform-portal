"""The Apps section of the Portal page: how it is wired into the shell, and what stays out while the feature is off.

Apps (APPS_PLAN.md) is a section like Tasks or Connectors: a rail button, a
hash route, a side-pane list and pages in the main area. Its pages are drawn
by app/static/js/apps.js from the owner API; chat_ui.js keeps the route, the
rail and the header. All of it is in the page only while APPS_ENABLED is on.

What is pinned here is the markup and the wiring. The behaviour of the pure
parts of apps.js is in test_apps_ui_node.py, and the sizes and colours were
looked at in a browser.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from _js_extract_helpers import _extract_js_function, _extract_js_set_values
from app.db import Base
from app.security.session_cookie import session_cookie_name
from app.models import User, UserAllowlistEntry

ROOT = Path(__file__).resolve().parents[1]
APP_HTML = (ROOT / "app/templates/app.html").read_text(encoding="utf-8")
DIALOG_HTML = (ROOT / "app/templates/partials/app_create_dialog.html").read_text(encoding="utf-8")
ADMIN_HTML = (ROOT / "app/templates/partials/admin_apps_panel.html").read_text(encoding="utf-8")
CHAT_JS = (ROOT / "app/static/js/chat_ui.js").read_text(encoding="utf-8")
APPS_JS = (ROOT / "app/static/js/apps.js").read_text(encoding="utf-8")
TOOLTIPS_JS = (ROOT / "app/static/js/tooltips.js").read_text(encoding="utf-8")
CSS = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")

# The three warnings the product owner asked for, word for word.
LLM_WARNING = "Visitors' use of this app consumes your Copilot and may slow down your own chats."
CONNECTOR_WARNING = "Visitors will read these systems with your connector's account."


def _guarded_blocks(html: str) -> str:
    """Everything the template renders only while Apps is enabled."""
    return "".join(re.findall(r"\{% if apps_enabled %\}(.*?)\{% endif %\}", html, flags=re.S))


# ------------------------------------------------------------------- the rail


def test_the_rail_entry_sits_under_assistants_in_the_top_group():
    top = APP_HTML[APP_HTML.index('<div class="portal-rail-top">') : APP_HTML.index('<div class="portal-rail-bottom">')]
    bottom = APP_HTML[APP_HTML.index('<div class="portal-rail-bottom">') : APP_HTML.index("</nav>")]

    assert top.index('id="rail-assistants-btn"') < top.index('id="apps-menu-btn"') < top.index('id="tasks-menu-btn"')
    button = top[top.index('<button id="apps-menu-btn"') : top.index("</button>", top.index('<button id="apps-menu-btn"'))]
    assert 'class="portal-rail-btn"' in button and 'data-lucide="blocks"' in button
    assert "title=\"{{ t('Apps') }}\"" in button
    # The bottom group is Connectors and the account button, and stays so.
    assert "apps-menu-btn" not in bottom
    assert bottom.count('class="portal-rail-btn') == 2


def test_the_rail_button_has_a_hint_and_opens_the_section():
    assert '["#apps-menu-btn", ' in TOOLTIPS_JS
    bind_events = _extract_js_function(CHAT_JS, "bindEvents")
    assert 'dom.appsMenuBtn?.addEventListener("click", () => openPortalSection("apps"))' in bind_events
    set_active = _extract_js_function(CHAT_JS, "setActiveNavSection")
    assert 'dom.appsMenuBtn?.classList.toggle("is-active", state.activeNavSection === "apps")' in set_active
    assert 'dom.appsNavSection?.classList.toggle("hidden", state.activeNavSection !== "apps")' in set_active


# ---------------------------------------------------------------- the routes


def test_apps_is_a_routable_section_with_lists_and_app_pages():
    assert "apps" in _extract_js_set_values(CHAT_JS, "PORTAL_ROUTE_SECTIONS")
    assert _extract_js_set_values(CHAT_JS, "PORTAL_APPS_VIEWS") == {"mine", "available", "templates"}
    assert _extract_js_set_values(CHAT_JS, "PORTAL_APP_TABS") == {
        "overview",
        "deployments",
        "logs",
        "usage",
        "settings",
        "data",
        "activity",
    }
    for name in ("openAppsInMain", "openAppsCenterInMain", "openAppInMain", "markAppsNavRows"):
        _extract_js_function(CHAT_JS, name)

    parse = _extract_js_function(CHAT_JS, "parsePortalHashRoute")
    assert 'if (encodedParts.length > (section === "apps" ? 3 : 2)) return fallback;' in parse
    apply_route = _extract_js_function(CHAT_JS, "applyPortalRoute")
    assert 'if (route.section === "apps") {' in apply_route
    assert "await openAppsInMain({ ensureSection: false, updateRoute: false });" in apply_route


def test_the_section_exists_only_where_its_rail_button_does():
    available = _extract_js_function(CHAT_JS, "isPortalRouteSectionAvailable")
    assert 'if (section === "apps") return Boolean(dom.appsMenuBtn);' in available
    set_active = _extract_js_function(CHAT_JS, "setActiveNavSection")
    assert 'if (section === "apps" && !dom.appsMenuBtn) return;' in set_active
    open_apps = _extract_js_function(CHAT_JS, "openAppsInMain")
    assert "if (!dom.appsMenuBtn || !dom.workspaceDetailContent) return;" in open_apps


def test_the_shell_and_the_pages_meet_in_two_small_interfaces():
    # What apps.js asks of the shell, and what the shell asks of apps.js.
    shell = CHAT_JS[CHAT_JS.index("window.portalAppsShell = {") :]
    shell = shell[: shell.index("};")]
    for name in ("openCenter", "openApp", "markView", "syncHeader"):
        assert f"{name}:" in shell, name
    exported = APPS_JS[APPS_JS.index("window.EfpApps = {") :]
    exported = exported[: exported.index("};")]
    for name in ("showCenter", "showApp", "heading", "leave", "openCreateDialog"):
        assert f"{name}:" in exported, name
    # Leaving the section ends the status refresh, a followed deployment and the live log.
    set_active = _extract_js_function(CHAT_JS, "setActiveNavSection")
    assert "window.EfpApps?.leave?.();" in set_active
    leave = _extract_js_function(APPS_JS, "leave")
    assert "stopCenter();" in leave and "stopPage();" in leave


def test_the_header_names_the_open_app_and_carries_its_actions():
    sync_header = _extract_js_function(CHAT_JS, "syncMainHeader")
    assert 'state.activeNavSection === "apps"' in sync_header
    assert "appsUi.heading()" in sync_header
    toolbars = _extract_js_function(CHAT_JS, "syncOverviewToolbars")
    assert 'document.getElementById("header-apps-actions")' in toolbars
    assert 'document.getElementById("header-app-actions")' in toolbars
    for marker in ('id="header-apps-actions" class="portal-header-group hidden"', 'id="header-app-actions" class="portal-header-group hidden"'):
        assert marker in APP_HTML, marker
    for hint in ('["#header-create-app", ', '["#header-app-open", ', '["#header-app-copy-link", '):
        assert hint in TOOLTIPS_JS, hint


# ------------------------------------------------------------- feature off


def test_everything_of_apps_in_the_shell_is_behind_the_flag():
    guarded = _guarded_blocks(APP_HTML)
    for marker in (
        'id="apps-menu-btn"',
        'id="apps-nav-section"',
        'id="header-apps-actions"',
        'id="header-app-actions"',
        'id="admin-apps-nav-item"',
        'include "partials/app_create_dialog.html"',
        "static_url('js/apps.js')",
    ):
        assert marker in guarded, f"{marker} must be inside an apps_enabled block"
        assert APP_HTML.count(marker) == 1, marker
    # The administration entry is a system administrator's.
    admin_item = APP_HTML.index('id="admin-apps-nav-item"')
    assert APP_HTML.rfind("{% if is_system_admin %}", 0, admin_item) > APP_HTML.rfind("{% if is_administrator %}", 0, admin_item)
    # The two guides about Apps are listed only where Apps is on.
    assert '{% if apps_enabled or topic.id not in ("using-apps", "building-apps") %}' in APP_HTML


def _database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)()


def _client(monkeypatch, *, role: str, apps_enabled: bool):
    from app.main import app
    import app.web as web_module
    from app.config import get_settings
    from app.services.auth_service import issue_session_token

    db = _database()
    user = User(username="casey", nickname="Casey", password_hash="hash", role=role, is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(UserAllowlistEntry(username="casey", role=role, is_active=True))
    db.commit()
    monkeypatch.setattr(web_module, "SessionLocal", lambda: db)
    monkeypatch.setattr(get_settings(), "apps_enabled", apps_enabled)
    client = TestClient(app)
    client.cookies.set(session_cookie_name(), issue_session_token(user.id))
    return client, db


def _get(monkeypatch, path: str, *, role: str = "user", apps_enabled: bool = True):
    client, db = _client(monkeypatch, role=role, apps_enabled=apps_enabled)
    try:
        return client.get(path, follow_redirects=False)
    finally:
        db.close()


def test_the_page_carries_nothing_of_apps_while_the_feature_is_off(monkeypatch):
    response = _get(monkeypatch, "/app", role="admin", apps_enabled=False)
    assert response.status_code == 200
    html = response.text
    assert 'data-apps-enabled="false"' in html
    for marker in ("apps-menu-btn", "apps-nav-section", "header-create-app", "app-create-modal", "admin-apps-nav-item", "js/apps.js", "data-apps-view-nav"):
        assert marker not in html, marker
    # The guides about Apps are not offered either.
    assert 'data-help-topic-nav="using-apps"' not in html
    assert 'data-help-topic-nav="building-apps"' not in html
    assert 'data-help-topic-nav="getting-started"' in html


def test_the_page_carries_the_section_while_the_feature_is_on(monkeypatch):
    response = _get(monkeypatch, "/app", role="user", apps_enabled=True)
    assert response.status_code == 200
    html = response.text
    assert 'data-apps-enabled="true"' in html
    for marker in ('id="apps-menu-btn"', 'id="apps-nav-section"', 'id="header-create-app"', 'id="app-create-modal"', "/static/js/apps.js"):
        assert marker in html, marker
    assert html.count('data-apps-view-nav="') == 3
    # apps.js comes after chat_ui.js, whose shell it calls.
    assert html.index("/static/js/chat_ui.js") < html.index("/static/js/apps.js")
    # A member gets no administration entry.
    assert "admin-apps-nav-item" not in html
    assert 'data-help-topic-nav="using-apps"' in html
    assert 'data-help-topic-nav="building-apps"' in html


def test_a_system_administrator_gets_the_administration_entry(monkeypatch):
    html = _get(monkeypatch, "/app", role="admin", apps_enabled=True).text
    assert 'id="admin-apps-nav-item"' in html
    assert 'data-admin-panel-url="/app/admin/apps/panel"' in html


@pytest.mark.parametrize(
    ("role", "apps_enabled", "status"),
    [("admin", True, 200), ("user", True, 403), ("group_admin", True, 403), ("admin", False, 404)],
)
def test_the_administration_panel_is_for_system_administrators(monkeypatch, role, apps_enabled, status):
    response = _get(monkeypatch, "/app/admin/apps/panel", role=role, apps_enabled=apps_enabled)
    assert response.status_code == status
    if status == 200:
        assert 'id="admin-apps-panel"' in response.text
        assert "data-admin-apps-panel" in response.text


def test_the_administration_panel_needs_a_session():
    from app.main import app
    from app.config import get_settings

    settings = get_settings()
    before = settings.apps_enabled
    settings.apps_enabled = True
    try:
        assert TestClient(app).get("/app/admin/apps/panel", follow_redirects=False).status_code == 401
    finally:
        settings.apps_enabled = before


# -------------------------------------------------------------- the app center


def test_the_app_center_has_three_lists_and_one_search_box():
    section = APP_HTML[APP_HTML.index('<section id="apps-nav-section"') :]
    section = section[: section.index("</section>")]
    assert re.findall(r'data-apps-view-nav="(\w+)"', section) == ["mine", "available", "templates"]
    assert re.findall(r'data-apps-view-count="(\w+)"', section) == ["mine", "available", "templates"]

    frame = _extract_js_function(APPS_JS, "drawCenterFrame")
    assert frame.count("data-apps-search") == 1
    refresh = _extract_js_function(APPS_JS, "refreshCenter")
    for call in ('request(API + "?scope=mine")', 'request(API + "?scope=available")', 'request(API + "/templates")'):
        assert call in refresh, call
    # Mine: what needs attention first. Available: what was used last first.
    assert "sortMine(list)" in refresh and "sortAvailable(list)" in refresh


def test_an_app_opens_in_a_new_tab_and_is_never_embedded():
    card = _extract_js_function(APPS_JS, "appCardHtml")
    assert 'target="_blank" rel="noopener"' in card
    assert "data-app-copy-link" in card
    # Only an app its viewer manages leads to the app's page.
    assert "if (app.can_manage) {" in card and "data-app-manage" in card
    assert "<iframe" not in APPS_JS and "createElement(\"iframe\")" not in APPS_JS
    # The header's Open goes through the one function that opens a tab, and
    # that one opens only an address safeUrl lets through.
    assert "if (page.app) openInNewTab(page.app.url);" in APPS_JS
    opener = _extract_js_function(APPS_JS, "openInNewTab")
    assert "const url = safeUrl(value);" in opener and 'if (url) window.open(url, "_blank", "noopener");' in opener
    assert APPS_JS.count("window.open(") == 1


def test_every_address_from_the_server_is_checked_before_it_is_a_link():
    # http, https, or inside the Portal: nothing else becomes a link, an image or a new tab.
    safe = _extract_js_function(APPS_JS, "safeUrl")
    assert 'return scheme === "http:" || scheme === "https:" ? text : "";' in safe
    checked = {"url", "icon", "repoLink", "forkLink"}
    for name in checked:
        assert f"const {name} = safeUrl(" in APPS_JS, name
    used = re.findall(r"""(?:href|src|data-app-copy-link|data-admin-app-open)="' \+ esc\(([\w.]+)\)""", APPS_JS)
    assert len(used) >= 10
    assert set(used) <= checked, sorted(set(used) - checked)
    # What a row menu carries back is checked again where it is opened.
    assert "openInNewTab(el.dataset.adminAppOpen);" in APPS_JS


def test_every_empty_list_says_what_to_do_next():
    empty = _extract_js_function(APPS_JS, "centerEmptyHtml")
    assert "data-app-create-open" in empty
    assert 'data-apps-view-open="templates"' in empty
    assert 'href="#/help/building-apps"' in empty
    for view in ('view === "available"', 'view === "templates"'):
        assert view in empty, view


# ------------------------------------------------------------------- New app


def test_the_dialog_asks_for_four_things_and_nothing_else():
    repository = DIALOG_HTML[DIALOG_HTML.index('data-app-create-panel="repository"') : DIALOG_HTML.index('data-app-create-panel="template"')]
    fields = re.findall(r"<(?:input|select|textarea)\b[^>]*\bname=\"(\w+)\"", repository)
    assert fields == ["repo_url", "ref", "entry_dir", "name"]
    template = DIALOG_HTML[DIALOG_HTML.index('data-app-create-panel="template"') :]
    assert re.findall(r"<(?:input|select|textarea)\b[^>]*\bname=\"(\w+)\"", template) == ["template_app_id", "version_id", "template_name"]
    # One button, with a fixed label, for both sources.
    assert DIALOG_HTML.count('type="submit"') == 1
    assert '{{ t("Create and deploy") }}' in DIALOG_HTML


def test_the_dialog_looks_at_the_repository_as_soon_as_it_can():
    schedule = _extract_js_function(APPS_JS, "scheduleInspect")
    assert "plausibleRepoUrl(repo)" in schedule and "plausibleEntryDir(" in schedule
    assert "window.setTimeout(runInspect" in schedule
    run = _extract_js_function(APPS_JS, "runInspect")
    assert 'request(API + "/repo-refs?repo_url="' in run
    assert 'request(API + "/inspect", { method: "POST"' in run
    # The default branch and the manifest's name are filled in, not asked for.
    assert "refField.value = refs.default_branch;" in run
    assert "nameField.value = inspection.suggested_name" in run
    # The button works only for what was just looked at.
    submit = _extract_js_function(APPS_JS, "syncCreateSubmit")
    assert "Boolean(dialog.inspection) && dialog.checkedKey === inspectKey()" in submit


def test_the_dialog_says_how_to_fix_each_kind_of_problem():
    problem = _extract_js_function(APPS_JS, "inspectProblem")
    for code in ("repo_not_readable", "not_listed_as_owner", "manifest_invalid", "manifest_missing", "ref_not_found", "repo_url_invalid"):
        assert f'code === "{code}"' in problem, code
    # The account to invite comes from the error, or from the settings.
    assert "params.account || (settings && settings.service_account)" in problem
    # The username to add to the manifest's owners.
    assert """problem.snippet = username ? '"' + username + '"' : "";""" in problem
    drawn = _extract_js_function(APPS_JS, "drawInspectProblem")
    assert "data-app-copy-text" in drawn and "data-app-inspect-again" in drawn


def test_the_dialog_is_a_steady_layer_whose_lists_open_only_when_asked():
    # Its place in tests/test_steady_layers.py is the frame; here, the fields.
    assert 'list="app-create-refs"' in DIALOG_HTML and '<datalist id="app-create-refs">' in DIALOG_HTML
    assert "autofocus" not in DIALOG_HTML
    opened = _extract_js_function(APPS_JS, "openCreateDialog")
    # Focus goes to a plain text field: nothing drops down as the dialog opens.
    assert 'createField(templateId ? "template_name" : "repo_url")' in opened
    assert ".showPicker(" not in APPS_JS
    # The result box scrolls on its own; the fields keep their width.
    assert "#app-create-modal .portal-app-create-panel > .portal-app-inspect {" in CSS


# ---------------------------------------------------------------- an app's page


def test_the_page_has_its_seven_parts():
    tabs = APPS_JS[APPS_JS.index("const APP_TABS = [") :]
    tabs = tabs[: tabs.index("];")]
    assert re.findall(r'key: "(\w+)"', tabs) == ["overview", "deployments", "logs", "usage", "settings", "data", "activity"]
    select = _extract_js_function(APPS_JS, "selectTab")
    for draw in ("drawOverview()", "drawDeployments()", "drawLogs()", "drawUsage()", "drawSettings()", "drawData()", "drawActivity()"):
        assert draw in select, draw


def test_controls_keep_their_names_and_show_state_by_tint():
    overview = _extract_js_function(APPS_JS, "drawOverview")
    for verb in ('lifecycleButtonHtml("start"', 'lifecycleButtonHtml("restart"', 'lifecycleButtonHtml("stop"'):
        assert verb in overview, verb
    pause = _extract_js_function(APPS_JS, "toggleLogPause")
    assert 'button.setAttribute("aria-pressed"' in pause
    assert "textContent" not in pause, "Pause keeps its label; aria-pressed and the tint say it is held"
    token = _extract_js_function(APPS_JS, "tokenSectionHtml")
    assert 'esc(t("Reveal"))' in token and "aria-pressed" in token


def test_the_three_warnings_are_said_at_the_moment_of_the_action():
    assert f't("{LLM_WARNING}")' in _extract_js_function(APPS_JS, "llmWarning")
    assert f't("{CONNECTOR_WARNING}")' in _extract_js_function(APPS_JS, "connectorWarning")

    capability = _extract_js_function(APPS_JS, "changeCapability")
    # Turning the model provider on, and turning a connector on, ask first.
    assert "message: llmWarning()" in capability
    assert "message: connectorWarning()" in capability
    # The dialog that asks is also the one that saves: nothing is changed
    # unless it is confirmed, and it stays open until the change is saved.
    assert "await window.showConfirm(Object.assign(question, {" in capability
    asked = capability.split("if (question) {", 1)[1].split("const saved = savingControl(input);", 1)[0]
    assert "action: function () { return save(); }," in asked and "return;" in asked
    # One place sends the change, for the page that asked, and holds the other
    # switches while it does. It is reached from the dialog, or directly when
    # nothing has to be asked.
    assert capability.count("saveHeld(owner, function () { return patchApp(owner, changes); })") == 1
    assert capability.count("return save();") == 1 and capability.count("await save();") == 1

    audience = _extract_js_function(APPS_JS, "changeAudience")
    # Letting more people in repeats what their use costs the owner.
    assert "if (audienceWidens(previous, next)) {" in audience
    assert "if (manage.llm_enabled) warnings.push(llmWarning());" in audience
    # With a warning to give, the audience is saved by the dialog and by nothing else.
    assert "action: async function () { updated = await save(); }," in audience
    assert audience.count("await save()") == 2
    direct = audience.split("if (!asked) {", 1)[1]
    assert "updated = await save();" in direct
    assert audience.index("window.showConfirm(") < audience.index("if (!asked) {")


def test_destructive_actions_ask_in_words_that_say_what_happens():
    reset = _extract_js_function(APPS_JS, "resetToken")
    assert "the app restarts with the new one" in reset and "danger: true" in reset
    delete = _extract_js_function(APPS_JS, "deleteApp")
    # A typed confirmation: the app's address name.
    assert "window.showPrompt(" in delete and "required: true" in delete
    assert "validate: function (typed) {" in delete
    assert 'return String(typed).trim() === app.slug ? ""' in delete
    # Nothing is sent before the name was typed right: the request is the dialog's action.
    assert delete.index("validate: function (typed) {") < delete.index("action: async function () {") < delete.index("await request(")
    clear = _extract_js_function(APPS_JS, "clearData")
    assert "window.showConfirm(" in clear and "cannot be brought back" in clear
    takedown = _extract_js_function(APPS_JS, "adminTakeDown")
    # Asked through adminAct, which keeps the dialog open while the app is taken down.
    assert "adminAct(window.showPrompt, {" in takedown and "required: true" in takedown


def test_the_token_is_read_only_when_asked_for():
    # Reading it is recorded, so drawing Settings must not fetch it.
    settings = _extract_js_function(APPS_JS, "drawSettings")
    assert "fetchToken" not in settings and "/token" not in settings
    assert "await fetchToken(owner)" in _extract_js_function(APPS_JS, "revealToken")
    used = _extract_js_function(APPS_JS, "tokenUsedText")
    assert "token_last_used_at" in used and "token_last_used_source" in used


def test_the_token_is_forgotten_when_the_page_is_left():
    # It was read for this visit of the page: leaving forgets it, and one that
    # arrives after the member left is kept nowhere.
    stop = _extract_js_function(APPS_JS, "stopPage")
    assert 'page.token_value = "";' in stop and "page.tokenShown = false;" in stop
    fetch = _extract_js_function(APPS_JS, "fetchToken")
    assert fetch.index("if (!pageLive(owner.token)) return value;") < fetch.index("owner.token_value = value;")
    assert "page." not in fetch, "the token is kept on the page that asked, never on `page`"


def _action_bodies(source: str) -> list[str]:
    """The body of every `action:` function handed to a dialog."""
    bodies = []
    for match in re.finditer(r"action: (?:async )?function \([^)]*\) \{", source):
        depth = 0
        for index in range(match.end() - 1, len(source)):
            if source[index] == "{":
                depth += 1
            elif source[index] == "}":
                depth -= 1
                if depth == 0:
                    bodies.append(source[match.end() : index])
                    break
    return bodies


def test_work_that_waits_belongs_to_the_page_it_started_on():
    # By the time a request answers or a dialog is confirmed, `page` may be
    # another app's page. So nothing a dialog runs reads it, and the two
    # functions that keep an answer keep it on the page they were given.
    actions = _action_bodies(APPS_JS)
    assert len(actions) >= 9
    for body in actions:
        assert not re.search(r"(?<![\w.])page\.", body), " ".join(body.split())[:120]
    patch = _extract_js_function(APPS_JS, "patchApp")
    assert "async function patchApp(owner, changes) {" in patch
    assert "request(appPath(owner.appId), { method: \"PATCH\", json: changes })" in patch
    assert "if (pageLive(owner.token)) syncHeader();" in patch and "page.app" not in patch
    assert "owner.app = app;" in _extract_js_function(APPS_JS, "keepApp")
    # Every function that asks before it acts takes its page before it asks.
    for name in ("clearData", "resetToken", "runRollback", "removeTemplateVersion", "confirmUpgrade", "changeAudience", "changeCapability"):
        body = _extract_js_function(APPS_JS, name)
        assert body.index("const owner = page;") < body.index("window.showConfirm("), name
        assert "page.appId" not in body, name


def test_a_deployment_is_followed_and_the_log_is_live():
    poll = _extract_js_function(APPS_JS, "pollRelease")
    assert "releaseIsActive(release)" in poll and "RELEASE_POLL_MS" in poll
    assert '"/log?offset=" + owner.releaseLogOffset' in _extract_js_function(APPS_JS, "pullReleaseLog")
    stream = _extract_js_function(APPS_JS, "openLogStream")
    assert 'appPath(page.appId, "/logs/stream")' in stream and "createSseParser(onLogLine)" in stream
    # The rollback is the dialog's action: it is sent for the page that asked.
    assert 'request(appPath(owner.appId, "/rollback"), { method: "POST" })' in _extract_js_function(APPS_JS, "runRollback")
    # Details on a release is the member's pick; the running one does not take its place.
    assert "pickRelease(el.dataset.appReleaseShow);" in APPS_JS
    changed = _extract_js_function(APPS_JS, "onStatusChanged")
    assert "if (!page.pickedReleaseId && !(page.following && page.shownReleaseId === active)) followRelease(active);" in changed


# --------------------------------------------------------- Administration > Apps


def test_the_administration_list_follows_the_user_management_table():
    for marker in (
        'class="portal-panel-stack portal-admin-users portal-admin-apps"',
        'class="portal-admin-member-toolbar"',
        'class="portal-data-table-wrap portal-admin-member-table-wrap"',
        'class="portal-data-table portal-admin-member-table portal-admin-apps-table"',
        'class="portal-admin-actions-cell"',
    ):
        assert marker in ADMIN_HTML, marker
    headers = re.findall(r'data-sort-key="(\w+)"', ADMIN_HTML)
    assert headers == ["name", "owner", "audience", "state", "requests", "credits", "memory", "data"]
    # Row actions live in the shared row menu (a popover, placed by admin_users.js).
    row = _extract_js_function(APPS_JS, "adminRowHtml")
    assert "data-admin-row-menu-trigger" in row and 'class="portal-admin-row-menu" popover role="menu"' in row
    for action in ("data-admin-app-takedown", "data-admin-app-restore", "data-admin-app-approve", "data-admin-app-transfer"):
        assert action in row, action
    host = _extract_js_function(APPS_JS, "adminHostHtml")
    for part in ("host.reachable", "info.apps_running", "info.memory", "info.cpu_percent", "info.disk", "info.isolation_mode"):
        assert part in host, part


def test_the_administration_panel_is_filled_when_it_is_swapped_in():
    assert 'document.addEventListener("htmx:afterSwap"' in APPS_JS
    load = _extract_js_function(APPS_JS, "loadAdminApps")
    assert 'request(API + "/admin/overview")' in load
    headings = CHAT_JS[CHAT_JS.index("const ADMIN_PANEL_HEADINGS = {") :]
    headings = headings[: headings.index("\n};")]
    assert '"apps": {' in headings


# ------------------------------------------------------------------- the styles


def test_the_styles_stand_on_their_own_classes():
    block = CSS[CSS.index("/* -- Apps ---") :]
    rules = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    # Tailwind's runtime sheet lands after app.css: a rule that names no class
    # of its own would lose to the preflight.
    for selector_list in re.findall(r"(?m)^([^{}@/\n][^{}@]*)\{", rules):
        for selector in selector_list.split(","):
            selector = selector.strip()
            if not selector or selector in {"to", "from"} or selector.endswith("%"):
                continue
            assert re.search(r"[.#\[]", selector), f"{selector!r} names no class, id or attribute"
    # The tab strip, the log and the cards set their own line-height.
    for selector in (".portal-app-tabs > button", ".portal-app-log", ".portal-app-card-copy strong", ".portal-app-card-copy small"):
        rule = rules[rules.index("\n" + selector + " {") :]
        assert "line-height:" in rule[: rule.index("}")], selector
