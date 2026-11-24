"""/api/app-sdk/v1: the SDK API of docs/CONTRACTS.md section 9.

Who may call it, what every status and error envelope looks like, what is
written down about each call, and what the daily budget refuses. The model
provider and the connectors are fakes: the gateway and the proxy have tests of
their own (tests/test_app_sdk_*.py).
"""

import asyncio
import base64
from datetime import datetime, timedelta
import json
import logging
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from _apps_helpers import (
    add_app,
    add_group,
    add_user,
    enable_apps,
    make_database,
    remove_from_allowlist,
    reset_apps_state,
    sign_visitor,
)
from app.config import get_settings
from app.db import Base, get_db
from app.security.session_cookie import session_cookie_name
from app.models import App, AppExecution, RuntimeProfile
from app.services import model_catalog_service
from app.services.app_sdk import image_analysis, llm_ai_platform, llm_copilot, llm_wire
from app.services.app_sdk.llm_types import LlmGatewayError, LlmResult, LlmUsage
from app.services.apps import budget as app_budget
from app.services.apps import tokens as app_tokens
from app.services.apps.rate_limit import sdk_rate_limiter
from app.services.auth_service import issue_session_token

PREFIX = "/api/app-sdk/v1"
GITHUB_TOKEN = "ghu_ownerDeviceFlowToken0123456789"
COPILOT_TOKEN = "tid=abc123;exp=4102444800;sku=business;proxy-ep=proxy.business.githubcopilot.com;st=dotcom"
CONNECTOR_TOKEN = "tok-9f8e7d6c5b4a3210"
HELLO = [{"role": "user", "content": "Hello"}]
NOON = datetime(2026, 11, 15, 12, 0, 0)
HANG = object()


def owner_settings() -> dict:
    """The owner's effective settings: Copilot connected (thinking set high for their assistants), Jira and GitHub set up."""
    return {
        "llm": {"provider": "github_copilot", "model": "auto", "api_key": GITHUB_TOKEN, "reasoning_effort": "xhigh"},
        "jira": {
            "enabled": True,
            "instances": [{"name": "main", "url": "https://jira.example.test", "username": "svc", "token": CONNECTOR_TOKEN}],
        },
        "github": {"enabled": True, "api_token": CONNECTOR_TOKEN},
    }


