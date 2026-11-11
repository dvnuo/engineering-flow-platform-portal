"""Groups: one group per member, Default connectors layered platform -> group -> member,
system and group administrators, and "public" meaning the owner's group.

app/services/group_service.py, connector_defaults_service.group_seed,
app/services/authorization.py, app/services/work_visibility.py,
docs/CONNECTORS_CONTRACT.md section 7.1.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, AgentTask, Group, User, UserAllowlistEntry
from app.models.runtime_profile import RuntimeProfile
from app.services.connector_defaults_service import (
    MODE_CUSTOM,
    MODE_SYSTEM,
    connector_mode,
    group_seed,
    parse_connector_modes,
    parse_group_modes,
)
from app.services.connector_defaults_sync import sync_followers
from app.services.connector_registry import get_connector_spec
from app.services.group_service import GroupService, member_llm_after_group_change
from app.services.runtime_profile_seed_service import RuntimeProfileSeedService
from app.services.runtime_profile_service import RuntimeProfileService
from app.services.work_visibility import can_see

JIRA = get_connector_spec("jira")
PROXY = get_connector_spec("proxy")

PLATFORM_JIRA = {"enabled": True, "instances": [{"name": "Corp", "url": "https://corp.atlassian.net", "project": "ABC"}]}
DEV_JIRA = {"enabled": True, "instances": [{"name": "Dev", "url": "https://dev.atlassian.net", "project": "DEV"}]}
PLATFORM_PROXY = {"enabled": True, "proxies": [{"name": "corp", "url": "http://proxy.corp:3128"}], "default": "corp"}
PLATFORM_SEED = {
    "jira": PLATFORM_JIRA,
    "confluence": {"enabled": True, "instances": [{"name": "Wiki", "url": "https://wiki.corp"}]},
    "proxy": PLATFORM_PROXY,
    "llm": {"provider": "github_copilot", "max_steps": 40},
}


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)()


def _group(db, name, *, default=False):
    group = Group(name=name, is_default=default, config_json="{}", connector_modes_json="{}")
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


def _user(db, name, *, role="user", group=None, allowlisted=True):
    user = User(username=name, password_hash="test", role=role, is_active=True, group_id=group.id if group else None)
    db.add(user)
    if allowlisted:
        db.add(UserAllowlistEntry(username=name, role=role, is_active=True, group_id=group.id if group else None))
    db.commit()
    db.refresh(user)
    return user


def _world():
    """A platform seed, the default group, Dev (sets its own Jira) and Ops (follows the platform)."""

    db = _session()
    RuntimeProfileSeedService(db).save_seed(PLATFORM_SEED)
    default = _group(db, "Default", default=True)
    dev = _group(db, "Dev")
    ops = _group(db, "Ops")
    GroupService(db).save_group_defaults(dev, {"jira": DEV_JIRA}, {"jira": "custom", "confluence": "none"})
    return db, default, dev, ops


def _jira_url(config):
    return config["jira"]["instances"][0]["url"]


# ------------------------------------------------------------------ layering


def test_a_group_replaces_the_connectors_it_sets_and_follows_the_platform_for_the_rest():
    modes = parse_group_modes(json.dumps({"jira": "custom", "confluence": "custom", "llm": "custom", "proxy": "custom"}))
    # The proxy is the platform's: a group cannot even store a choice for it.
    assert modes == {"jira": MODE_CUSTOM, "confluence": MODE_CUSTOM, "llm": MODE_CUSTOM}

    seed = group_seed(PLATFORM_SEED, {"jira": DEV_JIRA, "llm": {"provider": "ai_platform"}}, modes)

    assert seed["jira"] == DEV_JIRA
    assert "confluence" not in seed  # set by the group with no values: not provided
    assert seed["proxy"] == PLATFORM_PROXY
    # A group's model defaults without a step limit keep following the platform's.
    assert seed["llm"] == {"provider": "ai_platform", "max_steps": 40}
    assert group_seed(PLATFORM_SEED, {}, {}) == PLATFORM_SEED


def test_members_follow_their_own_groups_defaults():
    db, default, dev, ops = _world()
    service = RuntimeProfileService(db)
    alice = _user(db, "alice", group=dev)
    bob = _user(db, "bob", group=ops)
    carol = _user(db, "carol")  # no group: the default group

    effective = {
        user.username: service.effective_config_for(service.get_or_create_for_user(user)) for user in (alice, bob, carol)
    }

    assert _jira_url(effective["alice"]) == "https://dev.atlassian.net"
    assert "confluence" not in effective["alice"]
    assert _jira_url(effective["bob"]) == "https://corp.atlassian.net"
    assert _jira_url(effective["carol"]) == "https://corp.atlassian.net"
    for config in effective.values():
        assert config["proxy"]["default"] == "corp"


def test_a_platform_change_reaches_only_the_groups_that_follow_it():
    db, default, dev, ops = _world()
    new_platform = {**PLATFORM_SEED, "jira": {"enabled": True, "instances": [{"name": "Corp", "url": "https://new.corp"}]}}

    changes = GroupService(db).changes_by_group(PLATFORM_SEED, new_platform)

    assert changes[dev.id] == []
    assert changes[ops.id] == ["jira"]
    assert changes[default.id] == ["jira"]


def test_sync_followers_updates_only_the_members_of_the_changed_group():
    db, default, dev, ops = _world()
    service = RuntimeProfileService(db)
    alice = _user(db, "alice", group=dev)
    bob = _user(db, "bob", group=ops)
    for user in (alice, bob):
        service.get_or_create_for_user(user)
    alice_id = alice.id

    class Recorder:
        def __init__(self):
            self.owners = []

        def apply_profile_save(self, _db, profile):
            self.owners.append(profile.owner_user_id)
            return {}

    recorder = Recorder()
    summary = sync_followers(
        ["jira"],
        secret_service=recorder,
        session_factory=lambda: db,
        changed_by_group={dev.id: ["jira"]},
        audit_target=("group", str(dev.id)),
    )

    assert recorder.owners == [alice_id]
    assert summary["members"] == 1


def test_a_group_cannot_set_a_value_the_platform_would_refuse():
    db, default, dev, ops = _world()
    bad = {"default_instance": "Prod", "instances": [{"name": "Production", "url": "https://n"}]}

    with pytest.raises(ValueError, match="Default Nexus instance Prod"):
        GroupService(db).save_group_defaults(ops, {"nexus": bad}, {"nexus": "custom"})
    db.refresh(ops)
    assert parse_group_modes(ops.connector_modes_json) == {}


# ------------------------------------------------------------------ proxy


def test_the_proxy_is_the_platforms_for_every_member():
    db, default, dev, ops = _world()
    service = RuntimeProfileService(db)
    alice = _user(db, "alice", group=dev)
    profile = service.get_or_create_for_user(alice)

    assert connector_mode(PROXY, {"proxy": MODE_CUSTOM}) == MODE_SYSTEM
    assert parse_connector_modes(json.dumps({"proxy": "custom", "jira": "custom"})) == {"jira": MODE_CUSTOM}
    profile, restored = service.customize_connector(profile, PROXY)
    assert restored is False
    assert "proxy" not in service.connector_modes(profile)

    # Writing a proxy of one's own neither applies nor makes it custom.
    config = json.loads(profile.config_json)
    config["proxy"] = {"enabled": True, "proxies": [{"name": "mine", "url": "http://mine:8080"}], "default": "mine"}
    profile, _changed = service.save_config(profile, config)
    assert service.effective_config_for(profile)["proxy"]["default"] == "corp"
    assert "proxy" not in service.connector_modes(profile)


def test_rows_with_their_own_proxy_are_released_to_the_platforms_and_rolled_out():
    db, default, dev, ops = _world()
    alice = _user(db, "alice", group=ops)
    bob = _user(db, "bob", group=ops)
    own_proxy = {"enabled": True, "proxies": [{"name": "mine", "url": "http://mine:8080"}], "default": "mine"}
    custom = RuntimeProfile(
        owner_user_id=alice.id,
        name="Default",
        config_json=json.dumps({"proxy": own_proxy}),
        connector_modes_json=json.dumps({"proxy": "custom", "jira": "custom"}),
        revision=3,
        is_default=True,
    )
    following = RuntimeProfile(
        owner_user_id=bob.id, name="Default", config_json="{}", connector_modes_json="{}", revision=1, is_default=True
    )
    db.add_all([custom, following])
    db.commit()

    released = RuntimeProfileService(db).release_platform_only_connectors()

    assert [profile.id for profile in released] == [custom.id]
    db.refresh(custom)
    assert custom.revision == 4
    assert json.loads(custom.connector_modes_json) == {"jira": "custom"}
    # The member's own values stay parked in the row.
    assert json.loads(custom.config_json)["proxy"]["default"] == "mine"
    assert RuntimeProfileService(db).release_platform_only_connectors() == []


# ------------------------------------------------------------------ moving members


def test_model_defaults_follow_the_group_and_shared_credentials_never_cross_it():
    old = {"provider": "ai_platform", "model": "gpt-5", "ai_platform": {"auth": {"username": "dev-svc", "password": "p", "usercase": "dev"}}}
    new = {"provider": "github_copilot", "model": "gpt-5.4", "api_key": "ops-shared"}

    untouched, attention = member_llm_after_group_change(old, old, new)
    assert untouched == new and attention is False

    # Their own model choice stays; the old group's account goes.
    chosen = {**old, "model": "claude-x"}
    kept, attention = member_llm_after_group_change(chosen, old, new)
    assert kept == {"provider": "ai_platform", "model": "claude-x"} and attention is True

    # A personal Copilot sign-in survives the move.
    personal = {"provider": "github_copilot", "model": "gpt-5", "api_key": "gho_personal"}
    old_copilot = {"provider": "github_copilot", "model": "gpt-5"}
    moved, attention = member_llm_after_group_change(personal, old_copilot, new)
    assert moved == {"provider": "github_copilot", "model": "gpt-5.4", "api_key": "gho_personal"}
    assert attention is False


def test_moving_a_member_follows_the_new_group_and_a_group_admin_becomes_a_member():
    db, default, dev, ops = _world()
    service = RuntimeProfileService(db)
    lead = _user(db, "lead", role="group_admin", group=dev)
    profile = service.get_or_create_for_user(lead)
    revision = profile.revision

    result = GroupService(db).move_member(lead, ops, actor_id=None)

    db.refresh(lead)
    entry = db.query(UserAllowlistEntry).filter_by(username="lead").one()
    assert lead.group_id == ops.id and entry.group_id == ops.id
    assert result.demoted and lead.role == "user" and entry.role == "user"
    db.refresh(profile)
    assert result.profile_id == profile.id and profile.revision == revision + 1
    assert _jira_url(service.effective_config_for(profile)) == "https://corp.atlassian.net"


def test_deleting_a_group_moves_its_members_and_pending_entries():
    db, default, dev, ops = _world()
    alice = _user(db, "alice", group=dev)
    db.add(UserAllowlistEntry(username="pending", role="group_admin", is_active=True, group_id=dev.id))
    db.commit()

    result = GroupService(db).delete_group(dev, ops)

    db.refresh(alice)
    assert alice.group_id == ops.id
    assert result.pending_entries == 1
    pending = db.query(UserAllowlistEntry).filter_by(username="pending").one()
    assert pending.group_id == ops.id and pending.role == "user"
    assert db.get(Group, dev.id) is None
    with pytest.raises(ValueError):
        GroupService(db).delete_group(default, ops)


# ------------------------------------------------------------------ visibility


def _agent(db, owner, visibility):
    agent = Agent(
        name=f"agent-{owner.username}-{visibility}",
        owner_user_id=owner.id,
        visibility=visibility,
        status="running",
        image="example/image:latest",
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp-agents",
        deployment_name="dep",
        service_name="svc",
        pvc_name="pvc",
        endpoint_path="/",
        agent_type="workspace",
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


def test_public_reaches_the_owners_group_and_platform_reaches_everyone():
    from app.repositories.agent_repo import AgentRepository
    from app.repositories.agent_task_repo import AgentTaskRepository

    db, default, dev, ops = _world()
    owner = _user(db, "owner", group=dev)
    teammate = _user(db, "teammate", group=dev)
    outsider = _user(db, "outsider", group=ops)
    admin = _user(db, "root", role="admin", group=ops)
    group_admin = _user(db, "lead", role="group_admin", group=dev)
    public = _agent(db, owner, "public")
    platform = _agent(db, owner, "platform")
    private = _agent(db, owner, "private")

    assert can_see(public, teammate) and not can_see(public, outsider)
    assert can_see(platform, outsider)
    assert can_see(private, admin) and not can_see(private, group_admin)
    assert {agent.id for agent in AgentRepository(db).list_public(outsider)} == {platform.id}
    assert {agent.id for agent in AgentRepository(db).list_public(teammate)} == {public.id, platform.id}

    task = AgentTask(
        assignee_agent_id=public.id, owner_user_id=owner.id, visibility="public", source="manual", task_type="generic", status="done"
    )
    db.add(task)
    db.commit()
    repo = AgentTaskRepository(db)
    assert [row.id for row in repo.list_visible_to_user(user_id=teammate.id)] == [task.id]
    assert repo.list_visible_to_user(user_id=outsider.id) == []


# ------------------------------------------------------------------ API


@pytest.fixture
def api(monkeypatch):
    from app.db import get_db
    from app.main import app
    import app.api.groups as groups_api
    import app.api.users as users_api
    import app.deps as deps_module

    db, default, dev, ops = _world()
    state = {"user": None, "rollouts": [], "syncs": []}

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[deps_module.get_current_user] = lambda: state["user"]
    monkeypatch.setattr(users_api, "rollout_profiles", lambda ids, **_kw: state["rollouts"].append(list(ids)))
    monkeypatch.setattr(groups_api, "rollout_profiles", lambda ids, **_kw: state["rollouts"].append(list(ids)))
    monkeypatch.setattr(groups_api, "sync_followers", lambda changed, **kw: state["syncs"].append(kw.get("changed_by_group")))
    monkeypatch.setattr(users_api.settings, "bootstrap_admin_username", "root")
    state.update(db=db, default=default, dev=dev, ops=ops, client=TestClient(app))
    try:
        yield state
    finally:
        app.dependency_overrides.clear()


def test_a_system_administrator_names_the_group_of_every_allowlist_entry(api):
    db, dev = api["db"], api["dev"]
    api["user"] = _user(db, "root", role="admin", group=api["default"])
    client = api["client"]

    missing = client.post("/api/users/allowlist", json={"username": "newbie", "role": "user"})
    assert missing.status_code == 422

    added = client.post("/api/users/allowlist", json={"username": "newbie", "role": "user", "group_id": dev.id})
    assert added.status_code == 200 and added.json()["group_id"] == dev.id

    bulk = client.post("/api/users/allowlist/bulk", json={"usernames": ["newone", "newtwo"], "role": "group_admin", "group_id": dev.id})
    assert bulk.status_code == 200 and bulk.json()["added"] == ["newone", "newtwo"]
    rows = db.query(UserAllowlistEntry).filter(UserAllowlistEntry.username.in_(["newone", "newtwo"])).all()
    assert {row.group_id for row in rows} == {dev.id}


def test_a_group_administrator_adds_to_their_own_group_only(api):
    db, dev, ops, default = api["db"], api["dev"], api["ops"], api["default"]
    api["user"] = _user(db, "lead", role="group_admin", group=dev)
    _user(db, "taken", group=ops)
    waiting = _user(db, "waiting", group=default, allowlisted=False)
    RuntimeProfileService(db).get_or_create_for_user(waiting)
    client = api["client"]

    own = client.post("/api/users/allowlist", json={"username": "newbie"})
    assert own.status_code == 200 and own.json()["group_id"] == dev.id
    assert client.post("/api/users/allowlist", json={"username": "other1", "group_id": ops.id}).status_code == 403
    assert client.post("/api/users/allowlist", json={"username": "other2", "role": "admin"}).status_code == 403
    assert client.post("/api/users/allowlist", json={"username": "taken"}).status_code == 409

    # An account that signed in before anyone allowlisted it waits in the
    # default group; its group administrator claims it, which is a move.
    claimed = client.post("/api/users/allowlist", json={"username": "waiting"})
    assert claimed.status_code == 200
    db.refresh(waiting)
    assert waiting.group_id == dev.id
    assert api["rollouts"], "the claimed member's changed settings are rolled out"


def test_a_group_administrator_manages_roles_inside_the_group_only(api):
    db, dev, ops = api["db"], api["dev"], api["ops"]
    api["user"] = _user(db, "lead", role="group_admin", group=dev)
    member = _user(db, "member", group=dev)
    root = _user(db, "root", role="admin", group=dev)
    outsider = _user(db, "outsider", group=ops)
    client = api["client"]

    promoted = client.patch(f"/api/users/{member.id}", json={"role": "group_admin"})
    assert promoted.status_code == 200 and promoted.json()["role"] == "group_admin"
    assert client.patch(f"/api/users/{member.id}", json={"role": "admin"}).status_code == 403
    assert client.patch(f"/api/users/{root.id}", json={"role": "user"}).status_code == 403
    assert client.patch(f"/api/users/{outsider.id}", json={"role": "group_admin"}).status_code == 404
    assert client.patch(f"/api/users/{member.id}", json={"group_id": ops.id}).status_code == 403

    overview = client.get("/api/users/admin-overview").json()
    assert {row["username"] for row in overview["users"]} == {"lead", "member", "root"}
    # Other groups stay unseen, their names included.
    assert [group["id"] for group in overview["groups"]] == [dev.id]
    assert {row["username"] for row in client.get("/api/users").json()} == {"lead", "member", "root"}


def test_an_allowlist_entry_is_stored_with_its_group_in_one_commit():
    # An account created from the entry at any moment after the commit (a
    # first SSO sign-in, say) must already find the group on it.
    from app.repositories.user_allowlist_repo import UserAllowlistRepository

    db, default, dev, ops = _world()
    repo = UserAllowlistRepository(db)
    commits = []
    original_commit = db.commit

    def counting_commit():
        commits.append(1)
        original_commit()

    db.commit = counting_commit
    created = repo.ensure("fresh", role="user", reactivate=True, group_id=dev.id)
    assert created.group_id == dev.id and len(commits) == 1

    commits.clear()
    again = repo.ensure("fresh", role="user", reactivate=True, group_id=ops.id)
    assert again.group_id == ops.id and len(commits) == 1


def test_a_system_administrator_moves_members_between_groups(api):
    db, dev, ops = api["db"], api["dev"], api["ops"]
    api["user"] = _user(db, "root", role="admin", group=api["default"])
    member = _user(db, "member", group=dev)
    RuntimeProfileService(db).get_or_create_for_user(member)

    moved = api["client"].patch(f"/api/users/{member.id}", json={"group_id": ops.id})

    assert moved.status_code == 200 and moved.json()["group_id"] == ops.id
    assert len(api["rollouts"]) == 1


def test_groups_api_scopes_a_group_administrator_to_their_group(api):
    db, dev, ops = api["db"], api["dev"], api["ops"]
    api["user"] = _user(db, "lead", role="group_admin", group=dev)
    client = api["client"]

    assert [row["id"] for row in client.get("/api/groups").json()] == [dev.id]
    assert client.post("/api/groups", json={"name": "Mine"}).status_code == 403
    assert client.get(f"/api/groups/{ops.id}/defaults").status_code == 404

    saved = client.put(
        f"/api/groups/{dev.id}/defaults",
        json={"choices": {"jira": "platform", "proxy": "custom"}, "config": {"proxy": PLATFORM_PROXY}},
    )
    assert saved.status_code == 200
    assert saved.json()["choices"]["jira"] == "platform"
    assert "proxy" not in saved.json()["choices"]
    assert api["syncs"] == [{dev.id: ["jira"]}]
    # The platform's credentials never reach a group administrator.
    assert "[REDACTED]" not in json.dumps(saved.json()["config"])


def test_system_administrators_create_rename_and_delete_groups(api):
    db, dev, ops, default = api["db"], api["dev"], api["ops"], api["default"]
    api["user"] = _user(db, "root", role="admin", group=default)
    member = _user(db, "member", group=dev)
    client = api["client"]

    created = client.post("/api/groups", json={"name": "QA", "description": "Testers"})
    assert created.status_code == 200
    assert client.post("/api/groups", json={"name": "qa"}).status_code == 409
    renamed = client.patch(f"/api/groups/{created.json()['id']}", json={"name": "Quality"})
    assert renamed.json()["name"] == "Quality"

    assert client.delete(f"/api/groups/{default.id}?move_to={ops.id}").status_code == 409
    deleted = client.delete(f"/api/groups/{dev.id}?move_to={ops.id}")
    assert deleted.status_code == 200 and deleted.json()["moved"] == 1
    db.refresh(member)
    assert member.group_id == ops.id


def test_only_a_system_administrator_shares_an_assistant_with_every_group(api):
    db, dev = api["db"], api["dev"]
    owner = _user(db, "owner", group=dev)
    agent = _agent(db, owner, "private")
    api["user"] = owner
    client = api["client"]

    assert client.post(f"/api/agents/{agent.id}/share?scope=platform").status_code == 403
    shared = client.post(f"/api/agents/{agent.id}/share")
    assert shared.status_code == 200 and shared.json()["visibility"] == "public"

    api["user"] = _user(db, "root", role="admin", group=dev)
    everyone = client.post(f"/api/agents/{agent.id}/share?scope=platform")
    assert everyone.status_code == 200 and everyone.json()["visibility"] == "platform"
