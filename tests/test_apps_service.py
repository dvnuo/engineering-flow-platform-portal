"""The app service: registering, slugs, who may open what, changes, the host kept in step."""

import json

import pytest

from _apps_helpers import (
    REPO_URL,
    SHA_1,
    FakeHost,
    FakeSource,
    add_group,
    add_user,
    enable_apps,
    make_database,
    make_deploy_service,
    remove_from_allowlist,
    reset_apps_state,
)
from app.models import App, AppAuditEvent
from app.services.apps.access import (
    HandoffCodeStore,
    anyone_pending_approval,
    app_enabled,
    can_manage,
    can_open,
    effective_visibility,
    safe_app_next,
    user_payload,
)
from app.services.apps.app_service import AppService, check_env
from app.services.apps.errors import (
    AppConflict,
    AppForbidden,
    AppHostUnavailable,
    AppNotFound,
    AppValidationError,
    DeployInProgress,
    ManifestError,
    ManifestMissing,
    NotListedAsOwner,
    RefNotFound,
    RepoUrlInvalid,
)
from app.services.apps.host_spec import desired_app, host_spec
from app.services.apps.names import RESERVED_SLUGS, check_slug, is_valid_slug, slugify


@pytest.fixture
def world(monkeypatch):
    enable_apps(monkeypatch)
    reset_apps_state()
    factory, _engine = make_database()
    db = factory()
    default = add_group(db, "Default", is_default=True)
    qa = add_group(db, "QA")
    users = {
        "owner": add_user(db, "owner", group=qa, nickname="Olive"),
        "mate": add_user(db, "mate", group=qa),
        "other": add_user(db, "other", group=default),
        "nogroup": add_user(db, "nogroup"),
        "admin": add_user(db, "admin", role="admin", group=default),
        "gadmin": add_user(db, "gadmin", role="group_admin", group=default),
    }
    host = FakeHost()
    source = FakeSource()
    service = AppService(db, host=host, source=source)
    deploys = make_deploy_service(factory, host, source)

    class World:
        pass

    w = World()
    w.db, w.factory, w.users, w.host, w.source, w.service, w.deploys = db, factory, users, host, source, service, deploys
    w.groups = {"default": default, "qa": qa}

    def create(**overrides):
        fields = {"repo_url": REPO_URL, "ref": "main", "entry_dir": "apps/mindmap", "name": "Mind map"}
        fields.update(overrides)
        return service.create(users[fields.pop("owner", "owner")], **fields)

    def deployed(**overrides):
        app = create(**overrides)
        deploys.start_deploy(app, None, users["owner"])
        db.refresh(app)
        assert app.current_release_id
        return app

    w.create, w.deployed = create, deployed
    try:
        yield w
    finally:
        db.close()
        reset_apps_state()


def _actions(db, app):
    return [row.action for row in db.query(AppAuditEvent).filter(AppAuditEvent.app_id == app.id).order_by(AppAuditEvent.id)]


# --- slugs ------------------------------------------------------------------------


@pytest.mark.parametrize("slug", ["abc", "mind-map", "a1b", "0ab", "a" * 40, "x-1-y"])
def test_valid_slugs(slug):
    assert is_valid_slug(slug)
    assert check_slug(slug) == slug


@pytest.mark.parametrize("slug", ["", "ab", "a" * 41, "-ab", "ab-", "Mind", "mind_map", "mind map", "mind.map", "mind/map", "caf\u00e9s"])
def test_slugs_that_break_the_rule(slug):
    assert not is_valid_slug(slug)
    with pytest.raises(AppValidationError) as caught:
        check_slug(slug)
    assert caught.value.code == "slug_invalid"


def test_reserved_slugs_are_the_contracts_list():
    assert RESERVED_SLUGS == {"_efp", "api", "app", "apps", "admin", "assets", "auth", "efp", "health", "login", "logout", "portal", "static", "www"}
    for slug in sorted(RESERVED_SLUGS - {"_efp"}):
        with pytest.raises(AppValidationError) as caught:
            check_slug(slug)
        assert caught.value.code == "slug_reserved"
    assert not is_valid_slug("_efp")