class FakeGateway:
    """Stands in for ``llm_gateway.complete`` and ``llm_gateway.stream``."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.result = LlmResult(text="Hello there", model="gpt-6-sol", finish_reason="stop", usage=LlmUsage(10, 20, 0.004))
        self.error: Exception | None = None
        # What stream() yields: event dicts, an exception to raise, or HANG to wait for ever.
        self.script: list = [
            {"type": "delta", "text": "Hel"},
            {"type": "delta", "text": "lo"},
            {"type": "done", "model": "gpt-6-sol", "finish_reason": "stop", "usage": {"input_tokens": 10, "output_tokens": 20, "credits": 0.004}},
        ]
        self.closed = 0

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result

    async def stream(self, **kwargs):
        self.calls.append(kwargs)
        try:
            for item in self.script:
                if isinstance(item, Exception):
                    raise item
                if item is HANG:
                    await asyncio.Event().wait()
                yield item
        finally:
            self.closed += 1


@pytest.fixture
def sdk(monkeypatch):
    from app.main import app
    import app.api.app_sdk as api

    enable_apps(monkeypatch, apps_sdk_rate_per_minute=120, apps_default_daily_credits=50.0, apps_default_daily_tokens=2_000_000)
    reset_apps_state()
    app_budget.in_flight.reset()
    model_catalog_service.reset_catalog_state()
    llm_copilot.reset_token_cache()
    llm_wire.reset_cap_state()
    factory, _engine = make_database()
    db = factory()
    add_group(db, "Default", is_default=True)
    qa = add_group(db, "QA")
    owner = add_user(db, "owner", group=qa, nickname="Olive")
    visitor = add_user(db, "visitor", group=qa, nickname="Vera")
    row = add_app(db, owner, llm_enabled=True, connectors_enabled=["jira", "github"], connectors_write=["github"])
    gateway = FakeGateway()

    def override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    h = SimpleNamespace(
        api=api, asgi=app, db=db, factory=factory, owner=owner, visitor=visitor, app=row, token=row.token_value,
        gateway=gateway, config=owner_settings(), client=TestClient(app),
    )
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(api, "owner_config", lambda _db, _app: h.config)
    monkeypatch.setattr(api.llm_gateway, "complete", gateway.complete)
    monkeypatch.setattr(api.llm_gateway, "stream", gateway.stream)

    def auth(token=None, **extra):
        return {"Authorization": f"Bearer {token or h.token}", **extra}

    def get(path, **headers):
        return h.client.get(PREFIX + path, headers=auth(**headers))

    def post(path, body=None, **headers):
        return h.client.post(PREFIX + path, json=body, headers=auth(**headers))

    def complete(body=None, **headers):
        payload = {"messages": HELLO}
        payload.update(body or {})
        return post("/llm/complete", payload, **headers)

    def executions(**filters):
        db.expire_all()
        query = db.query(AppExecution).order_by(AppExecution.id)
        for name, value in filters.items():
            query = query.filter(getattr(AppExecution, name) == value)
        return query.all()

    def reload():
        db.expire_all()
        return db.get(App, row.id)

    def change(**fields):
        current = reload()
        for name, value in fields.items():
            setattr(current, name, value)
        db.commit()

    h.auth, h.get, h.post, h.complete, h.executions, h.reload, h.change = auth, get, post, complete, executions, reload, change
    app_id = row.id
    try:
        yield h
        # Whatever a test's calls came to (answered, refused, failed, abandoned),
        # none of them is still counted as under way once they have ended.
        assert app_budget.in_flight.calls(app_id) == 0
    finally:
        app.dependency_overrides.clear()
        db.close()
        reset_apps_state()
        app_budget.in_flight.reset()
        model_catalog_service.reset_catalog_state()
        llm_copilot.reset_token_cache()
        llm_wire.reset_cap_state()


def error_of(response) -> dict:
    body = response.json()
    assert set(body) == {"error"}, body
    assert set(body["error"]) == {"code", "message"}, body
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    return body["error"]


def events_of(response) -> list[dict]:
    assert response.headers["content-type"].startswith("text/event-stream")
    blocks = [block for block in response.text.split("\n\n") if block]
    assert all(block.startswith("data: ") and "\n" not in block for block in blocks), response.text
    return [json.loads(block[len("data: "):]) for block in blocks]


def at(monkeypatch, moment: datetime) -> None:
    monkeypatch.setattr(app_budget, "utcnow", lambda: moment)


def spend(h, *, credits=None, tokens=(0, 0), created_at=NOON, kind="llm", status="succeeded"):
    h.db.add(
        AppExecution(
            app_id=h.app.id, owner_user_id=h.owner.id, kind=kind, status=status, model="gpt-6-sol",
            input_tokens=tokens[0], output_tokens=tokens[1], credits=credits, created_at=created_at,
        )
    )
    h.db.commit()


# --- off, and who is calling -----------------------------------------------------------

ENDPOINTS = [
    ("GET", "/app"),
    ("POST", "/llm/complete"),
    ("GET", "/connectors"),
    ("POST", "/connectors/jira/request"),
    ("POST", "/images/inspect"),
    ("GET", "/nothing-here"),
]


@pytest.mark.parametrize("method, path", ENDPOINTS)
def test_everything_answers_404_while_apps_are_off(sdk, monkeypatch, method, path):
    monkeypatch.setattr(get_settings(), "apps_enabled", False)

    response = sdk.client.request(method, PREFIX + path, headers=sdk.auth(), json={"messages": HELLO})

    assert response.status_code == 404
    assert error_of(response)["code"] == "not_found"
    assert sdk.gateway.calls == [] and sdk.executions() == []


def test_the_scheme_is_case_insensitive_and_the_token_is_the_whole_credential(sdk):
    assert sdk.client.get(PREFIX + "/app", headers={"Authorization": f"bearer {sdk.token}"}).status_code == 200
    assert sdk.client.get(PREFIX + "/app", headers={"Authorization": f"Bearer {sdk.token}x"}).status_code == 401
    assert sdk.client.get(PREFIX + "/app", headers={"Authorization": f"Bearer {sdk.token[:-1]}"}).status_code == 401


def test_a_deleted_apps_token_and_a_replaced_token_stop_working(sdk):
    old = sdk.token
    app_tokens.reset_token(sdk.db, sdk.reload(), sdk.owner.id, host=SimpleNamespace(put_env=lambda *_args: {}))
    new = sdk.reload().token_value
    assert new != old

    assert error_of(sdk.get("/app", token=old))["code"] == "invalid_token"
    assert sdk.client.get(PREFIX + "/app", headers=sdk.auth(new)).status_code == 200

    sdk.change(deleted_at=datetime.utcnow())
    response = sdk.client.get(PREFIX + "/app", headers=sdk.auth(new))
    assert response.status_code == 401 and error_of(response)["code"] == "invalid_token"


def test_a_portal_session_is_never_consulted(sdk):
    cookie = {session_cookie_name(): issue_session_token(sdk.owner.id)}

    # The owner's own browser session opens nothing here.
    signed_in = TestClient(sdk.asgi, cookies=cookie)
    response = signed_in.get(PREFIX + "/app")
    assert response.status_code == 401 and error_of(response)["code"] == "invalid_token"
    assert signed_in.post(PREFIX + "/llm/complete", json={"messages": HELLO}).status_code == 401

    # And a session next to the token changes nothing about who is calling.
    other = TestClient(sdk.asgi, cookies={session_cookie_name(): issue_session_token(sdk.visitor.id)})
    answer = other.get(PREFIX + "/app", headers=sdk.auth())
    assert answer.status_code == 200 and answer.json()["owner"]["username"] == "owner"
    assert "set-cookie" not in answer.headers


def test_a_disabled_app_is_refused_with_app_disabled(sdk):
    sdk.change(disabled_reason="Leaks data")
    for method, path in ENDPOINTS:
        response = sdk.client.request(method, PREFIX + path, headers=sdk.auth(), json={"messages": HELLO})
        assert response.status_code == 403, path
        assert error_of(response)["code"] == "app_disabled"
        # The administrator's reason is for the Portal's pages, not for whoever holds the token.
        assert "Leaks data" not in response.text

    sdk.change(disabled_reason=None)
    assert sdk.get("/app").status_code == 200

    remove_from_allowlist(sdk.db, "owner")
    response = sdk.get("/app")
    assert response.status_code == 403
    assert error_of(response)["code"] == "app_disabled" and "owner" in error_of(response)["message"]
    assert sdk.gateway.calls == [] and sdk.executions() == []


# --- rate, source, CORS ---------------------------------------------------------------------


def test_calls_over_the_apps_rate_are_refused_until_the_window_moves_on(sdk, monkeypatch):
    monkeypatch.setattr(get_settings(), "apps_sdk_rate_per_minute", 3)
    clock = {"now": 1000.0}
    monkeypatch.setattr(sdk_rate_limiter, "_clock", lambda: clock["now"])
    other = add_app(sdk.db, sdk.owner, slug="other-app", name="Other")

    for _ in range(3):
        assert sdk.get("/app").status_code == 200
        clock["now"] += 5.0

    # 15 seconds in: the first call leaves the window 45 seconds from now.
    refused = sdk.get("/app")
    assert refused.status_code == 429
    assert error_of(refused)["code"] == "rate_limited"
    assert refused.headers["retry-after"] == "45"
    # Every endpoint counts against the same window, and a refused call does not extend it.
    assert sdk.complete().status_code == 429 and sdk.get("/connectors").status_code == 429
    assert sdk.gateway.calls == [] and sdk.executions() == []
    # Another app has a window of its own.
    assert sdk.client.get(PREFIX + "/app", headers=sdk.auth(other.token_value)).status_code == 200

    clock["now"] += 44.0
    assert sdk.get("/app").headers["retry-after"] == "1"
    clock["now"] += 1.0
    assert sdk.get("/app").status_code == 200
    assert sdk.get("/app").status_code == 429


def test_a_rate_of_zero_means_no_limit(sdk, monkeypatch):
    monkeypatch.setattr(get_settings(), "apps_sdk_rate_per_minute", 0)

    assert [sdk.get("/app").status_code for _ in range(150)] == [200] * 150


@pytest.mark.parametrize(
    "headers, source",
    [
        ({}, "cluster"),
        ({"X-Forwarded-For": "203.0.113.9"}, "external"),
        ({"X-Real-IP": "203.0.113.9"}, "external"),
        ({"X-Forwarded-For": "10.0.0.7, 203.0.113.9", "X-Real-IP": "10.0.0.7"}, "external"),
        # Present is enough: whatever sits in front of the Portal put it there.
        ({"X-Forwarded-For": ""}, "external"),
    ],
)
def test_a_call_is_tagged_with_where_it_came_from(sdk, headers, source):
    assert sdk.reload().token_last_used_at is None

    assert sdk.complete(**headers).status_code == 200

    used = sdk.reload()
    assert used.token_last_used_source == source and used.token_last_used_at is not None
    assert [row.source for row in sdk.executions()] == [source]


def test_a_refused_call_is_still_noted_as_a_use_of_the_token(sdk, monkeypatch):
    monkeypatch.setattr(get_settings(), "apps_sdk_rate_per_minute", 1)
    assert sdk.get("/app").status_code == 200
    app_tokens.reset_use_notes()
    sdk.change(token_last_used_at=None, token_last_used_source=None)

    assert sdk.get("/app", **{"X-Forwarded-For": "203.0.113.9"}).status_code == 429

    assert sdk.reload().token_last_used_source == "external"


def test_no_cors_header_is_ever_sent(sdk):
    origin = {"Origin": "https://app.example.com"}

    answers = [
        sdk.get("/app", **origin),
        sdk.complete(**origin),
        sdk.client.get(PREFIX + "/app", headers=origin),
        sdk.client.options(PREFIX + "/llm/complete", headers={**origin, "Access-Control-Request-Method": "POST"}),
        sdk.client.options(
            PREFIX + "/llm/complete",
            headers={**sdk.auth(), **origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"},
        ),
    ]

    assert [answer.status_code for answer in answers] == [200, 200, 401, 401, 404]
    for answer in answers:
        assert not [name for name in answer.headers if name.lower().startswith("access-control-")]


# --- the envelope for everything that is not an answer ---------------------------------------


@pytest.mark.parametrize(
    "method, path",
    [
        ("GET", "/nothing-here"),
        ("POST", "/llm"),
        ("GET", "/llm/complete"),
        ("DELETE", "/app"),
        ("PUT", "/connectors"),
        ("GET", "/connectors/jira/request"),
        ("POST", "/connectors/jira"),
        ("GET", "/images/inspect"),
        ("POST", "/images"),
        ("POST", "/images/inspect/"),
        ("GET", ""),
        ("GET", "/"),
        ("PATCH", "/app/"),
        ("HEAD", "/nothing-here"),
    ],
)
def test_an_unknown_path_or_method_below_the_prefix_is_404_in_the_envelope(sdk, method, path):
    response = sdk.client.request(method, PREFIX + path, headers=sdk.auth())

    assert response.status_code == 404
    if method != "HEAD":
        assert error_of(response)["code"] == "not_found"
    # Nothing about the API is told to someone without a token.
    assert sdk.client.request(method, PREFIX + path).status_code == 401


@pytest.mark.parametrize(
    "content",
    [b"{not json", b"[]", b'"text"', b"12", b"null", b""],
)
@pytest.mark.parametrize("path", ["/llm/complete", "/connectors/jira/request"])
def test_a_body_that_is_not_a_json_object_is_invalid_request(sdk, path, content):
    response = sdk.client.post(PREFIX + path, content=content, headers={**sdk.auth(), "Content-Type": "application/json"})

    assert response.status_code == 400
    assert error_of(response)["code"] == "invalid_request"
    assert sdk.gateway.calls == []


def test_a_body_that_is_not_json_at_all_is_invalid_request(sdk):
    response = sdk.client.post(PREFIX + "/llm/complete", content=b"messages=hello", headers={**sdk.auth(), "Content-Type": "text/plain"})

    assert response.status_code == 400 and error_of(response)["code"] == "invalid_request"


def test_an_unexpected_failure_is_500_in_the_envelope_and_says_nothing_about_itself(sdk):
    sdk.gateway.error = RuntimeError("secret detail: " + GITHUB_TOKEN)

    response = sdk.complete()

    assert response.status_code == 500
    assert error_of(response)["code"] == "internal_error"
    assert "secret detail" not in response.text and GITHUB_TOKEN not in response.text
    assert [(row.status, row.error_code) for row in sdk.executions()] == [("failed", "internal_error")]


# --- 9.1 GET /app ------------------------------------------------------------------------------


def test_app_describes_the_app_and_what_it_may_use(sdk):
    spend(sdk, credits=1.2, tokens=(3000, 400), created_at=datetime.utcnow())
    # Enabled for the app, but the owner never set Splunk up: not offered.
    sdk.change(connectors_enabled=["jira", "github", "splunk"])

    response = sdk.get("/app")

    assert response.status_code == 200
    body = response.json()
    models = body["llm"]["models"]
    assert body == {
        "id": sdk.app.id,
        "slug": "mindmap",
        "name": "Mind map",
        "url": "https://app.example.com/mindmap/",
        "owner": {"id": sdk.owner.id, "username": "owner", "display_name": "Olive", "group_id": sdk.owner.group_id, "group_name": "QA"},
        "llm": {"enabled": True, "default_model": "gpt-6-sol", "models": models},
        "connectors": [{"type": "jira", "write": False}, {"type": "github", "write": True}],
        # Not turned on for this app: the limits are told all the same (section 9.1).
        "image_analysis": {"enabled": False, "model": None, "max_images": 4, "max_image_bytes": 5242880, "max_total_bytes": 20971520},
        "budget": {"daily_credits": 50.0, "used_credits": 1.2, "daily_tokens": 2000000, "used_tokens": 3400},
    }
    assert list(body) == ["id", "slug", "name", "url", "owner", "llm", "connectors", "image_analysis", "budget"]
    assert "gpt-6-sol" in models and not [model for model in models if "auto" in model]
    # Nothing an app must not see.
    assert sdk.token not in response.text and GITHUB_TOKEN not in response.text and CONNECTOR_TOKEN not in response.text
    assert sdk.executions(kind="connector") == [] and len(sdk.executions()) == 1, "reading the app is not an execution"


def test_app_reports_its_own_limits_and_a_switched_off_model_provider(sdk):
    sdk.change(llm_enabled=False, daily_credits=2.5, daily_tokens=1000, connectors_enabled=[], connectors_write=[])

    body = sdk.get("/app").json()

    assert body["llm"]["enabled"] is False and body["llm"]["models"]
    assert body["connectors"] == []
    assert body["budget"] == {"daily_credits": 2.5, "used_credits": 0.0, "daily_tokens": 1000, "used_tokens": 0}


def test_app_reads_the_owners_own_settings_row(sdk, monkeypatch):
    # The real lookup: the owner's settings row is made on first use, as anywhere else in the Portal.
    monkeypatch.undo()
    enable_apps(monkeypatch)
    assert sdk.db.query(RuntimeProfile).count() == 0

    body = sdk.get("/app").json()

    assert sdk.db.query(RuntimeProfile).filter(RuntimeProfile.owner_user_id == sdk.owner.id).count() == 1
    assert body["llm"]["default_model"] and body["llm"]["models"]
    assert body["connectors"] == [], "a fresh settings row has no connector set up"
    # No Copilot authorization in it yet: a completion says so.
    response = sdk.complete()
    assert response.status_code == 424 and error_of(response)["code"] == "provider_auth_failed"


# --- 9.2 POST /llm/complete --------------------------------------------------------------------


def test_a_completion_returns_text_and_usage_and_is_recorded(sdk):
    response = sdk.complete({"system": "You are concise.", "max_output_tokens": 256, "temperature": 0.2, "model": "GPT-6-Sol"})

    assert response.status_code == 200
    assert response.json() == {
        "text": "Hello there",
        "model": "gpt-6-sol",
        "finish_reason": "stop",
        "usage": {"input_tokens": 10, "output_tokens": 20, "credits": 0.004},
    }
    (call,) = sdk.gateway.calls
    assert call == {
        "config": sdk.config,
        "model": "gpt-6-sol",
        "messages": HELLO,
        "system": "You are concise.",
        "max_output_tokens": 256,
        "temperature": 0.2,
        "reasoning_effort": None,
        "client": None,
    }
    (row,) = sdk.executions()
    assert (row.app_id, row.owner_user_id, row.kind, row.model) == (sdk.app.id, sdk.owner.id, "llm", "gpt-6-sol")
    assert (row.input_tokens, row.output_tokens, row.credits) == (10, 20, 0.004)
    assert (row.status, row.error_code, row.source, row.visitor_user_id, row.connector_type) == ("succeeded", None, "cluster", None, None)
    assert row.duration_ms is not None and row.duration_ms >= 0


def test_a_completion_passes_the_level_the_app_asked_for(sdk):
    assert sdk.complete({"reasoning_effort": "high"}).status_code == 200

    assert sdk.gateway.calls[-1]["reasoning_effort"] == "high"


def test_the_model_provider_must_be_switched_on_for_the_app(sdk):
    sdk.change(llm_enabled=False)

    for body in ({}, {"stream": True}):
        response = sdk.complete(body)
        assert response.status_code == 403 and error_of(response)["code"] == "llm_disabled"
    assert sdk.gateway.calls == [] and sdk.executions() == []


def test_an_owner_without_a_usable_provider_is_provider_auth_failed(sdk):
    sdk.config["llm"].pop("api_key")

    for body in ({}, {"stream": True}):
        response = sdk.complete(body)
        assert response.status_code == 424 and error_of(response)["code"] == "provider_auth_failed"
    assert sdk.gateway.calls == []
    assert [(row.status, row.error_code, row.model) for row in sdk.executions()] == [("failed", "provider_auth_failed", None)] * 2


@pytest.mark.parametrize(
    "body, code",
    [
        # Not a model the owner's provider offers.
        ({"model": "gpt-5.4"}, "model_not_allowed"),
        ({"model": "auto"}, "model_not_allowed"),
        ({"model": 7}, "invalid_request"),
        ({"stream": "yes"}, "invalid_request"),
        ({"stream": 1}, "invalid_request"),
    ],
)
def test_a_request_the_portal_itself_refuses_is_400(sdk, body, code):
    response = sdk.complete(body)

    assert response.status_code == 400 and error_of(response)["code"] == code
    assert sdk.gateway.calls == []


@pytest.mark.parametrize(
    "error, status",
    [
        (LlmGatewayError("invalid_request", "messages must be a non-empty list."), 400),
        (LlmGatewayError("model_not_allowed", "The provider does not serve it."), 400),
        (LlmGatewayError("provider_auth_failed", "GitHub did not accept the owner's Copilot authorization."), 424),
        (LlmGatewayError("rate_limited", "GitHub Copilot answered HTTP 429.", retry_after=7), 429),
        (LlmGatewayError("rate_limited", "GitHub Copilot answered HTTP 429."), 429),
        (LlmGatewayError("provider_error", "GitHub Copilot answered HTTP 500."), 502),
        (LlmGatewayError("upstream_timeout", "GitHub Copilot did not answer in time."), 504),
    ],
)
@pytest.mark.parametrize("streaming", [False, True])
def test_a_failed_completion_keeps_its_status_and_is_recorded(sdk, error, status, streaming):
    sdk.gateway.error = error
    sdk.gateway.script = [error]

    response = sdk.complete({"stream": streaming})

    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/json")
    assert error_of(response) == {"code": error.code, "message": error.message}
    assert response.headers.get("retry-after") == (str(error.retry_after) if error.retry_after is not None else None)
    (row,) = sdk.executions()
    assert (row.kind, row.status, row.error_code, row.model) == ("llm", "failed", error.code, "gpt-6-sol")
    assert (row.input_tokens, row.output_tokens, row.credits) == (0, 0, None)


def test_a_stream_is_server_sent_events_exactly_as_the_contract_shows(sdk):
    response = sdk.complete({"stream": True, "reasoning_effort": "medium"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.text == (
        'data: {"type": "delta", "text": "Hel"}\n\n'
        'data: {"type": "delta", "text": "lo"}\n\n'
        'data: {"type": "done", "model": "gpt-6-sol", "finish_reason": "stop", '
        '"usage": {"input_tokens": 10, "output_tokens": 20, "credits": 0.004}}\n\n'
    )
    assert sdk.gateway.calls[-1]["reasoning_effort"] == "medium" and sdk.gateway.calls[-1]["model"] == "gpt-6-sol"
    assert sdk.gateway.closed == 1
    (row,) = sdk.executions()
    assert (row.status, row.error_code, row.input_tokens, row.output_tokens, row.credits) == ("succeeded", None, 10, 20, 0.004)


def test_a_stream_that_fails_before_its_first_event_is_not_a_200(sdk):
    # The gateway raises when nothing was produced yet; the caller must see the real status.
    sdk.gateway.script = [LlmGatewayError("provider_auth_failed", "GitHub did not accept the owner's Copilot authorization.")]

    response = sdk.complete({"stream": True})

    assert response.status_code == 424
    assert response.headers["content-type"].startswith("application/json")
    assert error_of(response)["code"] == "provider_auth_failed"

    # A stream that ends without having said anything is the provider's failure too.
    sdk.gateway.script = []
    empty = sdk.complete({"stream": True})
    assert empty.status_code == 502 and error_of(empty)["code"] == "provider_error"
    assert [(row.status, row.error_code) for row in sdk.executions()] == [("failed", "provider_auth_failed"), ("failed", "provider_error")]


def test_a_stream_that_fails_after_it_began_ends_with_an_error_event(sdk):
    sdk.gateway.script = [
        {"type": "delta", "text": "Hello wor"},
        {"type": "error", "error": {"code": "upstream_timeout", "message": "GitHub Copilot did not answer in time."}},
    ]

    response = sdk.complete({"stream": True, "system": "Be brief."})

    assert response.status_code == 200
    assert events_of(response) == [
        {"type": "delta", "text": "Hello wor"},
        {"type": "error", "error": {"code": "upstream_timeout", "message": "GitHub Copilot did not answer in time."}},
    ]
    (row,) = sdk.executions()
    assert (row.status, row.error_code, row.credits) == ("failed", "upstream_timeout", None)
    # The provider never said what it used: counted from the text, about four characters to a token.
    assert (row.input_tokens, row.output_tokens) == (4, 3)


def test_a_stream_that_just_stops_after_it_began_is_told_as_an_error(sdk):
    sdk.gateway.script = [{"type": "delta", "text": "Hel"}]

    events = events_of(sdk.complete({"stream": True}))

    assert events[0] == {"type": "delta", "text": "Hel"}
    assert events[-1]["type"] == "error" and events[-1]["error"]["code"] == "provider_error"
    assert [(row.status, row.error_code) for row in sdk.executions()] == [("failed", "provider_error")]


def test_a_provider_that_reports_no_usage_is_counted_from_the_text(sdk):
    sdk.gateway.result = LlmResult(text="x" * 400, model="gpt-6-sol", finish_reason="stop", usage=LlmUsage(0, 0, None))

    response = sdk.complete({"messages": [{"role": "user", "content": "y" * 100}], "system": "z" * 20})

    # The app is told what the provider said; the budget counts an estimate.
    assert response.json()["usage"] == {"input_tokens": 0, "output_tokens": 0, "credits": None}
    (row,) = sdk.executions()
    assert (row.input_tokens, row.output_tokens, row.credits, row.status) == (30, 100, None, "succeeded")


async def _call_and_hang_up(asgi, path: str, headers: dict, body: dict, *, after_chunks: int = 1) -> list[dict]:
    """Drive the app the way a server does, and disconnect once ``after_chunks`` pieces of the body arrived."""
    raw = json.dumps(body).encode("utf-8")
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": path, "raw_path": path.encode("ascii"), "query_string": b"", "root_path": "",
        "headers": [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in headers.items()]
        + [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode("ascii"))],
        "client": ("127.0.0.1", 50000), "server": ("testserver", 80),
    }
    sent: list[dict] = []
    gone = asyncio.Event()
    delivered = {"request": False, "chunks": 0}

    async def receive():
        if not delivered["request"]:
            delivered["request"] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            delivered["chunks"] += 1
            if delivered["chunks"] >= after_chunks:
                gone.set()

    await asyncio.wait_for(asgi(scope, receive, send), timeout=20)
    return sent


def test_a_stream_the_caller_abandons_is_recorded_and_the_provider_is_let_go(sdk):
    # One piece of text arrives, then the provider keeps thinking and the caller goes away.
    sdk.gateway.script = [{"type": "delta", "text": "Hello wor"}, HANG]
    header = sign_visitor(sdk.app, sdk.visitor)

    sent = asyncio.run(
        _call_and_hang_up(
            sdk.asgi,
            PREFIX + "/llm/complete",
            {**sdk.auth(), "X-EFP-Visitor": header, "X-Forwarded-For": "203.0.113.9"},
            {"messages": [{"role": "user", "content": "Tell me a long story, please"}], "stream": True},
        )
    )

    start = next(message for message in sent if message["type"] == "http.response.start")
    assert start["status"] == 200
    body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    assert body.startswith(b'data: {"type": "delta", "text": "Hello wor"}\n\n')
    assert b'"done"' not in body
    assert sdk.gateway.closed == 1, "the provider's stream is closed with the caller's"
    (row,) = sdk.executions()
    assert (row.kind, row.status, row.error_code, row.model) == ("llm", "cancelled", "client_closed", "gpt-6-sol")
    assert (row.source, row.visitor_user_id, row.credits) == ("external", sdk.visitor.id, None)
    # What it cost is not known: counted from the text that went through.
    assert (row.input_tokens, row.output_tokens) == (7, 3)


def test_the_stream_generator_records_once_however_it_is_closed(sdk):
    import app.api.app_sdk as api

    async def scenario():
        async def provider():
            try:
                yield {"type": "delta", "text": "Hel"}
                yield {"type": "delta", "text": "lo"}
            finally:
                closed.append(True)

        closed: list[bool] = []
        events = provider()
        first = await events.__anext__()
        with sdk.factory() as db:
            call = api.SdkCall(app=sdk.app, app_id=sdk.app.id, owner_user_id=sdk.owner.id, source="cluster", visitor_user_id=None)
            record = api._CallRecord(db, call, "llm")
        record.model = "gpt-6-sol"
        body = api._stream_body(events, first, record, request_chars=5)
        assert await body.__anext__() == b'data: {"type": "delta", "text": "Hel"}\n\n'
        # The server drops the response: the generator is closed where it stands.
        await body.aclose()
        return closed

    assert asyncio.run(scenario()) == [True]
    assert [(row.status, row.error_code, row.output_tokens) for row in sdk.executions()] == [("cancelled", "client_closed", 1)]


def test_a_stream_that_is_thrown_away_before_anybody_read_it_is_still_recorded(sdk):
    import gc

    import app.api.app_sdk as api

    async def provider():
        yield {"type": "delta", "text": "never read"}

    def make(reservation):
        with sdk.factory() as db:
            call = api.SdkCall(app=sdk.app, app_id=sdk.app.id, owner_user_id=sdk.owner.id, source="cluster", visitor_user_id=None)
            record = api._CallRecord(db, call, "llm", reservation=reservation)
        record.model = "gpt-6-sol"
        return record

    # The request is torn down after its first event was read and before the
    # answer began to be sent: the generator never runs, so it has no ending.
    record = make(app_budget.in_flight.reserve(sdk.app.id, 500))
    body = api._recorded_stream(provider(), {"type": "delta", "text": "Hello wo"}, record, request_chars=20)
    assert app_budget.in_flight.calls(sdk.app.id) == 1
    del body
    gc.collect()

    assert _until(lambda: app_budget.in_flight.calls(sdk.app.id) == 0, 10), "not counted as under way for the rest of the hour"
    assert [(row.status, row.error_code, row.input_tokens, row.output_tokens) for row in sdk.executions()] == [("cancelled", "client_closed", 5, 2)]

    # A body that did run records the call itself, once, as before.
    async def read_to_the_end():
        record = make(None)
        body = api._recorded_stream(provider(), {"type": "done", "usage": {"input_tokens": 3, "output_tokens": 4, "credits": None}}, record, request_chars=20)
        return [chunk async for chunk in body]

    assert len(asyncio.run(read_to_the_end())) == 1
    gc.collect()
    time.sleep(0.2)
    assert [(row.status, row.input_tokens) for row in sdk.executions()] == [("cancelled", 5), ("succeeded", 3)]


# --- the real gateway behind the endpoint ------------------------------------------------------


def _sse(*events) -> httpx.Response:
    text = "".join("event: {0}\ndata: {1}\n\n".format(event["type"], json.dumps(event)) for event in events)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=text.encode("utf-8"))


def _copilot(answer):
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={"token": COPILOT_TOKEN, "expires_at": 4102444800})
        return answer(request)

    return seen, lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle))


def _answer(*deltas, charge=4_000_000):
    return _sse(
        *({"type": "response.output_text.delta", "delta": delta} for delta in deltas),
        {
            "type": "response.completed",
            "response": {"status": "completed", "usage": {"input_tokens": 10, "output_tokens": 20}},
            "copilot_usage": {"total_nano_aiu": charge},
        },
    )


def test_the_endpoint_and_the_gateway_together_complete_and_stream(sdk, monkeypatch):
    monkeypatch.undo()
    enable_apps(monkeypatch)
    seen, client = _copilot(lambda request: _answer("Hel", "lo"))
    monkeypatch.setattr(sdk.api, "owner_config", lambda _db, _app: sdk.config)
    monkeypatch.setattr(sdk.api, "llm_client", client)

    whole = sdk.complete({"system": "You are concise.", "max_output_tokens": 64})
    assert whole.status_code == 200
    assert whole.json() == {
        "text": "Hello",
        "model": "gpt-6-sol",
        "finish_reason": "stop",
        "usage": {"input_tokens": 10, "output_tokens": 20, "credits": 0.004},
    }
    sent = json.loads(seen[-1].content)
    # Low, although the owner's assistants think "xhigh": an app call does not inherit that.
    assert sent["reasoning"] == {"effort": "low"} and sent["max_output_tokens"] == 64
    assert sent["instructions"] == "You are concise."

    streamed = sdk.complete({"stream": True, "reasoning_effort": "high"})
    assert events_of(streamed) == [
        {"type": "delta", "text": "Hel"},
        {"type": "delta", "text": "lo"},
        {"type": "done", "model": "gpt-6-sol", "finish_reason": "stop", "usage": {"input_tokens": 10, "output_tokens": 20, "credits": 0.004}},
    ]
    assert json.loads(seen[-1].content)["reasoning"] == {"effort": "high"}
    assert [(row.status, row.input_tokens, row.output_tokens, row.credits) for row in sdk.executions()] == [("succeeded", 10, 20, 0.004)] * 2

    for body, code in (
        ({"messages": []}, "invalid_request"),
        ({"messages": [{"role": "system", "content": "x"}]}, "invalid_request"),
        ({"reasoning_effort": "max"}, "invalid_request"),
        ({"max_output_tokens": 0}, "invalid_request"),
        ({"temperature": 9}, "invalid_request"),
        ({"stream": True, "messages": "Hello"}, "invalid_request"),
    ):
        refused = sdk.post("/llm/complete", {"messages": HELLO, **body})
        assert refused.status_code == 400 and error_of(refused)["code"] == code, body
    assert GITHUB_TOKEN not in whole.text + streamed.text and COPILOT_TOKEN not in whole.text + streamed.text


def test_the_endpoint_and_the_gateway_together_keep_a_providers_refusal(sdk, monkeypatch):
    monkeypatch.undo()
    enable_apps(monkeypatch)
    answers = [httpx.Response(429, headers={"retry-after": "12"}, json={"error": {"message": "slow down"}})]
    seen, client = _copilot(lambda request: answers[0])
    monkeypatch.setattr(sdk.api, "owner_config", lambda _db, _app: sdk.config)
    monkeypatch.setattr(sdk.api, "llm_client", client)

    for streaming in (False, True):
        refused = sdk.complete({"stream": streaming})
        assert refused.status_code == 429 and error_of(refused)["code"] == "rate_limited"
        assert refused.headers["retry-after"] == "12"

    answers[0] = httpx.Response(500, text="boom")
    assert sdk.complete({"stream": True}).status_code == 502
    answers[0] = httpx.Response(401, text="bad credentials")
    denied = sdk.complete()
    assert denied.status_code == 424 and error_of(denied)["code"] == "provider_auth_failed"
    assert [row.error_code for row in sdk.executions()] == ["rate_limited", "rate_limited", "provider_error", "provider_auth_failed"]


# --- who caused the call -----------------------------------------------------------------------


def _visitor_cases(h):
    other_app = add_app(h.db, h.owner, slug="other-app", name="Other")
    ghost = SimpleNamespace(id=987654, username="ghost", nickname=None, group_id=None)
    genuine = sign_visitor(h.app, h.visitor)
    payload, signature = genuine.split(".")[1:]
    return {
        "valid": (genuine, h.visitor.id),
        "valid with spaces around": (f"  {genuine} ", h.visitor.id),
        # Streamlit keeps forwarding the header it got when the page was opened.
        "expired, signature good": (sign_visitor(h.app, h.visitor, expires_at=1_600_000_000), h.visitor.id),
        "forged with another key": (sign_visitor(h.app, h.visitor, key=other_app.visitor_key), None),
        "another app's header": (sign_visitor(other_app, h.visitor), None),
        "this key, another app named": (sign_visitor(h.app, h.visitor, app_id=other_app.id), None),
        "payload swapped": (f"v1.{sign_visitor(h.app, h.owner).split('.')[1]}.{signature}", None),
        "signature cut": (f"v1.{payload}.{signature[:-2]}", None),
        "another version": (f"v2.{payload}.{signature}", None),
        "not a header at all": ("visitor", None),
        "empty": ("", None),
        "a member the Portal does not know": (sign_visitor(h.app, ghost), None),
        "no header": (None, None),
    }


def test_the_visitor_header_names_who_caused_the_call_and_never_fails_it(sdk):
    for name, (header, expected) in _visitor_cases(sdk).items():
        headers = {} if header is None else {"X-EFP-Visitor": header}
        before = len(sdk.executions())

        whole = sdk.complete(**headers)
        streamed = sdk.complete({"stream": True}, **headers)
        connector = sdk.post("/connectors/jira/request", {"method": "GET", "path": "/../x"}, **headers)

        assert (whole.status_code, streamed.status_code, connector.status_code) == (200, 200, 400), name
        rows = sdk.executions()[before:]
        assert [row.kind for row in rows] == ["llm", "llm", "connector"], name
        assert [row.visitor_user_id for row in rows] == [expected] * 3, name
        assert sdk.get("/app", **headers).status_code == 200, name


def test_a_header_with_a_subject_that_is_not_a_member_id_is_no_visitor(sdk):
    import hashlib
    import hmac

    def signed(claims: dict) -> str:
        payload = base64.urlsafe_b64encode(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")
        key = base64.urlsafe_b64decode(sdk.app.visitor_key)
        signature = base64.urlsafe_b64encode(hmac.new(key, f"v1.{payload}".encode("ascii"), hashlib.sha256).digest()).decode("ascii").rstrip("=")
        return f"v1.{payload}.{signature}"

    for subject in ("7", True, None, 0, -3, 1.5, [sdk.visitor.id]):
        assert sdk.complete(**{"X-EFP-Visitor": signed({"app": sdk.app.id, "sub": subject, "exp": 4102444800})}).status_code == 200
    assert sdk.complete(**{"X-EFP-Visitor": signed({"app": sdk.app.id, "sub": sdk.visitor.id})}).status_code == 200, "no expiry at all"

    assert [row.visitor_user_id for row in sdk.executions()] == [None] * 7 + [sdk.visitor.id]


# --- the daily budget ----------------------------------------------------------------------------


def test_a_call_is_refused_once_the_days_credits_are_used(sdk, monkeypatch):
    at(monkeypatch, datetime(2026, 11, 15, 23, 0, 0))
    sdk.change(daily_credits=1.0)
    sdk.gateway.result = LlmResult("ok", "gpt-6-sol", "stop", LlmUsage(10, 20, 0.5))
    spend(sdk, credits=0.6, created_at=datetime(2026, 11, 15, 9, 0, 0))

    # 0.6 of 1.0 used: the call goes through, and what it costs takes the app over the limit.
    assert sdk.complete().status_code == 200
    assert sdk.get("/app").json()["budget"] == {"daily_credits": 1.0, "used_credits": 1.1, "daily_tokens": 2000000, "used_tokens": 30}

    for body in ({}, {"stream": True}):
        refused = sdk.complete(body)
        assert refused.status_code == 429
        assert error_of(refused)["code"] == "budget_exceeded" and "credits" in error_of(refused)["message"]
        # An hour to the next UTC midnight.
        assert refused.headers["retry-after"] == "3600"
    assert len(sdk.gateway.calls) == 1, "the provider is not asked"
    assert len(sdk.executions()) == 2, "a refused call is not recorded"


def test_a_call_is_refused_once_the_days_tokens_are_used(sdk, monkeypatch):
    at(monkeypatch, datetime(2026, 11, 15, 12, 0, 0))
    sdk.change(daily_tokens=100)
    sdk.gateway.result = LlmResult("ok", "gpt-6-sol", "stop", LlmUsage(60, 39, None))

    assert sdk.complete().status_code == 200
    assert sdk.get("/app").json()["budget"]["used_tokens"] == 99
    assert sdk.complete().status_code == 200, "99 of 100: one more call, whatever it turns out to cost"

    refused = sdk.complete()
    assert refused.status_code == 429
    assert error_of(refused)["code"] == "budget_exceeded" and "tokens" in error_of(refused)["message"]
    assert refused.headers["retry-after"] == str(12 * 3600)
    assert sdk.get("/app").json()["budget"] == {"daily_credits": 50.0, "used_credits": 0.0, "daily_tokens": 100, "used_tokens": 198}


def test_the_platforms_defaults_apply_until_the_app_has_figures_of_its_own(sdk, monkeypatch):
    at(monkeypatch, NOON)
    monkeypatch.setattr(get_settings(), "apps_default_daily_credits", 0.01)
    monkeypatch.setattr(get_settings(), "apps_default_daily_tokens", 500)
    spend(sdk, credits=0.01, tokens=(1, 1))

    assert sdk.get("/app").json()["budget"] == {"daily_credits": 0.01, "used_credits": 0.01, "daily_tokens": 500, "used_tokens": 2}
    assert error_of(sdk.complete())["code"] == "budget_exceeded"

    sdk.change(daily_credits=5.0)
    assert sdk.complete().status_code == 200
    sdk.change(daily_tokens=1)
    assert error_of(sdk.complete())["code"] == "budget_exceeded"


def test_a_limit_of_zero_refuses_every_call(sdk, monkeypatch):
    at(monkeypatch, NOON)
    sdk.change(daily_tokens=0)
    assert error_of(sdk.complete())["code"] == "budget_exceeded"

    sdk.change(daily_tokens=None, daily_credits=0.0)
    assert error_of(sdk.complete())["code"] == "budget_exceeded"
    assert sdk.gateway.calls == []


def test_the_budget_starts_again_at_utc_midnight(sdk, monkeypatch):
    sdk.change(daily_credits=1.0, daily_tokens=1000)
    sdk.gateway.result = LlmResult("ok", "gpt-6-sol", "stop", LlmUsage(600, 500, 1.5))
    at(monkeypatch, datetime(2026, 11, 15, 23, 59, 30))

    assert sdk.complete().status_code == 200
    refused = sdk.complete()
    assert refused.status_code == 429 and refused.headers["retry-after"] == "30"

    # Half a second before midnight: still yesterday's budget, and at least a second to wait.
    at(monkeypatch, datetime(2026, 11, 15, 23, 59, 59, 500000))
    assert sdk.complete().headers["retry-after"] == "1"

    at(monkeypatch, datetime(2026, 11, 16, 0, 0, 0))
    assert sdk.get("/app").json()["budget"] == {"daily_credits": 1.0, "used_credits": 0.0, "daily_tokens": 1000, "used_tokens": 0}
    assert sdk.complete().status_code == 200
    assert sdk.complete().status_code == 429
    assert [row.created_at.date().isoformat() for row in sdk.executions()] == ["2026-11-15", "2026-11-16"]


def test_only_the_apps_own_model_calls_of_today_count(sdk, monkeypatch):
    at(monkeypatch, NOON)
    sdk.change(daily_credits=1.0, daily_tokens=100)
    other = add_app(sdk.db, sdk.owner, slug="other-app", name="Other")
    # Yesterday's and tomorrow's calls, another app's, and a connector request: none of them is today's spend.
    spend(sdk, credits=9.0, tokens=(900, 900), created_at=NOON - timedelta(days=1))
    spend(sdk, credits=9.0, tokens=(900, 900), created_at=datetime(2026, 11, 16, 0, 0, 0))
    spend(sdk, credits=None, tokens=(900, 900), kind="connector")
    sdk.db.add(AppExecution(app_id=other.id, owner_user_id=sdk.owner.id, kind="llm", status="succeeded", credits=9.0, input_tokens=900, created_at=NOON))
    # A failed call that used something counts; one that reported no credits adds none.
    spend(sdk, credits=None, tokens=(30, 20), status="failed")
    spend(sdk, credits=0.25, tokens=(0, 0), created_at=datetime(2026, 11, 15, 0, 0, 0))
    sdk.db.commit()

    assert sdk.get("/app").json()["budget"] == {"daily_credits": 1.0, "used_credits": 0.25, "daily_tokens": 100, "used_tokens": 50}
    assert sdk.complete().status_code == 200


def test_connector_requests_cost_nothing_of_the_budget(sdk, monkeypatch):
    at(monkeypatch, NOON)
    sdk.change(daily_tokens=1, daily_credits=0.001)
    monkeypatch.setattr(sdk.api, "connector_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))))

    for _ in range(3):
        assert sdk.post("/connectors/jira/request", {"method": "GET", "path": "/rest/api/2/myself"}).status_code == 200

    assert sdk.get("/app").json()["budget"] == {"daily_credits": 0.001, "used_credits": 0.0, "daily_tokens": 1, "used_tokens": 0}
    # And a spent budget does not stop them.
    spend(sdk, credits=1.0, tokens=(10, 10))
    assert error_of(sdk.complete())["code"] == "budget_exceeded"
    assert sdk.post("/connectors/jira/request", {"method": "GET", "path": "/rest/api/2/myself"}).status_code == 200


# --- calls under way count toward the budget ---------------------------------------------------------


class _HeldProvider:
    """Stands in for ``llm_gateway.complete``: a call waits until the test lets one answer.

    One at a time, so that no two requests are ever in the database at once:
    the tests' sessions share a single in-memory connection.
    """

    def __init__(self, result: LlmResult) -> None:
        self.result = result
        self.entered = threading.Semaphore(0)
        self.hold = True
        self.calls: list[dict] = []
        # Threading primitives: every request of the test client runs on an event loop of its own.
        self._answers = threading.Semaphore(0)

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        self.entered.release()
        if self.hold:
            await asyncio.to_thread(self._answers.acquire, True, 30)
        return self.result

    def let_one_answer(self) -> None:
        self._answers.release()


def _until(condition, seconds: float = 30.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return bool(condition())


def _in_the_background(sdk, body: dict):
    """Start a completion on a thread of its own; ``(thread, responses)``."""
    responses: list = []
    client = TestClient(sdk.asgi)

    def call():
        responses.append(client.post(PREFIX + "/llm/complete", json={"messages": HELLO, **body}, headers=sdk.auth()))

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    return thread, responses


def test_a_call_under_way_is_counted_as_what_it_may_come_to(sdk, monkeypatch):
    at(monkeypatch, NOON)
    sdk.change(daily_tokens=1000)
    provider = _HeldProvider(LlmResult("ok", "gpt-6-sol", "stop", LlmUsage(10, 20, None)))
    monkeypatch.setattr(sdk.api.llm_gateway, "complete", provider.complete)

    # Nothing is spent yet. The first call may write an answer of 1000 tokens, and has not ended.
    thread, responses = _in_the_background(sdk, {"max_output_tokens": 1000})
    assert provider.entered.acquire(timeout=30)
    assert app_budget.in_flight.calls(sdk.app.id) == 1
    assert app_budget.in_flight.tokens(sdk.app.id) == 1002, "two tokens of text and the answer it asked for"

    # Before, a second call read "0 of 1000 used" and went through as well, and a third, and a hundredth.
    for body in ({}, {"stream": True}):
        refused = sdk.complete(body)
        assert refused.status_code == 429
        assert error_of(refused)["code"] == "rate_limited", "not spent: under way, and worth another try soon"
        assert refused.headers["retry-after"] == str(app_budget.IN_FLIGHT_RETRY_SECONDS)
    assert len(provider.calls) == 1, "the provider is not asked"
    assert sdk.executions() == [], "a refused call is not recorded"
    assert app_budget.in_flight.calls(sdk.app.id) == 1, "and is not counted either"
    # The budget an app reads is what is spent, as before.
    assert sdk.get("/app").json()["budget"]["used_tokens"] == 0

    provider.let_one_answer()
    thread.join(timeout=30)
    assert [response.status_code for response in responses] == [200]

    # It came to 30 tokens, not 1002: once its row is written it is counted as that.
    assert app_budget.in_flight.calls(sdk.app.id) == 0
    assert sdk.get("/app").json()["budget"]["used_tokens"] == 30
    provider.hold = False
    assert sdk.complete().status_code == 200


def test_calls_that_arrive_together_cannot_all_pass_on_the_same_spent_figure(sdk, monkeypatch):
    at(monkeypatch, NOON)
    sdk.change(daily_tokens=10_000)
    provider = _HeldProvider(LlmResult("ok", "gpt-6-sol", "stop", LlmUsage(10, 20, None)))
    monkeypatch.setattr(sdk.api.llm_gateway, "complete", provider.complete)

    # A call that names no cap is counted as an answer of RESERVED_OUTPUT_TOKENS.
    started = []
    for _ in range(3):
        started.append(_in_the_background(sdk, {}))
        if len(started) < 3:
            assert provider.entered.acquire(timeout=30)
    per_call = app_budget.RESERVED_OUTPUT_TOKENS + 2
    assert per_call == 4098

    # Two are under way (8196 of 10000). The third is admitted: what is spent
    # and under way is below the limit, whatever the third itself comes to.
    assert provider.entered.acquire(timeout=30)
    assert app_budget.in_flight.tokens(sdk.app.id) == 3 * per_call
    # A fourth is not: 12294 of 10000 are spoken for.
    refused = sdk.complete()
    assert refused.status_code == 429 and error_of(refused)["code"] == "rate_limited"
    assert len(provider.calls) == 3

    for answered in (1, 2, 3):
        provider.let_one_answer()
        assert _until(lambda: sum(len(responses) for _thread, responses in started) == answered)
    for thread, _responses in started:
        thread.join(timeout=30)
    assert [responses[0].status_code for _thread, responses in started] == [200, 200, 200]
    assert sdk.get("/app").json()["budget"]["used_tokens"] == 90
    assert app_budget.in_flight.calls(sdk.app.id) == 0


def test_every_way_a_call_ends_gives_back_what_it_was_counted_as(sdk, monkeypatch):
    at(monkeypatch, NOON)
    counted = lambda: app_budget.in_flight.calls(sdk.app.id)  # noqa: E731

    assert sdk.complete({"max_output_tokens": 500}).status_code == 200
    assert sdk.complete({"stream": True}).status_code == 200
    assert counted() == 0

    # Refused at a gate.
    assert sdk.complete({"model": "gpt-5.4"}).status_code == 400
    sdk.config["llm"].pop("api_key")
    assert sdk.complete().status_code == 424
    sdk.config["llm"]["api_key"] = GITHUB_TOKEN
    sdk.change(daily_tokens=0)
    assert error_of(sdk.complete())["code"] == "budget_exceeded"
    sdk.change(daily_tokens=None, llm_enabled=False)
    assert error_of(sdk.complete())["code"] == "llm_disabled"
    sdk.change(llm_enabled=True)
    assert counted() == 0

    # The provider failed, before and after the answer began.
    sdk.gateway.error = LlmGatewayError("provider_error", "GitHub Copilot answered HTTP 500.")
    assert sdk.complete().status_code == 502
    sdk.gateway.error = RuntimeError("boom")
    assert sdk.complete().status_code == 500
    sdk.gateway.script = [LlmGatewayError("upstream_timeout", "GitHub Copilot did not answer in time.")]
    assert sdk.complete({"stream": True}).status_code == 504
    sdk.gateway.script = [{"type": "delta", "text": "Hel"}, {"type": "error", "error": {"code": "provider_error", "message": "gone"}}]
    assert sdk.complete({"stream": True}).status_code == 200
    assert counted() == 0

    # The caller went away in the middle of a stream.
    sdk.gateway.script = [{"type": "delta", "text": "Hello wor"}, HANG]
    asyncio.run(_call_and_hang_up(sdk.asgi, PREFIX + "/llm/complete", sdk.auth(), {"messages": HELLO, "stream": True}))
    assert counted() == 0
    assert sdk.executions()[-1].status == "cancelled"


def test_the_ledger_of_calls_under_way():
    clock = {"now": 100.0}
    ledger = app_budget.InFlightLedger(max_seconds=60, clock=lambda: clock["now"])

    first = ledger.reserve("app-1", 500)
    second = ledger.reserve("app-1", 500)
    other = ledger.reserve("app-2", 70)
    assert (ledger.tokens("app-1"), ledger.calls("app-1")) == (1000, 2)
    # A call asks what the ones ahead of it are counted as: of two that arrive
    # together with room for one, the first goes through and the second waits.
    assert ledger.tokens("app-1", before=second) == 500
    assert ledger.tokens("app-1", before=first) == 0
    assert (ledger.tokens("app-2"), ledger.tokens("app-3")) == (70, 0)

    # Two calls counted as the same figure are still two: giving one back leaves the other.
    first.release()
    first.release()
    assert (ledger.tokens("app-1"), ledger.calls("app-1")) == (500, 1)
    second.release()
    assert (ledger.tokens("app-1"), ledger.calls("app-1")) == (0, 0)

    # A reservation nobody gave back is forgotten after max_seconds, not kept for the rest of the day.
    clock["now"] += 59
    assert ledger.tokens("app-2") == 70
    clock["now"] += 1
    assert (ledger.tokens("app-2"), ledger.calls("app-2")) == (0, 0)
    other.release()

    assert app_budget.RESERVATION_MAX_SECONDS == 3600.0 and app_budget.IN_FLIGHT_RETRY_SECONDS == 5


@pytest.mark.parametrize(
    "input_tokens, max_output_tokens, expected",
    [
        (10, 256, 266),
        (10, None, 10 + 4096),
        (0, 1, 1),
        # Anything that is not a positive whole number names no cap.
        (10, 0, 10 + 4096),
        (10, -5, 10 + 4096),
        (10, True, 10 + 4096),
        (10, "256", 10 + 4096),
        (10, 2.5, 10 + 4096),
        (None, None, 4096),
        (-3, 7, 7),
    ],
)
def test_what_a_call_under_way_is_counted_as(input_tokens, max_output_tokens, expected):
    assert app_budget.RESERVED_OUTPUT_TOKENS == 4096
    assert app_budget.reservation_tokens(input_tokens, max_output_tokens) == expected


def test_a_request_is_never_counted_beyond_what_the_gateway_accepts(sdk):
    api = sdk.api
    most = api.estimate_tokens(api.llm_gateway.MAX_INPUT_CHARS) + api.llm_gateway.MAX_OUTPUT_TOKENS

    assert api._in_flight_tokens(40, 256) == 10 + 256
    assert api._in_flight_tokens(40, None) == 10 + app_budget.RESERVED_OUTPUT_TOKENS
    assert api._in_flight_tokens(10**9, 10**12) == most == 1_128_000


@pytest.mark.parametrize(
    "used, daily, under_way, expected",
    [
        (0, 1000, 0, False),
        # Nothing under way never refuses a call here, whatever is spent: that is ``exceeded``'s to say.
        (1000, 1000, 0, False),
        (0, 1000, 999, False),
        (0, 1000, 1000, True),
        (400, 1000, 599, False),
        (400, 1000, 600, True),
        (0, 0, 1, True),
    ],
)
def test_the_calls_under_way_take_up_what_is_left(used, daily, under_way, expected):
    budget = {"daily_credits": 50.0, "used_credits": 0.0, "daily_tokens": daily, "used_tokens": used}

    assert app_budget.crowded(budget, under_way) is expected


# --- 9.3 connectors ------------------------------------------------------------------------------


class Upstream:
    def __init__(self, handler=None):
        self.requests: list[httpx.Request] = []
        self._handler = handler or (lambda request: httpx.Response(200, json={"key": "ABC-1"}))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


@pytest.fixture
def upstream(sdk, monkeypatch):
    target = Upstream()
    monkeypatch.setattr(sdk.api, "connector_client", target.client)
    return target


def test_connectors_lists_what_the_app_may_call_without_a_credential(sdk):
    sdk.change(connectors_enabled=["github", "jira", "splunk"])

    response = sdk.get("/connectors")

    assert response.status_code == 200
    assert response.json() == {
        "connectors": [
            {"type": "jira", "base_url": "https://jira.example.test", "write": False},
            {"type": "github", "base_url": "https://api.github.com", "write": True},
        ]
    }
    assert CONNECTOR_TOKEN not in response.text

    sdk.change(connectors_enabled=[], connectors_write=[])
    assert sdk.get("/connectors").json() == {"connectors": []}


def test_a_connector_request_is_signed_forwarded_recorded_and_logged_without_its_query(sdk, upstream, caplog):
    body = {
        "method": "get",
        "path": "/rest/api/2/issue/ABC-1?expand=names",
        "query": {"fields": "summary", "jql": "password=hunter2"},
        "headers": {"Accept": "application/json", "Authorization": "Bearer mine"},
        "json": None,
        "body_base64": None,
    }
    with caplog.at_level(logging.INFO):
        response = sdk.post("/connectors/jira/request", body, **{"X-EFP-Visitor": sign_visitor(sdk.app, sdk.visitor)})

    assert response.status_code == 200
    assert response.json() == {
        "status": 200,
        "headers": {"content-type": "application/json"},
        "json": {"key": "ABC-1"},
        "body_base64": None,
        "truncated": False,
    }
    (request,) = upstream.requests
    assert request.method == "GET"
    assert str(request.url) == "https://jira.example.test/rest/api/2/issue/ABC-1?expand=names&fields=summary&jql=password%3Dhunter2"
    # The connector's own sign-in, never the caller's: on a company-hosted Jira a token is sent as Bearer.
    expected = f"Bearer {CONNECTOR_TOKEN}"
    assert request.headers["authorization"] == expected

    (row,) = sdk.executions()
    assert (row.kind, row.connector_type, row.status, row.error_code) == ("connector", "jira", "succeeded", None)
    assert (row.model, row.input_tokens, row.output_tokens, row.credits) == (None, 0, 0, None)
    assert (row.source, row.visitor_user_id, row.owner_user_id) == ("cluster", sdk.visitor.id, sdk.owner.id)
    assert row.duration_ms is not None

    lines = [record.getMessage() for record in caplog.records if record.name == "app.api.app_sdk" and "connector request" in record.getMessage()]
    assert lines == [f"app sdk connector request app_id={sdk.app.id} type=jira method=GET path=/rest/api/2/issue/ABC-1 status=200"]
    everything = "\n".join(record.getMessage() for record in caplog.records)
    for secret in ("hunter2", "expand=names", "fields=summary", CONNECTOR_TOKEN, sdk.token, expected):
        assert secret not in everything


def test_an_upstream_that_answers_is_a_200_whatever_it_said(sdk, upstream, caplog):
    upstream._handler = lambda request: httpx.Response(404, json={"errorMessages": ["Issue does not exist"]})

    with caplog.at_level(logging.INFO, logger="app.api.app_sdk"):
        response = sdk.post("/connectors/jira/request", {"method": "GET", "path": "/rest/api/2/issue/NOPE-1"})

    assert response.status_code == 200
    assert response.json()["status"] == 404 and response.json()["json"] == {"errorMessages": ["Issue does not exist"]}
    assert [(row.status, row.error_code) for row in sdk.executions()] == [("succeeded", None)]
    assert "status=404" in caplog.records[-1].getMessage()


def test_a_connector_the_owner_did_not_switch_on_for_the_app_is_refused(sdk, upstream):
    for kind in ("confluence", "jenkins", "nexus", "splunk"):
        response = sdk.post(f"/connectors/{kind}/request", {"method": "GET", "path": "/"})
        assert response.status_code == 403, kind
        assert error_of(response)["code"] == "connector_disabled"

    sdk.change(connectors_enabled=["github"], connectors_write=["github"])
    assert error_of(sdk.post("/connectors/jira/request", {"method": "GET", "path": "/"}))["code"] == "connector_disabled"
    assert upstream.requests == [] and sdk.executions() == []


@pytest.mark.parametrize("kind", ["gitlab", "aws", "pgsql", "local_bridge", "JIRA%20x"])
def test_a_type_that_is_no_connector_of_the_sdk_is_invalid_request(sdk, upstream, kind):
    response = sdk.post(f"/connectors/{kind}/request", {"method": "GET", "path": "/"})

    assert response.status_code == 400 and error_of(response)["code"] == "invalid_request"
    assert upstream.requests == []


def test_writing_needs_the_owners_leave_for_that_connector(sdk, upstream):
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        refused = sdk.post("/connectors/jira/request", {"method": method, "path": "/rest/api/2/issue", "json": {"fields": {}}})
        assert refused.status_code == 403, method
        assert error_of(refused)["code"] == "method_not_allowed"
    assert upstream.requests == []
    assert [(row.status, row.error_code, row.connector_type) for row in sdk.executions()] == [("failed", "method_not_allowed", "jira")] * 4

    # Reading is always allowed, and GitHub may be written to.
    assert sdk.post("/connectors/jira/request", {"method": "HEAD", "path": "/rest/api/2/myself"}).status_code == 200
    written = sdk.post("/connectors/github/request", {"method": "POST", "path": "/repos/acme/tools/issues", "json": {"title": "Bug"}})
    assert written.status_code == 200
    request = upstream.requests[-1]
    assert (request.method, str(request.url)) == ("POST", "https://api.github.com/repos/acme/tools/issues")
    assert json.loads(request.content) == {"title": "Bug"}
    assert request.headers["authorization"] == f"Bearer {CONNECTOR_TOKEN}"

    # The leave is the app's setting at the time of the call.
    sdk.change(connectors_write=[])
    assert error_of(sdk.post("/connectors/github/request", {"method": "POST", "path": "/x", "json": {}}))["code"] == "method_not_allowed"


def test_a_raw_body_travels_as_bytes_and_bad_base64_is_refused(sdk, upstream):
    sent = sdk.post(
        "/connectors/github/request",
        {"method": "PUT", "path": "/upload", "headers": {"Content-Type": "application/octet-stream"}, "body_base64": base64.b64encode(b"\x00\x01binary").decode("ascii")},
    )
    assert sent.status_code == 200
    assert upstream.requests[-1].content == b"\x00\x01binary"

    for bad in ("not base64!", 12, ["x"]):
        refused = sdk.post("/connectors/github/request", {"method": "PUT", "path": "/upload", "body_base64": bad})
        assert refused.status_code == 400 and error_of(refused)["code"] == "invalid_request"
    assert len(upstream.requests) == 1


@pytest.mark.parametrize(
    "body, status, code",
    [
        ({"method": "GET", "path": "//evil.example/x"}, 400, "invalid_path"),
        ({"method": "GET", "path": "/rest/../../admin"}, 400, "invalid_path"),
        ({"method": "GET", "path": "https://evil.example/x"}, 400, "invalid_path"),
        ({"method": "GET", "path": "/a\\b"}, 400, "invalid_path"),
        ({"method": "GET"}, 400, "invalid_path"),
        ({"method": "GET", "path": "/x", "query": "a=b"}, 400, "invalid_request"),
        ({"method": "GET", "path": "/x", "headers": ["Accept"]}, 400, "invalid_request"),
        ({"method": "TRACE", "path": "/x"}, 403, "method_not_allowed"),
        ({"path": "/x"}, 403, "method_not_allowed"),
    ],
)
def test_a_request_the_proxy_refuses_keeps_its_code(sdk, upstream, body, status, code):
    response = sdk.post("/connectors/jira/request", body)

    assert response.status_code == status and error_of(response)["code"] == code
    assert upstream.requests == []
    assert [(row.status, row.error_code) for row in sdk.executions()] == [("failed", code)]


def test_a_connector_the_owner_has_not_set_up_is_connector_not_configured(sdk, upstream):
    sdk.change(connectors_enabled=["jira", "splunk"])
    sdk.config["jira"]["enabled"] = False

    for kind in ("jira", "splunk"):
        response = sdk.post(f"/connectors/{kind}/request", {"method": "GET", "path": "/"})
        assert response.status_code == 424 and error_of(response)["code"] == "connector_not_configured"
    assert upstream.requests == []
    assert [row.error_code for row in sdk.executions()] == ["connector_not_configured"] * 2


def test_an_upstream_that_cannot_be_reached_or_takes_too_long(sdk, upstream, caplog):
    def unreachable(request):
        raise httpx.ConnectError("connection refused")

    def slow(request):
        raise httpx.ReadTimeout("timed out")

    upstream._handler = unreachable
    with caplog.at_level(logging.INFO, logger="app.api.app_sdk"):
        down = sdk.post("/connectors/jira/request", {"method": "GET", "path": "/rest/api/2/myself?token=abc"})
        upstream._handler = slow
        late = sdk.post("/connectors/jira/request", {"method": "GET", "path": "/rest/api/2/myself"})

    assert (down.status_code, error_of(down)["code"]) == (502, "upstream_error")
    assert (late.status_code, error_of(late)["code"]) == (504, "upstream_timeout")
    assert CONNECTOR_TOKEN not in down.text + late.text
    assert [(row.status, row.error_code) for row in sdk.executions()] == [("failed", "upstream_error"), ("failed", "upstream_timeout")]
    lines = [record.getMessage() for record in caplog.records if "connector request" in record.getMessage()]
    assert [line.rsplit(" ", 2)[1:] for line in lines] == [["path=/rest/api/2/myself", "status=upstream_error"], ["path=/rest/api/2/myself", "status=upstream_timeout"]]


def test_the_log_line_cannot_be_broken_by_what_the_app_sends(sdk, upstream, caplog):
    with caplog.at_level(logging.INFO, logger="app.api.app_sdk"):
        sdk.post("/connectors/jira/request", {"method": "GET\nforged line", "path": "/x y\r\nstatus=200 forged" + "z" * 500})

    line = [record.getMessage() for record in caplog.records if "connector request" in record.getMessage()][-1]
    assert "\n" not in line and "\r" not in line and len(line) < 400
    assert line.count(" status=") == 1 and line.endswith("status=method_not_allowed")


def test_a_connector_at_an_address_apps_may_not_call_is_refused_and_not_listed(sdk, upstream):
    # The owner's Jira "address" is the Portal's own port: asked through the
    # proxy, the Portal would answer its cluster-only API to itself.
    sdk.config["jira"]["instances"][0]["url"] = "http://127.0.0.1:8000"

    response = sdk.post("/connectors/jira/request", {"method": "GET", "path": "/api/internal/agents/a1/sessions/metadata"})

    assert response.status_code == 424
    assert error_of(response)["code"] == "connector_not_configured" and "may not call" in error_of(response)["message"]
    assert upstream.requests == []
    assert [(row.status, row.error_code) for row in sdk.executions()] == [("failed", "connector_not_configured")]
    assert sdk.get("/connectors").json() == {"connectors": [{"type": "github", "base_url": "https://api.github.com", "write": True}]}
    assert sdk.get("/app").json()["connectors"] == [{"type": "github", "write": True}]


def test_a_connector_header_that_is_not_ascii_is_a_400_not_an_unreachable_connector(sdk, upstream):
    response = sdk.post("/connectors/jira/request", {"method": "GET", "path": "/x", "headers": {"X-Note": "caf" + chr(0xE9)}})

    assert response.status_code == 400 and error_of(response)["code"] == "invalid_request"
    assert upstream.requests == []


# --- the database while a call waits -----------------------------------------------------------------


@pytest.fixture
def pooled(sdk, tmp_path):
    """The SDK API on a database whose pool holds one connection: what a busy Portal's pool is to one more caller."""
    engine = create_engine(
        "sqlite:///" + (tmp_path / "pool.db").as_posix(),
        connect_args={"check_same_thread": False},
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.3,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    with factory() as db:
        add_group(db, "Default", is_default=True)
        owner = add_user(db, "owner", nickname="Olive")
        row = add_app(db, owner, llm_enabled=True, connectors_enabled=["jira"], connectors_write=[])
        token, app_id = row.token_value, row.id

    def override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    def rows():
        with factory() as db:
            return [(row.kind, row.status) for row in db.query(AppExecution).order_by(AppExecution.id)]

    def usable_by_others() -> bool:
        try:
            with engine.connect() as connection:
                connection.execute(text("select 1"))
            return True
        except Exception:  # noqa: BLE001 - the pool gave no connection in time
            return False

    sdk.asgi.dependency_overrides[get_db] = override_db
    sdk.token = token
    try:
        yield SimpleNamespace(engine=engine, app_id=app_id, rows=rows, usable_by_others=usable_by_others)
    finally:
        engine.dispose()


def test_no_pooled_connection_is_held_while_a_provider_or_a_connector_is_waited_for(sdk, pooled, monkeypatch):
    seen: dict[str, tuple] = {}

    async def look(name: str) -> None:
        # How many connections are out, and whether anybody else can have one.
        seen[name] = (pooled.engine.pool.checkedout(), await asyncio.to_thread(pooled.usable_by_others))

    async def complete(**kwargs):
        await look("completion")
        return sdk.gateway.result

    async def stream(**kwargs):
        await look("stream, before its first event")
        yield {"type": "delta", "text": "Hel"}
        await look("stream, while it is sent")
        yield {"type": "done", "model": "gpt-6-sol", "finish_reason": "stop", "usage": {"input_tokens": 1, "output_tokens": 1, "credits": None}}

    async def connector(**kwargs):
        await look("connector")
        return {"status": 200, "headers": {}, "json": {"ok": True}, "body_base64": None, "truncated": False}

    monkeypatch.setattr(sdk.api.llm_gateway, "complete", complete)
    monkeypatch.setattr(sdk.api.llm_gateway, "stream", stream)
    monkeypatch.setattr(sdk.api.connector_proxy, "request", connector)

    assert sdk.complete().status_code == 200
    assert sdk.complete({"stream": True}).status_code == 200
    assert sdk.post("/connectors/jira/request", {"method": "GET", "path": "/rest/api/2/myself"}).status_code == 200

    # Before, each of the first, second and fourth held the pool's connection while it waited.
    assert seen == {
        "completion": (0, True),
        "stream, before its first event": (0, True),
        "stream, while it is sent": (0, True),
        "connector": (0, True),
    }
    # And every call left its row: with the connection held, the row had none to be written through.
    assert pooled.rows() == [("llm", "succeeded"), ("llm", "succeeded"), ("connector", "succeeded")]
    assert app_budget.in_flight.calls(pooled.app_id) == 0


def test_a_row_that_cannot_be_written_at_once_is_written_later_and_the_answer_does_not_wait(sdk, pooled, monkeypatch):
    monkeypatch.setattr(sdk.api, "RECORD_RETRY_SECONDS", (0.2,) * 50)
    held = {}

    async def complete(**kwargs):
        # While the provider answers, the rest of the Portal takes the pool's only connection and keeps it.
        held["connection"] = pooled.engine.connect()
        return sdk.gateway.result

    monkeypatch.setattr(sdk.api.llm_gateway, "complete", complete)

    response = sdk.complete({"max_output_tokens": 100})

    try:
        assert response.status_code == 200, "the call happened: its bookkeeping does not fail it"
        # No row yet, so the call is still counted as under way: the budget sees it in one place or the other.
        assert app_budget.in_flight.calls(pooled.app_id) == 1
        assert app_budget.in_flight.tokens(pooled.app_id) == 102
    finally:
        held["connection"].close()

    def written() -> bool:
        try:
            return pooled.rows() == [("llm", "succeeded")]
        except Exception:  # noqa: BLE001 - the writer has the connection at this moment
            return False

    assert _until(written, 20), "the row is tried again until it is there"
    assert _until(lambda: app_budget.in_flight.calls(pooled.app_id) == 0, 5)


def test_a_row_that_can_never_be_written_gives_the_call_back_and_says_so(sdk, monkeypatch, caplog):
    import app.api.app_sdk as api

    monkeypatch.setattr(api, "RECORD_RETRY_SECONDS", (0.01, 0.01))
    attempts = []
    monkeypatch.setattr(api, "record_execution", lambda db, **fields: attempts.append(fields["status"]))
    reservation = app_budget.in_flight.reserve(sdk.app.id, 500)
    with sdk.factory() as db:
        call = api.SdkCall(app=sdk.app, app_id=sdk.app.id, owner_user_id=sdk.owner.id, source="cluster", visitor_user_id=None)
        record = api._CallRecord(db, call, "llm", reservation=reservation)

    with caplog.at_level(logging.ERROR, logger="app.api.app_sdk"):
        record.finish("succeeded", input_tokens=1, output_tokens=2)
        record.finish("failed")
        assert _until(lambda: app_budget.in_flight.calls(sdk.app.id) == 0, 10)

    assert attempts == ["succeeded"] * 3, "at once, then after each pause; a second finish writes nothing"
    assert [entry.getMessage() for entry in caplog.records if "is lost" in entry.getMessage()]


def test_the_row_is_written_off_the_event_loop_however_the_call_ends(sdk, upstream, monkeypatch):
    import app.api.app_sdk as api

    written_on_a_loop: list[bool] = []
    real = api.record_execution

    def recording(db, **fields):
        # A thread that runs an event loop has a running loop; a worker thread has none.
        try:
            asyncio.get_running_loop()
            written_on_a_loop.append(True)
        except RuntimeError:
            written_on_a_loop.append(False)
        return real(db, **fields)

    monkeypatch.setattr(api, "record_execution", recording)

    assert sdk.complete().status_code == 200
    assert sdk.complete({"stream": True}).status_code == 200
    sdk.gateway.error = LlmGatewayError("provider_error", "GitHub Copilot answered HTTP 500.")
    assert sdk.complete().status_code == 502
    assert sdk.post("/connectors/jira/request", {"method": "GET", "path": "/rest/api/2/myself"}).status_code == 200
    assert sdk.post("/connectors/jira/request", {"method": "GET", "path": "/../x"}).status_code == 400
    # A caller that goes away: the request is being cancelled when its row is written.
    sdk.gateway.script = [{"type": "delta", "text": "Hello wor"}, HANG]
    asyncio.run(_call_and_hang_up(sdk.asgi, PREFIX + "/llm/complete", sdk.auth(), {"messages": HELLO, "stream": True}))

    assert [row.status for row in sdk.executions()] == ["succeeded", "succeeded", "failed", "succeeded", "failed", "cancelled"]
    # A write waits for the database, which the event loop must never do.
    assert written_on_a_loop == [False] * 6


def test_the_endpoint_refuses_a_request_over_the_bounds(sdk, monkeypatch):
    monkeypatch.undo()
    enable_apps(monkeypatch)
    seen, client = _copilot(lambda request: _answer("ok"))
    monkeypatch.setattr(sdk.api, "owner_config", lambda _db, _app: sdk.config)
    monkeypatch.setattr(sdk.api, "llm_client", client)
    monkeypatch.setattr(sdk.api.llm_gateway, "MAX_INPUT_CHARS", 40)

    assert sdk.complete({"max_output_tokens": 128_000, "system": "s" * 35}).status_code == 200
    asked = len(seen)

    for body in (
        {"max_output_tokens": 128_001},
        {"system": "s" * 36},
        {"messages": [{"role": "user", "content": "x" * 41}]},
        {"stream": True, "max_output_tokens": 10**12},
        {"messages": [{"role": "user", "content": ""}] * 1000 + HELLO},
    ):
        refused = sdk.post("/llm/complete", {"messages": HELLO, **body})
        assert refused.status_code == 400 and error_of(refused)["code"] == "invalid_request", body
    assert len(seen) == asked, "nothing was sent for them"
    assert [row.error_code for row in sdk.executions()] == [None] + ["invalid_request"] * 5
    assert app_budget.in_flight.calls(sdk.app.id) == 0


# --- 9.4 POST /images/inspect ------------------------------------------------------------------

IA_PASSWORD = "pw-vision-s3cret"
IA_JWT = "eyJhbGciOiJSUzI1NiJ9.vision.signature"
AI_CHAT = "https://ai.example.test/v1/api/v1/chat/completions"
# How a file of each kind begins; nothing after the first twelve bytes is looked at.
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(16)
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + bytes(16)
QUESTION = "Which field is highlighted?"
IMAGE_LIMITS = {"max_images": 4, "max_image_bytes": 5242880, "max_total_bytes": 20971520}
# The default call under way: 27 characters of prompt, one image, an answer of 4,096 tokens.
ONE_IMAGE_UNDER_WAY = 7 + 1500 + 4096


def b64(data: bytes) -> str:
    return base64.b64encode(bytes(data)).decode("ascii")


def picture(data: bytes = PNG, media_type: str = "image/png") -> dict:
    return {"media_type": media_type, "data_base64": b64(data)}


def image_connector(**section) -> dict:
    """The owner's Image analysis connector, set up."""
    connector = {
        "enabled": True,
        "model": "gpt-5.6-sol",
        "ai_platform": {"auth": {"username": "vision-svc", "password": IA_PASSWORD, "usercase": "uc-vision"}},
    }
    connector.update(section)
    return connector


def vision_answer(text="The field Email is marked in red.", usage=None) -> dict:
    answer = {
        "id": "chatcmpl-1",
        "model": "gpt-5.6-sol-2026-05-01",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
    }
    if usage is not False:
        answer["usage"] = usage or {"prompt_tokens": 1830, "completion_tokens": 210, "total_tokens": 2040}
    return answer


class Vision:
    """AI Platform as an image call meets it: the iB2B exchange and chat/completions, each request remembered."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.answer = lambda request: httpx.Response(200, json=vision_answer())
        self.exchange = lambda request: httpx.Response(200, json={"issued_token": IA_JWT, "token_type": "JWT"})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.exchange(request) if request.url.host == "sts.example.test" else self.answer(request)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))

    def calls(self) -> list[httpx.Request]:
        return [request for request in self.requests if request.url.host == "ai.example.test"]


def ai_platform_deployment(monkeypatch, **overrides) -> None:
    values = {
        "ai_platform_chat_host": "https://ai.example.test",
        "ai_platform_chat_uri": "/v1/api/v1/chat/completions",
        "ai_platform_responses_uri": "",
        "ai_platform_ib2b_host": "https://sts.example.test",
        "ai_platform_ib2b_uri": "/sts/v1/token",
        "ai_platform_trust_token_header": "X-Test-Trust-Token",
        "ai_platform_tracking_prefix": "EFPT",
    }
    values.update(overrides)
    for name, value in values.items():
        monkeypatch.setattr(get_settings(), name, value)


@pytest.fixture
def vision(sdk, monkeypatch):
    """Image analysis turned on for the app, the owner's connector set up, AI Platform a fake."""
    ai_platform_deployment(monkeypatch)
    llm_ai_platform.reset_token_cache()
    sdk.change(connectors_enabled=["jira", "github", "image_analysis"])
    sdk.config["image_analysis"] = image_connector()
    target = Vision()
    monkeypatch.setattr(sdk.api, "image_client", target.client)

    def inspect(body=None, **headers):
        payload = {"images": [picture()], "prompt": QUESTION}
        payload.update(body or {})
        return sdk.post("/images/inspect", payload, **headers)

    target.inspect = inspect
    try:
        yield target
    finally:
        llm_ai_platform.reset_token_cache()


def test_app_says_whether_the_app_can_ask_about_images(sdk, vision, monkeypatch):
    # Turned on for the app, and the owner's connector can run.
    body = sdk.get("/app").json()
    assert body["image_analysis"] == {"enabled": True, "model": "gpt-5.6-sol", **IMAGE_LIMITS}
    assert list(body["image_analysis"]) == ["enabled", "model", "max_images", "max_image_bytes", "max_total_bytes"]
    # It is not one of the connectors: there is no address an app could call.
    assert body["connectors"] == [{"type": "jira", "write": False}, {"type": "github", "write": True}]
    assert sdk.get("/connectors").json() == {
        "connectors": [
            {"type": "jira", "base_url": "https://jira.example.test", "write": False},
            {"type": "github", "base_url": "https://api.github.com", "write": True},
        ]
    }
    assert IA_PASSWORD not in sdk.get("/app").text

    # Turned on for the app, but the owner's connector cannot run.
    off = {"enabled": False, "model": None, **IMAGE_LIMITS}
    for connector in (
        None,
        {},
        image_connector(enabled=False),
        image_connector(ai_platform={}),
        image_connector(ai_platform={"auth": {"username": "vision-svc", "password": IA_PASSWORD}}),
    ):
        sdk.config["image_analysis"] = connector
        assert sdk.get("/app").json()["image_analysis"] == off, connector
    sdk.config["image_analysis"] = image_connector()
    for missing in ("ai_platform_chat_host", "ai_platform_ib2b_host"):
        ai_platform_deployment(monkeypatch, **{missing: ""})
        assert sdk.get("/app").json()["image_analysis"] == off, missing
    ai_platform_deployment(monkeypatch)
    assert sdk.get("/app").json()["image_analysis"]["enabled"] is True

    # The owner's connector can run, but the owner did not turn it on for this app: the model is not told either.
    sdk.change(connectors_enabled=["jira", "github"])
    assert sdk.get("/app").json()["image_analysis"] == off
    assert sdk.executions() == [], "reading the app is not an execution"


def test_image_analysis_follows_the_owners_default_connectors(sdk, monkeypatch):
    # The real lookup: the owner never customized anything and reads images with the account their group shares.
    from app.services.runtime_profile_service import RuntimeProfileService

    monkeypatch.undo()
    enable_apps(monkeypatch)
    ai_platform_deployment(monkeypatch)
    sdk.change(connectors_enabled=["image_analysis"])
    assert sdk.get("/app").json()["image_analysis"] == {"enabled": False, "model": None, **IMAGE_LIMITS}

    profile = RuntimeProfileService(sdk.db).get_or_create_for_user(sdk.owner)
    seen = []

    def effective(self, row):
        seen.append(row.owner_user_id)
        return {"image_analysis": image_connector(model="gpt-5.6-terra")}

    monkeypatch.setattr(RuntimeProfileService, "effective_config_for", effective)

    assert sdk.get("/app").json()["image_analysis"] == {"enabled": True, "model": "gpt-5.6-terra", **IMAGE_LIMITS}
    assert seen == [profile.owner_user_id] == [sdk.owner.id], "the owner's effective settings, not the stored row"


def test_an_image_call_answers_whole_and_is_recorded_as_a_model_call(sdk, vision):
    response = vision.inspect({"system": "Answer in one sentence.", "max_output_tokens": 600, "reasoning_effort": "medium"})

    assert response.status_code == 200
    assert response.json() == {
        "text": "The field Email is marked in red.",
        "model": "gpt-5.6-sol",
        "finish_reason": "stop",
        "usage": {"input_tokens": 1830, "output_tokens": 210, "credits": None},
    }
    assert response.headers["content-type"].startswith("application/json")
    (row,) = sdk.executions()
    # A model call of the app (so the daily budget and the usage pages count it), named by its connector.
    assert (row.app_id, row.owner_user_id, row.kind, row.connector_type, row.model) == (sdk.app.id, sdk.owner.id, "llm", "image_analysis", "gpt-5.6-sol")
    assert (row.input_tokens, row.output_tokens, row.credits) == (1830, 210, None)
    assert (row.status, row.error_code, row.source, row.visitor_user_id) == ("succeeded", None, "cluster", None)
    assert row.duration_ms is not None and row.duration_ms >= 0
    assert sdk.get("/app").json()["budget"]["used_tokens"] == 2040
    assert IA_PASSWORD not in response.text and IA_JWT not in response.text


def test_the_request_ai_platform_receives_for_two_images(sdk, vision):
    response = vision.inspect(
        {
            "images": [picture(PNG, "image/png"), picture(JPEG, "image/jpeg")],
            "prompt": "What differs between the two screenshots?",
            "system": "Answer in one sentence.",
            "max_output_tokens": 600,
            "reasoning_effort": "medium",
        }
    )

    assert response.status_code == 200
    exchange, call = vision.requests
    assert (exchange.method, str(exchange.url)) == ("POST", "https://sts.example.test/sts/v1/token")
    assert json.loads(exchange.content)["input_token_state"] == {"token_type": "CREDENTIAL", "username": "vision-svc", "password": IA_PASSWORD}

    # chat/completions, never the Responses endpoint; the JWT in the trust header only.
    assert (call.method, str(call.url)) == ("POST", AI_CHAT)
    assert call.headers["x-test-trust-token"] == IA_JWT and "authorization" not in call.headers
    assert (call.headers["content-type"], call.headers["accept"]) == ("application/json", "application/json")
    assert call.headers["x-correlation-id"].startswith("EFPT-") and call.headers["x-correlation-id"] == call.headers["x-usersession-id"]
    assert json.loads(call.content) == {
        # The connector's model: the owner's chat model (Copilot, "auto") has no say.
        "model": "gpt-5.6-sol",
        "messages": [
            {"role": "system", "content": "Answer in one sentence."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What differs between the two screenshots?"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64(PNG)}},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64(JPEG)}},
                ],
            },
        ],
        "stream": False,
        "reasoning_effort": "medium",
        "user": "uc-vision",
        "max_completion_tokens": 600,
    }
    # The app's token and the owner's other credentials go nowhere.
    for request in vision.requests:
        assert sdk.token not in request.content.decode("utf-8") and sdk.token not in str(request.headers)
        assert GITHUB_TOKEN not in request.content.decode("utf-8")


