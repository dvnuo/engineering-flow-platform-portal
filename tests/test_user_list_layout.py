"""User Management fits the pane: one toolbar line, a table that scrolls in its own box.

Measured on 2026-11-11 at 1920 x 1200 with 41 members: the table was 1941px in
a 1475px pane, so the Manage column (role, Revoke) started at x=1938, past the
screen edge; its sideways scrollbar sat at the bottom of a box as tall as every
row, about 2,300px below the fold; rows were 71px and the first one started at
y=506 under five summary cards. Now the cards are gone (their useful counts
sit in the Show filter), six columns are kept for the CSV export and the member
drawer only, the role is a select, the access actions are a pinned row menu,
and the pane stops scrolling so the box's scrollbars stay on screen.
"""

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.models import AgentExecution, AgentTask, DelegationRule, User, UserAllowlistEntry
from app.services.member_management_service import MemberManagementService
from tests.test_agent_execution_registry import _db_session
from tests.test_user_admin_management import _database

USERS_HTML = Path("app/templates/partials/users_panel.html").read_text(encoding="utf-8")
DRAWER_HTML = Path("app/templates/partials/member_usage_drawer.html").read_text(encoding="utf-8")
ADMIN_JS = Path("app/static/js/admin_users.js").read_text(encoding="utf-8")
CSS = Path("app/static/css/app.css").read_text(encoding="utf-8")

EXPORT_ONLY = ("access", "chats", "tasks", "delegations", "tokens-in", "tokens-out", "joined")
SHOWN = ("member", "group", "role", "assistants", "executions", "credits", "active-days", "last-active")


def _rule(selector: str) -> str:
    return CSS.split(selector + " {", 1)[1].split("}", 1)[0]


def _panel(monkeypatch, *, viewer="admin"):
    from app.main import app
    import app.web as web_module

    db = _database()
    admin = User(username="admin", password_hash="hash", role="admin", is_active=True)
    lead = User(username="lead", nickname="Lea D", password_hash="hash", role="group_admin", is_active=True)
    member = User(username="member", nickname="Mem Ber", password_hash="hash", role="user", is_active=True)
    blocked = User(username="blocked", password_hash="hash", role="user", is_active=True)
    db.add_all([admin, lead, member, blocked])
    db.commit()
    for user in (admin, lead, member, blocked):
        db.refresh(user)
    db.add_all([
        UserAllowlistEntry(username="admin", role="admin", is_active=True),
        UserAllowlistEntry(username="lead", role="group_admin", is_active=True),
        UserAllowlistEntry(username="member", role="user", is_active=True),
        UserAllowlistEntry(username="invited", role="user", is_active=True),
    ])
    db.add(AgentExecution(agent_id=1, kind="chat", status="succeeded", owner_user_id=member.id, created_by_user_id=member.id,
                          created_at=datetime.utcnow() - timedelta(days=1), updated_at=datetime.utcnow()))
    db.commit()
    current = {"admin": admin, "member": member}[viewer]
    monkeypatch.setattr(web_module, "SessionLocal", lambda: db)
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: current)
    return TestClient(app), db, {"admin": admin, "lead": lead, "member": member, "blocked": blocked}


def test_no_summary_cards_and_one_toolbar(monkeypatch):
    client, db, _users = _panel(monkeypatch)
    try:
        html = client.get("/app/users/panel").text
        assert "portal-admin-summary-grid" not in html
        assert "portal-usage-card" not in html
        assert "Registered members</div>" not in html
        toolbar = html.split('class="portal-admin-member-toolbar"', 1)[1].split("</div>", 1)[0]
        for marker in ("data-admin-member-search", "data-admin-member-group-filter", "data-admin-member-access-filter",
                       "data-admin-member-period", "data-admin-member-result-count", "data-open-admin-pending-modal",
                       "data-admin-member-export"):
            assert marker in toolbar, marker
    finally:
        db.close()


def test_the_show_filter_counts_what_each_option_shows(monkeypatch):
    client, db, _users = _panel(monkeypatch)
    try:
        html = client.get("/app/users/panel").text
        assert '<option value="all">All members (4)</option>' in html
        assert '<option value="blocked">Needs access (1)</option>' in html
        assert '<option value="inactive">Inactive in 30 days (3)</option>' in html
        assert '<option value="admins">Administrators (2)</option>' in html
        assert 'value="allowed"' not in html.split("data-admin-member-access-filter", 1)[1].split("</select>", 1)[0]
    finally:
        db.close()


