"""Moving several members to one group at once.

A system administrator ticks rows in User Management and moves them together
(``POST /api/users/move-to-group``); each member moves the way one does from
the row's select (``GroupService.move_member``). users_panel.html adds the box
column and the bar, admin_users.js keeps the selection and sends the move.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from _js_extract_helpers import I18N_STUB, _extract_js_function
from app.models import AuditLog, User, UserAllowlistEntry
from app.services.group_service import GroupService
from app.services.runtime_profile_service import RuntimeProfileService
from tests.test_group_admin_pages import _as, _group
from tests.test_groups import _user, api  # noqa: F401  (the fixture)
from tests.test_web_connector_settings import _build_env

ADMIN_JS = Path("app/static/js/admin_users.js").read_text(encoding="utf-8")


# ------------------------------------------------------------------ API


def test_a_system_administrator_moves_several_members_to_one_group(api):  # noqa: F811
    db, default, dev, ops = api["db"], api["default"], api["dev"], api["ops"]
    api["user"] = _user(db, "root", role="admin", group=default)
    coder = _user(db, "coder", group=dev)
    lead = _user(db, "lead", role="group_admin", group=default)
    already = _user(db, "already", group=ops)
    profiles = RuntimeProfileService(db)
    coder_profile = profiles.get_or_create_for_user(coder)
    profiles.get_or_create_for_user(lead)

    response = api["client"].post(
        "/api/users/move-to-group",
        json={"user_ids": [coder.id, lead.id, already.id, 9999, coder.id], "group_id": ops.id},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["group_id"] == ops.id and body["group_name"] == "Ops"
    assert body["moved"] == ["coder", "lead"]
    assert body["unchanged"] == ["already"]
    assert body["demoted"] == ["lead"]
    assert body["not_found"] == [9999]
    assert body["failed"] == []
    for user in (coder, lead, already):
        db.refresh(user)
        assert user.group_id == ops.id
    assert lead.role == "user"
    entries = {row.username: row for row in db.query(UserAllowlistEntry).all()}
    assert entries["coder"].group_id == ops.id
    assert entries["lead"].group_id == ops.id and entries["lead"].role == "user"
    # Dev sets its own Jira and Ops follows the platform, so coder's
    # assistants pick up a change; the Default group and Ops both follow the
    # platform, so lead's do not. One rollout for the whole request.
    assert api["rollouts"] == [[coder_profile.id]]
    # One audit row per member moved, as a single move writes.
    moves = db.query(AuditLog).filter(AuditLog.action == "move_member_group").all()
    assert sorted(row.target_id for row in moves) == sorted([str(coder.id), str(lead.id)])


def test_only_a_system_administrator_moves_members_between_groups(api):  # noqa: F811
    db, dev, ops = api["db"], api["dev"], api["ops"]
    member = _user(db, "member", group=dev)
    client = api["client"]

    api["user"] = _user(db, "lead", role="group_admin", group=dev)
    refused = client.post("/api/users/move-to-group", json={"user_ids": [member.id], "group_id": ops.id})
    assert refused.status_code == 403
    assert refused.json()["detail"] == "Only a system administrator can move members between groups"

    api["user"] = _user(db, "plain", group=dev)
    assert client.post("/api/users/move-to-group", json={"user_ids": [member.id], "group_id": ops.id}).status_code == 403

    db.refresh(member)
    assert member.group_id == dev.id
    assert api["rollouts"] == []


def test_a_bulk_move_names_a_group_and_at_least_one_member(api):  # noqa: F811
    db, dev = api["db"], api["dev"]
    api["user"] = _user(db, "root", role="admin", group=api["default"])
    member = _user(db, "member", group=dev)
    client = api["client"]

    missing_group = client.post("/api/users/move-to-group", json={"user_ids": [member.id], "group_id": 9999})
    assert missing_group.status_code == 422 and missing_group.json()["detail"] == "Group not found"
    assert client.post("/api/users/move-to-group", json={"user_ids": [], "group_id": dev.id}).status_code == 422
    assert client.post("/api/users/move-to-group", json={"user_ids": [member.id]}).status_code == 422
    assert client.post("/api/users/move-to-group", json={"user_ids": [member.id], "group_id": dev.id, "role": "admin"}).status_code == 422


def test_one_failing_move_does_not_stop_the_others(api, monkeypatch):  # noqa: F811
    db, dev, ops = api["db"], api["dev"], api["ops"]
    api["user"] = _user(db, "root", role="admin", group=api["default"])
    first = _user(db, "first", group=dev)
    broken = _user(db, "broken", group=dev)
    last = _user(db, "last", group=dev)
    original = GroupService.move_member

    def flaky(self, user, target, **kwargs):
        if user.username == "broken":
            raise RuntimeError("database hiccup")
        return original(self, user, target, **kwargs)

    monkeypatch.setattr(GroupService, "move_member", flaky)

    response = api["client"].post("/api/users/move-to-group", json={"user_ids": [first.id, broken.id, last.id], "group_id": ops.id})

    assert response.status_code == 200
    assert response.json()["moved"] == ["first", "last"]
    assert response.json()["failed"] == ["broken"]
    db.refresh(broken)
    assert broken.group_id == dev.id


def test_a_move_that_fails_on_the_settings_leaves_the_member_where_they_were(api, monkeypatch):  # noqa: F811
    # move_member writes the group, the allowlist entry and the settings in
    # one commit, so a failure half-way does not leave a member in the new
    # group with the old group's settings and nothing rolled out.
    db, dev, ops = api["db"], api["dev"], api["ops"]
    api["user"] = _user(db, "root", role="admin", group=api["default"])
    broken = _user(db, "broken", role="group_admin", group=dev)
    RuntimeProfileService(db).get_or_create_for_user(broken)

    def explode(_config_json):
        raise RuntimeError("cannot store settings")

    monkeypatch.setattr(RuntimeProfileService, "normalize_persisted_config_json", staticmethod(explode))

    response = api["client"].post("/api/users/move-to-group", json={"user_ids": [broken.id], "group_id": ops.id})

    assert response.status_code == 200 and response.json()["failed"] == ["broken"]
    db.expire_all()
    assert db.get(User, broken.id).group_id == dev.id
    assert db.get(User, broken.id).role == "group_admin"
    entry = db.query(UserAllowlistEntry).filter(UserAllowlistEntry.username == "broken").one()
    assert entry.group_id == dev.id and entry.role == "group_admin"
    assert api["rollouts"] == []


# ------------------------------------------------------------------ the panel


def test_a_system_administrator_gets_boxes_and_the_move_bar(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _group(env.db, "Dev")
        html = env.client.get("/app/users/panel").text
        assert "portal-admin-member-table is-selectable" in html
        assert html.count("data-admin-member-select-all") == 1
        # One box per member row, each naming the member it picks.
        assert html.count('data-admin-member-select value="') == env.db.query(User).count()
        assert 'aria-label="Select other"' in html
        assert "data-admin-member-bulk-bar hidden" in html
        assert "data-admin-member-bulk-group" in html and ">Dev</option>" in html
        assert "data-admin-member-bulk-move disabled" in html
        # The box column is neither sorted nor exported.
        assert '<th class="portal-admin-select-cell" data-no-export>' in html
        assert '<td class="portal-admin-select-cell" data-no-export>' in html
    finally:
        env.cleanup()


def test_a_group_administrator_gets_neither_boxes_nor_the_bar(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        dev = _group(env.db, "Dev")
        env.other.role = "group_admin"
        env.other.group_id = dev.id
        env.db.commit()
        _as(monkeypatch, env, env.other)
        html = env.client.get("/app/users/panel").text
        assert "Members of Dev" in html
        assert "is-selectable" not in html
        assert "data-admin-member-select" not in html
        assert "data-admin-member-bulk-bar" not in html
    finally:
        env.cleanup()


# ------------------------------------------------------------------ the script


def _run_node(script: str) -> dict:
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed")
    with tempfile.TemporaryDirectory() as tmp:
        script_path = Path(tmp) / "case.js"
        script_path.write_text(I18N_STUB + script, encoding="utf-8")
        result = subprocess.run([node_bin, str(script_path)], check=False, text=True, capture_output=True, encoding="utf-8")
    if result.returncode != 0:
        raise AssertionError(f"node failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


# A table of five members with just enough DOM for the selection helpers.
FAKE_TABLE = r"""
function classList() {
  const names = new Set();
  return { toggle(name, on) { if (on) names.add(name); else names.delete(name); }, contains(name) { return names.has(name); } };
}
const rows = ["1", "2", "3", "4", "5"].map((id) => {
  const row = { dataset: { memberId: id, memberUsername: "user" + id }, hidden: false, classList: classList() };
  const box = { value: id, checked: false, closest: (sel) => (sel === "tr[data-member-id]" ? row : sel === "#admin-users-panel" ? root : null) };
  row.querySelector = (sel) => (sel === "[data-admin-member-select]" ? box : null);
  row.box = box;
  return row;
});
const all = { checked: false, indeterminate: false, disabled: false };
const count = { textContent: "" };
const target = { value: "" };
const move = { disabled: true };
const bar = {
  hidden: true,
  querySelector(sel) {
    return { "[data-admin-member-selected-count]": count, "[data-admin-member-bulk-group]": target, "[data-admin-member-bulk-move]": move }[sel] || null;
  },
};
const body = { querySelectorAll: () => rows.slice() };
const root = {
  contains: (el) => rows.some((row) => row.box === el),
  querySelectorAll: (sel) => (sel === "[data-admin-member-select]" ? rows.map((row) => row.box) : []),
  querySelector(sel) {
    return { "[data-admin-member-select-all]": all, "[data-admin-member-bulk-bar]": bar, "[data-admin-member-rows]": body }[sel] || null;
  },
};
var selectionAnchor = null;
function click(id, shift) {
  const box = rows[Number(id) - 1].box;
  box.checked = !box.checked;
  pickMember(box, !!shift);
}
function state() {
  return {
    selected: rows.filter((row) => row.box.checked).map((row) => row.dataset.memberId),
    tinted: rows.filter((row) => row.classList.contains("is-selected")).map((row) => row.dataset.memberId),
    all: all.checked, indeterminate: all.indeterminate, barHidden: bar.hidden, count: count.textContent, moveDisabled: move.disabled,
  };
}
"""


def _selection_script(body: str) -> str:
    names = ("panelRoot", "groupIdOf", "memberRows", "selectBoxes", "refreshSelection", "pickMember", "pickShownMembers", "clearSelection")
    return "\n".join(_extract_js_function(ADMIN_JS, name) for name in names) + FAKE_TABLE + body


def test_selection_follows_clicks_shift_ranges_and_the_header_box():
    result = _run_node(_selection_script(r"""