def test_what_the_sdk_sends_is_taken_as_it_is(sdk, vision):
    # The SDK leaves out what a call did not name and always sends system (sdk/efp_app/images.py).
    assert sdk.post("/images/inspect", {"images": [picture()], "prompt": QUESTION, "system": None}).status_code == 200

    sent = json.loads(vision.calls()[-1].content)
    assert [message["role"] for message in sent["messages"]] == ["user"]
    assert (sent["max_completion_tokens"], sent["reasoning_effort"]) == (4096, "low")

    # Null means the same as left out, and what the contract does not name is ignored.
    explicit = {"images": [picture()], "prompt": QUESTION, "system": None, "max_output_tokens": None, "reasoning_effort": None}
    assert sdk.post("/images/inspect", {**explicit, "model": "gpt-6-sol", "stream": True, "temperature": 2}).status_code == 200
    assert json.loads(vision.calls()[-1].content) == sent


def test_the_jwt_of_the_connectors_account_is_reused_and_renewed_when_refused(sdk, vision):
    assert vision.inspect().status_code == 200
    assert vision.inspect().status_code == 200
    assert len(vision.requests) == 3, "one exchange for two calls"

    # AI Platform no longer takes that JWT: exchanged again, once, and the call repeated.
    issued = ["second.jwt.value"]
    vision.exchange = lambda request: httpx.Response(200, json={"issued_token": issued[0]})
    vision.answer = lambda request: (
        httpx.Response(200, json=vision_answer("second try")) if request.headers["x-test-trust-token"] == issued[0] else httpx.Response(401, json={"message": "jwt expired"})
    )
    renewed = vision.inspect()
    assert renewed.status_code == 200 and renewed.json()["text"] == "second try"
    assert [request.url.host for request in vision.requests[3:]] == ["ai.example.test", "sts.example.test", "ai.example.test"]
    assert [row.status for row in sdk.executions()] == ["succeeded"] * 3