def test_overview_summary_carries_the_filter_counts():
    db, _factory, user, _agent = _db_session()
    try:
        db.add(User(username="lead2", password_hash="x", role="group_admin", is_active=True))
        db.add(UserAllowlistEntry(username=user.username, role="user", is_active=True))
        db.commit()
        summary = MemberManagementService(db).build_overview()["summary"]
        assert summary["total_users"] == 2
        assert summary["blocked_users"] == 1, "lead2 has no allowlist entry"
        assert summary["inactive_members_period"] == 2
        # The owner is an allowlisted admin, lead2 a group admin without access:
        # the filter counts both, the older admin_users only the allowlisted one.
        assert summary["administrator_members"] == 2
        assert summary["admin_users"] == 1
    finally:
        db.close()


def test_pending_registrations_open_in_a_modal_from_the_toolbar(monkeypatch):
    client, db, _users = _panel(monkeypatch)
    try:
        html = client.get("/app/users/panel").text
        assert ">Pending (1)</button>" in html
        modal = html.split("data-admin-pending-modal>", 1)[1]
        assert 'data-allowlist-username="invited"' in modal.split("data-admin-allowlist-modal", 1)[0]
        assert "portal-admin-pending-section" not in html and "<details" not in html
    finally:
        db.close()
    # No id on the modal: htmx would write the server's class back while settling a reload.
    tag = USERS_HTML.split("data-admin-pending-modal>", 1)[0].rsplit("<div", 1)[1]
    assert "id=" not in tag.replace("aria-labelledby=", "")
    reopen = ADMIN_JS.split("var fromPendingList", 1)[1]
    assert "if (fromPendingList) openPendingModal();" in reopen


def test_six_columns_are_kept_for_the_export_only(monkeypatch):
    for key in EXPORT_ONLY:
        header = USERS_HTML.split(f'data-sort-key="{key}"', 1)[0].rsplit("<th", 1)[1]
        assert "portal-admin-export-only" in header, key
    for key in SHOWN:
        header = USERS_HTML.split(f'data-sort-key="{key}"', 1)[0].rsplit("<th", 1)[1]
        assert "portal-admin-export-only" not in header, key
    assert "display: none" in _rule(".portal-admin-member-table .portal-admin-export-only")
    # The export reads the table's own header row, not the tables of an open drawer.
    export = ADMIN_JS.split("function exportMembersCsv(root)", 1)[1].split("\n  }\n", 1)[0]
    assert "table.tHead.rows[0].cells" in export
    assert 'querySelectorAll("thead th")' not in export
    client, db, _users = _panel(monkeypatch)
    try:
        html = client.get("/app/users/panel").text
        row = html.split('data-member-username="member"', 1)[1].split("</tr>", 1)[0]
        assert '<td class="portal-admin-export-only" data-sort-value="0">Allowed</td>' in row
        assert 'data-export-value="User"' in row
    finally:
        db.close()


def test_row_actions_are_a_pinned_menu(monkeypatch):
    client, db, users = _panel(monkeypatch)
    try:
        html = client.get("/app/users/panel").text

        def row(name):
            return html.split(f'data-member-username="{name}"', 1)[1].split("</tr>", 1)[0]

        member = row("member")
        menu_id = f'admin-member-menu-{users["member"].id}'
        assert f'popovertarget="{menu_id}"' in member and f'id="{menu_id}"' in member
        assert 'popover role="menu"' in member
        assert f'data-admin-member-usage="{users["member"].id}"' in member
        assert 'role="menuitem" class="is-danger" data-remove-allowlist=' in member
        assert "data-allow-member" not in member
        # Blocked: Allow beside the menu, a badge by the name, nothing to revoke.
        blocked = row("blocked")
        assert "data-allow-member" in blocked and "data-remove-allowlist" not in blocked
        assert 'portal-admin-status-badge is-danger' in blocked and "Needs access" in blocked
        # The own account cannot be revoked from here.
        assert "data-remove-allowlist" not in row("admin")
        assert "Manage</th>" not in html
    finally:
        db.close()


def test_a_group_administrator_sees_their_group_as_a_chip(monkeypatch):
    from app.main import app
    import app.web as web_module
    from tests.test_group_admin_pages import _group

    db = _database()
    try:
        dev = _group(db, "Dev")
        lead = User(username="lead", password_hash="hash", role="group_admin", is_active=True, group_id=dev.id)
        mate = User(username="mate", password_hash="hash", role="user", is_active=True, group_id=dev.id)
        db.add_all([lead, mate])
        db.commit()
        db.refresh(lead)
        db.add_all([UserAllowlistEntry(username="lead", role="group_admin", is_active=True, group_id=dev.id),
                    UserAllowlistEntry(username="mate", role="user", is_active=True, group_id=dev.id)])
        db.commit()
        monkeypatch.setattr(web_module, "SessionLocal", lambda: db)
        monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: SimpleNamespace(
            id=lead.id, role=lead.role, username=lead.username, nickname=lead.username, group_id=dev.id))
        html = TestClient(app).get("/app/users/panel").text
        assert '<span class="portal-admin-scope-chip"' in html and ">Dev</span>" in html
        assert "data-admin-member-group-filter" not in html
        assert "is-selectable" not in html and "data-admin-member-bulk-bar" not in html
        assert '<option value="admin"' not in html.split("data-admin-member-rows", 1)[1]
    finally:
        db.close()


