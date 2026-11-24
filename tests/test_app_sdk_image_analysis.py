"""An app's question about images (app/services/app_sdk/image_analysis.py).

docs/CONTRACTS.md section 9.4 of the app repository: what a request may
carry, which connector answers it, and the one request AI Platform receives.
Every exchange here runs against ``httpx.MockTransport``; the shapes are the
ones the Portal's own Test image analysis button sends and reads.
"""
from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace

import httpx
import pytest

from app.services import model_catalog_service
from app.services.app_sdk import _outbound, image_analysis, llm_ai_platform, llm_gateway, llm_wire
from app.services.app_sdk.image_analysis import InspectRequest
from app.services.app_sdk.llm_types import LlmGatewayError, LlmResult, LlmUsage
from app.services.app_sdk.llm_wire import CHAT_BODY_KEYS
from app.services.tls_trust import trust_context

IA_PASSWORD = "pw-vision-s3cret"
IA_JWT = "eyJhbGciOiJSUzI1NiJ9.vision.signature"
LLM_PASSWORD = "pw-chat-s3cret"
AI_CHAT = "https://ai.example.test/v1/api/v1/chat/completions"
STS = "https://sts.example.test/sts/v1/token"

# How a file of each kind begins; nothing after the first twelve bytes is looked at.
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(16)
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + bytes(16)
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + bytes(16)
GIF = b"GIF89a\x01\x00\x01\x00" + bytes(16)
MIB = 1024 * 1024


def b64(data: bytes) -> str:
    return base64.b64encode(bytes(data)).decode("ascii")


def image(data: bytes = PNG, media_type: str = "image/png") -> dict:
    return {"media_type": media_type, "data_base64": b64(data)}


def body(**fields) -> dict:
    request = {"images": [image()], "prompt": "Which field is highlighted?"}
    request.update(fields)
    return request


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


def owner_config(**section) -> dict:
    """An owner who chats on Copilot and whose Image analysis connector is set up."""
    connector = {
        "enabled": True,
        "model": "gpt-5.6-sol",
        "ai_platform": {"auth": {"username": "vision-svc", "password": IA_PASSWORD, "usercase": "uc-vision"}},
    }
    connector.update(section)
    return {
        "llm": {"provider": "github_copilot", "model": "auto", "api_key": "ghu_ownerDeviceFlowToken0123456789"},
        "image_analysis": connector,
    }


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    llm_ai_platform.reset_token_cache()
    llm_wire.reset_cap_state()
    model_catalog_service.reset_catalog_state()
    monkeypatch.setattr(image_analysis, "get_settings", lambda: ai_settings())
    monkeypatch.setattr(llm_gateway, "get_settings", lambda: ai_settings())
    yield
    llm_ai_platform.reset_token_cache()
    llm_wire.reset_cap_state()
    model_catalog_service.reset_catalog_state()


def chat_answer(text="The field Email is marked in red.", finish="stop", usage=None):
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": "gpt-5.6-sol-2026-05-01",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish}],
        "usage": usage or {"prompt_tokens": 1830, "completion_tokens": 210, "total_tokens": 2040},
    }


class Upstream:
    """A mock transport that remembers what it was asked."""

    def __init__(self, answer=None, *, exchange=None):
        self.requests: list[httpx.Request] = []
        self._answer = answer or (lambda request: httpx.Response(200, json=chat_answer()))
        self._exchange = exchange or (lambda request: httpx.Response(200, json={"issued_token": IA_JWT, "token_type": "JWT"}))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._exchange(request) if request.url.host == "sts.example.test" else self._answer(request)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))

    def to(self, host: str) -> list[httpx.Request]:
        return [request for request in self.requests if request.url.host == host]


def inspect(config, upstream, **fields) -> LlmResult:
    return asyncio.run(image_analysis.inspect(config=config, request=image_analysis.parse(body(**fields)), client=upstream.client()))


def failure(call, *args, **kwargs) -> LlmGatewayError:
    with pytest.raises(LlmGatewayError) as caught:
        call(*args, **kwargs)
    return caught.value


# ---------------------------------------------------------------- what a request may carry