def test_image_analysis_must_be_turned_on_for_the_app(sdk, monkeypatch):
    # The owner's connector can run; the owner did not turn it on for this app.
    ai_platform_deployment(monkeypatch)
    sdk.config["image_analysis"] = image_connector()
    target = Vision()
    monkeypatch.setattr(sdk.api, "image_client", target.client)

    response = sdk.post("/images/inspect", {"images": [picture()], "prompt": QUESTION})
    assert response.status_code == 403
    assert error_of(response)["code"] == "connector_disabled"

    # The body is not looked at for it: whatever was sent, the answer is the same.
    for content in (b"{not json", b"[]", b""):
        refused = sdk.client.post(PREFIX + "/images/inspect", content=content, headers={**sdk.auth(), "Content-Type": "application/json"})
        assert refused.status_code == 403 and error_of(refused)["code"] == "connector_disabled"
    # The model provider being on for the app, and a connector being on, do not turn it on.
    assert sdk.reload().llm_enabled is True and sdk.reload().connectors_enabled == ["jira", "github"]
    assert target.requests == [] and sdk.executions() == []


def test_image_analysis_does_not_need_the_model_provider(sdk, vision):
    # A switch of its own: the model provider is off for the app, and the owner never connected one.
    sdk.change(llm_enabled=False)
    sdk.config["llm"] = {"provider": "github_copilot", "model": "auto"}

    assert error_of(sdk.complete())["code"] == "llm_disabled"
    response = vision.inspect()
    assert response.status_code == 200 and response.json()["model"] == "gpt-5.6-sol"

    # Whatever goes wrong with an image call, llm_disabled is never its answer.
    sdk.config["image_analysis"] = image_connector(enabled=False)
    assert error_of(vision.inspect())["code"] == "connector_not_configured"
    sdk.change(connectors_enabled=["jira"])
    assert error_of(vision.inspect())["code"] == "connector_disabled"
    assert error_of(vision.inspect({"prompt": ""}))["code"] == "connector_disabled"


