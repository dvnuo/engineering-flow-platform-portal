import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User
from app.models.runtime_profile import RuntimeProfile

def _build_client(monkeypatch):
    from app.main import app
    import app.deps as deps_module
    import app.api.runtime_profiles as runtime_profiles_api
    import app.api.agents as agents_api

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    user1 = User(username="u1", password_hash="test", role="user", is_active=True)
    user2 = User(username="u2", password_hash="test", role="user", is_active=True)
    db.add_all([user1, user2])
    db.commit()
    db.refresh(user1)
    db.refresh(user2)

    state = {"user": user1}

    def _override_user():
        u = state["user"]
        return SimpleNamespace(id=u.id, role=u.role, username=u.username, nickname=u.username)

    def _override_db():
        yield db

    monkeypatch.setattr(agents_api.k8s_service, "create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
    app.dependency_overrides[deps_module.get_current_user] = _override_user
    app.dependency_overrides[runtime_profiles_api.get_db] = _override_db
    app.dependency_overrides[agents_api.get_db] = _override_db
    app.dependency_overrides[agents_api.get_current_user] = _override_user

    def _set_user(user):
        state["user"] = user

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    return TestClient(app), db, user1, user2, _set_user, _cleanup


def test_runtime_profiles_scoped_and_defaults(monkeypatch):
    client, db, u1, u2, set_user, cleanup = _build_client(monkeypatch)
    try:
        r1 = client.post("/api/runtime-profiles", json={"name": "Default", "description": "a", "config_json": json.dumps({"llm": {"provider": "openai"}})})
        assert r1.status_code == 200
        body1 = r1.json()
        assert body1["owner_user_id"] == u1.id
        assert body1["is_default"] is True

        r2 = client.post("/api/runtime-profiles", json={"name": "Reviewer", "config_json": "{}"})
        assert r2.status_code == 200
        body2 = r2.json()

        options_ordered = client.get("/api/runtime-profiles/options")
        assert options_ordered.status_code == 200
        ordered_names = [item["name"] for item in options_ordered.json()]
        assert ordered_names[:2] == ["Reviewer", "Default"]

        profiles_ordered = client.get("/api/runtime-profiles")
        assert profiles_ordered.status_code == 200
        ordered_profile_names = [item["name"] for item in profiles_ordered.json()]
        assert ordered_profile_names[:2] == ["Reviewer", "Default"]

        dup = client.post("/api/runtime-profiles", json={"name": "Reviewer", "config_json": "{}"})
        assert dup.status_code == 409

        set_user(u2)
        same_name_other_user = client.post("/api/runtime-profiles", json={"name": "Reviewer", "config_json": "{}"})
        assert same_name_other_user.status_code == 200

        options = client.get("/api/runtime-profiles/options")
        assert options.status_code == 200
        assert len(options.json()) == 1
        assert options.json()[0]["is_default"] is True

        list_resp = client.get("/api/runtime-profiles")
        assert len(list_resp.json()) == 1
        assert list_resp.json()[0]["owner_user_id"] == u2.id

        # cross-user read -> 404
        not_found = client.get(f"/api/runtime-profiles/{body1['id']}")
        assert not_found.status_code == 404

        set_user(u1)
        switch_default = client.patch(f"/api/runtime-profiles/{body2['id']}", json={"is_default": True})
        assert switch_default.status_code == 200
        options = client.get("/api/runtime-profiles/options").json()
        assert len([p for p in options if p["is_default"]]) == 1
        assert any(p["id"] == body2["id"] and p["is_default"] for p in options)

        # cannot delete last profile
        del1 = client.delete(f"/api/runtime-profiles/{body2['id']}")
        assert del1.status_code == 200
        del_last = client.delete(f"/api/runtime-profiles/{body1['id']}")
        assert del_last.status_code == 409

        # in-use profile cannot delete
        p = client.post("/api/runtime-profiles", json={"name": "InUse", "is_default": True, "config_json": "{}"}).json()
        agent = Agent(
            name="a1",
            owner_user_id=u1.id,
            visibility="private",
            status="running",
            image="example/image:latest",
            runtime_profile_id=p["id"],
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

        del_used = client.delete(f"/api/runtime-profiles/{p['id']}")
        assert del_used.status_code == 409
    finally:
        cleanup()


def test_runtime_profile_create_materializes_creation_seed_defaults(monkeypatch):
    client, _db, _u1, _u2, _set_user, cleanup = _build_client(monkeypatch)
    try:
        no_config = client.post(
            "/api/runtime-profiles",
            json={"name": "Seeded-Implicit", "description": "d", "is_default": False},
        )
        assert no_config.status_code == 200
        no_config_payload = json.loads(no_config.json()["config_json"])
        assert no_config_payload == {}

        empty_config = client.post(
            "/api/runtime-profiles",
            json={"name": "Seeded-Empty", "description": "d", "is_default": False, "config_json": "{}"},
        )
        assert empty_config.status_code == 200
        empty_config_payload = json.loads(empty_config.json()["config_json"])
        assert empty_config_payload == {}
    finally:
        cleanup()


def test_runtime_profile_get_sanitizes_legacy_provider_automation_fields(monkeypatch):
    client, db, u1, _u2, _set_user, cleanup = _build_client(monkeypatch)
    try:
        legacy = RuntimeProfile(
            owner_user_id=u1.id,
            name="Legacy Profile",
            config_json=json.dumps(
                {
                    "github": {"enabled": True, "automation": {"mentions": {"enabled": True}}},
                    "jira": {"enabled": True, "automation": {"assignments": {"enabled": True}}},
                    "confluence": {"enabled": True, "automation": {"mentions": {"enabled": True}}},
                }
            ),
            revision=1,
            is_default=False,
        )
        db.add(legacy)
        db.commit()
        db.refresh(legacy)

        resp = client.get(f"/api/runtime-profiles/{legacy.id}")
        assert resp.status_code == 200
        cfg = json.loads(resp.json()["config_json"])
        assert cfg["github"] == {"enabled": True, "api_token_present": False}
        assert cfg["jira"] == {"enabled": True}
        assert cfg["confluence"] == {"enabled": True}
    finally:
        cleanup()


def test_runtime_profile_list_sanitizes_legacy_provider_automation_fields(monkeypatch):
    client, db, u1, _u2, _set_user, cleanup = _build_client(monkeypatch)
    try:
        legacy = RuntimeProfile(
            owner_user_id=u1.id,
            name="Legacy Profile List",
            config_json=json.dumps(
                {
                    "github": {"enabled": True, "automation": {"mentions": {"enabled": True}}},
                    "jira": {"enabled": True, "automation": {"assignments": {"enabled": True}}},
                    "confluence": {"enabled": True, "automation": {"mentions": {"enabled": True}}},
                }
            ),
            revision=1,
            is_default=False,
        )
        db.add(legacy)
        db.commit()

        resp = client.get("/api/runtime-profiles")
        assert resp.status_code == 200
        by_id = {item["id"]: item for item in resp.json()}
        cfg = json.loads(by_id[legacy.id]["config_json"])
        assert cfg["github"] == {"enabled": True, "api_token_present": False}
        assert cfg["jira"] == {"enabled": True}
        assert cfg["confluence"] == {"enabled": True}
    finally:
        cleanup()


def test_runtime_profile_api_redacts_llm_oauth_secrets_in_response(monkeypatch):
    client, db, u1, _u2, _set_user, cleanup = _build_client(monkeypatch)
    try:
        profile = RuntimeProfile(owner_user_id=u1.id, name="OAuth Redact", config_json=json.dumps({"llm":{"provider":"github_copilot","oauth":{"type":"oauth","access":"gho_A","refresh":"gho_R","expires":0}}}), revision=1, is_default=False)
        db.add(profile); db.commit(); db.refresh(profile)
        resp = client.get(f"/api/runtime-profiles/{profile.id}")
        assert resp.status_code == 200
        assert "gho_A" not in resp.text and "gho_R" not in resp.text
        cfg = json.loads(resp.json()["config_json"])
        assert cfg["llm"]["api_key_present"] is False
        assert "api_key" not in cfg["llm"]
        assert "oauth" not in cfg["llm"]
    finally:
        cleanup()

def test_runtime_profile_api_redaction_does_not_remove_persisted_oauth(monkeypatch):
    client, db, u1, _u2, _set_user, cleanup = _build_client(monkeypatch)
    try:
        payload = {"llm":{"provider":"github_copilot","oauth":{"type":"oauth","access":"gho_A","refresh":"gho_R","expires":0}}}
        profile = RuntimeProfile(owner_user_id=u1.id, name="OAuth Persist", config_json=json.dumps(payload), revision=1, is_default=False)
        db.add(profile); db.commit(); db.refresh(profile)
        _ = client.get(f"/api/runtime-profiles/{profile.id}")
        db.refresh(profile)
        saved = json.loads(profile.config_json)
        assert saved["llm"]["oauth"]["access"] == "gho_A"
        assert saved["llm"]["oauth"]["refresh"] == "gho_R"
    finally:
        cleanup()


# ------------------------------------------------ where a new profile starts

# A profile can start from nothing, from the admin-maintained Default
# Connections, or as a copy of one the member already has. The copy is resolved
# on the server: API responses redact credentials, so a client could not
# assemble one even if it tried -- and a member must never be able to name
# someone else's profile as the source.


def _seed(db, config):
    from app.services.runtime_profile_seed_service import RuntimeProfileSeedService

    RuntimeProfileSeedService(db).save_seed(config)


SHARED_SEED = {
    "jira": {
        "enabled": True,
        "instances": [{"name": "Prod", "url": "https://company.atlassian.net", "token": "shared-token"}],
    }
}


def test_sources_offer_the_shared_setup_only_once_an_admin_fills_it_in(monkeypatch):
    client, db, _u1, _u2, _set_user, cleanup = _build_client(monkeypatch)
    try:
        before = client.get("/api/runtime-profiles/sources")
        assert before.status_code == 200
        assert [item["value"] for item in before.json()] == ["blank"]

        _seed(db, SHARED_SEED)

        after = client.get("/api/runtime-profiles/sources").json()
        # The shared setup leads, because the client selects whatever is first.
        assert [item["value"] for item in after] == ["default_connections", "blank"]
    finally:
        cleanup()


def test_the_shared_setup_is_named_and_described_for_whoever_opens_the_dialog(monkeypatch):
    # "Default Connections" is an Administration menu nobody else has seen, so
    # the option cannot be named after it.
    client, db, _u1, _u2, _set_user, cleanup = _build_client(monkeypatch)
    try:
        _seed(db, SHARED_SEED)
        entry = next(
            item
            for item in client.get("/api/runtime-profiles/sources").json()
            if item["value"] == "default_connections"
        )

        assert entry["label"] == "The setup your admin prepared"
        assert "Default Connections" not in entry["detail"]
        # It says which services it covers...
        assert "Jira" in entry["detail"]
        # ...and that this seed carries a shared token, which is what decides
        # whether the member has anything left to do.
        assert "Shared sign-in details are included" in entry["detail"]
    finally:
        cleanup()


def test_a_shared_setup_without_credentials_says_so_instead(monkeypatch):
    client, db, _u1, _u2, _set_user, cleanup = _build_client(monkeypatch)
    try:
        _seed(db, {"jira": {"enabled": True, "instances": [{"name": "Prod", "url": "https://x"}]}})
        entry = next(
            item
            for item in client.get("/api/runtime-profiles/sources").json()
            if item["value"] == "default_connections"
        )

        assert "You still add your own sign-in details." in entry["detail"]
    finally:
        cleanup()


def test_sources_list_the_members_own_profiles_as_copy_targets(monkeypatch):
    client, _db, _u1, u2, set_user, cleanup = _build_client(monkeypatch)
    try:
        created = client.post("/api/runtime-profiles", json={"name": "Production", "description": "Team wide."})
        assert created.status_code == 200

        mine = client.get("/api/runtime-profiles/sources").json()
        copy_entries = [item for item in mine if item["group"] == "copy"]
        assert [item["value"] for item in copy_entries] == ["profile:" + created.json()["id"]]
        assert copy_entries[0]["label"] == "A copy of Production"
        # "(Default)" beside a name invites "default what?"; the answer goes in
        # the description line, along with the fact that a copy takes secrets.
        assert copy_entries[0]["detail"] == (
            "Team wide. This is your current default. "
            "An exact copy, including any sign-in details you saved."
        )

        # Another member sees their own profiles, never this one.
        set_user(u2)
        assert [item for item in client.get("/api/runtime-profiles/sources").json() if item["group"] == "copy"] == []
    finally:
        cleanup()


def test_the_empty_option_says_what_it_leaves_you_with(monkeypatch):
    client, _db, _u1, _u2, _set_user, cleanup = _build_client(monkeypatch)
    try:
        entry = next(
            item for item in client.get("/api/runtime-profiles/sources").json() if item["value"] == "blank"
        )

        assert entry["label"] == "Nothing - I'll set it up myself"
        assert entry["detail"] == "Every service starts empty, for you to fill in."
    finally:
        cleanup()


def test_creating_from_default_connections_carries_the_shared_credentials(monkeypatch):
    client, db, _u1, _u2, _set_user, cleanup = _build_client(monkeypatch)
    try:
        _seed(db, SHARED_SEED)

        created = client.post(
            "/api/runtime-profiles",
            json={"name": "From defaults", "source": "default_connections"},
        )
        assert created.status_code == 200

        # The response redacts the token, so assert against what was persisted.
        stored = db.get(RuntimeProfile, created.json()["id"])
        assert json.loads(stored.config_json)["jira"]["instances"][0]["token"] == "shared-token"

        # And the response says a token is set without disclosing it.
        instance = json.loads(created.json()["config_json"])["jira"]["instances"][0]
        assert instance["token_present"] is True
        assert "token" not in instance
    finally:
        cleanup()


def test_an_existing_member_can_pick_up_default_connections_on_a_new_profile(monkeypatch):
    # The point of the source picker: seeding used to reach only members who had
    # no profile at all, which left everyone already signed up behind.
    client, db, _u1, _u2, _set_user, cleanup = _build_client(monkeypatch)
    try:
        first = client.post("/api/runtime-profiles", json={"name": "Existing"})
        assert first.status_code == 200
        _seed(db, SHARED_SEED)

        later = client.post(
            "/api/runtime-profiles",
            json={"name": "Now with defaults", "source": "default_connections"},
        )
        assert later.status_code == 200
        assert "company.atlassian.net" in db.get(RuntimeProfile, later.json()["id"]).config_json
        # The profile they already had is untouched.
        assert json.loads(db.get(RuntimeProfile, first.json()["id"]).config_json) == {}
    finally:
        cleanup()


def test_copying_a_profile_duplicates_its_config_including_credentials(monkeypatch):
    client, db, u1, _u2, _set_user, cleanup = _build_client(monkeypatch)
    try:
        origin = RuntimeProfile(
            owner_user_id=u1.id,
            name="Origin",
            config_json=json.dumps(
                {"github": {"enabled": True, "api_token": "ghp_origin"}, "llm": {"provider": "github_copilot"}}
            ),
            is_default=True,
        )
        db.add(origin)
        db.commit()
        db.refresh(origin)

        copied = client.post(
            "/api/runtime-profiles",
            json={"name": "Copy", "source": "profile:" + origin.id},
        )
        assert copied.status_code == 200

        stored = json.loads(db.get(RuntimeProfile, copied.json()["id"]).config_json)
        assert stored["github"]["api_token"] == "ghp_origin"
        assert stored["llm"]["provider"] == "github_copilot"

        # A copy, not a link.
        assert copied.json()["id"] != origin.id
    finally:
        cleanup()


def test_copying_someone_elses_profile_is_not_found(monkeypatch):
    client, db, _u1, u2, _set_user, cleanup = _build_client(monkeypatch)
    try:
        theirs = RuntimeProfile(
            owner_user_id=u2.id,
            name="Theirs",
            config_json=json.dumps({"github": {"api_token": "ghp_theirs"}}),
            is_default=True,
        )
        db.add(theirs)
        db.commit()
        db.refresh(theirs)

        stolen = client.post("/api/runtime-profiles", json={"name": "Nope", "source": "profile:" + theirs.id})

        assert stolen.status_code == 404
        assert not db.query(RuntimeProfile).filter_by(name="Nope").all()
    finally:
        cleanup()


def test_a_source_that_names_nothing_is_rejected(monkeypatch):
    client, _db, _u1, _u2, _set_user, cleanup = _build_client(monkeypatch)
    try:
        for bad in ("profile:", "somewhere-else", "profile"):
            resp = client.post("/api/runtime-profiles", json={"name": "Bad " + bad, "source": bad})
            assert resp.status_code == 422, bad
    finally:
        cleanup()


def test_the_blank_source_still_honours_a_posted_config(monkeypatch):
    # API clients that post a config directly must keep working; only a
    # non-blank source takes the config out of their hands.
    client, db, _u1, _u2, _set_user, cleanup = _build_client(monkeypatch)
    try:
        created = client.post(
            "/api/runtime-profiles",
            json={"name": "Posted", "config_json": json.dumps({"github": {"enabled": True}})},
        )
        assert created.status_code == 200
        assert json.loads(db.get(RuntimeProfile, created.json()["id"]).config_json)["github"]["enabled"] is True
    finally:
        cleanup()


def test_a_source_beats_a_posted_config_rather_than_merging_with_it(monkeypatch):
    client, db, _u1, _u2, _set_user, cleanup = _build_client(monkeypatch)
    try:
        _seed(db, SHARED_SEED)

        created = client.post(
            "/api/runtime-profiles",
            json={
                "name": "Both",
                "source": "default_connections",
                "config_json": json.dumps({"github": {"enabled": True}}),
            },
        )
        assert created.status_code == 200

        stored = json.loads(db.get(RuntimeProfile, created.json()["id"]).config_json)
        assert "jira" in stored
        assert "github" not in stored
    finally:
        cleanup()


def test_the_picker_never_repeats_machine_written_jargon_at_a_member(monkeypatch):
    # Older builds described a member's first profile as "Auto-created default
    # runtime profile". Nobody typed that, and "runtime profile" is a word from
    # the schema, so the picker drops it rather than reading it back.
    client, db, u1, _u2, _set_user, cleanup = _build_client(monkeypatch)
    try:
        legacy = RuntimeProfile(
            owner_user_id=u1.id,
            name="Default",
            description="Auto-created default runtime profile",
            config_json="{}",
            is_default=True,
        )
        db.add(legacy)
        db.commit()

        entry = next(
            item for item in client.get("/api/runtime-profiles/sources").json() if item["group"] == "copy"
        )

        assert "runtime profile" not in entry["detail"]
        assert entry["detail"] == (
            "This is your current default. An exact copy, including any sign-in details you saved."
        )
    finally:
        cleanup()


def test_a_first_profile_describes_itself_in_plain_words(monkeypatch):
    from app.services.runtime_profile_service import RuntimeProfileService

    client, db, _u1, u2, _set_user, cleanup = _build_client(monkeypatch)
    try:
        profile = RuntimeProfileService(db).ensure_user_has_default_profile(u2)

        assert profile.description == "Set up for you when you joined"
    finally:
        cleanup()


def test_a_description_the_member_wrote_is_kept_and_closed_off(monkeypatch):
    # Free text, so most people leave the full stop off; without one the next
    # sentence runs straight into it.
    client, db, u1, _u2, _set_user, cleanup = _build_client(monkeypatch)
    try:
        db.add(
            RuntimeProfile(
                owner_user_id=u1.id,
                name="Sandbox",
                description="For experiments",
                config_json="{}",
                is_default=False,
            )
        )
        db.commit()

        entry = next(
            item for item in client.get("/api/runtime-profiles/sources").json() if item["group"] == "copy"
        )

        assert entry["detail"].startswith("For experiments. An exact copy")
    finally:
        cleanup()
