"""Copilot bills per token (GitHub AI Credits): the Portal says what a turn cost.

The server side of that: context presets with the long-context warning,
medium as the default thinking level, a legacy profile projected with the
default 256K window, the runtime's quota endpoint reachable through the proxy
for every member who can read the assistant, the usage panel with credits and
the monthly balance, and the member-facing cost guide in the help centre.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.contracts.llm_catalog import (
    CONTEXT_SIZE_PRESETS,
    COPILOT_LONG_CONTEXT_THRESHOLD_TOKENS,
    DEFAULT_CONTEXT_SIZE,
    DEFAULT_REASONING_EFFORT,
    context_size_choices,
    context_size_label,
)
from app.services.runtime_profile_context_projection import build_runtime_profile_context_config
from app.services.runtime_profile_service import RuntimeProfileService

ROOT = Path(__file__).resolve().parents[1]


def test_context_presets_add_128k_and_flag_the_long_context_tier():
    assert CONTEXT_SIZE_PRESETS == (64_000, 128_000, 256_000, 1_000_000)
    assert DEFAULT_CONTEXT_SIZE == 256_000
    assert COPILOT_LONG_CONTEXT_THRESHOLD_TOKENS == 272_000
    assert context_size_label(64_000) == "64K"
    assert context_size_label(128_000) == "128K"
    assert context_size_label(256_000) == "256K"
    # Above 272K input tokens the GPT-6 line bills at twice the price; the one
    # preset past it says so wherever presets are listed.
    assert context_size_label(1_000_000) == "1M \u00b7 Long context 2\u00d7"
    assert context_size_choices() == [(size, context_size_label(size)) for size in CONTEXT_SIZE_PRESETS]


def test_thinking_level_defaults_to_medium_everywhere():
    assert DEFAULT_REASONING_EFFORT == "medium"
    assert RuntimeProfileService.default_profile_config()["llm"]["reasoning_effort"] == "medium"
    assert RuntimeProfileService.default_profile_config()["llm"]["max_context_tokens"] == 256_000

    from app.schemas.agent import AgentChatModelProfileResponse

    assert AgentChatModelProfileResponse().current_reasoning_effort == "medium"

    script = (ROOT / "app/static/js/chat_ui.js").read_text(encoding="utf-8")
    assert 'profileDefaultReasoningEffort: "medium"' in script
    assert '|| "high"' not in script, "a hard-coded high fallback survived in chat_ui.js"


def test_a_legacy_profile_without_a_context_size_is_projected_with_the_default_window():
    # Profiles created before the default carried max_context_tokens would
    # otherwise reach the runtime with no cap at all, i.e. the model's full 1M
    # window and the long-context price with it.
    projected = build_runtime_profile_context_config(
        {"llm": {"provider": "github_copilot", "model": "gpt-6-luna"}},
        runtime_type="native",
        default_llm=RuntimeProfileService.default_profile_config()["llm"],
    )

    assert projected["max_context_tokens"] == DEFAULT_CONTEXT_SIZE
    assert "max_context_tokens" not in projected["llm"]
    assert projected["llm"]["reasoning_effort"] == "medium"


def test_connector_forms_list_every_preset_from_the_catalog():
    member_form = (ROOT / "app/templates/partials/connectors/llm.html").read_text(encoding="utf-8")
    assert "{% for size, size_label in context_sizes %}" in member_form
    assert 'value="1000000"' not in member_form, "the member form still hard-codes the presets"

    web = (ROOT / "app/web.py").read_text(encoding="utf-8")
    assert "Context size must be 64K, 256K, or 1M." not in web
    assert '"context_sizes": context_size_choices(),' in web


def _proxy_client(monkeypatch, *, user, agent):
    from app.main import app
    import app.api.proxy as proxy_module

    app.dependency_overrides[proxy_module.get_current_user] = lambda: user

    def _override_db():
        yield object()

    app.dependency_overrides[proxy_module.get_db] = _override_db
    monkeypatch.setattr(proxy_module, "AgentRepository", lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: agent))
    monkeypatch.setattr(
        proxy_module.runtime_execution_context_service,
        "build_runtime_metadata",
        lambda _db, _agent: {"runtime_profile_id": None},
    )
    captured = {}

    async def _fake_forward(**kwargs):
        captured.update(kwargs)
        body = {
            "available": True,
            "source": "copilot_internal_user",
            "credits": {"included": 1900, "used": 1520, "remaining": 380},
            "reset_date": "2026-11-01",
        }
        return 200, json.dumps(body).encode("utf-8"), "application/json"

    monkeypatch.setattr(proxy_module.proxy_service, "forward", _fake_forward)
    return TestClient(app), captured, app


def test_quota_lookup_is_forwarded_for_a_member_who_can_only_read_the_assistant(monkeypatch):
    viewer = SimpleNamespace(id=99, username="viewer", nickname="Viewer", role="user")
    public_agent = SimpleNamespace(id="agent-1", owner_user_id=55, visibility="public", status="running", name="Agent One")
    client, captured, app = _proxy_client(monkeypatch, user=viewer, agent=public_agent)
    try:
        response = client.get("/a/agent-1/api/copilot/quota")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["credits"]["remaining"] == 380
    assert captured["method"] == "GET"
    assert captured["subpath"].strip("/") == "api/copilot/quota"


def _render_usage_panel(**context) -> str:
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader(str(ROOT / "app/templates")))
    return env.get_template("partials/usage_panel.html").render(**context)


def test_usage_panel_shows_credits_cached_and_reasoning_when_the_runtime_reports_them():
    html = _render_usage_panel(
        usage={
            "period_days": 30,
            "global": {
                "request_count": 12,
                "total_input_tokens": 1_900_000,
                "total_output_tokens": 45_000,
                "total_cached_input_tokens": 1_700_000,
                "total_reasoning_tokens": 30_000,
                "total_estimated_credits": 104.3,
                "total_cost": 0.0,
            },
            "by_model": {},
            "by_provider": {},
        },
        quota={"available": True, "credits": {"included": 1900, "used": 1600, "remaining": 300}, "reset_date": "2026-11-01"},
    )

    assert "Est. credits" in html and "104.3" in html
    assert "Cached Input" in html and "1700000" in html
    assert "Reasoning Tokens" in html and "30000" in html
    # The monthly balance: used, remaining, the reset date, and the warning
    # colour once more than 80% is gone.
    assert "Copilot balance this month" in html
    assert "1600 credits used" in html
    assert "300 left" in html
    assert "2026-11-01" in html
    assert "is-warning" in html


def test_usage_panel_keeps_the_old_cost_card_and_says_when_the_balance_is_unavailable():
    html = _render_usage_panel(
        usage={"period_days": 30, "global": {"request_count": 3, "total_input_tokens": 10, "total_output_tokens": 5, "total_cost": 0.5}},
        quota={"available": False, "error": "runtime answered 404"},
    )

    assert "Total Cost" in html and "$0.5000" in html
    assert "Est. credits" not in html
    assert "Cached Input" not in html
    assert "Unavailable: runtime answered 404" in html


def test_usage_panel_without_a_quota_lookup_renders_as_before():
    html = _render_usage_panel(usage={"period_days": 30, "global": {"request_count": 1, "total_input_tokens": 1, "total_output_tokens": 1}})

    assert "Copilot balance this month" not in html
    assert "Requests" in html


def test_usage_panel_route_asks_the_runtime_for_the_quota_but_never_fails_on_it(monkeypatch):
    import app.web as web_module

    calls = []

    async def _fake_forward(**kwargs):
        calls.append(kwargs["subpath"])
        if kwargs["subpath"] == "api/copilot/quota":
            raise RuntimeError("no such route")
        return 200, json.dumps({"period_days": 30, "global": {"request_count": 2}}).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module, "_forward_runtime", _fake_forward)

    import asyncio

    quota = asyncio.run(web_module._copilot_quota_for_panel(user=SimpleNamespace(id=1), agent=SimpleNamespace(id="agent-1")))

    assert calls == ["api/copilot/quota"]
    assert quota["available"] is False
    assert "no such route" in quota["error"]


def test_the_cost_guide_is_a_help_topic_and_a_doc():
    from app.services.help_center import get_topic

    topic = get_topic("copilot-costs")
    assert topic is not None
    assert topic.group == "Working"
    assert topic.title == "Copilot costs"
    assert "272K" in topic.body
    assert "models-and-pricing" in topic.body

    guide = (ROOT / "docs/COPILOT_COST_GUIDE.md").read_text(encoding="utf-8")
    assert "272K" in guide
    assert "models-and-pricing" in guide


def test_the_cost_ui_help_and_guide_are_english_only():
    # The whole Portal is English; the cost UI shipped once with Chinese
    # strings, so the strings, the template, the help topic and the guide are
    # pinned to contain no CJK text or CJK escape sequences.
    import re

    cjk = re.compile("[　-〿぀-ヿ一-鿿＀-￯]")
    cjk_escape = re.compile(r"\\u(3[0-9a-f]{3}|[4-9][0-9a-f]{3}|ff[0-9a-f]{2})", re.IGNORECASE)
    for relative in (
        "app/static/js/chat_ui.js",
        "app/templates/partials/usage_panel.html",
        "app/help/copilot-costs.md",
        "docs/COPILOT_COST_GUIDE.md",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        offenders = [
            number for number, line in enumerate(text.splitlines(), start=1)
            if cjk.search(line) or cjk_escape.search(line)
        ]
        assert offenders == [], f"{relative} has CJK text on lines {offenders[:10]}"