NOT_SET_UP = {
    "no connector": (None, {}),
    "switched off": (image_connector(enabled=False), {}),
    "no account": (image_connector(ai_platform={}), {}),
    "no usercase": (image_connector(ai_platform={"auth": {"username": "vision-svc", "password": IA_PASSWORD}}), {}),
    "no AI Platform address": (image_connector(), {"ai_platform_chat_host": ""}),
    "no iB2B address": (image_connector(), {"ai_platform_ib2b_host": ""}),
    "no iB2B path": (image_connector(), {"ai_platform_ib2b_uri": ""}),
}


@pytest.mark.parametrize(("connector", "deployment"), list(NOT_SET_UP.values()), ids=list(NOT_SET_UP))
def test_an_owner_whose_connector_cannot_run_is_connector_not_configured(sdk, vision, monkeypatch, connector, deployment):
    sdk.config["image_analysis"] = connector
    ai_platform_deployment(monkeypatch, **deployment)

    response = vision.inspect()

    assert response.status_code == 424
    assert error_of(response)["code"] == "connector_not_configured"
    assert "Image analysis connector" in error_of(response)["message"] and IA_PASSWORD not in response.text
    assert vision.requests == [], "nothing is sent"
    # The app was allowed to make the call, so it is written down; it used nothing.
    (row,) = sdk.executions()
    assert (row.kind, row.connector_type, row.status, row.error_code) == ("llm", "image_analysis", "failed", "connector_not_configured")
    assert (row.input_tokens, row.output_tokens, row.credits) == (0, 0, None)
    # GET /app goes by the rule of the call: an app is never told that image analysis is on while every call
    # answers connector_not_configured. So it names no model here, and neither does the row.
    told = sdk.get("/app").json()["image_analysis"]
    assert row.model is None
    assert (told["enabled"], told["model"]) == (False, None)