@pytest.mark.parametrize(
    "name, expected",
    [("Mind map", "mind-map"), ("  QA -- Dashboard!  ", "qa-dashboard"), ("R\u00e9sum\u00e9 2026", "r-sum-2026"), ("x" * 60, "x" * 40), ("\u601d\u7ef4\u5bfc\u56fe", "")],
)
def test_slugify(name, expected):
    assert slugify(name) == expected


def test_default_slug_comes_from_the_name_and_avoids_what_is_taken(world):
    first = world.create()
    assert first.slug == "mind-map"
    assert world.create().slug == "mind-map-2"
    assert world.create().slug == "mind-map-3"
    # A reserved word and a name with no ASCII letters still get an address.
    assert world.create(name="API").slug == "api-app"
    generated = world.create(name="\u601d\u7ef4\u5bfc\u56fe").slug
    assert generated.startswith("app-") and is_valid_slug(generated)


def test_explicit_slug_must_be_valid_free_and_not_reserved(world):
    assert world.create(slug="board").slug == "board"
    with pytest.raises(AppConflict) as caught:
        world.create(slug="board")
    assert caught.value.code == "slug_taken" and caught.value.status_code == 409
    with pytest.raises(AppValidationError):
        world.create(slug="Board")
    with pytest.raises(AppValidationError):
        world.create(slug="login")


def test_a_deleted_apps_slug_is_never_given_out_again(world):
    app = world.create(slug="board")
    world.service.delete(app, world.users["owner"])
    assert app.deleted_at is not None
    with pytest.raises(AppConflict):
        world.create(slug="board")
    assert world.create(name="Board").slug == "board-2"
    with pytest.raises(AppNotFound):
        world.service.get(app.id)


# --- registering ------------------------------------------------------------------


def test_create_reads_the_repository_and_fills_the_row(world):
    app = world.create(name="", entry_dir="/apps/mindmap/", repo_url="git@github.com:acme/tools.git")
    assert app.name == "Mind map"  # the manifest's
    assert app.description == "Draw and share mind maps."
    assert app.type == "fastapi"
    assert app.repo_url == REPO_URL
    assert app.repo_ref == "main"
    assert app.entry_dir == "apps/mindmap"
    assert app.visibility == "private"
    assert app.owner_user_id == world.users["owner"].id
    assert app.current_release_id is None
    assert app.llm_enabled is False and app.connectors_enabled == [] and app.env == {}
    assert app.token_value.startswith("efpa_") and len(app.token_value) == 48
    assert len(app.visitor_key) == 44
    assert _actions(world.db, app) == ["app.created"]
    # Nothing reaches the host before the first deployment.
    assert world.host.calls == []


def test_create_refuses_what_a_deployment_would_refuse(world):
    with pytest.raises(RepoUrlInvalid):
        world.create(repo_url="https://example.com/acme/tools")
    with pytest.raises(RefNotFound):
        world.create(ref="nope")
    world.source.manifest = None
    with pytest.raises(ManifestMissing):
        world.create()
    world.source.manifest = {"name": "x"}
    with pytest.raises(ManifestError):
        world.create()
    assert world.db.query(App).count() == 0


def test_the_registering_member_must_be_listed_under_owners(world):
    with pytest.raises(NotListedAsOwner) as caught:
        world.create(owner="other")
    assert caught.value.params == {"username": "other"}
    # A system administrator is a member like any other here.
    with pytest.raises(NotListedAsOwner):
        world.create(owner="admin")
    # An app made from a published template skips the check.
    app = world.service.create(
        world.users["other"], repo_url=REPO_URL, ref="main", name="From template",
        template_app_id="tpl-1", template_version_id="ver-1", skip_ownership_check=True,
    )
    assert app.template_app_id == "tpl-1" and app.owner_user_id == world.users["other"].id


def test_name_and_description_limits(world):
    with pytest.raises(AppValidationError):
        world.create(name="x" * 81)
    with pytest.raises(AppValidationError):
        world.create(description="d" * 501)


