"""Regression: landing on the Assistants section loads the selected agent's session.

Navigating into the Assistants section (rail/menu -> openPortalSection ->
setActiveNavSection with preferSectionLanding) previously did not load the
already-selected agent's last session, so the chat stayed empty until the
agent row was clicked again. Every other section has a section-landing refresh
block; assistants was missing one.
"""

from pathlib import Path

from _js_extract_helpers import _extract_js_function


def _chat_ui_js_source() -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / "app" / "static" / "js" / "chat_ui.js").read_text(encoding="utf-8")


def test_set_active_nav_section_loads_selected_agent_session_on_assistants_landing():
    js_source = _chat_ui_js_source()
    fn = _extract_js_function(js_source, "setActiveNavSection")

    # The assistants section-landing block must exist and load the selected
    # agent's session via syncSelectedAgentState.
    assert 'state.activeNavSection === "assistants"' in fn
    assert "syncSelectedAgentState()" in fn

    # It must be gated on preferSectionLanding + a selected agent so the
    # selectAgentById and route-apply paths (which run their own
    # syncSelectedAgentState without preferSectionLanding) do not double-load.
    landing_marker = 'state.activeNavSection === "assistants" &&'
    idx = fn.find(landing_marker)
    assert idx != -1, "assistants landing block not found in setActiveNavSection"
    block = fn[idx:idx + 400]
    assert "preferSectionLanding" in block
    assert "state.selectedAgentId" in block
    assert "await syncSelectedAgentState()" in block


def test_open_portal_section_lands_assistants_with_section_landing_flag():
    js_source = _chat_ui_js_source()
    fn = _extract_js_function(js_source, "openPortalSection")
    # The rail/menu entry point sets preferSectionLanding, which the new
    # assistants block keys on.
    assert "preferSectionLanding: true" in fn


def test_portal_section_route_preserves_selected_agent_for_assistants():
    js_source = _chat_ui_js_source()
    fn = _extract_js_function(js_source, "portalSectionRoute")
    # Landing keeps the selected agent in the committed route so a reload
    # re-selects the same agent.
    assert 'section: "assistants", agentId: state.selectedAgentId' in fn