def test_the_model_providers_account_is_never_used_for_images(sdk, vision):
    # The owner chats on AI Platform with an account of their own and has no Image analysis connector.
    sdk.config["llm"] = {
        "provider": "ai_platform",
        "model": "gpt-5.4",
        "ai_platform": {"auth": {"username": "alice", "password": "pw-chat-s3cret", "usercase": "uc-chat"}},
    }
    sdk.config.pop("image_analysis")

    response = vision.inspect()

    assert response.status_code == 424 and error_of(response)["code"] == "connector_not_configured"
    assert vision.requests == []


BAD_IMAGE_CALLS = {
    "no images": {"images": None},
    "an empty list": {"images": []},
    "five images": {"images": [picture()] * 5},
    "an image that is not an object": {"images": ["iVBORw0KGgo="]},
    "a media type the model is not given": {"images": [picture(PNG, "image/bmp")]},
    "no media type": {"images": [{"data_base64": b64(PNG)}]},
    "an image without bytes": {"images": [{"media_type": "image/png", "data_base64": ""}]},
    "base64 of the URL alphabet": {"images": [{"media_type": "image/png", "data_base64": base64.urlsafe_b64encode(b"\x89PNG\r\n\x1a\n\xfb\xff\xfe" + bytes(16)).decode("ascii")}]},
    "base64 without its padding": {"images": [{"media_type": "image/png", "data_base64": b64(PNG).rstrip("=")}]},
    "data that is not text": {"images": [{"media_type": "image/png", "data_base64": 7}]},
    "bytes that are no image": {"images": [picture(b"plain text, and no image at all")]},
    "a PNG given as a JPEG": {"images": [picture(PNG, "image/jpeg")]},
    "one image over five mebibytes": {"images": [picture(PNG + bytes(5 * 1024 * 1024))]},
    "a blank prompt": {"prompt": "   "},
    "a prompt that is not text": {"prompt": 7},
    "a system text that is not text": {"system": ["be brief"]},
    "more than 200,000 characters": {"prompt": "p" * 150_000, "system": "s" * 50_001},
    "an answer of no tokens": {"max_output_tokens": 0},
    "an answer over sixteen thousand tokens": {"max_output_tokens": 16_001},
    "tokens that are not whole": {"max_output_tokens": 1.5},
    "tokens that are true": {"max_output_tokens": True},
    "a level above high": {"reasoning_effort": "xhigh"},
    "a level that is a number": {"reasoning_effort": 3},
}