const out = {};
out.initial = (refreshSelection(root), state());
click("2");
out.one = state();
click("4", true);
out.range = state();
target.value = "7";
refreshSelection(root);
out.withGroup = state();
rows[0].hidden = true;
rows[4].hidden = true;
refreshSelection(root);
out.shownAll = state();
rows[0].hidden = false;
rows[0].box.checked = true;
rows[0].hidden = true;
refreshSelection(root);
out.hiddenPicked = state();
pickShownMembers(root, false);
out.dropShown = state();
clearSelection(root);
out.cleared = state();
// An anchor from a panel that was loaded again is not in this table: a
// shift-click picks only the row clicked.
click("1");
selectionAnchor = { closest: () => null };
rows[0].hidden = false;
rows[4].hidden = false;
clearSelection(root);
selectionAnchor = { closest: () => null };
click("5", true);
out.staleAnchor = state();
console.log(JSON.stringify(out));
"""))

    assert result["initial"]["barHidden"] is True and result["initial"]["moveDisabled"] is True
    assert result["one"]["selected"] == ["2"] and result["one"]["tinted"] == ["2"]
    assert result["one"]["barHidden"] is False and result["one"]["count"] == "1 member selected"
    assert result["one"]["indeterminate"] is True and result["one"]["all"] is False
    # Shift-click picks every shown row between the last click and this one.
    assert result["range"]["selected"] == ["2", "3", "4"] and result["range"]["count"] == "3 members selected"
    # Move needs a group as well as a selection.
    assert result["range"]["moveDisabled"] is True and result["withGroup"]["moveDisabled"] is False
    # With 1 and 5 filtered out, 2-4 are every shown row: the header box is checked.
    assert result["shownAll"]["selected"] == ["2", "3", "4"]
    assert result["shownAll"]["all"] is True and result["shownAll"]["indeterminate"] is False
    # A picked row the filters hide stays picked, and the bar says so.
    assert result["hiddenPicked"]["selected"] == ["1", "2", "3", "4"]
    assert result["hiddenPicked"]["count"] == "4 members selected \u00b7 1 hidden by the filters"
    # The header box drops the shown rows only.
    assert result["dropShown"]["selected"] == ["1"]
    assert result["cleared"]["selected"] == [] and result["cleared"]["barHidden"] is True
    assert result["staleAnchor"]["selected"] == ["5"]


def test_the_summary_says_what_the_move_did():
    script = "\n".join(_extract_js_function(ADMIN_JS, name) for name in ("usernameList", "moveSummary")) + r"""
