"""The LLM gateway of the app SDK (app/services/app_sdk/llm_gateway.py).

Every exchange here runs against ``httpx.MockTransport``. The request shapes
asserted are the ones the two runtimes send and the answers are the shapes
they parse (the native runtime's ``efp_runtime/llm`` and the OpenCode
adapter's loopback proxies); none of it has met a real provider.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.services import model_catalog_service
from app.services.app_sdk import _outbound, llm_ai_platform, llm_copilot, llm_gateway, llm_wire
from app.services.app_sdk.llm_gateway import LlmGatewayError, LlmResult, LlmUsage
from app.services.app_sdk.llm_wire import CHAT_BODY_KEYS, RESPONSES_BODY_KEYS
from app.services.tls_trust import trust_context

FAR_FUTURE = 4102444800  # 2100-01-01, as epoch seconds

GITHUB_TOKEN = "ghu_ownerDeviceFlowToken0123456789"
COPILOT_TOKEN = "tid=abc123;exp=4102444800;sku=business;proxy-ep=proxy.business.githubcopilot.com;st=dotcom"
COPILOT_API = "https://api.business.githubcopilot.com"

AI_PASSWORD = "pw-s3cret-value"
AI_JWT = "eyJhbGciOiJSUzI1NiJ9.first.signature"


def ai_settings(**overrides):
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
    return SimpleNamespace(**values)


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    llm_copilot.reset_token_cache()
    llm_ai_platform.reset_token_cache()
    llm_wire.reset_cap_state()
    model_catalog_service.reset_catalog_state()
    monkeypatch.setattr(llm_gateway, "get_settings", lambda: ai_settings())
    yield
    llm_copilot.reset_token_cache()
    llm_ai_platform.reset_token_cache()
    llm_wire.reset_cap_state()
    model_catalog_service.reset_catalog_state()


def copilot_config(**llm):
    return {"llm": {"provider": "github_copilot", "model": "auto", "api_key": GITHUB_TOKEN, **llm}}


def ai_config(**llm):
    section = {
        "provider": "ai_platform",
        "model": "gpt-5.4",
        "ai_platform": {"auth": {"username": "alice", "password": AI_PASSWORD, "usercase": "uc-apps"}},
    }
    section.update(llm)
    return {"llm": section}


def sse(*events, done=False) -> bytes:
    text = "".join("event: {0}\ndata: {1}\n\n".format(event.get("type", "message"), json.dumps(event)) for event in events)
    if done:
        text += "data: [DONE]\n\n"
    return text.encode("utf-8")


def sse_response(*events, done=False) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse(*events, done=done))


def responses_events(*deltas, usage=None, charge=None, final="response.completed", incomplete_reason=None):
    response = {"id": "resp_1", "status": "completed", "usage": usage or {"input_tokens": 10, "output_tokens": 20}}
    if incomplete_reason:
        response["status"] = "incomplete"
        response["incomplete_details"] = {"reason": incomplete_reason}
    last = {"type": final, "response": response}
    if charge is not None:
        last["copilot_usage"] = {"total_nano_aiu": charge}
    return [
        {"type": "response.created", "response": {"id": "resp_1"}},
        {"type": "response.output_item.added", "item": {"type": "reasoning", "id": "rs_1"}},
        *({"type": "response.output_text.delta", "delta": delta} for delta in deltas),
        last,
    ]


def chat_chunks(*deltas, finish="stop", usage=None):
    chunks = [
        {"choices": [], "prompt_filter_results": []},
        {"choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}}]},
        *({"choices": [{"index": 0, "delta": {"content": delta}}]} for delta in deltas),
        {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
    ]
    if usage is not None:
        chunks.append({"choices": [], "usage": usage})
    return chunks


class Upstream:
    """A mock transport that remembers what it was asked."""

    def __init__(self, handler):
        self.requests: list[httpx.Request] = []
        self._handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))

    def to(self, host: str) -> list[httpx.Request]:
        return [request for request in self.requests if request.url.host == host]


def copilot_upstream(answer, *, exchange=None) -> Upstream:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.github.com":
            if exchange is not None:
                return exchange(request)
            return httpx.Response(200, json={"token": COPILOT_TOKEN, "expires_at": FAR_FUTURE, "refresh_in": 1500})
        return answer(request)

    return Upstream(handle)


def ai_upstream(answer, *, exchange=None) -> Upstream:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "sts.example.test":
            if exchange is not None:
                return exchange(request)
            return httpx.Response(200, json={"issued_token": AI_JWT, "token_type": "JWT"})
        return answer(request)

    return Upstream(handle)


class BrokenStream(httpx.AsyncByteStream):
    """A body that sends some bytes and then fails the way a dropped connection does."""

    def __init__(self, chunks, error):
        self._chunks = chunks
        self._error = error

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk
        raise self._error

    async def aclose(self) -> None:
        return None


def broken_sse(events, error) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=BrokenStream([sse(*events)], error))


HELLO = [{"role": "user", "content": "Hello"}]


def complete(config, upstream, **kwargs):
    kwargs.setdefault("model", llm_gateway.resolve_model(config, None))
    kwargs.setdefault("messages", HELLO)
    return asyncio.run(llm_gateway.complete(config=config, client=upstream.client(), **kwargs))


def stream(config, upstream, **kwargs):
    kwargs.setdefault("model", llm_gateway.resolve_model(config, None))
    kwargs.setdefault("messages", HELLO)

    async def collect():
        return [event async for event in llm_gateway.stream(config=config, client=upstream.client(), **kwargs)]

    return asyncio.run(collect())


def failure(call, *args, **kwargs) -> LlmGatewayError:
    with pytest.raises(LlmGatewayError) as caught:
        call(*args, **kwargs)
    return caught.value


# ---------------------------------------------------------------- models


def test_copilot_models_are_the_static_list_without_the_auto_ids_until_a_catalog_is_known():
    config = copilot_config()

    assert llm_gateway.provider_id(config) == "github_copilot"
    assert llm_gateway.available_models(config) == ["gpt-6-astra", "gpt-6-luna", "gpt-6-sol", "gpt-6.1-sol"]


def test_copilot_models_follow_the_portal_wide_catalog_once_it_is_known():
    model_catalog_service.store_catalog(
        None,
        {
            "source": "catalog",
            "fetched_at": "2026-11-14T00:00:00+00:00",
            "models": [
                {"id": "auto", "label": "Auto", "kind": "auto"},
                {"id": "auto-efficiency", "label": "Auto (Efficiency)", "kind": "auto"},
                {"id": "gpt-6.1-sol", "label": "GPT-6.1 Sol", "kind": "model"},
                {"id": "mai-code-1.1-flash", "label": "MAI Code 1.1 Flash", "kind": "model"},
            ],
        },
    )
    config = copilot_config()

    assert llm_gateway.available_models(config) == ["gpt-6.1-sol", "mai-code-1.1-flash"]
    assert llm_gateway.resolve_model(config, "mai-code-1.1-flash") == "mai-code-1.1-flash"
    # gpt-6-sol is gone from this catalog's list, so the default is the first model it does list.
    assert llm_gateway.default_model(config) == "gpt-6.1-sol"
    assert failure(llm_gateway.resolve_model, config, "o9-preview").code == "model_not_allowed"


def test_ai_platform_models_are_its_fixed_list():
    config = ai_config()

    assert llm_gateway.provider_id(config) == "ai_platform"
    assert llm_gateway.available_models(config) == [
        "gpt-5.4",
        "gpt-5.6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-6-luna",
        "gpt-6-sol",
        "gpt-6.1-sol",
    ]
    assert llm_gateway.default_model(config) == "gpt-5.4"
    assert llm_gateway.default_model(ai_config(model="gpt-5.6-sol")) == "gpt-5.6-sol"
    # GPT-6 Sol is served by both providers, so the owner's choice of it stands.
    assert llm_gateway.default_model(ai_config(model="gpt-6-sol")) == "gpt-6-sol"
    # A model the provider does not have (left over from Copilot) falls back to its default.
    assert llm_gateway.default_model(ai_config(model="gpt-6-astra")) == "gpt-5.4"


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("auto", "gpt-6-sol"),
        ("auto-intelligence", "gpt-6-sol"),
        ("github_copilot/auto", "gpt-6-sol"),
        ("", "gpt-6-sol"),
        ("gpt-6.1-sol", "gpt-6.1-sol"),
        ("github-copilot/gpt-6-luna", "gpt-6-luna"),
        # Retired from Copilot: never sent.
        ("gpt-5.4", "gpt-6-sol"),
    ],
)
def test_copilot_default_model_is_always_concrete(configured, expected):
    assert llm_gateway.default_model(copilot_config(model=configured)) == expected


def test_resolve_model_names_the_default_when_none_is_asked_for():
    assert llm_gateway.resolve_model(copilot_config(), None) == "gpt-6-sol"
    assert llm_gateway.resolve_model(copilot_config(), "  ") == "gpt-6-sol"
    assert llm_gateway.resolve_model(copilot_config(), "GPT-6-Luna ") == "gpt-6-luna"
    assert llm_gateway.resolve_model(ai_config(), "gpt-5.6-terra") == "gpt-5.6-terra"
    # AI Platform's own since 2026-11-25, so an app of an AI Platform owner may name them.
    for model in ("gpt-6-luna", "gpt-6-sol", "gpt-6.1-sol"):
        assert llm_gateway.resolve_model(ai_config(), model) == model


@pytest.mark.parametrize("requested", ["auto", "auto-efficiency", "AUTO", "github_copilot/auto"])
def test_resolve_model_never_hands_an_app_an_auto_id(requested):
    error = failure(llm_gateway.resolve_model, copilot_config(), requested)

    assert (error.code, error.status) == ("model_not_allowed", 400)


@pytest.mark.parametrize(
    ("config", "requested"),
    [
        (copilot_config(), "gpt-5.4"),  # retired from Copilot
        (copilot_config(), "not a model"),
        (ai_config(), "gpt-6-astra"),  # Copilot's, not AI Platform's
        (ai_config(), "auto"),
    ],
)
def test_resolve_model_refuses_a_model_the_provider_does_not_offer(config, requested):
    error = failure(llm_gateway.resolve_model, config, requested)

    assert (error.code, error.status) == ("model_not_allowed", 400)


@pytest.mark.parametrize("requested", [7, ["gpt-6-sol"], {"id": "gpt-6-sol"}, True])
def test_resolve_model_refuses_a_model_that_is_not_text(requested):
    error = failure(llm_gateway.resolve_model, copilot_config(), requested)

    assert (error.code, error.status) == ("invalid_request", 400)


def test_is_configured_says_whether_a_call_can_be_attempted(monkeypatch):
    assert llm_gateway.is_configured(copilot_config()) is True
    assert llm_gateway.is_configured({"llm": {"provider": "github_copilot"}}) is False
    assert llm_gateway.is_configured({}) is False
    assert llm_gateway.is_configured(ai_config()) is True
    assert llm_gateway.is_configured(ai_config(ai_platform={"auth": {"username": "alice"}})) is False
    monkeypatch.setattr(llm_gateway, "get_settings", lambda: ai_settings(ai_platform_chat_host=""))
    assert llm_gateway.is_configured(ai_config()) is False


# ---------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "messages",
    [
        None,
        [],
        "Hello",
        ["Hello"],
        [{"role": "system", "content": "You are concise."}],
        [{"role": "tool", "content": "{}"}],
        [{"role": "user"}],
        [{"role": "user", "content": None}],
        [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}],
        [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}],
        [{"content": "Hello"}],
        [{"role": "user", "content": "   "}],
    ],
)
def test_messages_must_be_user_and_assistant_text(messages):
    upstream = copilot_upstream(lambda request: sse_response(*responses_events("never")))

    error = failure(complete, copilot_config(), upstream, messages=messages)

    assert (error.code, error.status) == ("invalid_request", 400)
    assert upstream.requests == []


@pytest.mark.parametrize(
    "extra",
    [
        {"system": 5},
        {"system": ["be brief"]},
        {"max_output_tokens": 0},
        {"max_output_tokens": -3},
        {"max_output_tokens": 2.5},
        {"max_output_tokens": "64"},
        {"max_output_tokens": True},
        {"temperature": -0.1},
        {"temperature": 2.5},
        {"temperature": "0.2"},
        {"temperature": True},
        {"temperature": float("nan")},
    ],
)
def test_the_other_arguments_are_checked_before_anything_is_sent(extra):
    upstream = copilot_upstream(lambda request: sse_response(*responses_events("never")))

    error = failure(complete, copilot_config(), upstream, **extra)

    assert (error.code, error.status) == ("invalid_request", 400)
    assert upstream.requests == []


def test_a_completion_checks_the_model_itself():
    upstream = copilot_upstream(lambda request: sse_response(*responses_events("never")))

    assert failure(complete, copilot_config(), upstream, model="auto").code == "model_not_allowed"
    assert failure(complete, copilot_config(), upstream, model="gpt-5.4").code == "model_not_allowed"
    assert failure(stream, copilot_config(), upstream, model="auto").code == "model_not_allowed"
    assert upstream.requests == []


# ---------------------------------------------------------------- GitHub Copilot


def test_copilot_completion_exchanges_the_token_and_posts_a_whitelisted_responses_request():
    upstream = copilot_upstream(lambda request: sse_response(*responses_events("Hel", "lo", charge=4_000_000)))

    result = complete(
        copilot_config(reasoning_effort="xhigh"),
        upstream,
        messages=[
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi."},
            {"role": "user", "content": "Again, please", "name": "ignored"},
        ],
        system="You are concise.",
        max_output_tokens=256,
        temperature=0.2,
        reasoning_effort="high",
    )

    assert result == LlmResult(
        text="Hello",
        model="gpt-6-sol",
        finish_reason="stop",
        usage=LlmUsage(input_tokens=10, output_tokens=20, credits=0.004),
    )

    exchange, call = upstream.requests
    assert (exchange.method, str(exchange.url)) == ("GET", "https://api.github.com/copilot_internal/v2/token")
    assert exchange.headers["authorization"] == f"Bearer {GITHUB_TOKEN}"
    assert exchange.headers["accept"] == "application/json"
    assert exchange.headers["user-agent"] == "GitHubCopilotChat/0.41.0"
    assert exchange.headers["editor-version"] == "vscode/1.133.0"
    assert exchange.headers["editor-plugin-version"] == "copilot-chat/0.41.0"
    assert exchange.headers["copilot-integration-id"] == "vscode-chat"

    # The API host is the one the Copilot token names (proxy-ep, "proxy." -> "api.").
    assert (call.method, str(call.url)) == ("POST", f"{COPILOT_API}/responses")
    assert call.headers["authorization"] == f"Bearer {COPILOT_TOKEN}"
    assert call.headers["content-type"] == "application/json"
    assert call.headers["accept"] == "text/event-stream"
    assert call.headers["user-agent"] == "GitHubCopilotChat/0.41.0"
    assert call.headers["editor-version"] == "vscode/1.133.0"
    assert call.headers["editor-plugin-version"] == "copilot-chat/0.41.0"
    assert call.headers["copilot-integration-id"] == "vscode-chat"
    assert call.headers["x-github-api-version"] == "2026-01-09"
    assert call.headers["openai-intent"] == "conversation-edits"
    assert call.headers["x-initiator"] == "agent"
    assert GITHUB_TOKEN not in call.headers["authorization"]

    body = json.loads(call.content)
    assert set(body) <= set(RESPONSES_BODY_KEYS)
    assert body == {
        "model": "gpt-6-sol",
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "Hello"}]},
            {"role": "assistant", "content": [{"type": "output_text", "text": "Hi."}]},
            {"role": "user", "content": [{"type": "input_text", "text": "Again, please"}]},
        ],
        "stream": True,
        "instructions": "You are concise.",
        "reasoning": {"effort": "high"},
        "max_output_tokens": 256,
    }
    # Never sent to Copilot: a temperature, the runtimes' metadata, tools.
    assert not {"temperature", "metadata", "tools", "tool_choice", "user", "store"} & set(body)


def test_copilot_request_without_optional_values_carries_only_the_core_keys():
    upstream = copilot_upstream(lambda request: sse_response(*responses_events("ok")))

    complete(copilot_config(), upstream)

    assert json.loads(upstream.requests[-1].content) == {
        "model": "gpt-6-sol",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Hello"}]}],
        "stream": True,
        "reasoning": {"effort": "low"},
    }


def test_copilot_stream_yields_deltas_then_one_done():
    upstream = copilot_upstream(lambda request: sse_response(*responses_events("Hel", "", "lo", charge=1_250_000_000)))

    events = stream(copilot_config(), upstream, model="gpt-6.1-sol")

    assert events == [
        {"type": "delta", "text": "Hel"},
        {"type": "delta", "text": "lo"},
        {
            "type": "done",
            "model": "gpt-6.1-sol",
            "finish_reason": "stop",
            "usage": {"input_tokens": 10, "output_tokens": 20, "credits": 1.25},
        },
    ]
    assert json.loads(upstream.requests[-1].content)["model"] == "gpt-6.1-sol"


def test_copilot_token_is_kept_until_shortly_before_it_expires(monkeypatch):
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(llm_copilot, "_now", lambda: clock["now"])
    issued = []

    def exchange(request):
        issued.append(clock["now"])
        token = f"tid=t{len(issued)};proxy-ep=proxy.individual.githubcopilot.com"
        return httpx.Response(200, json={"token": token, "expires_at": int(clock["now"]) + 1500})

    upstream = copilot_upstream(lambda request: sse_response(*responses_events("ok")), exchange=exchange)
    config = copilot_config()

    complete(config, upstream)
    clock["now"] += 1199  # 301 s left: still good
    complete(config, upstream)
    assert len(issued) == 1

    clock["now"] += 2  # 299 s left: inside the five-minute margin
    complete(config, upstream)
    assert len(issued) == 2

    used = [request.headers["authorization"] for request in upstream.to("api.individual.githubcopilot.com")]
    assert used == [
        "Bearer tid=t1;proxy-ep=proxy.individual.githubcopilot.com",
        "Bearer tid=t1;proxy-ep=proxy.individual.githubcopilot.com",
        "Bearer tid=t2;proxy-ep=proxy.individual.githubcopilot.com",
    ]


def test_copilot_tokens_are_kept_per_owner():
    upstream = copilot_upstream(lambda request: sse_response(*responses_events("ok")))

    complete(copilot_config(), upstream)
    complete(copilot_config(api_key="ghu_anotherOwnersToken9876543210"), upstream)
    complete(copilot_config(), upstream)

    assert [request.headers["authorization"] for request in upstream.to("api.github.com")] == [
        f"Bearer {GITHUB_TOKEN}",
        "Bearer ghu_anotherOwnersToken9876543210",
    ]


def test_copilot_expired_token_is_exchanged_again_and_the_call_repeated_once():
    issued = []

    def exchange(request):
        issued.append(1)
        return httpx.Response(200, json={"token": f"copilot-{len(issued)};proxy-ep=proxy.githubcopilot.com", "expires_at": FAR_FUTURE})

    def answer(request):
        if request.headers["authorization"].startswith("Bearer copilot-1;"):
            return httpx.Response(401, json={"error": {"message": "token expired", "code": "unauthorized"}})
        return sse_response(*responses_events("fresh"))

    upstream = copilot_upstream(answer, exchange=exchange)

    assert complete(copilot_config(), upstream).text == "fresh"
    assert len(issued) == 2
    assert len(upstream.to("api.githubcopilot.com")) == 2


def test_copilot_rejected_token_is_an_auth_failure_and_is_not_offered_again():
    issued = []

    def exchange(request):
        issued.append(1)
        return httpx.Response(200, json={"token": COPILOT_TOKEN, "expires_at": FAR_FUTURE})

    upstream = copilot_upstream(lambda request: httpx.Response(401, json={"message": "Bad credentials"}), exchange=exchange)

    error = failure(complete, copilot_config(), upstream)
    assert (error.code, error.status) == ("provider_auth_failed", 424)
    assert len(upstream.to("api.business.githubcopilot.com")) == 1  # not "expired": no second try

    failure(complete, copilot_config(), upstream)
    assert len(issued) == 2  # the refused token was dropped from the cache


def test_copilot_api_base_from_the_settings_wins_over_the_tokens():
    upstream = copilot_upstream(lambda request: sse_response(*responses_events("ok")))

    complete(copilot_config(api_base="https://copilot-gateway.example.test/v1/"), upstream)

    assert str(upstream.requests[-1].url) == "https://copilot-gateway.example.test/v1/responses"


def test_copilot_token_without_an_endpoint_uses_the_default_api_host():
    upstream = copilot_upstream(
        lambda request: sse_response(*responses_events("ok")),
        exchange=lambda request: httpx.Response(200, json={"token": "tid=abc;exp=1", "expires_at": FAR_FUTURE}),
    )

    complete(copilot_config(), upstream)

    assert str(upstream.requests[-1].url) == "https://api.githubcopilot.com/responses"


def test_a_credential_that_is_not_a_github_token_is_used_as_the_copilot_token():
    upstream = copilot_upstream(lambda request: sse_response(*responses_events("ok")))

    complete(copilot_config(api_key="tid=direct;exp=9"), upstream)

    assert upstream.to("api.github.com") == []
    call = upstream.requests[-1]
    assert str(call.url) == "https://api.githubcopilot.com/responses"
    assert call.headers["authorization"] == "Bearer tid=direct;exp=9"


@pytest.mark.parametrize(
    ("events", "expected"),
    [
        (responses_events("cut", final="response.incomplete", incomplete_reason="max_output_tokens"), "length"),
        (responses_events("whole"), "stop"),
        (responses_events("odd", final="response.incomplete"), "stop"),
    ],
)
def test_copilot_finish_reason(events, expected):
    upstream = copilot_upstream(lambda request: sse_response(*events))

    assert complete(copilot_config(), upstream).finish_reason == expected
    assert stream(copilot_config(), upstream)[-1]["finish_reason"] == expected


def test_copilot_answer_withheld_by_the_content_filter_is_a_provider_error():
    events = responses_events("par", final="response.incomplete", incomplete_reason="content_filter")
    upstream = copilot_upstream(lambda request: sse_response(*events))

    error = failure(complete, copilot_config(), upstream)

    assert (error.code, error.status) == ("provider_error", 502)
    assert stream(copilot_config(), upstream)[-1]["error"]["code"] == "provider_error"


@pytest.mark.parametrize(
    ("last_event", "credits"),
    [
        # Next to ``response`` on the final event: where Copilot puts it.
        ({"response": {"usage": {"input_tokens": 7, "output_tokens": 9}}, "copilot_usage": {"total_nano_aiu": 2_500_000_000}}, 2.5),
        ({"response": {"usage": {"input_tokens": 7, "output_tokens": 9, "copilot_usage": {"total_nano_aiu": 123_456_789}}}}, 0.123457),
        ({"response": {"usage": {"input_tokens": 7, "output_tokens": 9}, "copilot_usage": {"total_nano_aiu": 0}}}, 0.0),
        ({"response": {"usage": {"input_tokens": 7, "output_tokens": 9}}}, None),
        ({"response": {"usage": {"input_tokens": 7, "output_tokens": 9}}, "copilot_usage": {"total_nano_aiu": -1}}, None),
        ({"response": {"usage": {"input_tokens": 7, "output_tokens": 9}}, "copilot_usage": {"total_nano_aiu": "n/a"}}, None),
    ],
)
def test_copilot_usage_and_credits(last_event, credits):
    events = [{"type": "response.output_text.delta", "delta": "x"}, {"type": "response.completed", **last_event}]
    upstream = copilot_upstream(lambda request: sse_response(*events))

    assert complete(copilot_config(), upstream).usage == LlmUsage(input_tokens=7, output_tokens=9, credits=credits)


def test_copilot_answer_sent_as_one_json_object_is_understood():
    answer = {
        "id": "resp_2",
        "status": "completed",
        "output": [
            {"type": "reasoning", "id": "rs_1", "summary": []},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Hi "}, {"type": "output_text", "text": "there"}]},
        ],
        "usage": {"input_tokens": 3, "output_tokens": 5},
        "copilot_usage": {"total_nano_aiu": 1_500_000_000},
    }
    upstream = copilot_upstream(lambda request: httpx.Response(200, json=answer))

    assert complete(copilot_config(), upstream) == LlmResult("Hi there", "gpt-6-sol", "stop", LlmUsage(3, 5, 1.5))
    assert stream(copilot_config(), upstream) == [
        {"type": "delta", "text": "Hi there"},
        {"type": "done", "model": "gpt-6-sol", "finish_reason": "stop", "usage": {"input_tokens": 3, "output_tokens": 5, "credits": 1.5}},
    ]


def test_copilot_text_that_arrives_only_in_the_final_event_is_still_returned():
    final = {
        "type": "response.completed",
        "response": {"output": [{"type": "message", "content": [{"type": "output_text", "text": "late"}]}], "usage": {"input_tokens": 1, "output_tokens": 1}},
    }
    upstream = copilot_upstream(lambda request: sse_response(final))

    assert complete(copilot_config(), upstream).text == "late"
    assert stream(copilot_config(), upstream)[0] == {"type": "delta", "text": "late"}


def test_a_line_separator_inside_a_delta_does_not_break_the_stream():
    # U+2028 may stand unescaped inside a JSON string; httpx's own line
    # splitting would cut the event in two there.
    event = json.dumps({"type": "response.output_text.delta", "delta": "a\u2028b"}, ensure_ascii=False)
    final = json.dumps({"type": "response.completed", "response": {"usage": {"input_tokens": 1, "output_tokens": 2}}})
    body = f"data: {event}\r\n\r\ndata: {final}\r\n\r\n".encode("utf-8")
    upstream = copilot_upstream(lambda request: httpx.Response(200, headers={"content-type": "text/event-stream; charset=utf-8"}, content=body))

    assert complete(copilot_config(), upstream).text == "a\u2028b"


def test_copilot_without_an_authorization_is_an_auth_failure_and_nothing_is_sent():
    upstream = copilot_upstream(lambda request: sse_response(*responses_events("never")))

    error = failure(complete, {"llm": {"provider": "github_copilot", "model": "gpt-6-sol"}}, upstream)

    assert (error.code, error.status) == ("provider_auth_failed", 424)
    assert upstream.requests == []


@pytest.mark.parametrize("status", [401, 403, 404])
def test_copilot_token_exchange_refused_is_an_auth_failure(status):
    upstream = copilot_upstream(
        lambda request: sse_response(*responses_events("never")),
        exchange=lambda request: httpx.Response(status, json={"message": "Bad credentials"}),
    )

    error = failure(complete, copilot_config(), upstream)

    assert (error.code, error.status) == ("provider_auth_failed", 424)
    assert len(upstream.requests) == 1


@pytest.mark.parametrize(
    ("exchange", "code", "status"),
    [
        (lambda request: httpx.Response(500, text="oops"), "provider_error", 502),
        (lambda request: httpx.Response(200, text="<html>sign in</html>"), "provider_error", 502),
        (lambda request: httpx.Response(200, json={"message": "no seat"}), "provider_error", 502),
        (lambda request: httpx.Response(200, json={"token": COPILOT_TOKEN}), "provider_error", 502),
        (lambda request: httpx.Response(200, json={"token": COPILOT_TOKEN, "expires_at": "soon"}), "provider_error", 502),
        (lambda request: httpx.Response(429, headers={"retry-after": "7"}, json={"message": "slow down"}), "rate_limited", 429),
    ],
)
def test_copilot_token_exchange_failures(exchange, code, status):
    upstream = copilot_upstream(lambda request: sse_response(*responses_events("never")), exchange=exchange)

    error = failure(complete, copilot_config(), upstream)

    assert (error.code, error.status) == (code, status)
    assert GITHUB_TOKEN not in error.message


@pytest.mark.parametrize(
    ("answer", "code", "status"),
    [
        (httpx.Response(403, json={"error": {"message": "access to this endpoint is forbidden"}}), "provider_auth_failed", 424),
        (httpx.Response(500, json={"error": {"message": "upstream exploded"}}), "provider_error", 502),
        (httpx.Response(400, json={"error": {"message": "Unsupported parameter", "code": "invalid_request_body"}}), "provider_error", 502),
        (httpx.Response(402, json={"error": {"message": "quota exceeded"}}), "provider_error", 502),
        (httpx.Response(302, headers={"location": "https://elsewhere.example.test/"}), "provider_error", 502),
        (httpx.Response(504, text="gateway timeout"), "upstream_timeout", 504),
        (
            httpx.Response(400, json={"error": {"message": "The requested model is not available. Available models: gpt-6-sol", "code": "model_not_supported"}}),
            "model_not_allowed",
            400,
        ),
    ],
)
def test_copilot_http_errors_are_mapped(answer, code, status):
    upstream = copilot_upstream(lambda request: httpx.Response(answer.status_code, headers=answer.headers, content=answer.content))

    error = failure(complete, copilot_config(), upstream)
    assert (error.code, error.status) == (code, status)

    # The same before a stream has yielded anything: raised, not yielded.
    assert failure(stream, copilot_config(), upstream).code == code


@pytest.mark.parametrize(
    ("answer", "code"),
    [
        (httpx.Response(400, json={"error": {"message": "Your input exceeds the context window of this model.", "code": "invalid_request_body"}}), "invalid_request"),
        (httpx.Response(400, json={"error": {"message": "too long", "code": "context_length_exceeded"}}), "invalid_request"),
        (httpx.Response(400, text="This model's maximum context length is 400000 tokens."), "invalid_request"),
        (httpx.Response(413, text="<html>Request Entity Too Large</html>"), "invalid_request"),
        # A JSON body that only echoes the request back is not read for what the prompt says.
        (httpx.Response(400, json={"request": {"input": "what is the maximum context length?"}}), "provider_error"),
        (httpx.Response(400, json={"error": {"message": "Invalid schema for function 'x'", "code": "invalid_request_body"}}), "provider_error"),
    ],
)
def test_a_prompt_larger_than_the_model_takes_is_the_callers_error(answer, code):
    for config, upstream in (
        (copilot_config(), copilot_upstream(lambda request: httpx.Response(answer.status_code, headers=answer.headers, content=answer.content))),
        (ai_config(), ai_upstream(lambda request: httpx.Response(answer.status_code, headers=answer.headers, content=answer.content))),
    ):
        error = failure(complete, config, upstream)
        assert error.code == code
        assert error.status == (400 if code == "invalid_request" else 502)


@pytest.mark.parametrize(("retry_after", "expected"), [("12", 12), ("0.2", 1), (None, None), ("soon", None)])
def test_copilot_rate_limit_keeps_the_retry_after(retry_after, expected):
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    upstream = copilot_upstream(lambda request: httpx.Response(429, headers=headers, json={"error": {"message": "rate limit exceeded"}}))

    error = failure(complete, copilot_config(), upstream)

    assert (error.code, error.status, error.retry_after) == ("rate_limited", 429, expected)


def test_a_retry_after_given_as_a_date_becomes_seconds():
    upstream = copilot_upstream(lambda request: httpx.Response(429, headers={"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"}))

    assert failure(complete, copilot_config(), upstream).retry_after == 0


@pytest.mark.parametrize(
    ("raised", "code", "status"),
    [
        (httpx.ReadTimeout("timed out"), "upstream_timeout", 504),
        (httpx.ConnectTimeout("timed out"), "upstream_timeout", 504),
        (httpx.ConnectError("All connection attempts failed"), "provider_error", 502),
        (httpx.RemoteProtocolError("Server disconnected without sending a response."), "provider_error", 502),
    ],
)
def test_copilot_transport_failures_are_mapped(raised, code, status):
    def answer(request):
        raise raised

    upstream = copilot_upstream(answer)
    error = failure(complete, copilot_config(), upstream)
    assert (error.code, error.status) == (code, status)

    # The token exchange fails the same way.
    llm_copilot.reset_token_cache()
    upstream = copilot_upstream(lambda request: sse_response(*responses_events("never")), exchange=answer)
    error = failure(complete, copilot_config(), upstream)
    assert (error.code, error.status) == (code, status)


@pytest.mark.parametrize(
    "body",
    [
        b"data: {not json}\n\n",
        sse({"type": "response.output_text.delta", "delta": "half"}),  # ends without response.completed
        sse({"type": "response.failed", "response": {"error": {"code": "server_error", "message": "model crashed"}}}),
        sse({"type": "error", "code": "rate_limit_exceeded", "message": "too many requests"}),
        b"",
    ],
)
def test_copilot_malformed_or_failed_stream_is_a_provider_error(body):
    upstream = copilot_upstream(lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body))

    error = failure(complete, copilot_config(), upstream)

    assert (error.code, error.status) == ("provider_error", 502)


def test_copilot_stream_that_breaks_midway_ends_with_an_error_event():
    half = [{"type": "response.created", "response": {}}, {"type": "response.output_text.delta", "delta": "Hel"}]

    dropped = copilot_upstream(lambda request: broken_sse(half, httpx.ReadError("connection reset by peer")))
    events = stream(copilot_config(), dropped)
    assert events[0] == {"type": "delta", "text": "Hel"}
    assert events[-1]["type"] == "error"
    assert events[-1]["error"]["code"] == "provider_error"
    assert [event["type"] for event in events] == ["delta", "error"]  # no "done" after an error

    stalled = copilot_upstream(lambda request: broken_sse(half, httpx.ReadTimeout("timed out")))
    assert stream(copilot_config(), stalled)[-1] == {
        "type": "error",
        "error": {"code": "upstream_timeout", "message": "GitHub Copilot did not answer in time."},
    }

    ended = copilot_upstream(lambda request: sse_response(*half))
    assert stream(copilot_config(), ended)[-1]["error"]["code"] == "provider_error"

    failed = copilot_upstream(lambda request: sse_response(*half, {"type": "response.failed", "response": {"error": {"message": "model crashed"}}}))
    last = stream(copilot_config(), failed)[-1]
    assert last["type"] == "error" and "model crashed" in last["error"]["message"]

    # The whole answer fails the same way, as an exception.
    assert failure(complete, copilot_config(), dropped).code == "provider_error"
    assert failure(complete, copilot_config(), stalled).code == "upstream_timeout"


def test_copilot_stream_that_fails_before_any_text_raises():
    broken = copilot_upstream(lambda request: broken_sse([{"type": "response.created", "response": {}}], httpx.ReadError("reset")))

    error = failure(stream, copilot_config(), broken)

    assert (error.code, error.status) == ("provider_error", 502)


def test_closing_a_stream_early_ends_quietly():
    upstream = copilot_upstream(lambda request: sse_response(*responses_events("one", "two", "three")))

    async def first_only():
        events = llm_gateway.stream(config=copilot_config(), model="gpt-6-sol", messages=HELLO, client=upstream.client())
        first = await events.__anext__()
        await events.aclose()
        return first

    assert asyncio.run(first_only()) == {"type": "delta", "text": "one"}


def test_copilot_errors_never_carry_a_token():
    echo = f"invalid token {COPILOT_TOKEN} (from {GITHUB_TOKEN}); Authorization: Bearer {COPILOT_TOKEN}"
    upstream = copilot_upstream(lambda request: httpx.Response(500, json={"error": {"message": echo}}))

    error = failure(complete, copilot_config(), upstream)

    assert COPILOT_TOKEN not in error.message and GITHUB_TOKEN not in error.message
    assert COPILOT_TOKEN not in str(error) and COPILOT_TOKEN not in repr(error)
    assert error.__cause__ is None

    def refuse(request):
        raise httpx.ConnectError(f"proxy said no to {COPILOT_TOKEN} / {GITHUB_TOKEN}")

    error = failure(complete, copilot_config(), copilot_upstream(refuse))
    assert COPILOT_TOKEN not in error.message and GITHUB_TOKEN not in error.message
    # The failed request, Authorization header and all, is not attached to the error.
    assert error.__cause__ is None and error.__context__ is None

    # The same for the exchange, whose request carries the GitHub token.
    def refuse_exchange(request):
        raise httpx.ConnectError(f"proxy said no to {request.headers['authorization']}")

    llm_copilot.reset_token_cache()
    error = failure(complete, copilot_config(), copilot_upstream(lambda request: sse_response(), exchange=refuse_exchange))
    assert GITHUB_TOKEN not in error.message
    assert error.__cause__ is None and error.__context__ is None


# ---------------------------------------------------------------- AI Platform


def chat_answer(text="Hello", finish="stop", usage=None):
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": "gpt-5.4-2026-03-01",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish}],
        "usage": usage or {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


def test_ai_platform_completion_exchanges_a_jwt_and_posts_chat_completions():
    upstream = ai_upstream(lambda request: httpx.Response(200, json=chat_answer()))

    result = complete(
        ai_config(reasoning_effort="high"),
        upstream,
        messages=[{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi."}, {"role": "user", "content": "Again"}],
        system="You are concise.",
        max_output_tokens=64,
        temperature=0.7,
    )

    # The model is the catalog id that was asked for, not the snapshot the provider names.
    assert result == LlmResult("Hello", "gpt-5.4", "stop", LlmUsage(input_tokens=11, output_tokens=7, credits=None))

    exchange, call = upstream.requests
    assert (exchange.method, str(exchange.url)) == ("POST", "https://sts.example.test/sts/v1/token")
    assert exchange.headers["content-type"] == "application/json"
    assert exchange.headers["accept"] == "application/json"
    assert json.loads(exchange.content) == {
        "input_token_state": {"token_type": "CREDENTIAL", "username": "alice", "password": AI_PASSWORD},
        "output_token_state": {"token_type": "JWT"},
    }

    assert (call.method, str(call.url)) == ("POST", "https://ai.example.test/v1/api/v1/chat/completions")
    assert call.headers["x-test-trust-token"] == AI_JWT
    assert call.headers["content-type"] == "application/json"
    assert call.headers["accept"] == "application/json"
    assert call.headers["x-correlation-id"].startswith("EFPT-")
    assert call.headers["x-correlation-id"] == call.headers["x-usersession-id"]
    # chat/completions takes the JWT in the trust header only.
    assert "authorization" not in call.headers

    body = json.loads(call.content)
    assert set(body) <= set(CHAT_BODY_KEYS)
    assert body == {
        "model": "gpt-5.4",
        "messages": [
            {"role": "system", "content": "You are concise."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi."},
            {"role": "user", "content": "Again"},
        ],
        "stream": False,
        "reasoning_effort": "low",
        "user": "uc-apps",
        "max_completion_tokens": 64,
    }
    assert not {"temperature", "metadata", "tools", "stream_options"} & set(body)


def test_ai_platform_stream_reads_chat_completion_chunks():
    usage = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    upstream = ai_upstream(lambda request: sse_response(*chat_chunks("Hel", "lo", usage=usage), done=True))

    events = stream(ai_config(), upstream, model="gpt-5.6-sol")

    assert events == [
        {"type": "delta", "text": "Hel"},
        {"type": "delta", "text": "lo"},
        {"type": "done", "model": "gpt-5.6-sol", "finish_reason": "stop", "usage": {"input_tokens": 11, "output_tokens": 7, "credits": None}},
    ]
    call = upstream.requests[-1]
    assert call.headers["accept"] == "text/event-stream"
    body = json.loads(call.content)
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["user"] == "uc-apps"


def test_ai_platform_stream_without_a_usage_chunk_counts_nothing():
    upstream = ai_upstream(lambda request: sse_response(*chat_chunks("Hi"), done=True))

    assert stream(ai_config(), upstream)[-1]["usage"] == {"input_tokens": 0, "output_tokens": 0, "credits": None}


def test_ai_platform_responses_endpoint_puts_the_usercase_in_the_url_and_adds_a_bearer(monkeypatch):
    monkeypatch.setattr(llm_gateway, "get_settings", lambda: ai_settings(ai_platform_responses_uri="/v1/{usercase}/responses"))
    upstream = ai_upstream(lambda request: sse_response(*responses_events("Hel", "lo", usage={"input_tokens": 4, "output_tokens": 6})))

    result = complete(ai_config(), upstream, system="Short.", max_output_tokens=32)

    assert result == LlmResult("Hello", "gpt-5.4", "stop", LlmUsage(4, 6, None))
    call = upstream.requests[-1]
    assert str(call.url) == "https://ai.example.test/v1/uc-apps/responses"
    assert call.headers["x-test-trust-token"] == AI_JWT
    assert call.headers["authorization"] == f"Bearer {AI_JWT}"
    assert call.headers["accept"] == "text/event-stream"
    body = json.loads(call.content)
    assert set(body) <= set(RESPONSES_BODY_KEYS)
    assert body == {
        "model": "gpt-5.4",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Hello"}]}],
        "stream": True,
        "instructions": "Short.",
        "reasoning": {"effort": "low"},
        "max_output_tokens": 32,
    }

    assert stream(ai_config(), upstream) == [
        {"type": "delta", "text": "Hel"},
        {"type": "delta", "text": "lo"},
        {"type": "done", "model": "gpt-5.4", "finish_reason": "stop", "usage": {"input_tokens": 4, "output_tokens": 6, "credits": None}},
    ]


def test_ai_platform_jwt_is_kept_for_its_half_minute_and_then_exchanged_again(monkeypatch):
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(llm_ai_platform, "_now", lambda: clock["now"])
    issued = []

    def exchange(request):
        issued.append(clock["now"])
        return httpx.Response(200, json={"issued_token": f"jwt-number-{len(issued)}"})

    upstream = ai_upstream(lambda request: httpx.Response(200, json=chat_answer()), exchange=exchange)
    config = ai_config()

    complete(config, upstream)
    clock["now"] += 25  # 5 s of its 30 left: still used
    complete(config, upstream)
    assert len(issued) == 1

    clock["now"] += 1  # 4 s left: inside the margin
    complete(config, upstream)
    assert len(issued) == 2
    assert [request.headers["x-test-trust-token"] for request in upstream.to("ai.example.test")] == [
        "jwt-number-1",
        "jwt-number-1",
        "jwt-number-2",
    ]


@pytest.mark.parametrize("status", [401, 403])
def test_ai_platform_refused_jwt_is_exchanged_again_once(status):
    issued = []

    def exchange(request):
        issued.append(1)
        return httpx.Response(200, json={"issued_token": f"jwt-number-{len(issued)}"})

    def answer(request):
        if request.headers["x-test-trust-token"] == "jwt-number-1":
            return httpx.Response(status, json={"message": "token expired"})
        return httpx.Response(200, json=chat_answer("second try"))

    upstream = ai_upstream(answer, exchange=exchange)

    assert complete(ai_config(), upstream).text == "second try"
    assert len(issued) == 2


def test_ai_platform_refusing_every_jwt_is_an_auth_failure():
    issued = []

    def exchange(request):
        issued.append(1)
        return httpx.Response(200, json={"issued_token": f"jwt-number-{len(issued)}"})

    upstream = ai_upstream(lambda request: httpx.Response(401, json={"message": "not entitled"}), exchange=exchange)

    error = failure(complete, ai_config(), upstream)

    assert (error.code, error.status) == ("provider_auth_failed", 424)
    assert len(issued) == 2 and len(upstream.to("ai.example.test")) == 2


@pytest.mark.parametrize(
    ("exchange", "code", "status"),
    [
        (lambda request: httpx.Response(401, json={"message": "invalid credentials"}), "provider_auth_failed", 424),
        (lambda request: httpx.Response(400, json={"message": "invalid_grant"}), "provider_auth_failed", 424),
        (lambda request: httpx.Response(403, text="forbidden"), "provider_auth_failed", 424),
        (lambda request: httpx.Response(503, text="maintenance"), "provider_error", 502),
        (lambda request: httpx.Response(200, json={"token_type": "JWT"}), "provider_error", 502),
        (lambda request: httpx.Response(200, text="not json"), "provider_error", 502),
        (lambda request: httpx.Response(429, headers={"retry-after": "3"}), "rate_limited", 429),
    ],
)
def test_ai_platform_exchange_failures(exchange, code, status):
    upstream = ai_upstream(lambda request: httpx.Response(200, json=chat_answer()), exchange=exchange)

    error = failure(complete, ai_config(), upstream)

    assert (error.code, error.status) == (code, status)
    assert AI_PASSWORD not in error.message
    assert upstream.to("ai.example.test") == []


@pytest.mark.parametrize(
    "auth",
    [
        {},
        {"username": "alice", "password": AI_PASSWORD},
        {"username": "alice", "usercase": "uc-apps"},
        {"password": AI_PASSWORD, "usercase": "uc-apps"},
    ],
)
def test_ai_platform_without_its_three_credentials_is_an_auth_failure(auth):
    upstream = ai_upstream(lambda request: httpx.Response(200, json=chat_answer()))

    error = failure(complete, ai_config(ai_platform={"auth": auth}), upstream)

    assert (error.code, error.status) == ("provider_auth_failed", 424)
    assert upstream.requests == []


@pytest.mark.parametrize("missing", [{"ai_platform_chat_host": ""}, {"ai_platform_ib2b_host": ""}, {"ai_platform_ib2b_uri": ""}])
def test_ai_platform_not_set_up_in_the_deployment_is_a_provider_error(monkeypatch, missing):
    monkeypatch.setattr(llm_gateway, "get_settings", lambda: ai_settings(**missing))
    upstream = ai_upstream(lambda request: httpx.Response(200, json=chat_answer()))

    error = failure(complete, ai_config(), upstream)

    assert (error.code, error.status) == ("provider_error", 502)
    assert upstream.requests == []


@pytest.mark.parametrize(
    ("answer", "code", "status", "retry_after"),
    [
        (httpx.Response(429, headers={"retry-after": "30"}, json={"error": {"message": "slow down"}}), "rate_limited", 429, 30),
        (httpx.Response(500, json={"error": {"message": "boom"}}), "provider_error", 502, None),
        (httpx.Response(400, json={"error": {"message": "Unsupported value: temperature"}}), "provider_error", 502, None),
        (httpx.Response(504, text=""), "upstream_timeout", 504, None),
        (httpx.Response(200, json={"error": {"message": "backend unavailable", "code": "server_error"}}), "provider_error", 502, None),
        (httpx.Response(200, json={"choices": []}), "provider_error", 502, None),
        (httpx.Response(200, json=["not", "an", "object"]), "provider_error", 502, None),
    ],
)
def test_ai_platform_errors_are_mapped(answer, code, status, retry_after):
    upstream = ai_upstream(lambda request: httpx.Response(answer.status_code, headers=answer.headers, content=answer.content))

    error = failure(complete, ai_config(), upstream)

    assert (error.code, error.status, error.retry_after) == (code, status, retry_after)
    assert AI_JWT not in error.message and AI_PASSWORD not in error.message


@pytest.mark.parametrize(
    ("raised", "code", "status"),
    [
        (httpx.ReadTimeout("timed out"), "upstream_timeout", 504),
        (httpx.ConnectError("Name or service not known"), "provider_error", 502),
    ],
)
def test_ai_platform_transport_failures_are_mapped(raised, code, status):
    def answer(request):
        raise raised

    for upstream in (ai_upstream(answer), ai_upstream(lambda request: httpx.Response(200, json=chat_answer()), exchange=answer)):
        llm_ai_platform.reset_token_cache()
        error = failure(complete, ai_config(), upstream)
        assert (error.code, error.status) == (code, status)


@pytest.mark.parametrize(("finish", "expected"), [("stop", "stop"), ("length", "length"), ("max_tokens", "length"), (None, "stop")])
def test_ai_platform_finish_reason(finish, expected):
    whole = ai_upstream(lambda request: httpx.Response(200, json=chat_answer(finish=finish)))
    assert complete(ai_config(), whole).finish_reason == expected

    if finish is not None:
        chunks = ai_upstream(lambda request: sse_response(*chat_chunks("x", finish=finish), done=True))
        assert stream(ai_config(), chunks)[-1]["finish_reason"] == expected


def test_ai_platform_content_filter_is_a_provider_error():
    upstream = ai_upstream(lambda request: httpx.Response(200, json=chat_answer(text="", finish="content_filter")))

    assert failure(complete, ai_config(), upstream).code == "provider_error"


def test_ai_platform_stream_that_breaks_midway_ends_with_an_error_event():
    half = [{"choices": [{"index": 0, "delta": {"content": "Hel"}}]}]

    dropped = ai_upstream(lambda request: broken_sse(half, httpx.ReadError("connection reset by peer")))
    assert [event["type"] for event in stream(ai_config(), dropped)] == ["delta", "error"]
    assert stream(ai_config(), dropped)[-1]["error"]["code"] == "provider_error"

    # Ends without a finish_reason: the answer is not known to be whole.
    ended = ai_upstream(lambda request: sse_response(*half, done=True))
    assert stream(ai_config(), ended)[-1]["error"]["code"] == "provider_error"

    garbled = ai_upstream(lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse(*half) + b"data: {oops\n\n"))
    assert stream(ai_config(), garbled)[-1]["error"]["code"] == "provider_error"

    refused = ai_upstream(lambda request: httpx.Response(500, json={"error": {"message": "boom"}}))
    assert failure(stream, ai_config(), refused).code == "provider_error"


# ---------------------------------------------------------------- the way out


class _ClientFactory:
    """Stands in for httpx.AsyncClient: remembers how each client was built, answers from a mock."""

    def __init__(self, upstream: Upstream):
        self.built: list[dict] = []
        self.clients: list[httpx.AsyncClient] = []
        self._upstream = upstream
        self._real = httpx.AsyncClient

    def __call__(self, **kwargs):
        self.built.append(kwargs)
        client = self._real(transport=httpx.MockTransport(self._upstream))
        self.clients.append(client)
        return client


PROXIES = {
    "enabled": True,
    "default": "corp-a",
    "proxies": [
        {"name": "corp-a", "url": "http://proxy-a.example.test:3128", "username": "pu", "password": "pp-secret-1"},
        {"name": "corp-b", "url": "http://proxy-b.example.test:3128"},
    ],
}


def test_without_an_injected_client_the_call_leaves_like_the_model_provider_test(monkeypatch):
    upstream = copilot_upstream(lambda request: sse_response(*responses_events("ok")))
    factory = _ClientFactory(upstream)
    monkeypatch.setattr(_outbound.httpx, "AsyncClient", factory)

    # No Proxy connector: the Portal's own environment, the shared CA trust.
    asyncio.run(llm_gateway.complete(config=copilot_config(), model="gpt-6-sol", messages=HELLO))
    assert factory.built == [{"follow_redirects": False, "verify": trust_context()}]
    assert all(client.is_closed for client in factory.clients)

    # The proxy assigned to the model provider, for the exchange and the call alike.
    llm_copilot.reset_token_cache()
    factory.built.clear()
    config = {**copilot_config(), "proxy": {**PROXIES, "assignments": {"llm": "corp-b"}}}
    asyncio.run(llm_gateway.complete(config=config, model="gpt-6-sol", messages=HELLO))
    assert factory.built == [
        {"follow_redirects": False, "proxy": "http://proxy-b.example.test:3128", "trust_env": False, "verify": trust_context()}
    ]

    # No assignment: the default proxy, with its login.
    llm_copilot.reset_token_cache()
    factory.built.clear()
    config = {**copilot_config(), "proxy": PROXIES}
    asyncio.run(llm_gateway.complete(config=config, model="gpt-6-sol", messages=HELLO))
    assert [kwargs["proxy"] for kwargs in factory.built] == ["http://pu:pp-secret-1@proxy-a.example.test:3128"]

    # Assigned "none": direct, the environment's proxy left aside.
    llm_copilot.reset_token_cache()
    factory.built.clear()
    config = {**copilot_config(), "proxy": {**PROXIES, "assignments": {"llm": "none"}}}
    asyncio.run(llm_gateway.complete(config=config, model="gpt-6-sol", messages=HELLO))
    assert factory.built == [{"follow_redirects": False, "trust_env": False, "verify": trust_context()}]


def test_the_requests_carry_the_timeouts_of_a_completion():
    seen = []

    def answer(request):
        seen.append(request.extensions["timeout"])
        return sse_response(*responses_events("ok"))

    upstream = copilot_upstream(answer, exchange=lambda request: (seen.append(request.extensions["timeout"]), httpx.Response(200, json={"token": COPILOT_TOKEN, "expires_at": FAR_FUTURE}))[1])

    complete(copilot_config(), upstream)

    exchange_timeout, completion_timeout = seen
    assert (exchange_timeout["connect"], exchange_timeout["read"]) == (10.0, 30.0)
    assert (completion_timeout["connect"], completion_timeout["read"]) == (10.0, 120.0)


# ---------------------------------------------------------------- thinking level


def _effort_sent(upstream) -> str:
    body = json.loads(upstream.requests[-1].content)
    return body["reasoning"]["effort"] if "reasoning" in body else body["reasoning_effort"]


@pytest.mark.parametrize("owner_level", ["low", "medium", "high", "xhigh", "max", "", None])
def test_an_app_call_without_a_level_thinks_low_whatever_its_owner_chose(owner_level):
    upstream = copilot_upstream(lambda request: sse_response(*responses_events("ok")))

    complete(copilot_config(reasoning_effort=owner_level), upstream)
    assert _effort_sent(upstream) == "low"

    assert stream(copilot_config(reasoning_effort=owner_level), upstream)[-1]["type"] == "done"
    assert _effort_sent(upstream) == "low"


@pytest.mark.parametrize("level", ["low", "medium", "high", " High ", "MEDIUM"])
def test_an_app_call_may_ask_for_low_medium_or_high(level):
    copilot = copilot_upstream(lambda request: sse_response(*responses_events("ok")))
    complete(copilot_config(reasoning_effort="xhigh"), copilot, reasoning_effort=level)
    assert _effort_sent(copilot) == level.strip().lower()

    ai = ai_upstream(lambda request: httpx.Response(200, json=chat_answer()))
    complete(ai_config(reasoning_effort="max"), ai, reasoning_effort=level)
    assert _effort_sent(ai) == level.strip().lower()


@pytest.mark.parametrize("level", ["xhigh", "max", "minimal", "none", "", 3, True, ["low"]])
def test_any_other_level_is_refused_before_anything_is_sent(level):
    upstream = copilot_upstream(lambda request: sse_response(*responses_events("never")))

    for call in (complete, stream):
        error = failure(call, copilot_config(), upstream, reasoning_effort=level)
        assert (error.code, error.status) == ("invalid_request", 400)
        assert "reasoning_effort" in error.message
    assert upstream.requests == []


def test_the_level_is_clamped_to_what_the_model_takes():
    from app.contracts.llm_catalog import register_catalog_models

    register_catalog_models(
        ("gpt-6-sol", "thinker", "plain"),
        None,
        {"gpt-6-sol": ["low", "medium"], "thinker": ["medium", "high", "xhigh"], "plain": ["minimal"]},
    )
    upstream = copilot_upstream(lambda request: sse_response(*responses_events("ok")))

    # Asked for more than the model takes: the nearest lower level.
    complete(copilot_config(), upstream, model="gpt-6-sol", reasoning_effort="high")
    assert _effort_sent(upstream) == "medium"
    # The default (low) on a model that starts at medium: the nearest higher one.
    complete(copilot_config(), upstream, model="thinker")
    assert _effort_sent(upstream) == "medium"
    complete(copilot_config(), upstream, model="thinker", reasoning_effort="high")
    assert _effort_sent(upstream) == "high"
    # A row that lists no level the runtimes know leaves all of them open.
    complete(copilot_config(), upstream, model="plain", reasoning_effort="medium")
    assert _effort_sent(upstream) == "medium"


# ---------------------------------------------------------------- the output cap a provider refuses


COPILOT_RESPONSES = COPILOT_API + "/responses"
AI_CHAT = "https://ai.example.test/v1/api/v1/chat/completions"
AI_RESPONSES = "https://ai.example.test/v1/uc-apps/responses"


def refusal(message, status=400, **error):
    return httpx.Response(status, json={"error": {"message": message, **error}})


@pytest.mark.parametrize(
    "message, error",
    [
        ("Unsupported parameter: 'max_output_tokens' is not supported with this model.", {}),
        ("Unknown parameter: 'max_output_tokens'.", {"code": "unknown_parameter"}),
        ("Unrecognized request argument supplied: max_output_tokens", {"type": "invalid_request_error"}),
        ("The request body is invalid.", {"param": "max_output_tokens", "code": "unsupported_parameter"}),
    ],
)
def test_copilot_refusing_the_cap_is_asked_again_without_it_and_never_sent_it_again(message, error, caplog):
    def answer(request):
        if "max_output_tokens" in json.loads(request.content):
            return refusal(message, **error)
        return sse_response(*responses_events("Hel", "lo"))

    upstream = copilot_upstream(answer)
    with caplog.at_level("WARNING", logger="app.services.app_sdk.llm_wire"):
        result = complete(copilot_config(), upstream, max_output_tokens=64)
        assert result.text == "Hello"
        first, second = [json.loads(request.content) for request in upstream.to("api.business.githubcopilot.com")]
        assert first["max_output_tokens"] == 64
        assert "max_output_tokens" not in second
        assert {key: value for key, value in first.items() if key != "max_output_tokens"} == second

        # From now on the cap is left out from the start: one request, no refusal.
        assert complete(copilot_config(), upstream, max_output_tokens=128).text == "Hello"
        assert [event["type"] for event in stream(copilot_config(), upstream, max_output_tokens=128)] == ["delta", "delta", "done"]
        calls = upstream.to("api.business.githubcopilot.com")
        assert len(calls) == 4
        assert all("max_output_tokens" not in json.loads(request.content) for request in calls[1:])

    warnings = [record for record in caplog.records if "max_output_tokens" in record.getMessage()]
    assert len(warnings) == 1, "one warning, the first time"
    assert "github_copilot" in warnings[0].getMessage() and COPILOT_RESPONSES in warnings[0].getMessage()
    # What is remembered is the address that refused (it was the provider's
    # name before, which every address of that provider shared).
    assert llm_wire.cap_accepted(COPILOT_RESPONSES, "max_output_tokens") is False
    same_address = COPILOT_RESPONSES.replace("https://api.", "HTTPS://API.") + "/?x=1"
    assert llm_wire.cap_accepted(same_address, "max_output_tokens") is False, "one address, however its host is written"
    # Another plan's address, and AI Platform's, are still sent theirs.
    assert llm_wire.cap_accepted("https://api.individual.githubcopilot.com/responses", "max_output_tokens") is True
    assert llm_wire.cap_accepted(AI_CHAT, "max_completion_tokens") is True
    assert llm_wire.cap_accepted(AI_CHAT, "max_output_tokens") is True


def test_a_stream_refused_for_the_cap_starts_again_without_it():
    def answer(request):
        if "max_output_tokens" in json.loads(request.content):
            return refusal("Unsupported parameter: max_output_tokens")
        return sse_response(*responses_events("Hel", "lo"))

    upstream = copilot_upstream(answer)

    events = stream(copilot_config(), upstream, max_output_tokens=64)

    assert [event["type"] for event in events] == ["delta", "delta", "done"]
    assert len(upstream.to("api.business.githubcopilot.com")) == 2


@pytest.mark.parametrize(
    "response",
    [
        # The cap is known, its value is not liked: the app's to fix.
        refusal("Invalid 'max_output_tokens': integer below minimum value. Expected a value >= 16."),
        # Something else is unsupported.
        refusal("Unsupported parameter: 'temperature' is not supported with this model."),
        # A body that only echoes the request back names the cap without saying anything about it.
        httpx.Response(400, json={"request": {"max_output_tokens": 64, "note": "unsupported"}}),
        # Not a 400.
        refusal("Unsupported parameter: max_output_tokens", status=422),
        refusal("Unsupported parameter: max_output_tokens", status=500),
    ],
)
def test_other_refusals_are_not_taken_for_an_unsupported_cap(response):
    upstream = copilot_upstream(lambda request: response)

    error = failure(complete, copilot_config(), upstream, max_output_tokens=64)

    assert error.code == "provider_error"
    assert len(upstream.to("api.business.githubcopilot.com")) == 1, "not tried again"
    assert llm_wire.cap_accepted(COPILOT_RESPONSES, "max_output_tokens") is True


def test_a_request_without_a_cap_is_never_repeated_for_it():
    upstream = copilot_upstream(lambda request: refusal("Unsupported parameter: max_output_tokens"))

    error = failure(complete, copilot_config(), upstream)

    assert error.code == "provider_error"
    assert len(upstream.to("api.business.githubcopilot.com")) == 1
    assert llm_wire.cap_accepted(COPILOT_RESPONSES, "max_output_tokens") is True


def test_a_second_refusal_after_the_cap_was_dropped_is_the_callers_error():
    upstream = copilot_upstream(lambda request: refusal("Unsupported parameter: max_output_tokens"))

    error = failure(complete, copilot_config(), upstream, max_output_tokens=64)

    assert (error.code, error.status) == ("provider_error", 502)
    assert len(upstream.to("api.business.githubcopilot.com")) == 2, "once with the cap, once without, then it stops"


def test_an_expired_token_and_a_refused_cap_are_each_retried_once():
    seen = []

    def answer(request):
        body = json.loads(request.content)
        seen.append("max_output_tokens" in body)
        if len(seen) == 1:
            return httpx.Response(401, text="token expired")
        if "max_output_tokens" in body:
            return refusal("Unknown parameter: 'max_output_tokens'.")
        return sse_response(*responses_events("ok"))

    upstream = copilot_upstream(answer)

    assert complete(copilot_config(), upstream, max_output_tokens=64).text == "ok"
    assert seen == [True, True, False]
    assert len(upstream.to("api.github.com")) == 2, "the token was exchanged again once"


def test_ai_platform_chat_refusing_its_cap_is_asked_again_without_it(caplog):
    def answer(request):
        if "max_completion_tokens" in json.loads(request.content):
            return refusal("Unrecognized request argument supplied: max_completion_tokens")
        return httpx.Response(200, json=chat_answer())

    upstream = ai_upstream(answer)
    with caplog.at_level("WARNING", logger="app.services.app_sdk.llm_wire"):
        assert complete(ai_config(), upstream, max_output_tokens=64).text == "Hello"
        first, second = [json.loads(request.content) for request in upstream.to("ai.example.test")]
        assert first["max_completion_tokens"] == 64 and "max_completion_tokens" not in second

        assert complete(ai_config(), upstream, max_output_tokens=64).text == "Hello"
        assert len(upstream.to("ai.example.test")) == 3

    assert len([record for record in caplog.records if "max_completion_tokens" in record.getMessage()]) == 1
    assert llm_wire.cap_accepted(AI_CHAT, "max_completion_tokens") is False
    # The Responses endpoint is another address (and names its cap differently): not asked yet; Copilot neither.
    assert llm_wire.cap_accepted(AI_RESPONSES, "max_output_tokens") is True
    assert llm_wire.cap_accepted(COPILOT_RESPONSES, "max_output_tokens") is True


def test_ai_platform_responses_refusing_its_cap_is_asked_again_without_it(monkeypatch):
    monkeypatch.setattr(llm_gateway, "get_settings", lambda: ai_settings(ai_platform_responses_uri="/v1/{usercase}/responses"))

    def answer(request):
        if "max_output_tokens" in json.loads(request.content):
            return refusal("max_output_tokens is not supported")
        return sse_response(*responses_events("Hel", "lo"))

    upstream = ai_upstream(answer)

    assert complete(ai_config(), upstream, max_output_tokens=32).text == "Hello"
    assert len(upstream.to("ai.example.test")) == 2
    assert str(upstream.to("ai.example.test")[0].url) == AI_RESPONSES
    assert llm_wire.cap_accepted(AI_RESPONSES, "max_output_tokens") is False
    assert llm_wire.cap_accepted(AI_CHAT, "max_completion_tokens") is True
    assert llm_wire.cap_accepted(COPILOT_RESPONSES, "max_output_tokens") is True


def test_a_refused_jwt_and_a_refused_cap_are_each_retried_once_on_ai_platform():
    seen = []

    def answer(request):
        body = json.loads(request.content)
        seen.append("max_completion_tokens" in body)
        if len(seen) == 1:
            return httpx.Response(401, json={"message": "jwt expired"})
        if "max_completion_tokens" in body:
            return refusal("Unsupported parameter: max_completion_tokens")
        return httpx.Response(200, json=chat_answer())

    upstream = ai_upstream(answer)

    assert complete(ai_config(), upstream, max_output_tokens=64).text == "Hello"
    assert seen == [True, True, False]


OTHER_GITHUB_TOKEN = "ghu_anotherOwnersDeviceFlowToken9876"
INDIVIDUAL_TOKEN = "tid=def456;exp=4102444800;sku=individual;proxy-ep=proxy.individual.githubcopilot.com;st=dotcom"


def test_what_one_address_refuses_is_not_held_against_another():
    # Two owners on two Copilot plans: each plan has an address of its own.
    def exchange(request):
        token = INDIVIDUAL_TOKEN if OTHER_GITHUB_TOKEN in request.headers["authorization"] else COPILOT_TOKEN
        return httpx.Response(200, json={"token": token, "expires_at": FAR_FUTURE})

    def answer(request):
        if request.url.host == "api.business.githubcopilot.com" and "max_output_tokens" in json.loads(request.content):
            return refusal("Unsupported parameter: max_output_tokens")
        return sse_response(*responses_events("ok"))

    upstream = copilot_upstream(answer, exchange=exchange)
    business, individual = copilot_config(), copilot_config(api_key=OTHER_GITHUB_TOKEN)

    assert complete(business, upstream, max_output_tokens=64).text == "ok"
    assert len(upstream.to("api.business.githubcopilot.com")) == 2, "refused with the cap, answered without"

    # The other plan's address was never asked: it is sent the cap, and takes it.
    assert complete(individual, upstream, max_output_tokens=64).text == "ok"
    (sent,) = upstream.to("api.individual.githubcopilot.com")
    assert json.loads(sent.content)["max_output_tokens"] == 64

    # And the address that refused is not sent it again.
    assert complete(business, upstream, max_output_tokens=64).text == "ok"
    calls = upstream.to("api.business.githubcopilot.com")
    assert len(calls) == 3 and "max_output_tokens" not in json.loads(calls[-1].content)


def test_an_address_from_a_members_own_settings_teaches_nothing(caplog):
    # A member's own llm.base_url answers whatever its owner makes it answer.
    # Its refusal is honoured for that one request and remembered for nobody.
    def answer(request):
        if request.url.host == "llm.member.example.test" and "max_output_tokens" in json.loads(request.content):
            return refusal("Unsupported parameter: max_output_tokens")
        return sse_response(*responses_events("ok"))

    upstream = copilot_upstream(answer)
    member = copilot_config(api_key="tid=member;exp=9", base_url="https://llm.member.example.test")
    # The same address as everybody's, named by a member: still nothing is learnt from it.
    named = copilot_config(base_url=COPILOT_API)

    with caplog.at_level("WARNING", logger="app.services.app_sdk.llm_wire"):
        for _ in range(2):
            assert complete(member, upstream, max_output_tokens=64).text == "ok"
    sent = [("max_output_tokens" in json.loads(request.content)) for request in upstream.to("llm.member.example.test")]
    assert sent == [True, False, True, False], "asked with the cap each time, repeated without it each time"
    assert llm_wire.cap_accepted("https://llm.member.example.test/responses", "max_output_tokens") is True
    assert not [record for record in caplog.records if "max_output_tokens" in record.getMessage()]

    # Another owner's request still carries its cap.
    assert complete(copilot_config(), upstream, max_output_tokens=64).text == "ok"
    assert json.loads(upstream.to("api.business.githubcopilot.com")[-1].content)["max_output_tokens"] == 64

    upstream = copilot_upstream(lambda request: refusal("Unsupported parameter: max_output_tokens") if "max_output_tokens" in json.loads(request.content) else sse_response(*responses_events("ok")))
    assert complete(named, upstream, max_output_tokens=64).text == "ok"
    assert llm_wire.cap_accepted(COPILOT_RESPONSES, "max_output_tokens") is True


# ---------------------------------------------------------------- an address that is never called


@pytest.mark.parametrize(
    "setting",
    [
        {"base_url": "http://127.0.0.1:8000"},
        {"base_url": "http://localhost:11434/v1"},
        {"api_base": "http://[::1]:8000"},
        {"baseURL": "http://169.254.169.254/latest"},
        {"endpoint": "http://2130706433"},
        {"base_url": "http://0.0.0.0:8000"},
        {"base_url": "http://metadata.google.internal"},
    ],
)
def test_a_base_url_that_means_this_machine_or_a_metadata_service_is_refused_before_anything_is_sent(setting):
    upstream = copilot_upstream(lambda request: sse_response(*responses_events("never")))

    for call in (complete, stream):
        error = failure(call, copilot_config(**setting), upstream, max_output_tokens=64)
        # The address is the owner's setting: theirs to correct, like a provider that is not connected.
        assert (error.code, error.status) == ("provider_auth_failed", 424)
        assert "may not call" in error.message and GITHUB_TOKEN not in error.message
    assert upstream.requests == [], "not even the token exchange"


def test_a_base_url_in_a_private_range_is_called():
    upstream = copilot_upstream(lambda request: sse_response(*responses_events("ok")))

    assert complete(copilot_config(base_url="https://10.20.30.40/copilot"), upstream).text == "ok"

    assert str(upstream.requests[-1].url) == "https://10.20.30.40/copilot/responses"


# ---------------------------------------------------------------- how much one request may carry


def test_the_bounds_of_a_request():
    # docs/CONTRACTS.md of the app repository states these; change the two together.
    assert (llm_gateway.MAX_INPUT_CHARS, llm_gateway.MAX_MESSAGES, llm_gateway.MAX_OUTPUT_TOKENS) == (4_000_000, 1_000, 128_000)


def test_the_messages_and_the_system_text_have_an_upper_bound_together(monkeypatch):
    monkeypatch.setattr(llm_gateway, "MAX_INPUT_CHARS", 50)
    upstream = copilot_upstream(lambda request: sse_response(*responses_events("ok")))
    turns = [{"role": "user", "content": "x" * 20}, {"role": "assistant", "content": "y" * 10}, {"role": "user", "content": "z" * 5}]

    # 35 characters of messages and 15 of system text: exactly the bound.
    assert complete(copilot_config(), upstream, messages=turns, system="s" * 15).text == "ok"
    sent = len(upstream.requests)

    for call in (complete, stream):
        for extra in ({"system": "s" * 16}, {"messages": [*turns, {"role": "user", "content": "w" * 16}]}):
            error = failure(call, copilot_config(), upstream, **{"messages": turns, **extra})
            assert (error.code, error.status) == ("invalid_request", 400)
            assert "50" in error.message and "51" in error.message
    assert len(upstream.requests) == sent, "nothing was sent for a request over the bound"


def test_the_number_of_messages_has_an_upper_bound(monkeypatch):
    monkeypatch.setattr(llm_gateway, "MAX_MESSAGES", 3)
    upstream = copilot_upstream(lambda request: sse_response(*responses_events("ok")))
    turns = [{"role": "user", "content": "a"}, {"role": "assistant", "content": ""}, {"role": "user", "content": ""}]

    assert complete(copilot_config(), upstream, messages=turns).text == "ok"
    sent = len(upstream.requests)

    error = failure(complete, copilot_config(), upstream, messages=[*turns, {"role": "assistant", "content": ""}])
    assert (error.code, error.status) == ("invalid_request", 400) and "3" in error.message
    assert len(upstream.requests) == sent


def test_max_output_tokens_has_an_upper_bound():
    upstream = copilot_upstream(lambda request: sse_response(*responses_events("ok")))

    assert complete(copilot_config(), upstream, max_output_tokens=llm_gateway.MAX_OUTPUT_TOKENS).text == "ok"
    assert json.loads(upstream.requests[-1].content)["max_output_tokens"] == 128_000
    sent = len(upstream.requests)

    for call in (complete, stream):
        error = failure(call, copilot_config(), upstream, max_output_tokens=llm_gateway.MAX_OUTPUT_TOKENS + 1)
        assert (error.code, error.status) == ("invalid_request", 400) and "128000" in error.message
    assert len(upstream.requests) == sent