@pytest.mark.parametrize("fields", list(BAD_IMAGE_CALLS.values()), ids=list(BAD_IMAGE_CALLS))
def test_an_image_call_the_contract_does_not_allow_is_refused_before_anything_is_spent(sdk, vision, monkeypatch, fields):
    at(monkeypatch, NOON)
    # Even with nothing left of the day's budget: what is wrong with the request is what the app is told.
    sdk.change(daily_tokens=0)

    response = vision.inspect(fields)

    assert response.status_code == 400
    assert error_of(response)["code"] == "invalid_request"
    for item in fields.get("images") or []:
        data = item.get("data_base64") if isinstance(item, dict) else None
        if isinstance(data, str) and len(data) > 16:
            assert data[:16] not in response.text, "an image is never quoted back"
    assert vision.requests == [], "nothing is exchanged or sent"
    assert sdk.executions() == [], "nothing is written down"
    assert app_budget.in_flight.calls(sdk.app.id) == 0, "and nothing is counted"


@pytest.mark.parametrize("content", [b"{not json", b"[]", b'"text"', b"12", b"null", b"", b"images=1&prompt=x", b"\xff\xfe"])
def test_an_image_body_that_is_not_a_json_object_is_invalid_request(sdk, vision, content):
    response = sdk.client.post(PREFIX + "/images/inspect", content=content, headers={**sdk.auth(), "Content-Type": "application/json"})

    assert response.status_code == 400
    assert error_of(response)["code"] == "invalid_request"
    assert vision.requests == [] and sdk.executions() == []


async def _post_in_pieces(asgi, path: str, headers: dict, pieces: list, *, gone_after: int | None = None) -> tuple[int, dict, int]:
    """POST a body the way a server hands it over, piece by piece; ``(status, answer, pieces the app took)``.

    With ``gone_after`` the caller disconnects once that many pieces were taken, the rest unsent.
    """
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": path, "raw_path": path.encode("ascii"), "query_string": b"", "root_path": "",
        "headers": [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in headers.items()],
        "client": ("127.0.0.1", 50000), "server": ("testserver", 80),
    }
    taken = {"pieces": 0}
    sent: list[dict] = []
    answered = asyncio.Event()

    async def receive():
        if gone_after is not None and taken["pieces"] >= gone_after:
            return {"type": "http.disconnect"}
        if taken["pieces"] < len(pieces):
            taken["pieces"] += 1
            return {"type": "http.request", "body": pieces[taken["pieces"] - 1], "more_body": taken["pieces"] < len(pieces)}
        await answered.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body"):
            answered.set()

    await asyncio.wait_for(asgi(scope, receive, send), timeout=30)
    status = next(message["status"] for message in sent if message["type"] == "http.response.start")
    answer = json.loads(b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body"))
    return status, answer, taken["pieces"]


def test_a_body_larger_than_an_image_call_can_be_is_refused_and_not_read(sdk, vision, monkeypatch):
    limit = 64 * 1024
    monkeypatch.setattr(image_analysis, "MAX_BODY_BYTES", limit)
    path = PREFIX + "/images/inspect"
    json_headers = {**sdk.auth(), "Content-Type": "application/json"}
    kilobyte = b"x" * 1024

    # It says how large it is: refused without one byte of it being taken.
    status, answer, taken = asyncio.run(_post_in_pieces(sdk.asgi, path, {**json_headers, "Content-Length": str(limit + 1)}, [kilobyte] * 200))
    assert (status, answer["error"]["code"], taken) == (400, "invalid_request", 0)
    assert "larger than an image call can be" in answer["error"]["message"]

    # It does not say (a chunked upload): refused at the piece that crosses the bound, the rest never read.
    status, answer, taken = asyncio.run(_post_in_pieces(sdk.asgi, path, {**json_headers, "Transfer-Encoding": "chunked"}, [kilobyte] * 200))
    assert (status, answer["error"]["code"]) == (400, "invalid_request")
    assert "larger than an image call can be" in answer["error"]["message"]
    assert taken == 65, "64 pieces fit; the 65th is the last one looked at"

    # A length nobody can read is no length: the same bound holds while reading.
    status, answer, taken = asyncio.run(_post_in_pieces(sdk.asgi, path, {**json_headers, "Content-Length": "many"}, [kilobyte] * 200))
    assert (status, taken) == (400, 65)

    # Exactly the bound is read whole, and is then judged for what it holds.
    status, answer, taken = asyncio.run(_post_in_pieces(sdk.asgi, path, {**json_headers, "Content-Length": str(limit)}, [kilobyte] * 64))
    assert (status, answer["error"]["code"], taken) == (400, "invalid_request", 64)
    assert "larger than" not in answer["error"]["message"]

    # A call that fits is answered, in as many pieces as it comes.
    raw = json.dumps({"images": [picture()], "prompt": QUESTION}).encode("utf-8")
    status, answer, taken = asyncio.run(_post_in_pieces(sdk.asgi, path, json_headers, [raw[:10], raw[10:50], raw[50:]]))
    assert (status, answer["text"], taken) == (200, "The field Email is marked in red.", 3)

    assert len(vision.calls()) == 1 and len(sdk.executions()) == 1
    assert app_budget.in_flight.calls(sdk.app.id) == 0


def test_the_bound_on_the_body_is_forty_mebibytes(sdk, vision):
    assert image_analysis.MAX_BODY_BYTES == 40 * 1024 * 1024
    huge = {**sdk.auth(), "Content-Type": "application/json", "Content-Length": str(40 * 1024 * 1024 + 1)}

    status, answer, taken = asyncio.run(_post_in_pieces(sdk.asgi, PREFIX + "/images/inspect", huge, [b"{}"]))

    assert (status, answer["error"]["code"], taken) == (400, "invalid_request", 0)


def test_a_caller_that_goes_away_in_the_middle_of_its_upload_is_no_failure_of_the_portal(sdk, vision, caplog):
    raw = json.dumps({"images": [picture()], "prompt": QUESTION}).encode("utf-8")
    headers = {**sdk.auth(), "Content-Type": "application/json", "Content-Length": str(len(raw))}

    with caplog.at_level(logging.WARNING):
        status, answer, taken = asyncio.run(_post_in_pieces(sdk.asgi, PREFIX + "/images/inspect", headers, [raw[:20], raw[20:]], gone_after=1))

    # Nobody reads the answer; what matters is that nothing was counted, sent or logged as an error.
    assert (status, answer["error"]["code"], taken) == (400, "invalid_request", 1)
    assert answer["error"]["message"] == "The request body did not arrive whole."
    assert [record for record in caplog.records if record.levelno >= logging.WARNING and record.name.startswith("app.")] == []
    assert vision.requests == [] and sdk.executions() == []
    assert app_budget.in_flight.calls(sdk.app.id) == 0


def test_four_full_images_are_read_and_sent_whole(sdk, vision):
    # About 28 MB of JSON: the largest call the contract allows.
    full = PNG + bytes(5 * 1024 * 1024 - len(PNG))
    images = [picture(full)] * 4

    response = vision.inspect({"images": images, "prompt": "What changes from one to the next?"})

    assert response.status_code == 200
    sent = json.loads(vision.calls()[-1].content)
    parts = sent["messages"][0]["content"]
    assert [part["type"] for part in parts] == ["text", "image_url", "image_url", "image_url", "image_url"]
    assert all(part["image_url"]["url"] == "data:image/png;base64," + images[0]["data_base64"] for part in parts[1:])
    (row,) = sdk.executions()
    assert (row.status, row.input_tokens, row.output_tokens) == ("succeeded", 1830, 210)


def test_an_answer_without_usage_is_counted_as_what_the_call_was_reserved_as(sdk, vision):
    vision.answer = lambda request: httpx.Response(200, json=vision_answer("A dialog with two buttons.", usage=False))

    response = vision.inspect({"images": [picture(), picture(JPEG, "image/jpeg")], "prompt": "p" * 10, "system": "s" * 9})

    # The answer says what AI Platform reported: nothing.
    assert response.status_code == 200
    assert response.json()["usage"] == {"input_tokens": 0, "output_tokens": 0, "credits": None}
    # The row does not cost the budget nothing: a quarter of the 19 characters, 1,500 tokens an image, the answer's text.
    (row,) = sdk.executions()
    assert (row.input_tokens, row.output_tokens, row.credits) == (5 + 3000, 7, None)
    assert sdk.get("/app").json()["budget"]["used_tokens"] == 3012


@pytest.mark.parametrize(
    ("exchange", "answer", "status", "code", "retry_after"),
    [
        (httpx.Response(401, json={"message": "invalid credentials"}), None, 424, "provider_auth_failed", None),
        (httpx.Response(403, text="forbidden"), None, 424, "provider_auth_failed", None),
        (None, httpx.Response(401, json={"message": "not entitled"}), 424, "provider_auth_failed", None),
        (None, httpx.Response(500, json={"error": {"message": "boom"}}), 502, "provider_error", None),
        (None, httpx.Response(400, json={"error": {"message": "The requested model is not available."}}), 502, "provider_error", None),
        (httpx.Response(503, text="maintenance"), None, 502, "provider_error", None),
        (None, httpx.Response(429, headers={"retry-after": "30"}, json={"error": {"message": "slow down"}}), 429, "rate_limited", "30"),
        (None, httpx.Response(504, text=""), 504, "upstream_timeout", None),
    ],
    ids=["ib2b-401", "ib2b-403", "jwt-refused", "500", "model-not-served", "ib2b-503", "429", "504"],
)
def test_a_failed_image_call_keeps_its_status_and_is_recorded(sdk, vision, exchange, answer, status, code, retry_after):
    if exchange is not None:
        vision.exchange = lambda request: httpx.Response(exchange.status_code, headers=exchange.headers, content=exchange.content)
    if answer is not None:
        vision.answer = lambda request: httpx.Response(answer.status_code, headers=answer.headers, content=answer.content)

    response = vision.inspect()

    assert response.status_code == status
    assert error_of(response)["code"] == code
    assert response.headers.get("retry-after") == retry_after
    assert IA_PASSWORD not in response.text and IA_JWT not in response.text and b64(PNG) not in response.text
    (row,) = sdk.executions()
    assert (row.kind, row.connector_type, row.status, row.error_code, row.model) == ("llm", "image_analysis", "failed", code, "gpt-5.6-sol")
    assert (row.input_tokens, row.output_tokens, row.credits) == (0, 0, None)


def test_an_account_that_is_refused_says_which_connector_to_correct(sdk, vision):
    vision.exchange = lambda request: httpx.Response(401, json={"message": "invalid credentials"})

    message = error_of(vision.inspect())["message"]

    assert "Image analysis connector" in message and "Model provider" not in message
    assert vision.calls() == [], "no image is sent with an account that was refused"


def test_an_unexpected_failure_of_an_image_call_is_500_and_recorded(sdk, vision, monkeypatch):
    async def broken(**kwargs):
        raise RuntimeError("secret detail: " + IA_PASSWORD)

    monkeypatch.setattr(sdk.api.image_analysis, "inspect", broken)

    response = vision.inspect()

    assert response.status_code == 500 and error_of(response)["code"] == "internal_error"
    assert "secret detail" not in response.text and IA_PASSWORD not in response.text
    assert [(row.kind, row.connector_type, row.status, row.error_code) for row in sdk.executions()] == [("llm", "image_analysis", "failed", "internal_error")]


def test_an_image_call_cancelled_under_way_is_recorded_and_no_longer_counted(sdk, vision, monkeypatch):
    from starlette.requests import Request

    counted = []

    async def cancelled(**kwargs):
        counted.append(app_budget.in_flight.tokens(sdk.app.id))
        raise asyncio.CancelledError()

    monkeypatch.setattr(sdk.api.image_analysis, "inspect", cancelled)
    raw = json.dumps({"images": [picture()], "prompt": QUESTION}).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    scope = {"type": "http", "method": "POST", "path": PREFIX + "/images/inspect", "query_string": b"", "headers": [(b"content-length", str(len(raw)).encode("ascii"))]}
    call = sdk.api.SdkCall(app=sdk.reload(), app_id=sdk.app.id, owner_user_id=sdk.owner.id, source="cluster", visitor_user_id=sdk.visitor.id)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(sdk.api.images_inspect(Request(scope, receive), call=call, db=sdk.factory()))

    assert counted == [ONE_IMAGE_UNDER_WAY]
    (row,) = sdk.executions()
    assert (row.kind, row.connector_type, row.status, row.error_code, row.visitor_user_id) == ("llm", "image_analysis", "cancelled", "client_closed", sdk.visitor.id)
    assert app_budget.in_flight.calls(sdk.app.id) == 0


def test_no_pooled_connection_is_held_while_an_image_arrives_or_ai_platform_is_waited_for(sdk, pooled, monkeypatch):
    ai_platform_deployment(monkeypatch)
    sdk.config["image_analysis"] = image_connector()
    with Session(bind=pooled.engine) as db:
        db.get(App, pooled.app_id).connectors_enabled = ["jira", "image_analysis"]
        db.commit()
    seen: dict[str, tuple] = {}

    async def look(name: str) -> None:
        # How many connections are out, and whether anybody else can have one.
        seen[name] = (pooled.engine.pool.checkedout(), await asyncio.to_thread(pooled.usable_by_others))

    async def inspect(**kwargs):
        await look("AI Platform")
        return LlmResult("ok", "gpt-5.6-sol", "stop", LlmUsage(1830, 210, None))

    monkeypatch.setattr(sdk.api.image_analysis, "inspect", inspect)
    raw = json.dumps({"images": [picture()], "prompt": QUESTION}).encode("utf-8")
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": PREFIX + "/images/inspect", "raw_path": (PREFIX + "/images/inspect").encode("ascii"), "query_string": b"", "root_path": "",
        "headers": [(b"authorization", f"Bearer {sdk.token}".encode("ascii")), (b"content-type", b"application/json")],
        "client": ("127.0.0.1", 50000), "server": ("testserver", 80),
    }
    pieces = [raw[:30], raw[30:]]
    sent: list[dict] = []

    async def drive():
        answered = asyncio.Event()

        async def receive():
            if pieces:
                # The caller is slow: each piece of its upload is a wait of the Portal's.
                await look(f"upload, {len(pieces)} to come")
                return {"type": "http.request", "body": pieces.pop(0), "more_body": bool(pieces)}
            await answered.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)
            if message["type"] == "http.response.body" and not message.get("more_body"):
                answered.set()

        await asyncio.wait_for(sdk.asgi(scope, receive, send), timeout=30)

    asyncio.run(drive())

    assert next(message["status"] for message in sent if message["type"] == "http.response.start") == 200
    assert seen == {"upload, 2 to come": (0, True), "upload, 1 to come": (0, True), "AI Platform": (0, True)}
    assert pooled.rows() == [("llm", "succeeded")]
    assert app_budget.in_flight.calls(pooled.app_id) == 0


