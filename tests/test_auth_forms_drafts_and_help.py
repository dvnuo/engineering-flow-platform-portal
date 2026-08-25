"""Auth forms, composer drafts, shortcuts, and the in-app help surface.

Covers the smaller usability gaps found in the review:
- login/register awaited a bare fetch(), so a network failure produced no
  message at all, and nothing stopped a repeat submit on a slow connection;
- the composer draft lived only in memory, so a refresh discarded it;
- there were no global shortcuts and no in-app explanation of Portal's
  vocabulary (runtime profiles, delegations, runtime types, skill repos);
- the "not on the allowlist" page could only tell you to press refresh.
"""

from pathlib import Path

import pytest

from app.web import templates

AUTH_JS = Path("app/static/js/auth_form.js").read_text(encoding="utf-8")
CHAT_JS = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
APP_HTML = Path("app/templates/app.html").read_text(encoding="utf-8")
USERS_HTML = Path("app/templates/partials/users_panel.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("template", ["login.html", "register.html"])
def test_auth_forms_are_accessible_and_password_manager_friendly(template):
    html = Path("app/templates") / template
    source = html.read_text(encoding="utf-8")
    assert 'autocomplete="username"' in source
    assert "autofocus" in source
    assert 'role="alert"' in source
    assert "data-toggle-password" in source
    assert "auth_form.js" in source


def test_login_and_register_use_the_shared_handler_rather_than_inline_fetch():
    for template in ("login.html", "register.html"):
        source = (Path("app/templates") / template).read_text(encoding="utf-8")
        assert "initAuthForm({" in source
        assert "await fetch(" not in source


def test_network_failure_is_reported_instead_of_failing_silently():
    assert "} catch (networkError) {" in AUTH_JS
    assert "Could not reach the server. Check your connection and try again." in AUTH_JS


def test_submitting_disables_the_button_and_shows_progress():
    assert "function setPending(pending)" in AUTH_JS
    assert "submitButton.disabled = pending;" in AUTH_JS
    assert 'pendingText: "Signing in…"' in Path("app/templates/login.html").read_text(encoding="utf-8")


def test_server_side_validation_errors_are_surfaced_verbatim():
    assert "payload.detail" in AUTH_JS


def test_composer_drafts_survive_a_reload():
    assert "function getDraftKey(agentId)" in CHAT_JS
    assert "`portal-draft-${agentId}`" in CHAT_JS
    assert "function persistDraftToStorage(" in CHAT_JS
    assert "function readDraftFromStorage(" in CHAT_JS
    # Written on a debounce, not on every keystroke.
    assert "state.draftPersistTimer = setTimeout(() => persistComposerForAgent(state.selectedAgentId), 300);" in CHAT_JS
    # And dropped once the message is actually on its way.
    assert "clearDraftForAgent(agentIdAtSend);" in CHAT_JS


def test_draft_storage_failures_do_not_break_the_composer():
    # Private browsing and quota errors must not take the composer down with them.
    assert "} catch (_error) { /* quota or private mode — the in-memory draft still works */ }" in CHAT_JS


def test_global_shortcuts_exist_and_yield_to_fields_modals_and_ime():
    assert "if (topmostOpenManagedModal()) return;" in CHAT_JS
    assert "if (state.isComposingInput) return;" in CHAT_JS
    assert 'event.key === "/" && !inField && !mod' in CHAT_JS
    assert 'mod && !event.shiftKey && event.key.toLowerCase() === "k"' in CHAT_JS
    assert 'mod && event.shiftKey && event.key.toLowerCase() === "o"' in CHAT_JS


def test_help_surface_is_reachable_and_explains_portal_vocabulary():
    assert 'id="help-btn"' in APP_HTML
    assert "function openHelpPanel()" in CHAT_JS
    for concept in ("Runtime profile", "Runtime type", "Delegation", "Task", "Assistant"):
        assert f"<dt>{concept}</dt>" in CHAT_JS, concept
    assert "Keyboard shortcuts" in CHAT_JS
    assert '"help",' in CHAT_JS  # registered as an allowed utility panel key


def test_role_options_describe_what_they_actually_grant():
    assert "User — owns and runs their own assistants" in USERS_HTML
    assert "Administrator — full access to every assistant, plus member management" in USERS_HTML


def test_viewer_role_is_gone_everywhere():
    # It was assignable but never enforced — every permission check is
    # `role == "admin" or owner` — so it promised a read-only account that did
    # not exist.
    from app.services.access_control_service import ALLOWED_USER_ROLES

    assert ALLOWED_USER_ROLES == {"admin", "user"}
    assert "viewer" not in USERS_HTML.lower()
    # The explanatory comment in user.py names the removed role, so assert the
    # Literal itself rather than the whole file.
    from app.schemas.user import UserRole
    from typing import get_args

    assert set(get_args(UserRole)) == {"admin", "user"}


def test_member_count_is_pluralised():
    assert '{{ summary.total_users }} member{{ "" if summary.total_users == 1 else "s" }}' in USERS_HTML
    assert "function pluralize(count, singular, plural = null)" in CHAT_JS


def test_filter_summaries_use_the_same_separator_as_the_rest_of_the_ui():
    assert "shown - " not in CHAT_JS
    assert "${filterLabel} · ${countLabel}" in CHAT_JS


def test_unauthorized_page_names_the_username_to_allowlist_and_who_to_ask():
    html = templates.get_template("unauthorized.html").render(
        request=None,
        title="t",
        username="Alice",
        allowlist_username="alice",
        access_message="msg",
        support_contact="#efp-support",
    )
    assert ">alice<" in html
    assert "Copy my username" in html
    assert "#efp-support" in html


def test_unauthorized_page_omits_the_contact_line_when_none_is_configured():
    html = templates.get_template("unauthorized.html").render(
        request=None,
        title="t",
        username="Alice",
        allowlist_username="alice",
        access_message="msg",
        support_contact="",
    )
    assert "Request access from" not in html
    assert "Copy my username" in html


def test_assistant_removal_is_a_single_action():
    # Delete and Destroy were two danger buttons for one behaviour: destroy_data
    # was never implemented cluster-side, so both removed the deployment and
    # left the shared workspace volume alone.
    assert CHAT_JS.count('variantClass: "is-danger"') == 1
    assert 'label: "Destroy"' not in CHAT_JS
    assert "async function removeAgent(agent) {" in CHAT_JS
    assert "/destroy`" not in CHAT_JS


def test_delete_confirmation_states_what_survives():
    assert "Files already written to the shared workspace volume are kept on the cluster." in CHAT_JS
    assert 'confirmText: "Delete assistant"' in CHAT_JS
    assert "danger: true" in CHAT_JS