# --- who may open what --------------------------------------------------------------

VIEWERS = ("owner", "mate", "other", "nogroup", "admin", "gadmin", None)
MATRIX = {
    # visibility: who may open (the owner's group is QA; "nogroup" sits in the default group)
    "private": {"owner", "admin"},
    "public": {"owner", "mate", "admin"},
    "platform": {"owner", "mate", "other", "nogroup", "admin", "gadmin"},
}


@pytest.mark.parametrize("visibility", sorted(MATRIX))
def test_visibility_matrix(world, visibility):
    app = world.create(visibility=visibility)
    allowed = {name for name in VIEWERS if can_open(world.users.get(name) if name else None, app, world.db)}
    assert allowed == MATRIX[visibility]
    assert effective_visibility(app) == visibility


def test_anyone_needs_the_platform_switch_and_an_administrators_approval(world, monkeypatch):
    with pytest.raises(AppValidationError) as caught:
        world.create(visibility="anyone")
    assert caught.value.code == "anyone_disabled"
    app = world.create()
    with pytest.raises(AppValidationError):
        world.service.update(app, world.users["owner"], visibility="anyone")

    enable_apps(monkeypatch, apps_anyone_enabled=True)
    assert world.service.update(app, world.users["owner"], visibility="anyone") == ["visibility"]
    # Asked for, not approved: every member, no visitor.
    assert app.visibility == "anyone" and app.anyone_approved_by is None
    assert effective_visibility(app) == "platform" and anyone_pending_approval(app)
    assert can_open(world.users["other"], app, world.db)
    assert not can_open(None, app, world.db)
    assert host_spec(world.db, app)["visibility"] == "platform"

    world.service.set_anyone_approval(app, world.users["admin"], True)
    assert app.anyone_approved_by == world.users["admin"].id
    assert effective_visibility(app) == "anyone" and not anyone_pending_approval(app)
    assert can_open(None, app, world.db)
    assert host_spec(world.db, app)["visibility"] == "anyone"

    # The platform switch going off ends it without touching the row.
    enable_apps(monkeypatch, apps_anyone_enabled=False)
    assert effective_visibility(app) == "platform" and not can_open(None, app, world.db)
    enable_apps(monkeypatch, apps_anyone_enabled=True)

    # Leaving "anyone" ends the approval: asking again needs a new one.
    world.service.update(app, world.users["owner"], visibility="private")
    assert app.anyone_approved_by is None
    world.service.update(app, world.users["owner"], visibility="anyone")
    assert effective_visibility(app) == "platform"
    with pytest.raises(AppConflict):
        private = world.create()
        world.service.set_anyone_approval(private, world.users["admin"], True)


def test_an_administrator_setting_anyone_approves_it(world, monkeypatch):
    enable_apps(monkeypatch, apps_anyone_enabled=True)
    app = world.create()
    world.service.update(app, world.users["admin"], visibility="anyone")
    assert app.anyone_approved_by == world.users["admin"].id
    assert effective_visibility(app) == "anyone"
    assert "app.visibility_changed" in _actions(world.db, app)


def test_owner_and_system_administrator_manage(world):
    app = world.create(visibility="platform")
    assert can_manage(world.users["owner"], app)
    assert can_manage(world.users["admin"], app)
    for name in ("mate", "other", "gadmin"):
        assert not can_manage(world.users[name], app)
    assert not can_manage(None, app)

    assert world.service.get_for_manage(app.id, world.users["admin"]).id == app.id
    with pytest.raises(AppForbidden):
        world.service.get_for_manage(app.id, world.users["mate"])
    private = world.create()
    with pytest.raises(AppNotFound):
        world.service.get_for_view(private.id, world.users["mate"])
    with pytest.raises(AppNotFound):
        world.service.get_for_manage(private.id, world.users["mate"])


