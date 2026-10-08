"""Image analysis: inspect-image on AI Platform, whatever the chat provider.

GitHub Copilot's models no longer accept image input, so images are read
through the inspect-image CLI on AI Platform. The block lives at llm.vision and
reuses the AI Platform account at llm.ai_platform.auth, which therefore has to
survive a GitHub Copilot profile while image analysis is on: the whole point is
a Copilot member who can still have screenshots read.
"""
import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.datastructures import FormData

from _js_extract_helpers import _extract_js_function
from app.contracts.llm_catalog import (
    DEFAULT_VISION_MODEL,
    coerce_to_vision_model,
    uses_ai_platform_credentials,
    vision_enabled,
    vision_model_for,
)
from app.schemas.runtime_profile import sanitize_runtime_profile_config_dict as sanitize
from app.services.ai_platform_config import materialize_ai_platform_llm_config
from app.services.runtime_profile_config_policy import canonicalize_portal_runtime_profile_config as canon
from app.services.runtime_profile_context_projection import (
    RUNTIME_PROFILE_CLI_TOOL_INSTRUCTIONS,
    RUNTIME_PROFILE_IMAGE_ANALYSIS_INSTRUCTIONS,
    build_canonical_profile_config,
    project_canonical_for_runtime,
)
from app.services.runtime_profile_test_service import RuntimeProfileTestService
from app.web import _seed_config_from_form, _settings_merge_payload


def _copilot_vision_profile(vision_model="gpt-5.6-sol", enabled=True):
    return {
        "llm": {
            "provider": "github_copilot",
            "model": "gpt-5.6-terra",
            "api_key": "ghu_token",
            "vision": {"enabled": enabled, "model": vision_model},
            "ai_platform": {"auth": {"username": "u", "password": "pw", "usercase": "uc"}},
        }
    }


