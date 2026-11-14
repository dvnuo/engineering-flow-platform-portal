"""The picker endpoint and the sources the Portal answers from what it holds.

The endpoint serves the signed-in member their own recent work. These tests
pin whose settings are read, who may reach an assistant's workspace through
it, and that nothing a browser could cache or another member could ask for
comes back.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.suggestions as suggestions_api
from app.db import Base, get_db
from app.deps import get_current_user
from app.main import app
from app.models import Agent, User
from app.models.agent_task import AgentTask
from app.models.runtime_profile import RuntimeProfile
from app.services.suggestions import SOURCES, clear_cache
from tests._suggestions_helpers import JIRA_CONFIG, Recorder, group_ids, make_context, run, values


@pytest.fixture(autouse=True)
def _fresh_caches():
    clear_cache()
    yield
    clear_cache()


def _session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)()


def _user(db: Session, name: str = "alice", role: str = "user") -> User:
    user = User(username=name, password_hash="test", role=role, is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _agent(db: Session, owner: User, *, status: str = "running", name: str = "Dev") -> Agent:
    agent = Agent(
        name=name,
        owner_user_id=owner.id,
        status=status,
        image="img",
        namespace="efp-agents",
        deployment_name="d",
        service_name="s",
        pvc_name="p",
    )
    for key, value in (("visibility", "private"), ("runtime_type", "native")):
        if hasattr(agent, key):
            setattr(agent, key, value)
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


def _profile(db: Session, user: User, config: dict, modes: dict | None = None) -> RuntimeProfile:
    profile = RuntimeProfile(
        owner_user_id=user.id,
        name="Default",
        config_json=json.dumps(config),
        connector_modes_json=json.dumps(modes if modes is not None else {"jira": "custom", "github": "custom", "aws": "custom"}),
        revision=1,
    )
    db.add(profile)
    db.commit()
    return profile


@pytest.fixture
def env(monkeypatch):
    db = _session()
    user = _user(db)
    recorder = Recorder()
    monkeypatch.setattr(suggestions_api, "HTTP_TRANSPORT", httpx.MockTransport(recorder))
    state = SimpleNamespace(db=db, user=user, recorder=recorder)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: state.user
    try:
        yield state
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        db.close()


def _jira_routes(recorder: Recorder, issues: list[str]) -> None:
    recorder.routes[("GET", "/rest/api/2/myself")] = {"name": "alice"}
    recorder.routes[("GET", "/rest/api/2/search")] = {
        "issues": [{"key": key, "fields": {"summary": f"Summary of {key}"}} for key in issues]
    }


# ------------------------------------------------------------------ endpoint


def test_the_source_list_names_every_source_the_page_may_ask_for(env):
    response = TestClient(app).get("/api/suggestions")

    assert response.status_code == 200
    listed = {row["id"]: row for row in response.json()["sources"]}
    assert set(listed) == set(SOURCES)
    assert listed["jira.issues"] == {"id": "jira.issues", "connector": "jira", "searchable": True, "uses_agent": False}
    assert listed["aws.accounts"]["searchable"] is False
    assert listed["github.branches"]["uses_agent"] is True


def test_a_member_gets_their_own_issues_and_the_answer_is_not_cacheable(env):
    _profile(env.db, env.user, JIRA_CONFIG)
    _jira_routes(env.recorder, ["ABC-1"])

    response = TestClient(app).get("/api/suggestions/jira.issues", params={"p.types": ["Epic", "Story"], "p.Bad-Name": "x", "q": ""})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["state"] == "ok" and body["identity"] == "personal" and body["connector"] == "jira"
    assert body["groups"][0]["items"][0] == {
        "value": "ABC-1",
        "title": "ABC-1",
        "subtitle": "Summary of ABC-1",
        "url": "https://jira.corp/browse/ABC-1",
        "fields": {"key": "ABC-1"},
    }
    jqls = env.recorder.queries("/rest/api/2/search", "jql")
    assert all('issuetype in ("Epic", "Story")' in jql for jql in jqls)
    # The credential is the member's connector setting; the request brought none.
    assert {request.headers["authorization"] for request in env.recorder.requests} == {"Bearer pat-1"}
    assert "pat-1" not in response.text


def test_a_service_account_in_a_connector_the_member_customized_is_still_not_the_member(env, monkeypatch):
    """The state the defect was reported in. The member's GitHub connector is
    marked as their own (rows from before the Default connectors existed were
    classified that way), and the token in it opens a shared service account.
    The lists are the signed-in member's all the same, by their username."""
    monkeypatch.setattr(suggestions_api.settings, "github_username_suffix", "_corp")
    _profile(env.db, env.user, {"github": {"enabled": True, "api_token": "ghp-shared"}}, modes={"github": "custom"})
    env.recorder.routes[("GET", "/user")] = {"login": "svc-bot"}
    env.recorder.routes[("GET", "/users/alice_corp")] = {"login": "alice_corp", "type": "User"}
    env.recorder.routes[("GET", "/search/issues")] = {"items": []}

    body = TestClient(app).get("/api/suggestions/github.pull_requests").json()

    assert body["state"] == "ok" and body["identity"] == "shared"
    asked = env.recorder.queries("/search/issues", "q")
    assert len(asked) == 2
    assert all("alice_corp" in q and "@me" not in q and "svc-bot" not in q for q in asked)