def test_lists(world):
    mine = world.deployed(name="Alpha", visibility="public")
    world.create(name="Beta draft", visibility="public")  # never deployed
    private = world.deployed(name="Gamma", visibility="private")
    assert [app.name for app in world.service.list_mine(world.users["owner"])] and len(world.service.list_mine(world.users["owner"])) == 3
    assert [app.name for app in world.service.list_mine(world.users["owner"], q="gam")] == ["Gamma"]

    # Shared with a group mate: only what is deployed and enabled, never one's own.
    assert [app.id for app in world.service.list_available(world.users["mate"])] == [mine.id]
    assert world.service.list_available(world.users["other"]) == []
    assert world.service.list_available(world.users["owner"]) == []
    # An administrator's "shared with me" is a member's; everything is the other list.
    assert world.service.list_available(world.users["admin"]) == []
    assert len(world.service.list_all()) == 3 and private.id in {app.id for app in world.service.list_all()}

    world.service.disable(mine, world.users["admin"], "Under review")
    assert world.service.list_available(world.users["mate"]) == []


def test_an_app_is_disabled_by_an_administrator_or_by_its_owner_losing_access(world):
    app = world.deployed()
    assert app_enabled(world.db, app) == (True, None)
    assert host_spec(world.db, app)["enabled"] is True

    world.service.disable(app, world.users["admin"], "Leaks customer data")
    assert app_enabled(world.db, app) == (False, "Leaks customer data")
    assert world.host.specs[app.id]["enabled"] is False
    assert world.host.specs[app.id]["disabled_reason"] == "Leaks customer data"
    world.service.enable(app, world.users["admin"])
    assert world.host.specs[app.id]["enabled"] is True
    with pytest.raises(AppValidationError):
        world.service.disable(app, world.users["admin"], "  ")

    remove_from_allowlist(world.db, "owner")
    assert app_enabled(world.db, app) == (False, "owner_inactive")
    spec = host_spec(world.db, app)
    assert spec["enabled"] is False and "owner" in spec["disabled_reason"].lower()
    assert desired_app(world.db, app)["enabled"] is False


def test_user_payload_names_the_group_and_falls_back_to_the_default_one(world):
    assert user_payload(world.db, world.users["owner"]) == {
        "id": world.users["owner"].id, "username": "owner", "display_name": "Olive",
        "group_id": world.groups["qa"].id, "group_name": "QA",
    }
    payload = user_payload(world.db, world.users["nogroup"])
    assert payload["display_name"] == "nogroup"
    assert (payload["group_id"], payload["group_name"]) == (world.groups["default"].id, "Default")


# --- the host's record ----------------------------------------------------------------


def test_host_spec_and_desired_state(world):
    app = world.create(visibility="public")
    world.service.update(app, world.users["owner"], env={"MY_SETTING": "x"})
    spec = host_spec(world.db, app)
    assert spec == {
        "slug": "mind-map",
        "name": "Mind map",
        "owner_username": "owner",
        "visibility": "public",
        "enabled": True,
        "disabled_reason": None,
        "visitor_key": app.visitor_key,
        "env": {"MY_SETTING": "x", "EFP_APP_TOKEN": app.token_value},
        "limits": {"memory_mb": None, "data_mb": None},
    }
    assert desired_app(world.db, app) == {"id": app.id, **spec, "current_sha": None}
    world.deploys.start_deploy(app, None, world.users["owner"])
    world.db.refresh(app)
    assert desired_app(world.db, app)["current_sha"] == SHA_1


# --- changes ------------------------------------------------------------------------


def test_update_changes_only_what_was_sent_and_audits_it(world):
    app = world.create()
    changed = world.service.update(app, world.users["owner"], name="Mind maps", description=None, llm_enabled=True)
    assert changed == ["name", "description", "llm_enabled"]
    assert (app.name, app.description, app.llm_enabled) == ("Mind maps", None, True)
    assert world.service.update(app, world.users["owner"], name="Mind maps") == []
    world.service.update(app, world.users["owner"], connectors_enabled=["jira", "github"], connectors_write=["jira", "nexus"])
    # A connector the app may write to is one it may call.
    assert app.connectors_enabled == ["jira", "github"] and app.connectors_write == ["jira"]
    world.service.update(app, world.users["owner"], connectors_enabled=["github"])
    assert app.connectors_write == []
    with pytest.raises(AppValidationError):
        world.service.update(app, world.users["owner"], connectors_enabled=["aws"])
    world.service.update(app, world.users["owner"], repo_ref="release/1.0")
    assert app.repo_ref == "release/1.0"
    assert _actions(world.db, app) == ["app.created", "app.updated", "app.updated", "app.updated", "app.updated"]