def test_the_limits_are_the_contracts():
    assert (image_analysis.MAX_IMAGES, image_analysis.MAX_IMAGE_BYTES, image_analysis.MAX_TOTAL_BYTES) == (4, 5242880, 20971520)
    assert image_analysis.MEDIA_TYPES == ("image/jpeg", "image/png", "image/webp", "image/gif")
    assert (image_analysis.MAX_TEXT_CHARS, image_analysis.MAX_OUTPUT_TOKENS, image_analysis.DEFAULT_MAX_OUTPUT_TOKENS) == (200_000, 16_000, 4_096)
    assert image_analysis.IN_FLIGHT_TOKENS_PER_IMAGE == 1_500
    assert image_analysis.limits() == {"max_images": 4, "max_image_bytes": 5242880, "max_total_bytes": 20971520}
    # Everything the contract allows fits the body the route reads, with room to spare.
    largest = 4 * ((image_analysis.MAX_TOTAL_BYTES + 2) // 3) + 6 * image_analysis.MAX_TEXT_CHARS + 4096
    assert largest < image_analysis.MAX_BODY_BYTES == 40 * MIB


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (JPEG, "image/jpeg"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"\xff\xd8\xfe\xe0", None),
        (b"\xff\xd8", None),
        (PNG, "image/png"),
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\x89PNG\r\n\x1a", None),
        (b"\x89PNG\n\n\x1a\n" + bytes(8), None),
        (WEBP, "image/webp"),
        (b"RIFF\xff\xff\xff\xffWEBP", "image/webp"),
        (b"RIFF\x24\x00\x00\x00WAVEfmt ", None),
        (b"RIFFWEBP", None),
        (GIF, "image/gif"),
        (b"GIF87a", "image/gif"),
        (b"GIF88a\x01\x00", None),
        (b"BM\x36\x00\x00\x00", None),
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", None),
        (b"%PDF-1.7", None),
        (b"", None),
    ],
)
def test_the_kind_of_an_image_is_read_from_its_first_bytes(data, expected):
    assert image_analysis.media_type_of(data) == expected


def test_a_request_is_read_the_way_the_contract_words_it():
    request = image_analysis.parse(
        {
            "images": [image(PNG, "image/png"), image(JPEG, "image/jpeg"), image(WEBP, "image/webp"), image(GIF, "image/gif")],
            "prompt": "What differs between these four?",
            "system": "Answer in one sentence.",
            "max_output_tokens": 600,
            "reasoning_effort": "medium",
        }
    )

    assert isinstance(request, InspectRequest)
    assert [(item.media_type, item.size) for item in request.images] == [
        ("image/png", len(PNG)),
        ("image/jpeg", len(JPEG)),
        ("image/webp", len(WEBP)),
        ("image/gif", len(GIF)),
    ]
    assert [item.url for item in request.images] == [
        "data:image/png;base64," + b64(PNG),
        "data:image/jpeg;base64," + b64(JPEG),
        "data:image/webp;base64," + b64(WEBP),
        "data:image/gif;base64," + b64(GIF),
    ]
    assert (request.prompt, request.system) == ("What differs between these four?", "Answer in one sentence.")
    assert request.text_chars == len("What differs between these four?") + len("Answer in one sentence.")
    assert (request.max_output_tokens, request.reasoning_effort) == (600, "medium")
    assert request.image_bytes == len(PNG) + len(JPEG) + len(WEBP) + len(GIF)


@pytest.mark.parametrize("left_out", [{}, {"system": None, "max_output_tokens": None, "reasoning_effort": None}])
def test_what_is_left_out_or_null_has_the_contracts_meaning(left_out):
    request = image_analysis.parse(body(**left_out))

    assert request.system is None
    assert request.max_output_tokens == 4096
    assert request.reasoning_effort == "low"


def test_a_blank_system_text_is_not_sent_but_its_characters_count():
    request = image_analysis.parse(body(prompt="abcd", system="   "))

    assert request.system is None and request.text_chars == 7


def test_a_field_the_contract_does_not_name_is_ignored():
    request = image_analysis.parse(
        body(
            model="gpt-6-sol",
            stream=True,
            temperature=1.5,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": "ignored"}],
            images=[{**image(), "detail": "high", "name": "screenshot.png"}],
        )
    )

    assert request == image_analysis.parse(body())


@pytest.mark.parametrize("level", ["low", "medium", "high", " High ", "MEDIUM"])
def test_the_thinking_level_is_the_model_calls(level):
    assert image_analysis.parse(body(reasoning_effort=level)).reasoning_effort == level.strip().lower()


@pytest.mark.parametrize("tokens", [1, 4096, 16_000])
def test_the_answer_may_be_one_to_sixteen_thousand_tokens(tokens):
    assert image_analysis.parse(body(max_output_tokens=tokens)).max_output_tokens == tokens


def test_the_text_may_hold_two_hundred_thousand_characters_together():
    request = image_analysis.parse(body(prompt="p" * 150_000, system="s" * 50_000))
    assert request.text_chars == 200_000

    for fields in ({"prompt": "p" * 150_000, "system": "s" * 50_001}, {"prompt": "p" * 200_001}):
        error = failure(image_analysis.parse, body(**fields))
        assert (error.code, error.status) == ("invalid_request", 400) and "200000" in error.message


