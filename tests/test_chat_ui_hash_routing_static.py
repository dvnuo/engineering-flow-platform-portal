from pathlib import Path

from _js_extract_helpers import _extract_js_function, _extract_js_set_values


CHAT_UI = Path("app/static/js/chat_ui.js")


def _chat_ui_source() -> str:
    return CHAT_UI.read_text(encoding="utf-8")


def test_portal_hash_route_helpers_exist():
    js = _chat_ui_source()

    for function_name in [
        "parsePortalHashRoute",
        "portalHashForRoute",
        "currentPortalRouteFromState",
        "commitPortalRoute",
        "replacePortalRouteFromState",
        "clearPortalSectionDetailSelection",
        "portalSectionRoute",
        "openPortalSection",
        "applyPortalRouteFromHash",
        "applyPortalRoute",
    ]:
        _extract_js_function(js, function_name)


def test_section_only_helpers_are_wired_into_active_nav():
    js = _chat_ui_source()
    set_active_section = _extract_js_function(js, "setActiveNavSection")

    assert "preferSectionLanding = false" in set_active_section
    assert "clearPortalSectionDetailSelection(section)" in set_active_section
    assert "preferSectionLanding ? portalSectionRoute(section) : currentPortalRouteFromState()" in set_active_section


def test_portal_hash_route_sections_are_declared():
    js = _chat_ui_source()

    assert _extract_js_set_values(js, "PORTAL_ROUTE_SECTIONS") == {
        "assistants",
        "bundles",
        "tasks",
        "runtime-profiles",
        "automations",
    }


def test_refresh_all_prefers_assistant_hash_route_over_local_storage():
    js = _chat_ui_source()
    refresh_all = _extract_js_function(js, "refreshAll")

    assert 'await setActiveNavSection("assistants", {' not in refresh_all
    assert "parsePortalHashRoute(window.location.hash)" in refresh_all
    assert 'route.valid && route.section === "assistants" && route.agentId' in refresh_all
    assert "available.has(route.agentId)" in refresh_all
    assert "state.selectedAgentId = route.agentId" in refresh_all
    assert "state.selectedAgentId = null" in refresh_all
    assert "LAST_AGENT_STORAGE_KEY" in refresh_all
    assert "state.mineAgents[0].id" in refresh_all
    assert refresh_all.index('route.valid && route.section === "assistants" && route.agentId') < refresh_all.index(
        "state.mineAgents[0].id"
    )
    assert "applyPortalRouteFromHash({ replaceInvalid: true })" in refresh_all


def test_dom_content_loaded_applies_hash_route_instead_of_forcing_assistants():
    js = _chat_ui_source()
    start = js.index('document.addEventListener("DOMContentLoaded", async () => {')
    end = js.index('window.addEventListener("hashchange"', start)
    dom_loaded = js[start:end]
    after_refresh = dom_loaded[dom_loaded.index("await refreshAll") :]

    assert "await refreshAll({ preserveLayout: true, skipRouteApply: true })" in dom_loaded
    assert "await applyPortalRouteFromHash({ replaceInvalid: true })" in dom_loaded
    assert 'await setActiveNavSection("assistants"' not in after_refresh


def test_user_actions_commit_hash_routes():
    js = _chat_ui_source()

    select_agent = _extract_js_function(js, "selectAgentById")
    set_active_section = _extract_js_function(js, "setActiveNavSection")
    open_bundle = _extract_js_function(js, "openRequirementBundleInMain")
    open_task = _extract_js_function(js, "openTaskDetailInMain")
    open_runtime_profile = _extract_js_function(js, "openRuntimeProfileInMain")
    open_automation = _extract_js_function(js, "openAutomationRulePanel")

    assert 'commitPortalRoute({ section: "assistants", agentId })' in select_agent
    assert "commitPortalRoute(" in set_active_section
    assert "currentPortalRouteFromState()" in set_active_section
    assert 'commitPortalRoute({ section: "bundles", bundleRef })' in open_bundle
    assert 'commitPortalRoute({ section: "tasks", taskId })' in open_task
    assert 'commitPortalRoute({ section: "runtime-profiles", runtimeProfileId: profileId })' in open_runtime_profile
    assert 'commitPortalRoute({ section: "automations", automationRuleId: ruleId })' in open_automation