def test_nothing_is_changed_when_one_value_is_bad(world):
    app = world.create()
    with pytest.raises(AppValidationError):
        world.service.update(app, world.users["owner"], name="Renamed", visibility="everyone")
    world.db.refresh(app)
    assert app.name == "Mind map"
    assert _actions(world.db, app) == ["app.created"]


def test_env_rules_and_audit_without_values(world):
    app = world.create()
    world.service.update(app, world.users["owner"], env={"API_BASE": "https://x", "SECRET_KEY": "s3cr3t-value", "COUNT": 3})
    assert app.env == {"API_BASE": "https://x", "SECRET_KEY": "s3cr3t-value", "COUNT": "3"}
    world.service.update(app, world.users["owner"], env={"API_BASE": "https://y", "NEW": "1"})
    events = world.db.query(AppAuditEvent).filter(AppAuditEvent.action == "app.env_changed").order_by(AppAuditEvent.id).all()
    assert events[-1].detail == {"added": ["NEW"], "removed": ["COUNT", "SECRET_KEY"], "changed": ["API_BASE"]}
    assert "s3cr3t-value" not in json.dumps([event.detail_json for event in events])

    for bad in ({"EFP_APP_TOKEN": "x"}, {"efp_x": "1"}, {"PORT": "1"}, {"PATH": "/x"}, {"HOME": "/"}, {"TMPDIR": "/"}, {"1X": "1"}, {"A-B": "1"}, {"": "1"}, {"A": None}, {"A": {"b": 1}}, {"A": "x" * 8193}, "A=1"):
        with pytest.raises(AppValidationError):
            check_env(bad)
    with pytest.raises(AppValidationError):
        check_env({f"V{index}": "1" for index in range(101)})
    assert check_env(None) == {}


def test_daily_limits_are_a_system_administrators(world):
    app = world.create()
    with pytest.raises(AppForbidden):
        world.service.update(app, world.users["owner"], daily_credits=500)
    assert world.service.update(app, world.users["admin"], daily_credits=12.5, daily_tokens=1000) == ["daily_credits", "daily_tokens"]
    assert (app.daily_credits, app.daily_tokens) == (12.5, 1000)
    # Sending the stored value back is not a change, so an owner's form may carry it.
    assert world.service.update(app, world.users["owner"], daily_credits=12.5, name="Renamed") == ["name"]
    world.service.update(app, world.users["admin"], daily_credits=None)
    assert app.daily_credits is None
    with pytest.raises(AppValidationError):
        world.service.update(app, world.users["admin"], daily_tokens=-1)


def test_the_host_is_told_before_a_change_is_saved(world):
    app = world.deployed()
    world.host.calls.clear()
    world.service.update(app, world.users["owner"], visibility="public", env={"A": "1"})
    assert world.host.names() == ["put_app"]
    assert world.host.specs[app.id]["visibility"] == "public"
    assert world.host.env[app.id] == {"A": "1", "EFP_APP_TOKEN": app.token_value}

    # A change the host does not need to know about does not go to it.
    world.host.calls.clear()
    world.service.update(app, world.users["owner"], description="New words", llm_enabled=True)
    assert world.host.calls == []

    # The host is away: the narrowing is refused rather than recorded here alone.
    world.host.fail["put_app"] = AppHostUnavailable()
    with pytest.raises(AppHostUnavailable):
        world.service.update(app, world.users["owner"], visibility="private")
    world.db.refresh(app)
    assert app.visibility == "public"
    assert world.host.specs[app.id]["visibility"] == "public"
    assert "app.visibility_changed" in _actions(world.db, app) and _actions(world.db, app).count("app.visibility_changed") == 1