BAD_BODIES = {
    "not an object: a list": [],
    "not an object: text": "images",
    "not an object: null": None,
    "no images": {"prompt": "?"},
    "images is not a list": body(images=image()),
    "images is a string": body(images=b64(PNG)),
    "no image in the list": body(images=[]),
    "five images": body(images=[image()] * 5),
    "an image that is not an object": body(images=[b64(PNG)]),
    "an image that is null": body(images=[None]),
    "no media type": body(images=[{"data_base64": b64(PNG)}]),
    "a media type the model is not given": body(images=[image(PNG, "image/bmp")]),
    "a media type in another spelling": body(images=[image(PNG, "IMAGE/PNG")]),
    "a media type with a parameter": body(images=[image(PNG, "image/png; charset=binary")]),
    "a media type that is not text": body(images=[{"media_type": 7, "data_base64": b64(PNG)}]),
    "no data": body(images=[{"media_type": "image/png"}]),
    "data that is null": body(images=[{"media_type": "image/png", "data_base64": None}]),
    "data that is not text": body(images=[{"media_type": "image/png", "data_base64": [137, 80, 78, 71]}]),
    "an image without bytes": body(images=[{"media_type": "image/png", "data_base64": ""}]),
    "base64 of the URL alphabet": body(images=[{"media_type": "image/png", "data_base64": base64.urlsafe_b64encode(b"\x89PNG\r\n\x1a\n\xfb\xff\xfe" + bytes(16)).decode("ascii")}]),
    "base64 broken into lines": body(images=[{"media_type": "image/png", "data_base64": base64.encodebytes(PNG * 4).decode("ascii")}]),
    "base64 without its padding": body(images=[{"media_type": "image/png", "data_base64": b64(PNG).rstrip("=")}]),
    "base64 with padding it does not need": body(images=[{"media_type": "image/png", "data_base64": b64(PNG) + "="}]),
    "two base64 texts in one": body(images=[{"media_type": "image/png", "data_base64": b64(PNG) + b64(PNG)}]),
    "base64 with a blank in it": body(images=[{"media_type": "image/png", "data_base64": b64(PNG)[:8] + " " + b64(PNG)[8:]}]),
    "a data URI in place of base64": body(images=[{"media_type": "image/png", "data_base64": "data:image/png;base64," + b64(PNG)}]),
    "text that is not ASCII": body(images=[{"media_type": "image/png", "data_base64": chr(0x56FE) + chr(0x7247) + "AA=="}]),
    "bytes that are no image": body(images=[image(b"hello, this is plain text and no image at all")]),
    "a PDF given as a PNG": body(images=[image(b"%PDF-1.7\n" + bytes(32))]),
    "an SVG": body(images=[image(b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/png")]),
    "a PNG given as a JPEG": body(images=[image(PNG, "image/jpeg")]),
    "a JPEG given as a PNG": body(images=[image(JPEG, "image/png")]),
    "a GIF given as a WebP": body(images=[image(GIF, "image/webp")]),
    "the second image is wrong": body(images=[image(), image(JPEG, "image/gif")]),
    "no prompt": {"images": [image()]},
    "a blank prompt": body(prompt="  \n\t "),
    "an empty prompt": body(prompt=""),
    "a prompt that is not text": body(prompt=["What is shown?"]),
    "a prompt that is null": body(prompt=None),
    "a system text that is not text": body(system={"role": "system"}),
    "a system text that is a number": body(system=0),
    "an answer of no tokens": body(max_output_tokens=0),
    "an answer of less than no tokens": body(max_output_tokens=-5),
    "an answer over sixteen thousand tokens": body(max_output_tokens=16_001),
    "tokens that are not whole": body(max_output_tokens=600.5),
    "tokens written as a float": body(max_output_tokens=600.0),
    "tokens that are text": body(max_output_tokens="600"),
    "tokens that are true": body(max_output_tokens=True),
    "a level above high": body(reasoning_effort="xhigh"),
    "the level max": body(reasoning_effort="max"),
    "a level nobody knows": body(reasoning_effort="minimal"),
    "an empty level": body(reasoning_effort=""),
    "a level that is a number": body(reasoning_effort=3),
    "a level that is true": body(reasoning_effort=True),
}


@pytest.mark.parametrize("bad", list(BAD_BODIES.values()), ids=list(BAD_BODIES))
def test_a_request_the_contract_does_not_allow_is_invalid_request(bad):
    error = failure(image_analysis.parse, bad)

    assert (error.code, error.status) == ("invalid_request", 400)
    assert error.message
    # Whatever is wrong with an image, its data is not what the message quotes.
    for item in bad.get("images") if isinstance(bad, dict) and isinstance(bad.get("images"), list) else []:
        data = item.get("data_base64") if isinstance(item, dict) else item
        if isinstance(data, str) and len(data) > 8:
            assert data not in error.message and data[:16] not in error.message


@pytest.mark.parametrize(
    "raw",
    [b"", b"{not json", b"images=1", b"\xff\xfe\x00", b"[" * 100_000, b'{"prompt": NaN'],
    ids=["empty", "cut short", "a form", "not text", "nested without end", "half a value"],
)
def test_a_body_that_is_not_json_is_invalid_request(raw):
    error = failure(image_analysis.parse_body, raw)

    assert (error.code, error.status) == ("invalid_request", 400)


def test_a_body_is_parsed_as_it_arrived():
    raw = json.dumps(body(system="Be brief.", max_output_tokens=64)).encode("utf-8")

    assert image_analysis.parse_body(raw) == image_analysis.parse(body(system="Be brief.", max_output_tokens=64))


def test_one_image_holds_five_mebibytes_at_most():
    full = PNG + bytes(5 * MIB - len(PNG))
    assert len(full) == image_analysis.MAX_IMAGE_BYTES
    assert image_analysis.parse(body(images=[image(full)])).images[0].size == 5 * MIB

    for extra in (1, 2, 3, 300):
        error = failure(image_analysis.parse, body(images=[image(full + bytes(extra))]))
        assert (error.code, error.status) == ("invalid_request", 400)
        assert "images[0]" in error.message and "5242880" in error.message


def test_four_full_images_are_twenty_mebibytes_and_are_taken():
    full = [kind + bytes(5 * MIB - len(kind)) for kind in (PNG, JPEG, WEBP, GIF)]
    request = image_analysis.parse(
        body(images=[image(data, media_type) for data, media_type in zip(full, ("image/png", "image/jpeg", "image/webp", "image/gif"))])
    )

    assert request.image_bytes == image_analysis.MAX_TOTAL_BYTES == 20 * MIB
    assert len(request.images) == 4


def test_the_images_hold_a_bounded_number_of_bytes_together(monkeypatch):
    # Four images of five mebibytes are exactly the bound, so it is moved to see it refuse.
    monkeypatch.setattr(image_analysis, "MAX_TOTAL_BYTES", 2 * len(PNG) + 10)

    assert image_analysis.parse(body(images=[image(), image()])).image_bytes == 2 * len(PNG)
    error = failure(image_analysis.parse, body(images=[image(), image(), image()]))
    assert (error.code, error.status) == ("invalid_request", 400) and "together" in error.message


def test_a_request_never_shows_its_images_or_its_text_when_printed():
    request = image_analysis.parse(body(prompt="a confidential question", system="a confidential instruction"))

    shown = repr(request) + str(request) + repr(request.images[0])
    assert b64(PNG) not in shown and "confidential" not in shown
    assert "image/png" in shown


# ---------------------------------------------------------------- the connector


def test_the_model_is_the_connectors_and_only_while_it_can_run(monkeypatch):
    assert image_analysis.configured_model(owner_config()) == "gpt-5.6-sol"
    # A model the catalogue does not list is repaired when the section is materialized.
    assert image_analysis.configured_model(owner_config(model="gpt-4-vision")) == "gpt-5.4"
    assert image_analysis.configured_model(owner_config(model=None)) == "gpt-5.4"

    for config in (
        {},
        None,
        {"llm": {"provider": "ai_platform"}},
        owner_config(enabled=False),
        owner_config(enabled="off"),
        owner_config(ai_platform={}),
        owner_config(ai_platform={"auth": {"username": "vision-svc", "password": IA_PASSWORD}}),
        owner_config(ai_platform={"auth": {"username": "vision-svc", "usercase": "uc-vision"}}),
        owner_config(ai_platform={"auth": {"password": IA_PASSWORD, "usercase": "uc-vision"}}),
    ):
        assert image_analysis.configured_model(config) is None, config

    for missing in ({"ai_platform_chat_host": ""}, {"ai_platform_ib2b_host": ""}):
        monkeypatch.setattr(image_analysis, "get_settings", lambda: ai_settings(**missing))
        assert image_analysis.configured_model(owner_config()) is None, missing


def test_the_account_of_the_model_provider_is_never_borrowed():
    # The owner chats on AI Platform with an account of their own, and has no Image analysis connector.
    config = {
        "llm": {
            "provider": "ai_platform",
            "model": "gpt-5.4",
            "ai_platform": {"auth": {"username": "alice", "password": LLM_PASSWORD, "usercase": "uc-chat"}},
        },
    }
    upstream = Upstream()

    assert image_analysis.configured_model(config) is None
    error = failure(inspect, config, upstream)

    assert (error.code, error.status) == ("connector_not_configured", 424)
    assert upstream.requests == []


NOT_SET_UP = {
    "switched off": (owner_config(enabled=False), {}),
    "no section at all": ({"llm": {"provider": "github_copilot"}}, {}),
    "no account": (owner_config(ai_platform={}), {}),
    "no password": (owner_config(ai_platform={"auth": {"username": "vision-svc", "usercase": "uc-vision"}}), {}),
    "no usercase": (owner_config(ai_platform={"auth": {"username": "vision-svc", "password": IA_PASSWORD}}), {}),
    "no AI Platform address in the deployment": (owner_config(), {"ai_platform_chat_host": ""}),
    "no iB2B address in the deployment": (owner_config(), {"ai_platform_ib2b_host": ""}),
    "no iB2B path in the deployment": (owner_config(), {"ai_platform_ib2b_uri": ""}),
}


@pytest.mark.parametrize(("config", "deployment"), list(NOT_SET_UP.values()), ids=list(NOT_SET_UP))
def test_a_connector_that_cannot_run_is_connector_not_configured_and_nothing_is_sent(monkeypatch, config, deployment):
    monkeypatch.setattr(image_analysis, "get_settings", lambda: ai_settings(**deployment))
    upstream = Upstream()

    error = failure(inspect, config, upstream)

    assert (error.code, error.status) == ("connector_not_configured", 424)
    assert "Image analysis connector" in error.message
    assert IA_PASSWORD not in error.message
    assert upstream.requests == []


# ---------------------------------------------------------------- the request AI Platform receives


def test_a_call_exchanges_the_connectors_account_and_posts_one_chat_completion():
    upstream = Upstream()

    result = inspect(
        owner_config(),
        upstream,
        images=[image(PNG, "image/png"), image(JPEG, "image/jpeg")],
        prompt="What differs between the two screenshots?",
        system="Answer in one sentence.",
        max_output_tokens=600,
        reasoning_effort="medium",
    )

    # The model is the connector's, not the snapshot AI Platform names.
    assert result == LlmResult("The field Email is marked in red.", "gpt-5.6-sol", "stop", LlmUsage(1830, 210, None))

    exchange, call = upstream.requests
    assert (exchange.method, str(exchange.url)) == ("POST", STS)
    assert json.loads(exchange.content) == {
        "input_token_state": {"token_type": "CREDENTIAL", "username": "vision-svc", "password": IA_PASSWORD},
        "output_token_state": {"token_type": "JWT"},
    }

    assert (call.method, str(call.url)) == ("POST", AI_CHAT)
    assert call.headers["x-test-trust-token"] == IA_JWT
    assert call.headers["content-type"] == "application/json"
    assert call.headers["accept"] == "application/json"
    assert call.headers["x-correlation-id"].startswith("EFPT-")
    assert call.headers["x-correlation-id"] == call.headers["x-usersession-id"]
    # chat/completions takes the JWT in the trust header only.
    assert "authorization" not in call.headers

    sent = json.loads(call.content)
    assert set(sent) <= set(CHAT_BODY_KEYS)
    assert sent == {
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
    # Nothing of the Portal's own: no instruction, no response format, no tools.
    assert not {"response_format", "tools", "temperature", "stream_options", "metadata"} & set(sent)


def test_a_call_without_options_sends_the_prompt_and_the_image_and_the_defaults():
    upstream = Upstream()

    inspect(owner_config(), upstream)

    sent = json.loads(upstream.requests[-1].content)
    assert sent == {
        "model": "gpt-5.6-sol",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Which field is highlighted?"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64(PNG)}},
                ],
            },
        ],
        "stream": False,
        "reasoning_effort": "low",
        "user": "uc-vision",
        "max_completion_tokens": 4096,
    }


def test_four_images_travel_in_the_order_given():
    upstream = Upstream()
    kinds = [(GIF, "image/gif"), (PNG, "image/png"), (WEBP, "image/webp"), (JPEG, "image/jpeg")]

    inspect(owner_config(), upstream, images=[image(data, media_type) for data, media_type in kinds], prompt="Order?")

    (message,) = json.loads(upstream.requests[-1].content)["messages"]
    assert message["content"][0] == {"type": "text", "text": "Order?"}
    assert [part["image_url"]["url"] for part in message["content"][1:]] == [
        f"data:{media_type};base64,{b64(data)}" for data, media_type in kinds
    ]


def test_the_prompt_reaches_the_model_as_it_was_written():
    upstream = Upstream()
    prompt = "  Read the table.\n\nThen: what is in row 3?  "

    inspect(owner_config(), upstream, prompt=prompt, system="\tKeep the units.\n")

    system, user = json.loads(upstream.requests[-1].content)["messages"]
    assert system == {"role": "system", "content": "\tKeep the units.\n"}
    assert user["content"][0] == {"type": "text", "text": prompt}


def test_the_responses_endpoint_is_never_used_for_images(monkeypatch):
    # The deployment routes model calls to the Responses endpoint.
    settings = ai_settings(ai_platform_responses_uri="/v1/{usercase}/responses")
    monkeypatch.setattr(image_analysis, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_gateway, "get_settings", lambda: settings)
    upstream = Upstream()

    assert inspect(owner_config(), upstream).text == "The field Email is marked in red."

    call = upstream.requests[-1]
    assert str(call.url) == AI_CHAT
    assert "authorization" not in call.headers
    sent = json.loads(call.content)
    assert "messages" in sent and "input" not in sent and sent["user"] == "uc-vision"


def test_a_chat_path_that_carries_the_usercase_is_filled_from_the_connectors_account(monkeypatch):
    monkeypatch.setattr(image_analysis, "get_settings", lambda: ai_settings(ai_platform_chat_uri="/v1/{usercase}/chat/completions"))
    upstream = Upstream()

    inspect(owner_config(), upstream)

    assert str(upstream.requests[-1].url) == "https://ai.example.test/v1/uc-vision/chat/completions"


def test_the_level_is_lowered_to_what_the_model_takes_the_way_a_model_calls_is(monkeypatch):
    asked = []

    def clamp(provider, model, wanted):
        asked.append((provider, model, wanted))
        return "low"

    monkeypatch.setattr(image_analysis, "effective_reasoning_effort", clamp)
    upstream = Upstream()

    inspect(owner_config(), upstream, reasoning_effort="high")

    assert asked == [("ai_platform", "gpt-5.6-sol", "high")]
    assert json.loads(upstream.requests[-1].content)["reasoning_effort"] == "low"


@pytest.mark.parametrize(("finish", "expected"), [("stop", "stop"), ("length", "length"), ("max_tokens", "length"), (None, "stop")])
def test_the_answer_says_whether_it_was_cut(finish, expected):
    upstream = Upstream(lambda request: httpx.Response(200, json=chat_answer(finish=finish)))

    assert inspect(owner_config(), upstream).finish_reason == expected


def test_an_answer_without_usage_reports_none():
    answer = chat_answer()
    answer.pop("usage")
    upstream = Upstream(lambda request: httpx.Response(200, json=answer))

    assert inspect(owner_config(), upstream).usage == LlmUsage(0, 0, None)


# ---------------------------------------------------------------- the account


def test_the_jwt_is_kept_for_its_half_minute_and_then_exchanged_again(monkeypatch):
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(llm_ai_platform, "_now", lambda: clock["now"])
    issued = []

    def exchange(request):
        issued.append(clock["now"])
        return httpx.Response(200, json={"issued_token": f"jwt-number-{len(issued)}"})

    upstream = Upstream(exchange=exchange)
    config = owner_config()

    inspect(config, upstream)
    clock["now"] += 25  # 5 s of its 30 left: still used
    inspect(config, upstream)
    assert len(issued) == 1

    clock["now"] += 1  # 4 s left: inside the margin
    inspect(config, upstream)
    assert len(issued) == 2
    assert [request.headers["x-test-trust-token"] for request in upstream.to("ai.example.test")] == [
        "jwt-number-1",
        "jwt-number-1",
        "jwt-number-2",
    ]


def test_a_model_call_and_an_image_call_on_one_account_share_its_jwt():
    # The owner chats on AI Platform and gave the Image analysis connector the same account.
    account = {"auth": {"username": "alice", "password": LLM_PASSWORD, "usercase": "uc-chat"}}
    config = {
        "llm": {"provider": "ai_platform", "model": "gpt-5.4", "ai_platform": account},
        "image_analysis": {"enabled": True, "model": "gpt-5.6-sol", "ai_platform": account},
    }
    upstream = Upstream()

    asyncio.run(llm_gateway.complete(config=config, model="gpt-5.4", messages=[{"role": "user", "content": "Hello"}], client=upstream.client()))
    inspect(config, upstream)

    assert len(upstream.to("sts.example.test")) == 1
    text_call, image_call = upstream.to("ai.example.test")
    assert json.loads(text_call.content)["model"] == "gpt-5.4" and json.loads(image_call.content)["model"] == "gpt-5.6-sol"


def test_two_accounts_never_share_a_jwt():
    issued = []

    def exchange(request):
        issued.append(json.loads(request.content)["input_token_state"]["username"])
        return httpx.Response(200, json={"issued_token": "jwt-of-" + issued[-1]})

    config = owner_config()
    config["llm"] = {
        "provider": "ai_platform",
        "model": "gpt-5.4",
        "ai_platform": {"auth": {"username": "alice", "password": LLM_PASSWORD, "usercase": "uc-chat"}},
    }
    upstream = Upstream(exchange=exchange)

    asyncio.run(llm_gateway.complete(config=config, model="gpt-5.4", messages=[{"role": "user", "content": "Hello"}], client=upstream.client()))
    inspect(config, upstream)

    assert issued == ["alice", "vision-svc"]
    assert [request.headers["x-test-trust-token"] for request in upstream.to("ai.example.test")] == ["jwt-of-alice", "jwt-of-vision-svc"]
    # And the image call says who it is: the connector's use case, not the model provider's.
    assert [json.loads(request.content)["user"] for request in upstream.to("ai.example.test")] == ["uc-chat", "uc-vision"]


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_jwt_is_exchanged_again_once(status):
    issued = []

    def exchange(request):
        issued.append(1)
        return httpx.Response(200, json={"issued_token": f"jwt-number-{len(issued)}"})

    def answer(request):
        if request.headers["x-test-trust-token"] == "jwt-number-1":
            return httpx.Response(status, json={"message": "token expired"})
        return httpx.Response(200, json=chat_answer("second try"))

    upstream = Upstream(answer, exchange=exchange)

    assert inspect(owner_config(), upstream).text == "second try"
    assert len(issued) == 2
    first, second = upstream.to("ai.example.test")
    assert first.content == second.content, "the same request, signed anew"


def test_ai_platform_refusing_every_jwt_is_an_auth_failure():
    issued = []

    def exchange(request):
        issued.append(1)
        return httpx.Response(200, json={"issued_token": f"jwt-number-{len(issued)}"})

    upstream = Upstream(lambda request: httpx.Response(401, json={"message": "not entitled"}), exchange=exchange)

    error = failure(inspect, owner_config(), upstream)

    assert (error.code, error.status) == ("provider_auth_failed", 424)
    assert len(issued) == 2 and len(upstream.to("ai.example.test")) == 2


@pytest.mark.parametrize("status", [400, 401, 403])
def test_ib2b_refusing_the_account_is_an_auth_failure_that_names_the_connector(status):
    upstream = Upstream(exchange=lambda request: httpx.Response(status, json={"message": "invalid credentials for vision-svc"}))

    error = failure(inspect, owner_config(), upstream)

    assert (error.code, error.status) == ("provider_auth_failed", 424)
    assert "Image analysis connector" in error.message and "Model provider" not in error.message
    assert IA_PASSWORD not in error.message
    assert upstream.to("ai.example.test") == [], "no image is sent with an account that was refused"


# ---------------------------------------------------------------- failures


@pytest.mark.parametrize(
    ("answer", "code", "status", "retry_after"),
    [
        (httpx.Response(429, headers={"retry-after": "30"}, json={"error": {"message": "slow down"}}), "rate_limited", 429, 30),
        (httpx.Response(500, json={"error": {"message": "boom"}}), "provider_error", 502, None),
        (httpx.Response(400, json={"error": {"message": "Invalid image data."}}), "provider_error", 502, None),
        (httpx.Response(504, text=""), "upstream_timeout", 504, None),
        (httpx.Response(200, json={"error": {"message": "backend unavailable", "code": "server_error"}}), "provider_error", 502, None),
        (httpx.Response(200, json={"choices": []}), "provider_error", 502, None),
        (httpx.Response(200, json=["not", "an", "object"]), "provider_error", 502, None),
        (httpx.Response(200, text="<html>gateway</html>", headers={"content-type": "text/html"}), "provider_error", 502, None),
        # The app named no model: one AI Platform does not serve is the connector's to change.
        (httpx.Response(400, json={"error": {"message": "The requested model is not available."}}), "provider_error", 502, None),
        # More than the model takes is the caller's to change.
        (httpx.Response(413, text="too large"), "invalid_request", 400, None),
        (httpx.Response(400, json={"error": {"code": "context_length_exceeded", "message": "too many tokens"}}), "invalid_request", 400, None),
    ],
)
def test_what_ai_platform_answers_is_mapped_to_the_contracts_codes(answer, code, status, retry_after):
    upstream = Upstream(lambda request: httpx.Response(answer.status_code, headers=answer.headers, content=answer.content))

    error = failure(inspect, owner_config(), upstream)

    assert (error.code, error.status, error.retry_after) == (code, status, retry_after)
    assert IA_JWT not in error.message and IA_PASSWORD not in error.message
    assert b64(PNG) not in error.message


def test_an_answer_withheld_by_the_content_filter_is_a_provider_error():
    upstream = Upstream(lambda request: httpx.Response(200, json=chat_answer(finish="content_filter")))

    error = failure(inspect, owner_config(), upstream)

    assert (error.code, error.status) == ("provider_error", 502)


@pytest.mark.parametrize(
    ("raised", "code", "status"),
    [
        (httpx.ReadTimeout("timed out"), "upstream_timeout", 504),
        (httpx.WriteTimeout("timed out"), "upstream_timeout", 504),
        (httpx.ConnectError("Name or service not known"), "provider_error", 502),
    ],
)
def test_a_request_that_gets_no_answer_is_mapped(raised, code, status):
    def answer(request):
        raise raised

    for upstream in (Upstream(answer), Upstream(exchange=answer)):
        llm_ai_platform.reset_token_cache()
        error = failure(inspect, owner_config(), upstream)
        assert (error.code, error.status) == (code, status)


@pytest.mark.parametrize(
    "exchange",
    [
        lambda request: httpx.Response(503, text="maintenance"),
        lambda request: httpx.Response(200, json={"token_type": "JWT"}),
        lambda request: httpx.Response(200, text="not json"),
    ],
)
def test_an_ib2b_that_fails_otherwise_is_a_provider_error(exchange):
    upstream = Upstream(exchange=exchange)

    error = failure(inspect, owner_config(), upstream)

    assert (error.code, error.status) == ("provider_error", 502)
    assert IA_PASSWORD not in error.message
    assert upstream.to("ai.example.test") == []


def test_no_error_carries_the_password_the_jwt_or_an_image():
    secret_echo = f"bad request for {IA_PASSWORD} with {IA_JWT}: " + b64(PNG)
    answers = [
        httpx.Response(400, json={"error": {"message": secret_echo}}),
        httpx.Response(500, text=secret_echo),
        httpx.Response(401, json={"message": secret_echo}),
    ]
    for answer in answers:
        llm_ai_platform.reset_token_cache()
        upstream = Upstream(lambda request, answer=answer: httpx.Response(answer.status_code, headers=answer.headers, content=answer.content))
        error = failure(inspect, owner_config(), upstream)
        assert IA_PASSWORD not in error.message and IA_JWT not in error.message
        assert error.__cause__ is None, "the failed request, with its token and its images, is not dragged along"

    llm_ai_platform.reset_token_cache()

    def unreachable(request):
        raise httpx.ConnectError(f"cannot connect with {IA_PASSWORD}")

    error = failure(inspect, owner_config(), Upstream(exchange=unreachable))
    assert IA_PASSWORD not in error.message and error.__cause__ is None and error.__context__ is None


# ---------------------------------------------------------------- the output cap


def refusal(message, status=400, **error):
    return httpx.Response(status, json={"error": {"message": message, **error}})


def test_a_refused_cap_is_dropped_for_images_and_model_calls_of_that_address_alike(caplog):
    def answer(request):
        if "max_completion_tokens" in json.loads(request.content):
            return refusal("Unrecognized request argument supplied: max_completion_tokens")
        return httpx.Response(200, json=chat_answer())

    upstream = Upstream(answer)
    with caplog.at_level("WARNING", logger="app.services.app_sdk.llm_wire"):
        assert inspect(owner_config(), upstream, max_output_tokens=64).text == "The field Email is marked in red."
        first, second = [json.loads(request.content) for request in upstream.to("ai.example.test")]
        assert first["max_completion_tokens"] == 64 and "max_completion_tokens" not in second
        assert first["messages"] == second["messages"], "the images are sent again, whole"

        inspect(owner_config(), upstream)
        assert len(upstream.to("ai.example.test")) == 3

    assert llm_wire.cap_accepted(AI_CHAT, "max_completion_tokens") is False
    assert b64(PNG) not in caplog.text


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
        {"name": "corp-c", "url": "http://proxy-c.example.test:3128"},
    ],
}


