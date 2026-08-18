"""AI Platform provider credentials plus centrally managed connection config."""
from types import SimpleNamespace

from app.contracts.llm_catalog import (
    coerce_to_provider_model,
    normalize_provider,
)
from app.schemas.runtime_profile import sanitize_runtime_profile_config_dict as sanitize
from app.services.runtime_profile_config_policy import (
    canonicalize_portal_runtime_profile_config as canon,
)
from app.services.runtime_profile_context_projection import (
    build_canonical_profile_config,
    project_canonical_for_runtime,
)
from app.services.runtime_profile_service import RuntimeProfileService


def _ai_profile(model="gpt-5.4"):
    return {
        "llm": {
            "provider": "ai-platform",
            "model": model,
            "ai_platform": {
                "auth": {
                    "username": "u",
                    "password": "pw",
                    "usercase": "uc",
                },
            },
        }
    }


def _ai_settings():
    return SimpleNamespace(
        ai_platform_chat_host="https://chat.int",
        ai_platform_chat_uri="/v1/api/v1/chat/completions",
        ai_platform_ib2b_host="https://ib2b.int",
        ai_platform_ib2b_uri="/dsp/token",
        ai_platform_trust_token_header="X-Trust",
        ai_platform_tracking_prefix="EFP",
    )


def test_normalize_provider_whitelist():
    assert normalize_provider("ai-platform") == "ai_platform"
    assert normalize_provider("ai_platform") == "ai_platform"
    assert normalize_provider("copilot") == "github_copilot"
    assert normalize_provider("openai") == "github_copilot"  # unknown -> default
    assert normalize_provider("") == "github_copilot"


def test_coerce_model_is_provider_aware():
    assert coerce_to_provider_model("ai_platform", "gpt-9-bogus") == "gpt-5.4"
    assert coerce_to_provider_model("ai_platform", "gpt-5.4") == "gpt-5.4"
    assert coerce_to_provider_model("github_copilot", "gpt-5.4") == "gpt-5.4"
    assert coerce_to_provider_model("github_copilot", "bogus") == "gpt-5.6-terra"


def test_managed_models_per_provider():
    assert RuntimeProfileService.managed_model_values_for_provider("ai_platform") == ("gpt-5.4",)
    assert "gpt-5.6-terra" in RuntimeProfileService.managed_model_values_for_provider("github_copilot")
    assert RuntimeProfileService.normalize_managed_llm_provider("ai-platform") == "ai_platform"
    assert RuntimeProfileService.normalize_managed_llm_provider("") == ""


def test_canonicalize_keeps_only_ai_platform_profile_credentials_and_coerces_model():
    c = canon(_ai_profile(model="gpt-9-bogus"))["llm"]
    assert c["provider"] == "ai_platform"
    assert c["model"] == "gpt-5.4"
    assert c["ai_platform"] == {"auth": {"username": "u", "password": "pw", "usercase": "uc"}}


def test_sanitize_keeps_credentials_and_drops_profile_managed_connection_fields():
    profile = _ai_profile()
    profile["llm"]["ai_platform"].update(
        {
            "chat": {"host": "https://profile-chat.invalid"},
            "ib2b": {"host": "https://profile-ib2b.invalid"},
        }
    )
    profile["llm"]["ai_platform"]["auth"].update(
        {"trust_token_header": "X-Profile-Trust", "tracking_prefix": "PROFILE"}
    )
    s = sanitize(profile)["llm"]
    assert s["ai_platform"]["auth"]["username"] == "u"
    assert "chat" not in s["ai_platform"]
    assert "ib2b" not in s["ai_platform"]
    assert "trust_token_header" not in s["ai_platform"]["auth"]
    assert "tracking_prefix" not in s["ai_platform"]["auth"]
    # a junk nested key is filtered out by the field tree
    junk = _ai_profile()
    junk["llm"]["ai_platform"]["auth"]["evil"] = "x"
    assert "evil" not in sanitize(junk)["llm"]["ai_platform"]["auth"]


def test_projection_maps_ai_platform_and_preserves_block():
    canonical = build_canonical_profile_config(_ai_profile(), settings=_ai_settings())
    nat = project_canonical_for_runtime(canonical, "native")["llm"]
    oc = project_canonical_for_runtime(canonical, "opencode")["llm"]
    assert nat["provider"] == "ai_platform" and nat["model"] == "gpt-5.4"
    assert oc["provider"] == "ai-platform" and oc["model"] == "ai-platform/gpt-5.4"
    assert nat["ai_platform"]["chat"]["host"] == "https://chat.int"
    assert nat["ai_platform"]["ib2b"]["uri"] == "/dsp/token"
    assert nat["ai_platform"]["auth"]["trust_token_header"] == "X-Trust"
    assert nat["ai_platform"]["auth"]["password"] == "pw"
    assert oc["ai_platform"]["auth"]["password"] == "pw"


def test_public_redaction_hides_ai_platform_password_and_token():
    from app.schemas.runtime_profile import (
        redact_runtime_profile_config_for_public_response as redact,
    )

    prof = _ai_profile()
    prof["llm"]["ai_platform"]["auth"]["token"] = "JWT-secret"
    auth = redact(prof)["llm"]["ai_platform"]["auth"]
    assert "password" not in auth and auth["password_present"] is True
    assert "token" not in auth and auth["token_present"] is True
    assert auth["username"] == "u"  # non-secret preserved


def test_legacy_and_copilot_still_coerce_to_copilot():
    c = canon({"llm": {"provider": "openai", "model": "gpt-4o", "api_key": "sk"}})["llm"]
    assert c["provider"] == "github_copilot"
    assert c["model"] == "gpt-5.6-terra"
    assert c["api_key"] == "sk"
