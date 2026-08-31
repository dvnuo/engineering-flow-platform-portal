"""Administration header actions belong to a panel, not to the whole section.

Administration used to hold a single panel, so gating its header action on the
active nav section was indistinguishable from gating it on the panel. With three
panels it is not: every panel inherited User Management's "Add to allowlist".
"""
from pathlib import Path

import pytest

from tests._js_extract_helpers import _extract_js_function


def _chat_ui() -> str:
    return Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")


def _app_html() -> str:
    return Path("app/templates/app.html").read_text(encoding="utf-8")


def _assistant_setup() -> str:
    return Path("app/static/js/assistant_setup.js").read_text(encoding="utf-8")


def _panel() -> str:
    return Path("app/templates/partials/assistant_types_panel.html").read_text(encoding="utf-8")


# ------------------------------------------------------- header action gating


def test_header_actions_are_gated_on_the_panel_not_the_section():
    body = _extract_js_function(_chat_ui(), "syncAdminHeaderActions")

    assert 'panel !== "users"' in body
    assert 'panel !== "assistant-types"' in body


def test_no_header_action_is_toggled_on_the_section_alone():
    # The regression: a direct toggle keyed only on the section shows the
    # action on every Administration panel.
    js = _chat_ui()

    assert 'headerAddAllowlistBtn?.classList.toggle("hidden", normalized !== "users")' not in js
    assert 'headerAddAllowlistBtn?.classList.toggle("hidden", !userManagementMode)' not in js


def test_leaving_administration_hides_both_actions():
    body = _extract_js_function(_chat_ui(), "syncAdminHeaderActions")

    # `panel` is empty outside Administration, so neither name can match.
    assert 'const inAdmin = String(section || "") === "users"' in body
    assert 'const panel = inAdmin ?' in body


def test_panel_identity_is_not_read_from_state():
    # applyInitialPortalRouteShell() runs at module load, before `const state`
    # is initialized. Reading the panel from `state` there is a TDZ
    # ReferenceError that leaves the entire app shell unrendered.
    js = _chat_ui()

    assert "state.activeAdminPanel" not in js
    assert "let activeAdminPanel" in js

    declaration = js.index("let activeAdminPanel")
    first_shell_call = js.index("applyInitialPortalRouteShell();")
    state_declaration = js.index("const state = {")
    assert declaration < first_shell_call, "the declaration must precede the module-load call"
    assert declaration < state_declaration, "the whole point is that it does not depend on state"


def test_opening_user_management_reclaims_the_header():
    body = _extract_js_function(_chat_ui(), "openUsersInMain")

    assert 'activeAdminPanel = "users"' in body
    assert "[data-admin-panel]" in body


def test_admin_panels_announce_themselves():
    assert "window.setPortalAdminPanel(button.dataset.adminPanel)" in _assistant_setup()


def test_both_header_actions_exist_and_start_hidden():
    html = _app_html()

    for button_id in ("header-add-allowlist-btn", "header-add-assistant-type-btn"):
        assert button_id in html
        start = html.index(button_id)
        opening_tag = html[start : html.index(">", start)]
        assert "hidden" in opening_tag, f"{button_id} must not flash before gating runs"


# -------------------------------------------------------------- create modal


def test_creating_a_type_happens_in_a_modal():
    html = _panel()

    assert "assistant-type-create-modal" in html
    assert 'role="dialog"' in html
    assert 'aria-modal="true"' in html


def test_the_create_modal_starts_hidden():
    html = _panel()

    start = html.index('id="assistant-type-create-modal"')
    opening_tag = html[start - 60 : html.index(">", start)]
    assert "hidden" in opening_tag
    assert 'aria-hidden="true"' in opening_tag


def test_the_create_form_lives_inside_the_modal():
    # It stays in this partial rather than app.html so its branch selects keep
    # the server-side prefill.
    html = _panel()

    assert html.index("assistant-type-create-modal") < html.index("data-assistant-type-create-form")


def test_the_modal_is_height_capped_so_submit_stays_reachable():
    # The icon grid plus six fields overflows a short viewport; without a cap
    # the submit button sits below the fold with no way to scroll to it.
    css = Path("app/static/css/app.css").read_text(encoding="utf-8")

    assert ".modal-card.assistant-type-modal-card" in css
    block = css[css.index(".modal-card.assistant-type-modal-card") :][:220]
    assert "max-height" in block
    assert "overflow-y: auto" in block
    assert "assistant-type-modal-card" in _panel()


@pytest.mark.parametrize("selector", ["data-open-assistant-type-modal", "data-close-assistant-type-modal"])
def test_modal_has_an_opener_and_a_closer(selector):
    assert selector in _app_html() + _panel()


def test_escape_closes_the_modal():
    js = _assistant_setup()

    assert 'event.key !== "Escape"' in js
    assert "setAssistantTypeModalOpen(false)" in js


def test_a_successful_create_closes_the_modal_before_reloading():
    # The reload replaces the modal markup; closing afterwards would act on a
    # detached node and the fresh panel would come back with the dialog open.
    js = _assistant_setup()
    body = _extract_js_function(js, "submitAssistantTypeCreate")

    assert body.index("setAssistantTypeModalOpen(false)") < body.index('reloadAdminPanel("assistant-types")')