def test_the_call_leaves_the_way_the_proxy_connector_says_image_analysis_does(monkeypatch):
    upstream = Upstream()
    factory = _ClientFactory(upstream)
    monkeypatch.setattr(_outbound.httpx, "AsyncClient", factory)
    request = image_analysis.parse(body())

    def call(config):
        llm_ai_platform.reset_token_cache()
        factory.built.clear()
        asyncio.run(image_analysis.inspect(config=config, request=request))
        return list(factory.built)

    # No Proxy connector: the Portal's own environment, the shared CA trust.
    assert call(owner_config()) == [{"follow_redirects": False, "verify": trust_context()}]
    assert all(client.is_closed for client in factory.clients)

    # The proxy assigned to Image analysis, for the exchange and the call alike; the model provider's is another.
    config = {**owner_config(), "proxy": {**PROXIES, "assignments": {"image_analysis": "corp-b", "llm": "corp-c"}}}
    assert call(config) == [
        {"follow_redirects": False, "proxy": "http://proxy-b.example.test:3128", "trust_env": False, "verify": trust_context()}
    ]
    assert len(upstream.requests) == 4, "an exchange and a call each time, through one client"

    # Only the model provider is assigned one: Image analysis takes the default, with its login.
    config = {**owner_config(), "proxy": {**PROXIES, "assignments": {"llm": "corp-c"}}}
    assert [kwargs["proxy"] for kwargs in call(config)] == ["http://pu:pp-secret-1@proxy-a.example.test:3128"]

    # Assigned "none": direct, the environment's proxy left aside.
    config = {**owner_config(), "proxy": {**PROXIES, "assignments": {"image_analysis": "none", "llm": "corp-c"}}}
    assert call(config) == [{"follow_redirects": False, "trust_env": False, "verify": trust_context()}]
    assert all(client.is_closed for client in factory.clients)


def test_the_requests_carry_the_timeouts_of_an_image_call():
    seen = []

    def answer(request):
        seen.append(request.extensions["timeout"])
        return httpx.Response(200, json=chat_answer())

    def exchange(request):
        seen.append(request.extensions["timeout"])
        return httpx.Response(200, json={"issued_token": IA_JWT})

    inspect(owner_config(), Upstream(answer, exchange=exchange))

    exchange_timeout, call_timeout = seen
    assert (exchange_timeout["connect"], exchange_timeout["read"]) == (10.0, 30.0)
    # The answer comes whole: longer than a model call waits between the pieces of a stream.
    assert (call_timeout["connect"], call_timeout["read"], call_timeout["write"]) == (10.0, 300.0, 60.0)
    assert llm_ai_platform.COMPLETION_TIMEOUT.read == 120.0, "a model call keeps its own"