def test_an_unknown_source_is_a_404_and_a_signed_out_request_is_refused(env):
    assert TestClient(app).get("/api/suggestions/nope.nothing").status_code == 404

    app.dependency_overrides.pop(get_current_user, None)
    assert TestClient(app).get("/api/suggestions/jira.issues").status_code == 401
    assert TestClient(app).get("/api/suggestions").status_code == 401


def test_a_member_without_the_connector_is_told_which_one_to_set_up(env):
    response = TestClient(app).get("/api/suggestions/jira.issues")

    assert response.json() == {"source": "jira.issues", "state": "not_configured", "groups": [], "connector": "jira"}
    assert env.recorder.requests == []


def test_an_assistant_the_member_may_not_see_is_refused(env):
    owner = _user(env.db, "bob")
    agent = _agent(env.db, owner)

    response = TestClient(app).get("/api/suggestions/portal.mobile_runs", params={"agent_id": agent.id})
    assert response.status_code == 403

    missing = TestClient(app).get("/api/suggestions/portal.mobile_runs", params={"agent_id": "no-such-agent"})
    assert missing.status_code == 200


def test_only_the_owner_or_an_administrator_reads_an_assistants_workspace():
    owner = SimpleNamespace(id=1, role="user")
    other = SimpleNamespace(id=2, role="user")
    admin = SimpleNamespace(id=3, role="admin")
    running = SimpleNamespace(id="a1", owner_user_id=1, status="running")
    paused = SimpleNamespace(id="a1", owner_user_id=1, status="paused")

    assert suggestions_api._runtime_get(owner, running) is not None
    assert suggestions_api._runtime_get(admin, running) is not None
    assert suggestions_api._runtime_get(other, running) is None
    assert suggestions_api._runtime_get(owner, paused) is None
    assert suggestions_api._runtime_get(owner, None) is None


def test_history_values_and_parameters_are_bounded(env):
    _profile(env.db, env.user, {})
    captured = {}

    async def fake_suggest(source_id, context):
        captured["context"] = context
        from app.services.suggestions.base import Result

        return Result(source_id)

    original = suggestions_api.suggest
    suggestions_api.suggest = fake_suggest
    try:
        params = [("recent", f"value-{index}" + "x" * 600) for index in range(9)]
        params += [("p.types", f"T{index}") for index in range(15)]
        params += [(f"p.k{index}", "v") for index in range(12)]
        params += [("p.long", "y" * 500), ("q", "  hello  ")]
        response = TestClient(app).get("/api/suggestions/jira.issues", params=params)
    finally:
        suggestions_api.suggest = original

    assert response.status_code == 200
    context = captured["context"]
    assert context.q == "hello"
    assert len(context.recent) == 5 and all(len(value) <= 500 for value in context.recent)
    assert len(context.params) == 8
    assert len(context.params["types"]) == 10
    assert all(len(value) <= 200 for values_ in context.params.values() for value in values_)


# -------------------------------------------------------------- mobile runs