def test_an_app_removed_while_its_image_arrived_is_no_longer_answered(sdk, vision, monkeypatch):
    # The session the app was read through is given back before the body is read; afterwards it is read again.
    real = sdk.api._image_request

    async def remove_meanwhile(request):
        asked = await real(request)
        sdk.db.query(AppExecution).delete()
        sdk.db.delete(sdk.reload())
        sdk.db.commit()
        return asked

    monkeypatch.setattr(sdk.api, "_image_request", remove_meanwhile)

    response = vision.inspect()

    assert response.status_code == 401 and error_of(response)["code"] == "invalid_token"
    assert vision.requests == [] and sdk.db.query(AppExecution).count() == 0


def test_the_visitor_header_names_who_caused_an_image_call(sdk, vision):
    header = sign_visitor(sdk.app, sdk.visitor)

    assert vision.inspect(**{"X-EFP-Visitor": header, "X-Forwarded-For": "203.0.113.9"}).status_code == 200
    assert vision.inspect(**{"X-EFP-Visitor": header[:-2]}).status_code == 200, "a header that does not verify never fails the call"
    assert vision.inspect().status_code == 200

    assert [(row.visitor_user_id, row.source) for row in sdk.executions()] == [(sdk.visitor.id, "external"), (None, "cluster"), (None, "cluster")]
    # The header is the Portal's to read: AI Platform is not told who the visitor was.
    assert all("x-efp-visitor" not in request.headers for request in vision.requests)


def test_no_image_data_and_no_secret_reaches_a_log(sdk, vision, caplog):
    marker = b"\x89PNG\r\n\x1a\n" + b"confidential-screenshot-bytes" * 8
    encoded = b64(marker)

    with caplog.at_level(logging.DEBUG):
        assert vision.inspect({"images": [picture(marker)], "prompt": "a confidential question"}).status_code == 200
        vision.answer = lambda request: httpx.Response(400, json={"error": {"message": "cannot decode " + encoded}})
        assert vision.inspect({"images": [picture(marker)], "prompt": "a confidential question"}).status_code == 502
        vision.exchange = lambda request: httpx.Response(401, json={"message": "bad password " + IA_PASSWORD})
        llm_ai_platform.reset_token_cache()
        assert vision.inspect({"images": [picture(marker)]}).status_code == 424
        assert vision.inspect({"images": [picture(marker, "image/gif")]}).status_code == 400

    assert encoded not in caplog.text and encoded[:24] not in caplog.text
    assert "confidential" not in caplog.text
    assert IA_PASSWORD not in caplog.text and IA_JWT not in caplog.text
    # What is logged: that the call happened, how many images, how large, how it ended.
    lines = [record.getMessage() for record in caplog.records if record.getMessage().startswith("app sdk image call")]
    size = len(marker)
    assert lines == [
        f"app sdk image call app_id={sdk.app.id} images=1 image_bytes={size} status=succeeded",
        f"app sdk image call app_id={sdk.app.id} images=1 image_bytes={size} status=provider_error",
        f"app sdk image call app_id={sdk.app.id} images=1 image_bytes={size} status=provider_auth_failed",
    ]


def test_image_analysis_is_never_a_connector_an_app_can_call(sdk, vision, upstream):
    # Turned on for the app, and still no type of 9.3: answered as any unknown type is.
    unknown = sdk.post("/connectors/aws/request", {"method": "GET", "path": "/"})
    for kind in ("image_analysis", "IMAGE_ANALYSIS", "image-analysis"):
        response = sdk.post(f"/connectors/{kind}/request", {"method": "POST", "path": "/v1/api/v1/chat/completions", "json": {"model": "gpt-5.4"}})
        assert response.status_code == 400 and error_of(response)["code"] == "invalid_request"
        assert error_of(response) == error_of(unknown)
    assert "image_analysis" not in error_of(unknown)["message"]

    # Not even when a row was written by hand: nothing is proxied and nothing is writable.
    sdk.change(connectors_enabled=["image_analysis"], connectors_write=["image_analysis"])
    assert sdk.post("/connectors/image_analysis/request", {"method": "GET", "path": "/"}).status_code == 400
    assert sdk.get("/connectors").json() == {"connectors": []}
    assert sdk.get("/app").json()["connectors"] == []
    assert upstream.requests == [] and vision.requests == [] and sdk.executions() == []


# --- image calls and the daily budget ------------------------------------------------------------


def _image_in_the_background(sdk, body: dict):
    """Start an image call on a thread of its own; ``(thread, responses)``."""
    responses: list = []
    client = TestClient(sdk.asgi)

    def call():
        responses.append(client.post(PREFIX + "/images/inspect", json={"images": [picture()], "prompt": QUESTION, **body}, headers=sdk.auth()))

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    return thread, responses


def test_an_image_call_under_way_is_counted_as_its_text_its_images_and_its_answer(sdk, vision, monkeypatch):
    at(monkeypatch, NOON)
    sdk.change(daily_tokens=5000)
    provider = _HeldProvider(LlmResult("ok", "gpt-5.6-sol", "stop", LlmUsage(1830, 210, None)))
    monkeypatch.setattr(sdk.api.image_analysis, "inspect", provider.complete)

    # Nothing is spent yet. The call has two images, 19 characters of text, and may get 600 tokens back.
    body = {"images": [picture(), picture(JPEG, "image/jpeg")], "prompt": "p" * 10, "system": "s" * 9, "max_output_tokens": 600}
    thread, responses = _image_in_the_background(sdk, body)
    assert provider.entered.acquire(timeout=30)
    assert app_budget.in_flight.calls(sdk.app.id) == 1
    assert app_budget.in_flight.tokens(sdk.app.id) == 5 + 2 * 1500 + 600 == 3605

    # 3605 of 5000 are spoken for: another call is admitted, whatever it comes to itself.
    second, second_responses = _image_in_the_background(sdk, {})
    assert provider.entered.acquire(timeout=30)
    assert app_budget.in_flight.tokens(sdk.app.id) == 3605 + ONE_IMAGE_UNDER_WAY == 9208

    # Now nothing is left, for an image call and for a model call alike: they share the budget.
    refused = vision.inspect()
    assert refused.status_code == 429
    assert error_of(refused)["code"] == "rate_limited", "not spent: under way, and worth another try soon"
    assert refused.headers["retry-after"] == str(app_budget.IN_FLIGHT_RETRY_SECONDS)
    model_call = sdk.complete()
    assert model_call.status_code == 429 and error_of(model_call)["code"] == "rate_limited"
    assert len(provider.calls) == 2 and sdk.gateway.calls == [], "neither provider is asked"
    assert sdk.executions() == [], "a refused call is not recorded"
    assert app_budget.in_flight.calls(sdk.app.id) == 2, "and is not counted either"

    for answered in (1, 2):
        provider.let_one_answer()
        assert _until(lambda: len(responses) + len(second_responses) == answered)
    thread.join(timeout=30)
    second.join(timeout=30)
    assert [response.status_code for response in responses + second_responses] == [200, 200]

    # Each came to 2040 tokens: once the rows are written they are counted as that.
    assert app_budget.in_flight.calls(sdk.app.id) == 0
    assert sdk.get("/app").json()["budget"]["used_tokens"] == 4080
    provider.hold = False
    assert vision.inspect().status_code == 200, "4080 of 5000: one more call"
    assert error_of(vision.inspect())["code"] == "budget_exceeded"


def test_a_model_call_under_way_holds_an_image_call_back(sdk, vision, monkeypatch):
    at(monkeypatch, NOON)
    sdk.change(daily_tokens=1000)
    provider = _HeldProvider(LlmResult("ok", "gpt-6-sol", "stop", LlmUsage(10, 20, None)))
    monkeypatch.setattr(sdk.api.llm_gateway, "complete", provider.complete)

    thread, responses = _in_the_background(sdk, {"max_output_tokens": 1000})
    assert provider.entered.acquire(timeout=30)

    refused = vision.inspect()
    assert refused.status_code == 429 and error_of(refused)["code"] == "rate_limited"
    assert vision.requests == []

    provider.let_one_answer()
    thread.join(timeout=30)
    assert [response.status_code for response in responses] == [200]
    assert vision.inspect().status_code == 200


def test_image_calls_are_refused_once_the_days_budget_is_used(sdk, vision, monkeypatch):
    at(monkeypatch, datetime(2026, 11, 15, 23, 0, 0))
    sdk.change(daily_tokens=2000)

    # Nothing used: the call goes through, and its 2040 tokens take the app over the limit.
    assert vision.inspect().status_code == 200
    assert sdk.get("/app").json()["budget"] == {"daily_credits": 50.0, "used_credits": 0.0, "daily_tokens": 2000, "used_tokens": 2040}

    refused = vision.inspect()
    assert refused.status_code == 429
    assert error_of(refused)["code"] == "budget_exceeded" and "tokens" in error_of(refused)["message"]
    # An hour to the next UTC midnight.
    assert refused.headers["retry-after"] == "3600"
    # The model calls of the app are refused by the same figure: one budget.
    assert error_of(sdk.complete())["code"] == "budget_exceeded"
    assert len(vision.calls()) == 1 and sdk.gateway.calls == [], "no provider is asked"
    assert len(sdk.executions()) == 1, "a refused call is not recorded"

    # Credits a model call used count against image calls too, and a limit of zero refuses every call.
    sdk.change(daily_tokens=None, daily_credits=1.0)
    spend(sdk, credits=1.0, created_at=datetime(2026, 11, 15, 9, 0, 0))
    spent = vision.inspect()
    assert error_of(spent)["code"] == "budget_exceeded" and "credits" in error_of(spent)["message"]
    sdk.change(daily_credits=None, daily_tokens=0)
    assert error_of(vision.inspect())["code"] == "budget_exceeded"

    # The next day starts again.
    at(monkeypatch, datetime(2026, 11, 16, 0, 0, 0))
    sdk.change(daily_tokens=2000)
    assert vision.inspect().status_code == 200


def test_every_way_an_image_call_ends_gives_back_what_it_was_counted_as(sdk, vision, monkeypatch):
    at(monkeypatch, NOON)
    counted = lambda: app_budget.in_flight.calls(sdk.app.id)  # noqa: E731

    assert vision.inspect({"max_output_tokens": 500}).status_code == 200
    # Refused before it was counted, and at each gate after.
    assert vision.inspect({"prompt": ""}).status_code == 400
    sdk.config["image_analysis"] = image_connector(enabled=False)
    assert vision.inspect().status_code == 424
    sdk.config["image_analysis"] = image_connector()
    sdk.change(daily_tokens=0)
    assert error_of(vision.inspect())["code"] == "budget_exceeded"
    sdk.change(daily_tokens=None, connectors_enabled=[])
    assert error_of(vision.inspect())["code"] == "connector_disabled"
    sdk.change(connectors_enabled=["image_analysis"])
    assert counted() == 0

    # AI Platform failed, and so did the Portal.
    vision.answer = lambda request: httpx.Response(500, text="boom")
    assert vision.inspect().status_code == 502

    async def broken(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(sdk.api.image_analysis, "inspect", broken)
    assert vision.inspect().status_code == 500
    assert counted() == 0
    assert [row.status for row in sdk.executions()] == ["succeeded", "failed", "failed", "failed"]
