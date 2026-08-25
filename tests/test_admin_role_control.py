"""The member role control is a segmented toggle, not a dropdown.

Two things went wrong when descriptive copy was first added to this row:

1. A helper paragraph was inserted as a fourth child of
   `.portal-admin-member-controls`, which declares three grid columns — so the
   actions wrapped onto a new row and the hint was squeezed into an 85px column.
2. The `<option>` text ("User — owns and runs their own assistants") needed
   388px inside a column capped at 200px, so it rendered truncated to
   "User — owns and runs their own assis…", which is worse than no explanation.

Member cards are ~380px wide, so a dropdown carrying descriptions can never fit.
A two-pill segmented control cannot truncate, shows both states at once, and
changes the role in one click; the explanation lives in each pill's tooltip.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

USERS_HTML = Path("app/templates/partials/users_panel.html").read_text(encoding="utf-8")
ADMIN_JS = Path("app/static/js/admin_users.js").read_text(encoding="utf-8")
TOOLTIP_JS = Path("app/static/js/tooltips.js").read_text(encoding="utf-8")
CSS = Path("app/static/css/app.css").read_text(encoding="utf-8")


class _ChildCounter(HTMLParser):
    """Direct element children of every .portal-admin-member-controls row."""

    VOID = {"input", "br", "img", "hr", "meta", "link", "source"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = None
        self.current = 0
        self.rows: list[int] = []

    def handle_starttag(self, tag, attrs):
        classes = dict(attrs).get("class", "")
        if self.depth is None:
            if "portal-admin-member-controls" in classes:
                self.depth = 0
                self.current = 0
            return
        if self.depth == 0:
            self.current += 1
        if tag not in self.VOID:
            self.depth += 1

    def handle_startendtag(self, tag, attrs):
        if self.depth == 0:
            self.current += 1

    def handle_endtag(self, tag):
        if self.depth is None or tag in self.VOID:
            return
        self.depth -= 1
        if self.depth < 0:
            self.rows.append(self.current)
            self.depth = None


def _member_controls_children(html: str) -> list[int]:
    parser = _ChildCounter()
    parser.feed(html)
    return parser.rows


def test_controls_row_child_count_matches_its_grid_columns(rendered_panel):
    # The row declares three columns; a fourth child silently wraps the actions
    # onto a second row, which is exactly how this broke.
    rule = CSS.split(".portal-admin-member-controls {", 1)[1].split("}", 1)[0]
    declared = rule.split("grid-template-columns:", 1)[1].split(";", 1)[0]
    assert len(re.findall(r"minmax\([^)]*\)|auto|\d+px|1fr", declared)) == 3
    rows = _member_controls_children(rendered_panel)
    assert rows, "no member rows rendered"
    assert all(count == 3 for count in rows), rows


def test_role_is_a_segmented_toggle_rather_than_a_dropdown():
    assert 'class="portal-role-toggle"' in USERS_HTML
    assert 'role="radiogroup"' in USERS_HTML
    assert USERS_HTML.count("data-admin-role-option") == 2
    assert "data-admin-role-select" not in USERS_HTML
    assert "<select" not in USERS_HTML.split("portal-admin-role-control", 1)[1].split("</label>", 1)[0]


def test_pill_labels_stay_short_so_they_cannot_truncate():
    toggle = USERS_HTML.split('class="portal-role-toggle"', 1)[1].split("</div>", 1)[0]
    labels = re.findall(r"<span>([^<]+)</span>", toggle)
    assert labels == ["User", "Admin"]


def test_radio_names_are_scoped_per_member():
    # A shared name would make every card's pills one mutually exclusive group.
    assert 'name="role-{{ u.id }}"' in USERS_HTML


def test_own_account_cannot_change_its_own_role():
    toggle = USERS_HTML.split('class="portal-role-toggle"', 1)[1].split("</div>", 1)[0]
    assert toggle.count("{% if u.id == current_user.id %}disabled{% endif %}") == 2


def test_no_permanent_helper_text_under_the_control():
    assert "portal-admin-role-hint" not in USERS_HTML
    assert "portal-admin-role-hint" not in CSS


def test_each_pill_explains_what_it_grants():
    assert '[data-admin-role-option][value="user"]' in TOOLTIP_JS
    assert '[data-admin-role-option][value="admin"]' in TOOLTIP_JS
    assert "Owns and runs their own assistants" in TOOLTIP_JS
    assert "Full access to every member's assistants, plus member management" in TOOLTIP_JS


def test_toggle_is_width_bounded_and_never_overflows_its_card():
    rule = CSS.split(".portal-role-toggle {", 1)[1].split("}", 1)[0]
    assert "width: max-content" in rule
    assert "max-width: 100%" in rule
    label_rule = CSS.split(".portal-role-option > span {", 1)[1].split("}", 1)[0]
    assert "white-space: nowrap" in label_rule


def test_selected_pill_is_distinguishable_without_relying_on_colour_alone():
    rule = CSS.split(".portal-role-option input:checked + span {", 1)[1].split("}", 1)[0]
    assert "background" in rule
    assert "font-weight" in rule


def test_change_is_saved_optimistically_and_rolled_back_on_failure():
    assert "function checkRoleOption(group, role)" in ADMIN_JS
    assert 'group.classList.add("is-saving")' in ADMIN_JS
    assert "checkRoleOption(group, updated.role);" in ADMIN_JS
    # The control must never show a role the server rejected.
    assert "checkRoleOption(group, previousRole);" in ADMIN_JS
    assert 'group.classList.remove("is-saving")' in ADMIN_JS


def test_allowlisting_reads_the_checked_pill():
    assert "function selectedRole(scope)" in ADMIN_JS
    assert "selectedRole(allowCard)" in ADMIN_JS
    assert "roleSelect" not in ADMIN_JS


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
    with TestClient(app) as client:
        response = client.get("/app/users/panel")
    assert response.status_code == 200
    return response.text