def test_the_pane_stops_scrolling_and_the_box_scrolls():
    pane = _rule(".portal-workspace-detail:has(> .portal-workspace-detail-content > .portal-admin-users)")
    assert "overflow: hidden" in pane and "flex-direction: column" in pane
    assert "min-height: 0" in _rule(".portal-workspace-detail-content:has(> .portal-admin-users)")
    section = _rule(".portal-admin-members-section")
    assert "flex: 1" in section and "min-height: 0" in section
    box = _rule(".portal-admin-member-table-wrap")
    assert "flex: 0 1 auto" in box and "min-height: 0" in box and "overflow: auto" in box
    # The bulk bar is in the flow under the box, no longer floating over rows.
    assert "position: sticky" not in _rule(".portal-admin-bulk-bar")


def test_header_row_and_actions_column_are_pinned():
    header = _rule(".portal-admin-member-table > thead > tr > th")
    assert "position: sticky" in header and "top: 0" in header
    # Only the table's own header: a drawer's tables must not stick to the box.
    assert ".portal-admin-member-table thead th {" not in CSS
    actions = _rule(".portal-admin-member-table .portal-admin-actions-cell")
    assert "position: sticky" in actions and "right: 0" in actions
    assert ".portal-admin-member-table td:last-child {" not in CSS


def test_the_row_menu_is_a_popover_placed_by_the_script():
    menu = _rule(".portal-admin-row-menu")
    assert "position: fixed" in menu and "display" not in menu, "an author display would show closed popovers"
    assert "display: grid" in _rule(".portal-admin-row-menu:popover-open")
    assert 'document.addEventListener("beforetoggle"' in ADMIN_JS
    assert 'document.addEventListener("toggle"' in ADMIN_JS
    assert "function placeRowMenu(menu, flip)" in ADMIN_JS
    # Scrolling would leave it floating away from its row.
    assert 'document.addEventListener("scroll"' in ADMIN_JS
    assert "closeRowMenu(button);" in ADMIN_JS.split('var button = event.target.closest("[data-remove-allowlist]");', 1)[1]


def test_filter_counts_and_administrators_follow_the_rows():
    counts = ADMIN_JS.split("function refreshFilterCounts(root)", 1)[1].split("\n  }\n", 1)[0]
    for label in ('t("All members ({n})"', 't("Needs access ({n})"', 't("Inactive in {days} days ({n})"', 't("Administrators ({n})"'):
        assert label in counts, label
    assert 'access === "admins" ? isAdminRole(card.dataset.memberRole)' in ADMIN_JS


def test_the_drawer_shows_what_left_the_table(monkeypatch):
    assert 't("Joined {date}"' in DRAWER_HTML
    assert 't("{done} / {total} tasks done"' in DRAWER_HTML
    assert 'tn(member.delegation_count, "{n} delegation", "{n} delegations")' in DRAWER_HTML
    from app.main import app
    import app.web as web_module

    db, _factory, user, agent = _db_session()
    try:
        db.add_all([
            AgentTask(id="t-1", assignee_agent_id=agent.id, source="portal", task_type="agent_async_task", status="done",
                      owner_user_id=user.id, created_by_user_id=user.id),
            AgentTask(id="t-2", assignee_agent_id=agent.id, source="portal", task_type="agent_async_task", status="running",
                      owner_user_id=user.id, created_by_user_id=user.id),
        ])
        db.commit()
        counts = MemberManagementService(db).member_work_counts(user.id)
        assert counts["task_count"] == 2 and counts["completed_task_count"] == 1
        assert counts["delegation_count"] == db.query(DelegationRule).filter(DelegationRule.owner_user_id == user.id).count()

        admin = SimpleNamespace(id=user.id, username=user.username, nickname=user.username, role="admin", group_id=None)
        monkeypatch.setattr(web_module, "SessionLocal", lambda: db)
        monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: admin)
        html = TestClient(app).get(f"/app/users/{user.id}/usage/panel").text
        assert "data-member-usage-work" in html and "1 / 2 tasks done" in html and "Joined" in html
    finally:
        db.close()