def test_rail_clicks_use_section_only_navigation():
    js = _chat_ui_source()
    bind_events = _extract_js_function(js, "bindEvents")

    assert 'dom.railAssistantsBtn?.addEventListener("click", () => openPortalSection("assistants"))' in bind_events
    assert 'dom.bundlesMenuBtn?.addEventListener("click", () => openPortalSection("bundles"))' in bind_events
    assert 'dom.tasksMenuBtn?.addEventListener("click", () => openPortalSection("tasks"))' in bind_events
    assert 'dom.runtimeProfilesMenuBtn?.addEventListener("click", () => openPortalSection("runtime-profiles"))' in bind_events
    assert 'dom.automationsMenuBtn?.addEventListener("click", () => openPortalSection("automations"))' in bind_events

    assert 'dom.bundlesMenuBtn?.addEventListener("click", () => setActiveNavSection("bundles"))' not in bind_events
    assert 'dom.tasksMenuBtn?.addEventListener("click", () => setActiveNavSection("tasks"))' not in bind_events
    assert 'dom.runtimeProfilesMenuBtn?.addEventListener("click", () => setActiveNavSection("runtime-profiles"))' not in bind_events
    assert 'dom.automationsMenuBtn?.addEventListener("click", () => setActiveNavSection("automations"))' not in bind_events


def test_return_from_task_detail_routes_back_to_tasks_section():
    js = _chat_ui_source()
    return_from_task = _extract_js_function(js, "returnFromTaskDetailToSidebar")

    if 'openPortalSection("tasks")' in return_from_task:
        return

    assert "state.selectedTaskId = null" in return_from_task
    assert 'commitPortalRoute({ section: "tasks" })' in return_from_task
    assert return_from_task.index("state.selectedTaskId = null") < return_from_task.index(
        'commitPortalRoute({ section: "tasks" })'
    )


def test_runtime_profiles_section_landing_does_not_auto_open_default_profile():
    js = _chat_ui_source()
    set_active_section = _extract_js_function(js, "setActiveNavSection")
    start = set_active_section.index('if (state.activeNavSection === "runtime-profiles"')
    end = set_active_section.index('if (state.activeNavSection === "tasks"', start)
    runtime_branch = set_active_section[start:end]
    prefer_branch = runtime_branch[
        runtime_branch.index("if (preferSectionLanding)") : runtime_branch.index("} else {")
    ]

    assert "preferSectionLanding" in runtime_branch
    assert "state.selectedRuntimeProfileId = null" in prefer_branch
    assert "Select a runtime profile from the left sidebar." in prefer_branch
    assert "loadRuntimeProfilePanelContent(targetProfileId" not in prefer_branch


def test_detail_row_clicks_do_not_write_section_route_before_detail_open():
    js = _chat_ui_source()
    render_bundles = _extract_js_function(js, "renderRequirementBundleList")
    render_tasks = _extract_js_function(js, "renderTaskNavList")
    open_bundle = _extract_js_function(js, "openRequirementBundleInMain")
    open_task = _extract_js_function(js, "openTaskDetailInMain")

    assert 'await setActiveNavSection("bundles", { toggleIfSame: false, updateRoute: false })' in render_bundles
    assert "await openRequirementBundleInMain(item.bundle_ref)" in render_bundles
    assert "await openTaskDetailInMain(task.id)" in render_tasks
    assert 'await setActiveNavSection("tasks", { toggleIfSame: false })' not in render_tasks
    assert 'commitPortalRoute({ section: "bundles", bundleRef })' in open_bundle
    assert 'commitPortalRoute({ section: "tasks", taskId })' in open_task


def test_browser_navigation_and_history_api_are_wired():
    js = _chat_ui_source()

    assert 'window.addEventListener("hashchange"' in js
    assert 'window.addEventListener("popstate"' in js
    assert "history.pushState" in js
    assert "history.replaceState" in js


def test_hash_routing_does_not_add_forbidden_runtime_paths():
    js = _chat_ui_source()

    assert ":4096" not in js
    assert '"/api/tasks"' not in js