def _ai_settings(**overrides):
    values = dict(
        ai_platform_chat_host="https://chat.int",
        ai_platform_chat_uri="/v1/api/v1/chat/completions",
        ai_platform_responses_uri="",
        ai_platform_ib2b_host="https://ib2b.int",
        ai_platform_ib2b_uri="/dsp/token",
        ai_platform_trust_token_header="X-Trust",
        ai_platform_tracking_prefix="EFP",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _form(fields: dict) -> FormData:
    return FormData(list(fields.items()))


# --- catalog -----------------------------------------------------------------


def test_the_vision_model_is_always_an_ai_platform_model():
    assert coerce_to_vision_model("gpt-5.6-sol") == "gpt-5.6-sol"
    # Copilot-only ids and junk both land on the AI Platform default.
    assert coerce_to_vision_model("gpt-5.4-mini") == DEFAULT_VISION_MODEL == "gpt-5.4"
    assert coerce_to_vision_model("gpt-5.5") == "gpt-5.4"
    assert coerce_to_vision_model("") == "gpt-5.4"


def test_vision_flag_and_credential_need():
    assert vision_enabled({"vision": {"enabled": True}})
    assert vision_enabled({"vision": {"enabled": "on"}})
    assert not vision_enabled({"vision": {"enabled": False}})
    assert not vision_enabled({"provider": "github_copilot"})
    assert not vision_enabled(None)

    assert uses_ai_platform_credentials({"provider": "ai_platform"})
    assert uses_ai_platform_credentials({"provider": "github_copilot", "vision": {"enabled": True}})
    assert not uses_ai_platform_credentials({"provider": "github_copilot"})


def test_vision_model_prefers_the_block_then_the_ai_platform_chat_model():
    assert vision_model_for({"provider": "github_copilot", "vision": {"model": "gpt-5.6-luna"}}) == "gpt-5.6-luna"
    # An AI Platform chat profile without a vision model reads images with its chat model.
    assert vision_model_for({"provider": "ai_platform", "model": "gpt-5.6-terra", "vision": {"enabled": True}}) == "gpt-5.6-terra"
    # A Copilot chat model is never used for images.
    assert vision_model_for({"provider": "github_copilot", "model": "gpt-5.6-terra", "vision": {"enabled": True}}) == "gpt-5.4"
    assert vision_model_for(None) == "gpt-5.4"


# --- persistence ---------------------------------------------------------------


def test_sanitize_keeps_vision_and_the_ai_platform_account_on_a_copilot_profile():
    profile = _copilot_vision_profile()
    profile["llm"]["vision"]["enabled"] = "on"
    profile["llm"]["vision"]["evil"] = "x"
    llm = sanitize(profile)["llm"]
    assert llm["vision"] == {"enabled": True, "model": "gpt-5.6-sol"}
    assert llm["api_key"] == "ghu_token"
    assert llm["ai_platform"]["auth"] == {"username": "u", "password": "pw", "usercase": "uc"}

    # A non-dict vision value is dropped rather than persisted.
    broken = _copilot_vision_profile()
    broken["llm"]["vision"] = "yes"
    assert "vision" not in sanitize(broken)["llm"]


def test_canonicalize_coerces_the_vision_model_and_keeps_the_account_only_while_needed():
    on = canon(_copilot_vision_profile(vision_model="gpt-9-bogus"))["llm"]
    assert on["provider"] == "github_copilot"
    assert on["model"] == "gpt-5.6-terra"
    assert on["vision"] == {"enabled": True, "model": "gpt-5.4"}
    assert on["ai_platform"] == {"auth": {"username": "u", "password": "pw", "usercase": "uc"}}
    assert on["api_key"] == "ghu_token"

    off = canon(_copilot_vision_profile(enabled=False))["llm"]
    assert off["vision"] == {"enabled": False, "model": "gpt-5.6-sol"}
    # Neither AI Platform chat nor image analysis needs the account: a stale one is dropped.
    assert "ai_platform" not in off

    # An AI Platform chat profile keeps its account without the vision block.
    chat = canon({"llm": {"provider": "ai_platform", "model": "gpt-5.4", "ai_platform": {"auth": {"username": "u", "password": "pw", "usercase": "uc"}}}})["llm"]
    assert chat["ai_platform"]["auth"]["usercase"] == "uc"
    assert "vision" not in chat


def test_materialize_injects_the_endpoints_for_image_analysis_on_a_copilot_profile():
    llm = materialize_ai_platform_llm_config(_copilot_vision_profile()["llm"], settings=_ai_settings())
    assert llm["provider"] == "github_copilot"
    assert llm["ai_platform"]["chat"]["host"] == "https://chat.int"
    assert llm["ai_platform"]["ib2b"]["uri"] == "/dsp/token"
    assert llm["ai_platform"]["auth"]["password"] == "pw"
    assert llm["ai_platform"]["auth"]["trust_token_header"] == "X-Trust"

    plain = materialize_ai_platform_llm_config(_copilot_vision_profile(enabled=False)["llm"], settings=_ai_settings())
    assert "ai_platform" not in plain


# --- projection ----------------------------------------------------------------


def test_native_projection_appends_the_image_analysis_instructions_when_configured():
    canonical = build_canonical_profile_config(_copilot_vision_profile(), settings=_ai_settings())
    assert canonical["llm"]["vision"] == {"enabled": True, "model": "gpt-5.6-sol"}
    assert canonical["llm"]["ai_platform"]["chat"]["host"] == "https://chat.int"

    native = project_canonical_for_runtime(canonical, "native")
    assert native["instruction_texts"] == [RUNTIME_PROFILE_IMAGE_ANALYSIS_INSTRUCTIONS]
    # No CLI connector is configured, so only the image text is added.
    assert RUNTIME_PROFILE_CLI_TOOL_INSTRUCTIONS not in native["instruction_texts"]
    assert "inspect-image inspect --image <path>" in RUNTIME_PROFILE_IMAGE_ANALYSIS_INSTRUCTIONS
    assert "Never pass --model" in RUNTIME_PROFILE_IMAGE_ANALYSIS_INSTRUCTIONS

    # OpenCode gets its instructions from the workspace files, as before.
    assert "instruction_texts" not in project_canonical_for_runtime(canonical, "opencode")


def test_native_projection_skips_the_image_analysis_instructions_when_it_cannot_run():
    off = build_canonical_profile_config(_copilot_vision_profile(enabled=False), settings=_ai_settings())
    assert "instruction_texts" not in project_canonical_for_runtime(off, "native")

    # Toggle on but no endpoints configured in this deployment: nothing to promise.
    no_endpoints = build_canonical_profile_config(
        _copilot_vision_profile(), settings=_ai_settings(ai_platform_chat_host="", ai_platform_ib2b_host="")
    )
    assert "instruction_texts" not in project_canonical_for_runtime(no_endpoints, "native")

    # An AI Platform chat profile reads images with inspect-image too.
    chat = build_canonical_profile_config(
        {"llm": {"provider": "ai_platform", "model": "gpt-5.4", "ai_platform": {"auth": {"username": "u", "password": "pw", "usercase": "uc"}}}},
        settings=_ai_settings(),
    )
    assert RUNTIME_PROFILE_IMAGE_ANALYSIS_INSTRUCTIONS in project_canonical_for_runtime(chat, "native")["instruction_texts"]


# --- forms ---------------------------------------------------------------------


def test_settings_merge_reads_vision_and_requires_the_account_for_image_analysis():
    merged, error = _settings_merge_payload(
        {},
        {
            "__touch_llm": "1",
            "llm_provider": "github_copilot",
            "llm_model": "gpt-5.6-terra",
            "llm_api_key": "ghu_token",
            "llm_vision_enabled": "on",
            "llm_vision_model": "gpt-5.6-sol",
            "llm_ai_platform_username": "u",
            "llm_ai_platform_password": "pw",
            "llm_ai_platform_usercase": "uc",
        },
    )
    assert error is None
    assert merged["llm"]["api_key"] == "ghu_token"
    assert merged["llm"]["vision"] == {"enabled": True, "model": "gpt-5.6-sol"}
    assert merged["llm"]["ai_platform"] == {"auth": {"username": "u", "password": "pw", "usercase": "uc"}}

    _merged, error = _settings_merge_payload(
        {},
        {
            "__touch_llm": "1",
            "llm_provider": "github_copilot",
            "llm_vision_enabled": "on",
            "llm_vision_model": "",
            "llm_ai_platform_username": "u",
            "llm_ai_platform_password": "pw",
            "llm_ai_platform_usercase": "",
        },
    )
    assert error == "AI Platform username, password, and usercase are required for image analysis."


def test_settings_merge_turning_vision_off_drops_the_account_a_copilot_profile_no_longer_needs():
    stored = _copilot_vision_profile()
    merged, error = _settings_merge_payload(
        stored,
        {
            "__touch_llm": "1",
            "llm_provider": "github_copilot",
            "llm_api_key": "ghu_token",
            # The checkbox is absent when unchecked; the select marks the controls as rendered.
            "llm_vision_model": "gpt-5.6-sol",
            "llm_ai_platform_username": "u",
            "llm_ai_platform_usercase": "uc",
        },
    )
    assert error is None
    assert merged["llm"]["vision"] == {"enabled": False, "model": "gpt-5.6-sol"}
    assert "ai_platform" not in merged["llm"]


def test_settings_merge_from_a_form_without_vision_controls_keeps_the_stored_block():
    stored = _copilot_vision_profile()
    merged, error = _settings_merge_payload(
        stored,
        {
            "__touch_llm": "1",
            "llm_provider": "github_copilot",
            "llm_api_key": "ghu_token",
            "llm_ai_platform_username": "u",
            "llm_ai_platform_usercase": "uc",
        },
    )
    assert error is None
    assert merged["llm"]["vision"] == {"enabled": True, "model": "gpt-5.6-sol"}
    # The stored password is kept when the field is left blank.
    assert merged["llm"]["ai_platform"]["auth"]["password"] == "pw"


def test_seed_form_reads_vision_and_the_shared_account_while_it_is_on():
    seeded = _seed_config_from_form(
        _form(
            {
                "llm_provider": "github_copilot",
                "llm_api_key": "shared-key",
                "llm_vision_enabled": "on",
                "llm_vision_model": "gpt-5.6-luna",
                "llm_ai_platform_username": "svc",
                "llm_ai_platform_password": "svc-pw",
                "llm_ai_platform_usercase": "svc-uc",
            }
        )
    )
    assert seeded["llm"]["api_key"] == "shared-key"
    assert seeded["llm"]["vision"] == {"enabled": True, "model": "gpt-5.6-luna"}
    assert seeded["llm"]["ai_platform"]["auth"] == {"username": "svc", "password": "svc-pw", "usercase": "svc-uc"}

    # Off: the hidden AI Platform fields are not read, and no vision block is stored.
    plain = _seed_config_from_form(
        _form({"llm_provider": "github_copilot", "llm_api_key": "shared-key", "llm_ai_platform_password": "svc-pw"})
    )
    assert "vision" not in plain["llm"]
    assert "ai_platform" not in plain["llm"]


# --- smoke test ----------------------------------------------------------------


def test_image_analysis_smoke_sends_one_image_to_chat_completions(monkeypatch):
    svc = RuntimeProfileTestService(_ai_settings(ai_platform_responses_uri="/v1/responses"))
    calls = []

    async def _fake_http_json_request(*, method, url, headers, payload, timeout):
        calls.append({"url": url, "headers": headers, "payload": payload})
        if "ib2b" in url:
            return True, "ok", {"issued_token": "JWT-123"}
        return True, "ok", {"choices": [{"message": {"content": "pong"}}]}

    monkeypatch.setattr(svc, "_http_json_request", _fake_http_json_request)
    ok, message = asyncio.run(svc.run_test("image_analysis", _copilot_vision_profile(), runtime_type="native"))
    assert ok is True
    assert message == "Image analysis smoke test OK: ai_platform/gpt-5.6-sol."
    assert len(calls) == 2
    # inspect-image speaks chat/completions, so the smoke test does too even when
    # a Responses path is configured for chat.
    assert calls[1]["url"] == "https://chat.int/v1/api/v1/chat/completions"
    assert calls[1]["headers"]["X-Trust"] == "JWT-123"
    content = calls[1]["payload"]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "ping"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert calls[1]["payload"]["model"] == "gpt-5.6-sol"
    assert calls[1]["payload"]["user"] == "uc"


def test_image_analysis_smoke_needs_the_toggle_or_the_ai_platform_provider():
    svc = RuntimeProfileTestService(_ai_settings())
    ok, message = asyncio.run(svc.run_test("image_analysis", _copilot_vision_profile(enabled=False)))
    assert ok is False
    assert "Turn on Image analysis" in message


# --- browser -------------------------------------------------------------------


def test_the_ai_platform_fields_follow_the_image_analysis_toggle():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping connector form behavior test")

    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    functions = "\n".join(
        _extract_js_function(js, name)
        for name in ("visionAnalysisEnabled", "updateVisionModelOptions", "updateModelOptions")
    )
    script = f"""
const managedProviderModels = {{
  github_copilot: [{{ value: "gpt-5.6-terra", label: "Terra" }}],
  ai_platform: [{{ value: "gpt-5.4", label: "GPT-5.4" }}, {{ value: "gpt-5.6-sol", label: "Sol" }}],
}};
function updateCopilotAuthCardsVisibility() {{}}
function stopCopilotPolling() {{}}
function updateTemperatureInputState() {{}}
function makeSelect(value) {{
  const select = {{ value, options: [], dataset: {{}}, innerHTML: "" }};
  Object.defineProperty(select, "innerHTML", {{ set(v) {{ select.options = []; }}, get() {{ return ""; }} }});
  select.appendChild = (option) => {{ select.options.push(option); }};
  return select;
}}
const document = {{ createElement: () => ({{}}) }};
function makeBlock(attr, value) {{
  const classes = new Set(["hidden"]);
  return {{
    getAttribute: (name) => (name === attr ? value : null),
    classList: {{ toggle: (name, force) => {{ if (force) classes.add(name); else classes.delete(name); }}, has: (name) => classes.has(name) }},
  }};
}}
const copilotBlock = makeBlock("data-provider-fields", "github_copilot");
const aiBlock = makeBlock("data-provider-fields", "ai_platform");
const visionBlock = makeBlock("data-vision-fields", "");
const provider = {{ value: "github_copilot", dataset: {{}} }};
const model = makeSelect("gpt-5.6-terra");
const visionModel = makeSelect("");
visionModel.dataset.currentValue = "gpt-5.6-sol";
const toggle = {{ checked: true }};
const root = {{
  querySelector(sel) {{
    if (sel === "#llm_provider") return provider;
    if (sel === "#llm_model") return model;
    if (sel === "#llm_vision_model") return visionModel;
    if (sel === "#llm_vision_enabled") return toggle;
    return null;
  }},
  querySelectorAll(sel) {{
    if (sel === "[data-provider-fields]") return [copilotBlock, aiBlock];
    if (sel === "[data-vision-fields]") return [visionBlock];
    return [];
  }},
}};
{functions}
updateModelOptions(root);
const on = {{
  copilotHidden: copilotBlock.classList.has("hidden"),
  aiHidden: aiBlock.classList.has("hidden"),
  visionHidden: visionBlock.classList.has("hidden"),
  visionOptions: visionModel.options.map((o) => o.value),
  visionValue: visionModel.value,
}};
toggle.checked = false;
updateModelOptions(root);
const off = {{
  copilotHidden: copilotBlock.classList.has("hidden"),
  aiHidden: aiBlock.classList.has("hidden"),
  visionHidden: visionBlock.classList.has("hidden"),
}};
console.log(JSON.stringify({{ on, off }}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    result = json.loads(completed.stdout.strip())
    # On: the Copilot key stays (chat provider) and the AI Platform account appears (image analysis).
    assert result["on"] == {
        "copilotHidden": False,
        "aiHidden": False,
        "visionHidden": False,
        "visionOptions": ["", "gpt-5.4", "gpt-5.6-sol"],
        "visionValue": "gpt-5.6-sol",
    }
    # Off: back to provider-only visibility.
    assert result["off"] == {"copilotHidden": False, "aiHidden": True, "visionHidden": True}