def _task(db, owner, agent, *, title, status="done", skill="run-mobile-scenarios", minutes_ago=0, result=None) -> AgentTask:
    when = datetime(2026, 11, 12, 8, 0, 0) - timedelta(minutes=minutes_ago)
    task = AgentTask(
        assignee_agent_id=agent.id,
        source="portal",
        task_type="skill",
        title=title,
        skill_name=skill,
        status=status,
        owner_user_id=owner.id,
        created_by_user_id=owner.id,
        result_payload_json=json.dumps(result) if result is not None else None,
        created_at=when,
        updated_at=when,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _matrix_result(passed: int, failed: int) -> dict:
    card = {"summary": {"total": passed + failed, "passed": passed, "failed": failed}}
    text = "Run finished.\n\n```efp-matrix\n" + json.dumps(card) + "\n```\n"
    return {"final_response": text, "response": text, "output": text}


def test_mobile_runs_are_the_members_own_with_failures_first(env):
    agent = _agent(env.db, env.user)
    other_agent = _agent(env.db, env.user, name="QA")
    stranger = _user(env.db, "bob")
    green = _task(env.db, env.user, agent, title="FX-12 regression", minutes_ago=5, result=_matrix_result(8, 0))
    red = _task(env.db, env.user, agent, title="FX-13 regression", minutes_ago=30, result=_matrix_result(5, 3))
    broke = _task(env.db, env.user, agent, title="FX-14 regression", status="failed", minutes_ago=60)
    slashed = _task(env.db, env.user, agent, title="FX-15 regression", skill="/run-mobile-scenarios", minutes_ago=90)
    _task(env.db, env.user, agent, title="Not a run", skill="design-mobile-scenarios")
    elsewhere = _task(env.db, env.user, other_agent, title="FX-16 regression", minutes_ago=1)
    _task(env.db, stranger, agent, title="Someone else's run")

    context = make_context(Recorder(), {}, db=env.db, user_id=env.user.id, agent=agent)
    result = run("portal.mobile_runs", context)

    assert group_ids(result) == ["failed", "recent"]
    assert values(result, "failed") == [red.id, broke.id]
    assert values(result, "recent") == [green.id, slashed.id]
    failed_row = result.groups[0].items[0]
    assert (failed_row.status, failed_row.count, failed_row.title) == ("failed", 3, "FX-13 regression")
    assert failed_row.url == f"/app/tasks/{red.id}"

    # With no assistant selected, every run of the member is offered.
    everywhere = run("portal.mobile_runs", make_context(Recorder(), {}, db=env.db, user_id=env.user.id))
    assert elsewhere.id in values(everywhere, "recent")
    assert all("Someone else" not in item.title for group in everywhere.groups for item in group.items)


# ------------------------------------------------------------ mobile stories


def _workspace_plans(plans: dict[str, dict]):
    async def runtime_get(subpath, query_items):
        path = dict(query_items).get("path", "")
        if subpath == "api/server-files":
            assert path == "mobile/scenarios"
            items = [{"name": name, "is_dir": True, "modified_at": plan.get("updated_at", "")} for name, plan in plans.items()]
            items.append({"name": "notes.txt", "is_dir": False})
            items.append({"name": "../escape", "is_dir": True})
            return 200, {"items": items}
        if subpath == "api/server-files/read":
            name = path.split("/")[2]
            return 200, {"content": json.dumps(plans[name]), "truncated": False}
        return 404, None

    return runtime_get


def test_stories_planned_in_the_workspace_lead_and_jira_follows_without_repeating_them(env):
    plans = {
        "FX-12": {"issue": "FX-12", "title": "Buy foreign currency", "scenarios": [{}, {}, {}], "updated_at": "2026-11-10T08:00:00Z"},
        "FX-20": {"issue": "FX-20", "title": "Sell foreign currency", "scenarios": [{}], "updated_at": "2026-11-12T08:00:00Z"},
    }
    recorder = Recorder()
    _jira_routes(recorder, ["FX-12", "FX-31"])
    context = make_context(recorder, JIRA_CONFIG, runtime_get=_workspace_plans(plans), params={"types": ["Story"]})

    result = run("mobile.stories", context)

    assert result.groups[0].id == "planned"
    assert values(result, "planned") == ["FX-20", "FX-12"]
    planned = result.groups[0].items[1]
    assert (planned.subtitle, planned.status, planned.count) == ("Buy foreign currency", "scenarios", 3)
    # FX-12 is planned here already, so the Jira groups do not list it again.
    jira_values = [item.value for group in result.groups[1:] for item in group.items]
    assert jira_values == ["FX-31"]
    assert all('issuetype in ("Story")' in jql for jql in recorder.queries("/rest/api/2/search", "jql"))


def test_the_workspace_alone_answers_when_jira_is_not_set_up_and_neither_is_not_configured():
    plans = {"FX-12": {"issue": "FX-12", "title": "Buy", "scenarios": []}}
    planned = run("mobile.stories", make_context(Recorder(), {}, runtime_get=_workspace_plans(plans)))
    assert planned.state == "ok" and values(planned, "planned") == ["FX-12"]

    neither = run("mobile.stories", make_context(Recorder(), {}, user_id=7))
    assert neither.state == "not_configured" and neither.connector == "jira"


# -------------------------------------------------------------- aws accounts


def test_aws_accounts_are_read_from_the_connector_and_nothing_is_asked_of_aws():
    config = {
        "aws": {
            "enabled": True,
            "default_account": "cps-prod",
            "accounts": [
                {"name": "cps-dev", "account_id": "111111111111", "regions": ["eu-west-1", "us-east-1"]},
                {"name": "cps-prod", "account_id": "222222222222", "regions": ["eu-west-1"]},
                {"name": "retired", "account_id": "333333333333", "enabled": False},
            ],
        }
    }
    recorder = Recorder()
    result = run("aws.accounts", make_context(recorder, config))

    assert values(result, "accounts") == ["cps-prod", "cps-dev"]
    assert result.groups[0].items[1].subtitle == "111111111111, eu-west-1 us-east-1"
    assert result.groups[0].items[1].fields == {"account": "cps-dev", "account_id": "111111111111", "region": "eu-west-1"}
    assert recorder.requests == []

    off = run("aws.accounts", make_context(recorder, {"aws": {"enabled": True, "accounts": []}}))
    assert off.state == "not_configured" and off.connector == "aws"
