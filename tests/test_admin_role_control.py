"""The member role control is a quiet inline select in its own Role column.

It used to be a three-pill toggle (User / Group admin / Admin) under a "Role"
label in the last cell, next to Revoke. That cell was 401px wide and made each
row 71px tall; with sixteen columns the table measured 1941px in a 1475px pane
at 1920 x 1200, so the role and Revoke sat past the right edge of the screen.
A select with three short words is about 140px, reads like the text around it
until it is pointed at, saves on change (asking first when Admin is granted or
removed), and the Role header explains the roles.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

USERS_HTML = Path("app/templates/partials/users_panel.html").read_text(encoding="utf-8")
ADMIN_JS = Path("app/static/js/admin_users.js").read_text(encoding="utf-8")
TOOLTIP_JS = Path("app/static/js/tooltips.js").read_text(encoding="utf-8")
CSS = Path("app/static/css/app.css").read_text(encoding="utf-8")


def _function(source: str, name: str) -> str:
    start = source.index(name)
    return source[start:source.index("\n  }\n", start)]


def test_role_is_a_select_in_its_own_column():
    assert 'data-sort-key="role"' in USERS_HTML
    assert "data-admin-role-select" in USERS_HTML
    assert "portal-role-toggle" not in USERS_HTML
    assert "data-admin-role-option" not in USERS_HTML
    assert "data-admin-role-group" not in USERS_HTML
    select = USERS_HTML.split("data-admin-role-select", 1)[1].split("</select>", 1)[0]
    assert '<option value="user"' in select and '<option value="group_admin"' in select
    # Only a system administrator can give Admin, so the option is not offered otherwise.
    assert '{% if is_system_admin %}<option value="admin"' in select


def test_own_account_and_a_group_admins_view_of_a_system_admin_show_text():
    assert "{% set role_locked = u.id == current_user.id or (not is_system_admin and u.role == 'admin') %}" in USERS_HTML
    locked = USERS_HTML.split("{% if role_locked %}", 1)[1].split("{% else %}", 1)[0]
    assert "portal-admin-role-text" in locked and "<select" not in locked


def test_the_role_header_explains_the_three_roles():
    header = USERS_HTML.split('data-sort-key="role"', 1)[1].split("</th>", 1)[0]
    assert "User: runs their own assistants. Group admin:" in header
    assert '["[data-admin-role-select]", t("What this member can do. Saved as soon as you pick another role.")]' in TOOLTIP_JS
    assert "[data-admin-role-option]" not in TOOLTIP_JS


def test_change_is_saved_on_change_and_rolled_back_on_failure():
    change = _function(ADMIN_JS, "async function changeRole(select)")
    assert 'select.classList.add("is-saving")' in change
    assert 'select.classList.remove("is-saving")' in change
    assert "body: JSON.stringify({ role: nextRole })" in change
    # The select must never show a role the server rejected.
    assert change.count("select.value = previousRole;") == 2
    # The row's role feeds the Administrators filter, the sort and the export.
    assert "row.dataset.memberRole = savedRole;" in change
    assert "cell.dataset.exportValue = roleWord(savedRole);" in change
    assert "refreshFilterCounts(root);" in change
    assert 'closest("[data-admin-role-select]")' in ADMIN_JS


def test_granting_or_removing_admin_asks_first():
    change = _function(ADMIN_JS, "async function changeRole(select)")
    assert 'if (previousRole === "admin" || nextRole === "admin")' in change
    assert "window.showConfirm" in change
    assert 'confirmText: t("Change role")' in change


def test_the_saving_select_cannot_be_changed_twice():
    rule = CSS.split(".portal-admin-member-table .portal-admin-inline-select.is-saving {", 1)[1].split("}", 1)[0]
    assert "pointer-events: none" in rule


def test_allowlisting_reads_the_rows_role():
    selected = _function(ADMIN_JS, "function selectedRole(row)")
    assert 'row.querySelector("[data-admin-role-select]")' in selected
    assert "row.dataset.memberRole" in selected
    assert "selectedRole(allowCard)" in ADMIN_JS


def test_the_segmented_toggle_stays_for_task_visibility():
    # The pill styles serve the task visibility switch now.
    assert "portal-role-toggle portal-visibility-toggle" in Path("app/templates/partials/task_detail_panel.html").read_text(encoding="utf-8")
    assert ".portal-role-toggle {" in CSS
    assert ".portal-role-option input:checked + span {" in CSS


def test_rendered_rows_offer_the_right_roles(rendered_panel):
    html = rendered_panel
    # The member's select carries their current role; the own account is text.
    assert 'data-admin-role-select data-user-id' in html
    assert 'data-original-role="user"' in html
    assert '<option value="admin" >' in html or '<option value="admin">' in html
    assert 'class="portal-admin-role-text"' in html


@pytest.fixture()
def rendered_panel(monkeypatch):
    """The users panel as actually rendered, not the Jinja source."""
    from app.main import app
    import app.web as web_module
    from tests.test_user_admin_management import _database
    from app.models.user import User
    from app.models.user_allowlist import UserAllowlistEntry

    db = _database()
    admin = User(username="admin", password_hash="hash", role="admin", is_active=True)
    member = User(username="member", password_hash="hash", role="user", is_active=True)
    db.add_all([admin, member])
    db.commit()
    db.refresh(admin)
    db.add(UserAllowlistEntry(username="member", role="user", is_active=True))
    db.commit()
    monkeypatch.setattr(web_module, "SessionLocal", lambda: db)
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: admin)
    # Not the `with` form: it fires startup, whose schema guard needs a
    # migrated database this test does not have (and CI never has).
    client = TestClient(app)
    response = client.get("/app/users/panel")
    assert response.status_code == 200
    return response.text