def test_an_app_the_host_has_never_seen_changes_freely(world):
    app = world.create()
    world.host.fail["put_app"] = AppHostUnavailable()
    world.service.update(app, world.users["owner"], visibility="public", env={"A": "1"})
    assert app.visibility == "public" and world.host.calls == []


# --- lifecycle ------------------------------------------------------------------------


def test_start_stop_restart(world):
    app = world.deployed()
    world.host.calls.clear()
    assert world.service.stop(app, world.users["owner"])["state"] == "stopped"
    assert app.desired_state == "stopped" and world.host.names() == ["stop"]
    # A start carries the app's record again, so whatever the host missed goes with it.
    world.host.calls.clear()
    assert world.service.start(app, world.users["owner"])["state"] == "running"
    assert app.desired_state == "running" and world.host.names() == ["put_app", "start"]
    world.host.calls.clear()
    world.service.restart(app, world.users["admin"])
    assert world.host.names() == ["put_app", "restart"]
    assert _actions(world.db, app)[-3:] == ["app.stopped", "app.started", "app.restarted"]


def test_lifecycle_needs_a_deployment_and_an_enabled_app(world):
    app = world.create()
    with pytest.raises(AppConflict) as caught:
        world.service.start(app, world.users["owner"])
    assert caught.value.code == "not_deployed"

    app = world.deployed(name="Second")
    world.service.disable(app, world.users["admin"], "Paused")
    with pytest.raises(AppConflict) as caught:
        world.service.start(app, world.users["owner"])
    assert caught.value.code == "app_disabled"
    world.service.stop(app, world.users["owner"])  # stopping a disabled app is fine

    world.service.enable(app, world.users["admin"])
    world.host.fail["start"] = AppHostUnavailable()
    with pytest.raises(AppHostUnavailable):
        world.service.start(app, world.users["owner"])
    world.db.refresh(app)
    assert app.desired_state == "stopped"


def test_status_live_then_from_the_last_report(world):
    app = world.create()
    result = world.service.status(app)
    assert result == {"host_reachable": True, "live": True, "status": None, "reported_at": None, "error": None}

    world.deploys.start_deploy(app, None, world.users["owner"])
    world.db.refresh(app)
    live = world.service.status(app)
    assert live["live"] is True and live["status"]["state"] == "running" and live["error"] is None

    world.host.fail["get_app"] = AppHostUnavailable()
    stale = world.service.status(app)
    assert stale["host_reachable"] is False and stale["live"] is False
    assert stale["status"]["state"] == "running"  # what the host said last
    assert stale["error"].code == "host_unreachable"
    assert stale["reported_at"] is not None


def test_delete_takes_the_app_off_the_host(world):
    app = world.deployed()
    world.host.fail["delete_app"] = AppHostUnavailable()
    with pytest.raises(AppHostUnavailable):
        world.service.delete(app, world.users["owner"])
    world.db.refresh(app)
    assert app.deleted_at is None  # it would have kept running there

    world.host.fail.clear()
    world.service.delete(app, world.users["owner"], purge_data=True)
    assert world.host.deleted == [(app.id, True)]
    assert app.deleted_at is not None and app.desired_state == "stopped"
    assert _actions(world.db, app)[-1] == "app.deleted"
    assert not can_open(world.users["owner"], app, world.db)

    # One nobody deployed is taken off the host too: the host registers every
    # app the Portal lists when it boots, deployed or not.
    draft = world.create(name="Draft")
    world.host.calls.clear()
    world.host.fail["delete_app"] = AppHostUnavailable()
    with pytest.raises(AppHostUnavailable):
        world.service.delete(draft, world.users["owner"])
    world.db.refresh(draft)
    assert draft.deleted_at is None
    world.host.fail.clear()
    world.service.delete(draft, world.users["owner"])
    assert world.host.deleted[-1] == (draft.id, False) and draft.deleted_at is not None


