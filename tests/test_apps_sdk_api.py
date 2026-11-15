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
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

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
from app.db import get_db
from app.security.session_cookie import session_cookie_name
from app.models import App, AppExecution, RuntimeProfile
from app.services import model_catalog_service
from app.services.app_sdk import llm_copilot, llm_wire
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
    try:
        yield h
    finally:
        app.dependency_overrides.clear()
        db.close()
        reset_apps_state()
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
    ("GET", "/nothing-here"),
]


@pytest.mark.parametrize("method, path", ENDPOINTS)
def test_everything_answers_404_while_apps_are_off(sdk, monkeypatch, method, path):
    monkeypatch.setattr(get_settings(), "apps_enabled", False)

    response = sdk.client.request(method, PREFIX + path, headers=sdk.auth(), json={"messages": HELLO})

    assert response.status_code == 404
    assert error_of(response)["code"] == "not_found"
    assert sdk.gateway.calls == [] and sdk.executions() == []


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        "Bearer not-a-token",
        "Bearer efpa_" + "x" * 43,
        "Basic b3duZXI6cGFzc3dvcmQ=",
        "Token efpa_" + "x" * 43,
    ],
)
@pytest.mark.parametrize("method, path", ENDPOINTS)
def test_a_call_without_the_apps_token_is_401(sdk, authorization, method, path):
    headers = {} if authorization is None else {"Authorization": authorization}

    response = sdk.client.request(method, PREFIX + path, headers=headers, json={"messages": HELLO})

    assert response.status_code == 401
    assert error_of(response)["code"] == "invalid_token"
    assert response.headers["www-authenticate"] == "Bearer"
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
        "budget": {"daily_credits": 50.0, "used_credits": 1.2, "daily_tokens": 2000000, "used_tokens": 3400},
    }
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
    # The connector's own sign-in, never the caller's.
    expected = "Basic " + base64.b64encode(f"svc:{CONNECTOR_TOKEN}".encode("utf-8")).decode("ascii")
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
