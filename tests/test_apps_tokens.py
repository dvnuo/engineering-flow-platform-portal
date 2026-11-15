"""App tokens: their format, the lookup the SDK endpoints use, last use, and resetting."""

import base64
from datetime import datetime
import hashlib
import logging
import re

import pytest

from _apps_helpers import (
    REPO_URL,
    FakeHost,
    FakeSource,
    add_user,
    enable_apps,
    make_database,
    make_deploy_service,
    reset_apps_state,
)
from app.models import App, AppAuditEvent
from app.services.apps import tokens
from app.services.apps.app_service import AppService
from app.services.apps.errors import AppHostUnavailable
from app.services.apps.tokens import find_app_by_token, hash_token, new_token, new_visitor_key, note_token_use, reset_token


@pytest.fixture
def world(monkeypatch):
    enable_apps(monkeypatch)
    reset_apps_state()
    factory, _engine = make_database()
    db = factory()
    owner = add_user(db, "owner")
    host = FakeHost()
    source = FakeSource()
    service = AppService(db, host=host, source=source)
    app = service.create(owner, repo_url=REPO_URL, ref="main", name="Mind map")

    class World:
        pass

    w = World()
    w.db, w.owner, w.host, w.source, w.service, w.app = db, owner, host, source, service, app
    w.deploys = make_deploy_service(factory, host, source)
    try:
        yield w
    finally:
        db.close()
        reset_apps_state()


def test_token_format_is_the_contracts():
    seen = {new_token() for _ in range(50)}
    assert len(seen) == 50
    for value in seen:
        assert re.fullmatch(r"efpa_[A-Za-z0-9_-]{43}", value)
    assert hash_token("efpa_x") == hashlib.sha256(b"efpa_x").hexdigest()


def test_visitor_key_is_32_random_bytes_in_base64url():
    key = new_visitor_key()
    assert len(base64.urlsafe_b64decode(key)) == 32
    assert "+" not in key and "/" not in key
    assert key != new_visitor_key()


def test_a_new_app_has_a_token_stored_with_its_hash(world):
    app = world.app
    assert tokens.TOKEN_RE.match(app.token_value)
    assert app.token_hash == hash_token(app.token_value)
    assert app.token_last_used_at is None and app.token_last_used_source is None


def test_find_app_by_token(world):
    assert find_app_by_token(world.db, world.app.token_value).id == world.app.id
    for wrong in (
        None,
        "",
        5,
        "efpa_",
        world.app.token_value[:-1],
        world.app.token_value + "x",
        world.app.token_value.upper(),
        "efpb_" + world.app.token_value[5:],
        new_token(),
        f" {world.app.token_value}",
        world.app.token_hash,
    ):
        assert find_app_by_token(world.db, wrong) is None


def test_a_deleted_apps_token_finds_nothing(world):
    value = world.app.token_value
    world.service.delete(world.app, world.owner)
    assert find_app_by_token(world.db, value) is None


def test_last_use_is_recorded_at_most_once_a_minute(world, monkeypatch):
    clock = {"now": 5000.0}
    monkeypatch.setattr(tokens.time, "monotonic", lambda: clock["now"])
    first = datetime(2026, 11, 15, 8, 0, 0)
    assert note_token_use(world.db, world.app, "cluster", now=first) is True
    world.db.refresh(world.app)
    assert (world.app.token_last_used_at, world.app.token_last_used_source) == (first, "cluster")

    clock["now"] += 30
    assert note_token_use(world.db, world.app, "external", now=datetime(2026, 11, 15, 8, 0, 30)) is False
    world.db.refresh(world.app)
    assert (world.app.token_last_used_at, world.app.token_last_used_source) == (first, "cluster")

    clock["now"] += 31
    later = datetime(2026, 11, 15, 8, 1, 1)
    assert note_token_use(world.db, world.app, "external", now=later) is True
    world.db.refresh(world.app)
    assert (world.app.token_last_used_at, world.app.token_last_used_source) == (later, "external")

    # An unknown source is not trusted to be the cluster.
    clock["now"] += 61
    note_token_use(world.db, world.app, "somewhere")
    world.db.refresh(world.app)
    assert world.app.token_last_used_source == "external"


def test_reset_replaces_the_token_tells_the_host_and_is_audited(world):
    world.deploys.start_deploy(world.app, None, world.owner)
    world.db.refresh(world.app)
    note_token_use(world.db, world.app, "cluster")
    old = world.app.token_value
    world.host.calls.clear()

    sync = reset_token(world.db, world.app, world.owner.id, host=world.host)
    new = world.app.token_value
    assert sync.synced is True and sync.error is None
    assert new != old and tokens.TOKEN_RE.match(new)
    assert world.app.token_hash == hash_token(new)
    assert world.app.token_last_used_at is None and world.app.token_last_used_source is None
    # The old one stops working at once; the host got the new one.
    assert find_app_by_token(world.db, old) is None
    assert find_app_by_token(world.db, new).id == world.app.id
    assert world.host.names() == ["put_env"]
    assert world.host.env[world.app.id]["EFP_APP_TOKEN"] == new

    event = world.db.query(AppAuditEvent).filter(AppAuditEvent.action == "token.reset").one()
    assert event.actor_user_id == world.owner.id
    assert event.detail == {"host_synced": True}
    assert old not in (event.detail_json or "") and new not in (event.detail_json or "")
    # The once-a-minute bookkeeping starts over with the new token.
    assert note_token_use(world.db, world.app, "cluster") is True


def test_reset_before_the_first_deployment_needs_no_host(world):
    sync = reset_token(world.db, world.app, world.owner.id, host=world.host)
    # The host has no record of the app: the first deployment carries the token.
    assert sync.synced is True
    assert world.host.names() == ["put_env"]


def test_reset_when_the_host_is_away_still_changes_the_token_and_says_so(world):
    world.deploys.start_deploy(world.app, None, world.owner)
    world.db.refresh(world.app)
    old = world.app.token_value
    world.host.fail["put_env"] = AppHostUnavailable()
    sync = reset_token(world.db, world.app, world.owner.id, host=world.host)
    assert sync.synced is False
    assert sync.error_code == "host_unreachable" and "cannot be reached" in sync.error_message
    assert world.app.token_value != old
    assert find_app_by_token(world.db, old) is None
    assert world.host.env[world.app.id]["EFP_APP_TOKEN"] == old  # until a restart or a deployment
    event = world.db.query(AppAuditEvent).filter(AppAuditEvent.action == "token.reset").one()
    assert event.detail == {"host_synced": False, "host_error": "host_unreachable"}

    # A restart sends the app's record again and with it the token.
    world.host.fail.clear()
    world.service.restart(world.app, world.owner)
    assert world.host.env[world.app.id]["EFP_APP_TOKEN"] == world.app.token_value


def test_tokens_never_reach_the_log(world, caplog):
    caplog.set_level(logging.DEBUG)
    world.deploys.start_deploy(world.app, None, world.owner)
    world.db.refresh(world.app)
    old = world.app.token_value
    world.host.fail["put_env"] = AppHostUnavailable()
    reset_token(world.db, world.app, world.owner.id, host=world.host)
    note_token_use(world.db, world.app, "cluster")
    text = caplog.text
    assert old not in text and world.app.token_value not in text and world.app.visitor_key not in text


def test_two_apps_never_share_a_token(world):
    second = world.service.create(world.owner, repo_url=REPO_URL, ref="main", name="Second")
    assert second.token_value != world.app.token_value
    assert second.visitor_key != world.app.visitor_key
    assert find_app_by_token(world.db, second.token_value).id == second.id
    assert world.db.query(App).count() == 2