def test_delete_waits_for_a_running_deployment(world):
    from app.models import AppRelease

    app = world.create()
    world.db.add(AppRelease(app_id=app.id, kind="deploy", status="running", steps_json="[]"))
    world.db.commit()
    with pytest.raises(DeployInProgress):
        world.service.delete(app, world.users["owner"])


def test_clear_data(world):
    app = world.deployed()
    assert world.service.clear_data(app, world.users["owner"])["data_bytes"] == 0
    assert "delete_data" in world.host.names()
    assert _actions(world.db, app)[-1] == "app.data_cleared"


def test_transfer_gives_the_new_owner_a_clean_app(world):
    app = world.deployed()
    world.service.update(app, world.users["owner"], llm_enabled=True, connectors_enabled=["jira"], connectors_write=["jira"])
    old_token = app.token_value
    world.service.transfer_owner(app, world.users["admin"], world.users["other"])
    assert app.owner_user_id == world.users["other"].id
    assert app.token_value != old_token
    assert (app.llm_enabled, app.connectors_enabled, app.connectors_write) == (False, [], [])
    assert world.host.specs[app.id]["owner_username"] == "other"
    assert world.host.env[app.id]["EFP_APP_TOKEN"] == app.token_value
    event = world.db.query(AppAuditEvent).filter(AppAuditEvent.action == "app.owner_changed").one()
    assert event.detail == {"from": "owner", "to": "other"} and event.actor_user_id == world.users["admin"].id
    assert old_token not in (event.detail_json or "")

    remove_from_allowlist(world.db, "mate")
    with pytest.raises(AppValidationError):
        world.service.transfer_owner(app, world.users["admin"], world.users["mate"])


# --- hand-off codes and next --------------------------------------------------------------


def test_handoff_codes_work_once_for_sixty_seconds():
    now = {"value": 1000.0}
    store = HandoffCodeStore(clock=lambda: now["value"])
    code = store.issue("app-1", 12)
    assert len(code) >= 43 and code != store.issue("app-1", 12)
    grant = store.redeem(code)
    assert (grant.app_id, grant.user_id) == ("app-1", 12)
    assert store.redeem(code) is None  # once

    late = store.issue("app-1", 12)
    now["value"] += 59.9
    assert store.redeem(late) is not None
    expired = store.issue("app-2", 7)
    now["value"] += 60.0
    assert store.redeem(expired) is None
    for junk in (None, "", "nope", 5, ["x"]):
        assert store.redeem(junk) is None


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, "/mindmap/"),
        ("", "/mindmap/"),
        ("/mindmap/", "/mindmap/"),
        ("/mindmap/board?id=3&x=%2F", "/mindmap/board?id=3&x=%2F"),
        ("/mindmap", "/mindmap/"),
        ("/mindmapper/", "/mindmap/"),
        ("/other/", "/mindmap/"),
        ("https://evil.example/mindmap/", "/mindmap/"),
        ("//evil.example/mindmap/", "/mindmap/"),
        ("/mindmap/../other/", "/mindmap/"),
        ("/mindmap/./x", "/mindmap/"),
        ("/mindmap/%2e%2e/other/", "/mindmap/"),
        ("/mindmap/.%2E/other/", "/mindmap/"),
        ("/mindmap/%2e/x", "/mindmap/"),
        ("/mindmap/v1%2e2/notes", "/mindmap/v1%2e2/notes"),
        ("/mindmap/\\evil", "/mindmap/"),
        ("/mindmap/a\r\nSet-Cookie: x=1", "/mindmap/"),
        ("/mindmap/" + "a" * 2000, "/mindmap/"),
    ],
    ids=["none", "empty", "home", "path-and-query", "no-slash", "prefix-of-another", "other-app", "absolute", "scheme-relative", "parent", "dot", "encoded-parent", "half-encoded-parent", "encoded-dot", "encoded-dot-in-a-name", "backslash", "header-injection", "too-long"],
)
def test_next_is_only_ever_a_path_of_the_same_app(value, expected):
    assert safe_app_next("mindmap", value) == expected