const full = moveSummary({
  group_name: "Ops", moved: ["a", "b", "c"], unchanged: ["d"], demoted: ["b"],
  llm_needs_attention: ["c"], failed: ["e"], not_found: [9],
});
const plain = moveSummary({ group_name: "Ops", moved: ["a"], unchanged: [], demoted: [], llm_needs_attention: [], failed: [], not_found: [] });
const names = (n) => usernameList(Array.from({ length: n }, (_v, i) => ({ dataset: { memberUsername: "u" + (i + 1) } })));
console.log(JSON.stringify({ full, plain, six: names(6), eight: names(8) }));
"""
    result = _run_node(script)
    assert result["plain"] == "Moved 1 member to Ops."
    assert result["full"] == (
        "Moved 3 members to Ops. 1 was already there. 1 group administrator is now a member. "
        "c needs to sign in to the model provider again. Not moved: e. 1 member no longer exists."
    )
    assert result["six"] == "u1, u2, u3, u4, u5, u6"
    assert result["eight"] == "u1, u2, u3, u4, u5 and 3 more"


def test_large_selections_go_in_batches_and_the_answers_add_up():
    script = "var MOVE_BATCH_SIZE = 500;\n" + _extract_js_function(ADMIN_JS, "sendMoves") + r"""
const sent = [];
async function request(url, options) {
  const body = JSON.parse(options.body);
  sent.push({ url, size: body.user_ids.length, first: body.user_ids[0], group: body.group_id });
  return { group_id: 3, group_name: "Ops", moved: body.user_ids.map(String), unchanged: [], demoted: [], llm_needs_attention: [], failed: [], not_found: [] };
}
(async () => {
  const ids = Array.from({ length: 1200 }, (_v, i) => i + 1);
  const total = await sendMoves(ids, 3);
  console.log(JSON.stringify({ sent, moved: total.moved.length, last: total.moved[total.moved.length - 1] }));
})();
"""
    result = _run_node(script)
    assert [(item["size"], item["first"]) for item in result["sent"]] == [(500, 1), (500, 501), (200, 1001)]
    assert {item["url"] for item in result["sent"]} == {"/api/users/move-to-group"}
    assert result["moved"] == 1200 and result["last"] == "1200"


def test_the_script_sends_every_selected_member_after_a_confirm():
    move = _extract_js_function(ADMIN_JS, "moveSelectedMembers")
    assert "sendMoves(rows.map(" in move and "groupId)" in move
    # Group administrators are named only when some are moving.
    assert 'row.dataset.memberRole === "group_admin"' in move
    assert "A group administrator becomes a member of the new group." not in move
    # Members already in the group are counted out of the title, not out of the request.
    assert move.index("window.showConfirm") < move.index("sendMoves(")
    assert "if (!confirmed) return;" in move
    assert "reloadPanel(summary, partial)" in move
    # Filters keep the bar and the header box in step.
    assert "refreshSelection(root);" in _extract_js_function(ADMIN_JS, "applyMemberFilters")
    assert 'closest("[data-admin-member-select]")' in ADMIN_JS
    assert 'closest("[data-admin-member-bulk-group]")' in ADMIN_JS
