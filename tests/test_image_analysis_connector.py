"""The Image analysis connector: inspect-image on AI Platform, a connector of its own.

The chat model is treated as having no vision, whichever provider it is, so
images are read by the inspect-image CLI through AI Platform. Unlike the first
version (a block inside the Model provider connector, ``llm.vision``), this is
a settings connector with its own config section ``image_analysis``: a switch,
a model and an AI Platform account, kept whatever ``llm.provider`` is, managed
like every other connector (a member follows the Default connectors or keeps
their own values). The names, the canonical shape, the refused-save message and
the instruction text are shared with the native runtime; the tests below pin
them.
"""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.datastructures import FormData

from app.config import Settings
from app.schemas.runtime_profile import (
    ALLOWED_RUNTIME_PROFILE_SECTIONS,
    PORTAL_MANAGED_FIELD_TREE,
    PROXY_ASSIGNABLE_CONNECTORS,
    PROXY_DEFAULT_ONLY_CONNECTORS,
    redact_runtime_profile_config_for_public_response,
    sanitize_runtime_profile_config_dict as sanitize,
    sanitize_runtime_profile_proxy,
)
from app.services.ai_platform_config import (
    IMAGE_ANALYSIS_SECTION,
    image_analysis_configured,
    materialize_image_analysis_config,
)
from app.services.connection_guidance import CONNECTION_GUIDANCE
from app.services.connector_defaults_service import MODE_SYSTEM, connector_mode, effective_config, managed_specs
from app.services.connector_registry import (
    PROXY_CHOICE_SELECTABLE,
    SETTINGS_CONNECTORS,
    STATE_CONNECTED,
    STATE_NOT_SET_UP,
    STATE_OFF,
    get_connector_spec,
    list_connector_specs,
)
from app.services.help_center import get_topic
from app.services.profile_secret_encryption import ENC_PREFIX, encrypt_sensitive_fields
from app.services.runtime_profile_config_policy import canonicalize_portal_runtime_profile_config as canon
from app.services.runtime_profile_context_projection import (
    PORTAL_RUNTIME_PROFILE_SECTIONS,
    RUNTIME_PROFILE_CLI_TOOL_INSTRUCTIONS,
    RUNTIME_PROFILE_IMAGE_ANALYSIS_INSTRUCTIONS,
    _has_image_analysis_config,
    build_canonical_profile_config,
    project_canonical_for_runtime,
    runtime_profile_managed_sections,
)
from app.services.runtime_profile_references import (
    IMAGE_ANALYSIS_ACCOUNT_REQUIRED,
    image_analysis_account_error,
    seed_config_error,
)
from app.services.runtime_profile_seed_service import RuntimeProfileSeedService, redact_seed_for_display
from app.services.runtime_profile_service import RuntimeProfileService
from app.services.runtime_profile_test_service import RuntimeProfileTestService
from app.services.usage_insights_service import CONNECTOR_SECTIONS
from app.web import _MANAGED_TEST_TARGETS, _seed_config_from_form, _settings_merge_payload, _settings_view_payload
from tests.test_default_connections_form import _database, _panel_html as _default_connections_html
from tests.test_web_connector_settings import _bind_profile, _build_env, _saved

REQUIRED_MESSAGE = "AI Platform username, password, and usercase are required for image analysis."
ACCOUNT = {"username": "u", "password": "pw", "usercase": "uc"}

# Section 5 of the shared contract, byte for byte: the native runtime holds the
# same string under the same name.
CONTRACT_INSTRUCTIONS = (
    "Images are read through the inspect-image CLI on AI Platform (the Image analysis connector); the chat model "
    "cannot see an image unless the prompt says it was attached as image content. For a screenshot, photo, diagram, "
    "or any image file in the workspace or listed in an Attached files block, run `inspect-image inspect --image "
    '<path> --prompt "<what to read or explain>" --json` from bash and answer from data.result.answer and '
    "data.result.visible_text; add --preset ocr, ui, diagram, chart, or error when it fits, and ask a follow-up "
    "question with another call. Never pass --model (the model comes from the connector), never run inspect-image "
    "auth login, never guess what an image shows, and never fall back to OCR or image-parsing scripts. If "
    "inspect-image returns auth_required or config_error, or still fails after one retry, use the attached-files "
    "analysis skill (Productivity Suite through the Local bridge) when the Local bridge is on for this chat (the "
    "browser tool is available); otherwise tell the user the image could not be analyzed and that the Image "
    "analysis connector under Portal > Connectors needs checking."
)


def _section(enabled=True, model="gpt-5.6-sol", auth=ACCOUNT):
    section = {"enabled": enabled, "model": model}
    if auth:
        section["ai_platform"] = {"auth": dict(auth)}
    return section


def _copilot_profile(**section_kwargs):
    """A GitHub Copilot member whose assistants read images through AI Platform."""
    return {
        "llm": {"provider": "github_copilot", "model": "gpt-6-luna", "api_key": "ghu_token"},
        "image_analysis": _section(**section_kwargs),
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


def _member_form(**fields):
    """What the member's panel posts once a field of the section was changed."""
    posted = {
        "__touch_image_analysis": "1",
        "image_analysis_enabled": "on",
        "image_analysis_model": "gpt-5.6-sol",
        "image_analysis_ai_platform_username": "u",
        "image_analysis_ai_platform_password": "pw",
        "image_analysis_ai_platform_usercase": "uc",
    }
    posted.update(fields)
    return {key: value for key, value in posted.items() if value is not None}


# --- registry ----------------------------------------------------------------


def test_the_connector_is_registered_next_to_the_model_provider():
    spec = get_connector_spec("image_analysis")
    assert (spec.type, spec.label, spec.category, spec.kind) == ("image_analysis", "Image analysis", "Model", "settings")
    assert spec.description and spec.description.count(".") == 1
    assert spec.panel_template == "partials/connectors/image_analysis.html"
    assert spec.config_sections == spec.form_sections == spec.managed_sections == ("image_analysis",)
    assert spec.test_targets == ("image_analysis",) and spec.guidance_key == "image_analysis"
    assert IMAGE_ANALYSIS_SECTION == "image_analysis"
    # The list shows a category's connectors in registry order: right after the model provider.
    types = [item.type for item in list_connector_specs()]
    assert types.index("image_analysis") == types.index("llm") + 1
    assert [item.type for item in SETTINGS_CONNECTORS][:2] == ["llm", "image_analysis"]
    # Unlike the model provider it follows the Default connectors: that is how most members get it.
    assert spec in managed_specs()
    assert connector_mode(spec, {}) == MODE_SYSTEM
    assert not spec.platform_only


def test_state_is_on_only_with_the_switch_on_and_a_password_stored():
    state_of = get_connector_spec("image_analysis").state_of
    assert state_of({"image_analysis": _section()}) == STATE_CONNECTED
    assert state_of({"image_analysis": _section(enabled=False)}) == STATE_OFF
    assert state_of({"image_analysis": _section(auth={"username": "u", "usercase": "uc"})}) == STATE_NOT_SET_UP
    assert state_of({"image_analysis": {"enabled": True}}) == STATE_NOT_SET_UP
    assert state_of({}) == STATE_NOT_SET_UP
    # The model provider's own AI Platform account says nothing about this connector.
    assert state_of({"llm": {"provider": "ai_platform", "ai_platform": {"auth": dict(ACCOUNT)}}}) == STATE_NOT_SET_UP


def test_the_proxy_connector_offers_it_a_proxy_of_its_own():
    assert "image_analysis" in PROXY_ASSIGNABLE_CONNECTORS and "image_analysis" not in PROXY_DEFAULT_ONLY_CONNECTORS
    assert get_connector_spec("image_analysis").proxy_choice == PROXY_CHOICE_SELECTABLE
    proxy = sanitize_runtime_profile_proxy(
        {
            "enabled": True,
            "proxies": [{"name": "corp-a", "url": "http://a.example.test:1"}, {"name": "corp-b", "url": "http://b.example.test:1"}],
            "assignments": {"image_analysis": "corp-b"},
        }
    )
    assert proxy["assignments"] == {"image_analysis": "corp-b"}
    rows = {row["type"]: row for row in _settings_view_payload({"proxy": proxy})["proxy_connectors"]}
    assert rows["image_analysis"]["label"] == "Image analysis" and rows["image_analysis"]["current"] == "corp-b"


# --- persistence -------------------------------------------------------------


def test_the_section_is_a_portal_managed_section_with_only_the_switch_model_and_account():
    assert "image_analysis" in ALLOWED_RUNTIME_PROFILE_SECTIONS
    assert "image_analysis" in PORTAL_RUNTIME_PROFILE_SECTIONS
    assert "image_analysis" in runtime_profile_managed_sections("native")
    assert PORTAL_MANAGED_FIELD_TREE["image_analysis"] == {
        "enabled": True,
        "model": True,
        "ai_platform": {"auth": {"username": True, "password": True, "usercase": True}},
    }


def test_sanitize_keeps_the_section_on_a_copilot_profile_and_coerces_the_model():
    profile = _copilot_profile()
    profile["image_analysis"].update({"enabled": "on", "provider": "ai_platform", "evil": "x"})
    profile["image_analysis"]["ai_platform"].update({"chat": {"host": "https://stored.invalid"}, "ib2b": {"host": "https://x"}})
    profile["image_analysis"]["ai_platform"]["auth"].update({"username": "  u  ", "token": "t", "trust_token_header": "X"})
    cleaned = sanitize(profile)
    # Only the switch, the model and the account are stored: no provider, no endpoints.
    assert cleaned["image_analysis"] == {"enabled": True, "model": "gpt-5.6-sol", "ai_platform": {"auth": ACCOUNT}}
    assert cleaned["llm"] == {"provider": "github_copilot", "model": "gpt-6-luna", "api_key": "ghu_token"}
    # Nothing of the section leaks into llm, and canonicalizing (every save) keeps it as it is.
    assert "ai_platform" not in cleaned["llm"] and "vision" not in cleaned["llm"]
    assert canon(cleaned)["image_analysis"] == cleaned["image_analysis"]

    # The model is always an AI Platform catalogue model: a Copilot id, junk and blank become the default.
    for model in ("gpt-6-sol", "auto", "gpt-9-bogus", "", None):
        assert sanitize({"image_analysis": {"enabled": True, "model": model}})["image_analysis"]["model"] == "gpt-5.4"
    assert sanitize({"image_analysis": {"enabled": True, "model": " gpt-5.6-terra "}})["image_analysis"]["model"] == "gpt-5.6-terra"


def test_sanitize_normalizes_the_switch_and_drops_a_section_that_says_nothing():
    assert sanitize({"image_analysis": {"enabled": "off"}})["image_analysis"] == {"enabled": False, "model": "gpt-5.4"}
    # A blank account field is left out rather than stored empty.
    partial = sanitize({"image_analysis": {"enabled": False, "ai_platform": {"auth": {"username": "u", "password": " "}}}})
    assert partial["image_analysis"] == {"enabled": False, "model": "gpt-5.4", "ai_platform": {"auth": {"username": "u"}}}
    for nothing in ({}, "yes", None, ["x"], {"ai_platform": {"auth": {}}}, {"unknown": 1}):
        assert "image_analysis" not in sanitize({"image_analysis": nothing})


def test_the_section_does_not_depend_on_the_chat_provider():
    for llm in (
        {"provider": "github_copilot", "model": "auto"},
        {"provider": "ai_platform", "model": "gpt-5.4", "ai_platform": {"auth": {"username": "chat", "password": "cpw", "usercase": "cuc"}}},
        None,
    ):
        profile = {"image_analysis": _section()}
        if llm:
            profile["llm"] = llm
        stored = canon(sanitize(profile))
        assert stored["image_analysis"] == _section()
        canonical = build_canonical_profile_config(stored, settings=_ai_settings())
        # Its account is its own: the chat provider's is never copied in, nor the other way round.
        assert canonical["image_analysis"]["ai_platform"]["auth"]["username"] == "u"
        if llm and llm["provider"] == "ai_platform":
            assert canonical["llm"]["ai_platform"]["auth"]["username"] == "chat"
        elif llm:
            assert "ai_platform" not in canonical["llm"]


def test_public_redaction_reports_the_password_without_returning_it():
    redacted = redact_runtime_profile_config_for_public_response(_copilot_profile())
    assert redacted["image_analysis"] == {
        "enabled": True,
        "model": "gpt-5.6-sol",
        "ai_platform": {"auth": {"username": "u", "usercase": "uc", "password_present": True}},
    }
    assert "pw" not in json.dumps(redacted["image_analysis"])
    # The flag is there with no account too, so a client never has to guess.
    bare = redact_runtime_profile_config_for_public_response({"image_analysis": {"enabled": False, "model": "gpt-5.4"}})
    assert bare["image_analysis"]["ai_platform"]["auth"] == {"password_present": False}
    # The input is not changed.
    profile = _copilot_profile()
    redact_runtime_profile_config_for_public_response(profile)
    assert profile["image_analysis"]["ai_platform"]["auth"]["password"] == "pw"


def test_the_seed_dump_masks_the_shared_password():
    shown = redact_seed_for_display({"image_analysis": _section()})
    assert shown["image_analysis"]["ai_platform"]["auth"]["password"] == "[REDACTED]"
    assert shown["image_analysis"]["ai_platform"]["auth"]["username"] == "u"


# --- the member's save ---------------------------------------------------------


def test_settings_merge_stores_the_section_from_the_form():
    merged, error = _settings_merge_payload({"llm": {"provider": "github_copilot", "api_key": "ghu_token"}}, _member_form())
    assert error is None
    assert merged["image_analysis"] == _section()
    # The model provider is not touched by this connector's save.
    assert merged["llm"]["api_key"] == "ghu_token" and "ai_platform" not in merged["llm"]

    coerced, error = _settings_merge_payload({}, _member_form(image_analysis_model="gpt-6-sol"))
    assert error is None and coerced["image_analysis"]["model"] == "gpt-5.4"


@pytest.mark.parametrize(
    "missing",
    ["image_analysis_ai_platform_username", "image_analysis_ai_platform_password", "image_analysis_ai_platform_usercase"],
)
def test_settings_merge_refuses_the_connector_on_without_its_whole_account(missing):
    stored = {"llm": {"provider": "github_copilot"}}
    merged, error = _settings_merge_payload(stored, _member_form(**{missing: " "}))
    assert error == REQUIRED_MESSAGE == IMAGE_ANALYSIS_ACCOUNT_REQUIRED
    # Nothing of the refused form is kept.
    assert merged == stored
    # A posted blank also clears a stored value: the panel shows the stored password, so blank means emptied.
    _merged, error = _settings_merge_payload({"image_analysis": _section()}, _member_form(**{missing: ""}))
    assert error == REQUIRED_MESSAGE


def test_settings_merge_off_needs_no_account_and_keeps_what_was_typed():
    merged, error = _settings_merge_payload(
        {},
        _member_form(image_analysis_enabled=None, image_analysis_ai_platform_password="", image_analysis_ai_platform_usercase=""),
    )
    assert error is None
    assert merged["image_analysis"] == {"enabled": False, "model": "gpt-5.6-sol", "ai_platform": {"auth": {"username": "u"}}}

    # Turning it off keeps the stored account for the day it is turned on again.
    off, error = _settings_merge_payload({"image_analysis": _section()}, _member_form(image_analysis_enabled=None))
    assert error is None and off["image_analysis"] == _section(enabled=False)


def test_settings_merge_without_the_touch_flag_keeps_the_stored_block():
    stored = _copilot_profile()
    # Another connector's save, or a page that never changed this section: the fields are ignored.
    merged, error = _settings_merge_payload(
        stored,
        _member_form(
            __touch_image_analysis="0",
            image_analysis_enabled=None,
            image_analysis_model="gpt-5.4",
            image_analysis_ai_platform_username="someone-else",
            image_analysis_ai_platform_password="",
        ),
    )
    assert error is None
    assert merged["image_analysis"] == _section()
    untouched, error = _settings_merge_payload(stored, {"__touch_llm": "1", "llm_provider": "github_copilot", "llm_api_key": "ghu_token"})
    assert error is None and untouched["image_analysis"] == _section()


def test_settings_merge_keeps_what_a_partial_form_did_not_post():
    # An API client that only flips the switch: the model and the account stay as stored.
    merged, error = _settings_merge_payload(
        {"image_analysis": _section()}, {"__touch_image_analysis": "1", "image_analysis_enabled": "on"}
    )
    assert error is None and merged["image_analysis"] == _section()
    # With nothing stored, the same post is refused: there is no account to sign in with.
    _merged, error = _settings_merge_payload({}, {"__touch_image_analysis": "1", "image_analysis_enabled": "on"})
    assert error == REQUIRED_MESSAGE


def test_the_account_rule_is_the_same_wherever_a_config_is_checked():
    assert image_analysis_account_error(_section()) is None
    assert image_analysis_account_error(_section(enabled=False, auth=None)) is None
    assert image_analysis_account_error(None) is None
    assert image_analysis_account_error(_section(auth={"username": "u", "password": "pw"})) == REQUIRED_MESSAGE
    assert image_analysis_account_error({"enabled": True}) == REQUIRED_MESSAGE
    # The Default connectors are refused the same way: members who follow them would receive them as they are.
    assert seed_config_error({"image_analysis": {"enabled": True, "model": "gpt-5.4"}}) == REQUIRED_MESSAGE
    assert seed_config_error({"image_analysis": _section()}) is None
    assert seed_config_error({"image_analysis": _section(enabled=False, auth={"username": "u"})}) is None


# --- the Default connectors form ----------------------------------------------


def test_seed_form_reads_the_switch_the_model_and_the_shared_account():
    seeded = _seed_config_from_form(
        _form(
            {
                "llm_provider": "github_copilot",
                "llm_api_key": "shared-key",
                "image_analysis_enabled": "on",
                "image_analysis_model": "gpt-5.6-luna",
                "image_analysis_ai_platform_username": "svc",
                "image_analysis_ai_platform_password": "svc-pw",
                "image_analysis_ai_platform_usercase": "svc-uc",
            }
        )
    )
    assert seeded["image_analysis"] == {
        "enabled": True,
        "model": "gpt-5.6-luna",
        "ai_platform": {"auth": {"username": "svc", "password": "svc-pw", "usercase": "svc-uc"}},
    }
    # The model provider's seed is its own block; the two accounts never mix.
    assert seeded["llm"] == {"provider": "github_copilot", "api_key": "shared-key"}

    # The model select always posts a value; alone it does not make a section.
    assert _seed_config_from_form(_form({"image_analysis_model": "gpt-5.4"})) == {}
    # An account without the switch is kept (offered later), a blank field left out, a Copilot model repaired.
    parked = _seed_config_from_form(
        _form({"image_analysis_model": "gpt-6-sol", "image_analysis_ai_platform_username": "svc", "image_analysis_ai_platform_password": " "})
    )
    assert parked == {"image_analysis": {"enabled": False, "model": "gpt-5.4", "ai_platform": {"auth": {"username": "svc"}}}}
    # Offered without an account is what the save then refuses.
    assert seed_config_error(_seed_config_from_form(_form({"image_analysis_enabled": "on"}))) == REQUIRED_MESSAGE


def test_the_default_connectors_panel_renders_the_block_and_the_stored_values():
    blank = _default_connections_html()
    assert "<h6>Image analysis</h6>" in blank
    for name in (
        "image_analysis_enabled",
        "image_analysis_model",
        "image_analysis_ai_platform_username",
        "image_analysis_ai_platform_password",
        "image_analysis_ai_platform_usercase",
    ):
        assert f'name="{name}"' in blank, name
    assert 'type="password" name="image_analysis_ai_platform_password"' in blank
    # The AI Platform catalogue, the default preselected while nothing is seeded.
    assert '<option value="gpt-5.4" selected>gpt-5.4</option>' in blank
    assert '<option value="gpt-5.6-sol" >gpt-5.6-sol</option>' in blank
    assert '<option value="gpt-6-sol"' not in blank.split('name="image_analysis_model"', 1)[1].split("</select>", 1)[0]
    # No provider and no endpoint field: AI Platform is the only provider, the endpoints are the deployment's.
    assert 'name="image_analysis_provider"' not in blank and "image_analysis_ai_platform_chat" not in blank

    seeded = _default_connections_html(seed={"image_analysis": _section(auth={"username": "svc", "password": "svc-pw", "usercase": "svc-uc"})})
    assert 'name="image_analysis_enabled" checked' in seeded
    assert 'name="image_analysis_ai_platform_username" value="svc"' in seeded
    assert 'name="image_analysis_ai_platform_password" value="svc-pw"' in seeded
    assert 'name="image_analysis_ai_platform_usercase" value="svc-uc"' in seeded
    assert '<option value="gpt-5.6-sol" selected>gpt-5.6-sol</option>' in seeded
    # The raw dump under the form never shows the password.
    assert "svc-pw" not in seeded.split('<pre class="portal-panel-pre">', 1)[1]


def test_members_follow_the_seeded_account_until_they_customize():
    db = _database()
    seed = {"image_analysis": _section(auth={"username": "svc", "password": "svc-pw", "usercase": "svc-uc"})}
    assert RuntimeProfileSeedService(db).save_seed(seed)["image_analysis"] == seed["image_analysis"]
    assert RuntimeProfileSeedService(db).get_seed() == seed

    # Nothing in the row, the connector in system mode: the assistants get the Default connectors' section.
    followed = effective_config({"llm": {"provider": "github_copilot"}}, {}, seed)
    assert followed["image_analysis"] == seed["image_analysis"]
    # Customized, the member's own values win.
    own = effective_config({"image_analysis": _section()}, {"image_analysis": "custom"}, seed)
    assert own["image_analysis"] == _section()
    # A new member's first row holds no copy of it (only what no mode governs is copied).
    from app.models import User

    user = User(username="newcomer", password_hash="test", role="user", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    service = RuntimeProfileService(db)
    profile = service.get_or_create_for_user(user)
    assert "image_analysis" not in json.loads(profile.config_json)
    assert service.effective_config_for(profile)["image_analysis"] == seed["image_analysis"]


# --- materialization -----------------------------------------------------------


def test_materialize_adds_the_deployment_endpoints_and_never_the_responses_endpoint():
    settings = _ai_settings(ai_platform_responses_uri="/v1/{usercase}/responses")
    section = materialize_image_analysis_config(_section(), settings=settings)
    assert section == {
        "enabled": True,
        "model": "gpt-5.6-sol",
        "ai_platform": {
            "chat": {"host": "https://chat.int", "uri": "/v1/api/v1/chat/completions"},
            "ib2b": {"host": "https://ib2b.int", "uri": "/dsp/token"},
            "auth": {"username": "u", "password": "pw", "usercase": "uc", "trust_token_header": "X-Trust", "tracking_prefix": "EFP"},
        },
    }
    assert "responses" not in section["ai_platform"]
    assert image_analysis_configured(section)

    # Endpoints a section tries to carry itself are ignored; a Copilot or junk model becomes the default.
    stored = _section(model="gpt-6-sol")
    stored["ai_platform"].update({"chat": {"host": "https://stored.invalid"}, "responses": {"uri": "/x"}})
    forced = materialize_image_analysis_config(stored, settings=settings)
    assert forced["model"] == "gpt-5.4"
    assert forced["ai_platform"]["chat"] == {"host": "https://chat.int", "uri": "/v1/api/v1/chat/completions"}
    assert "responses" not in forced["ai_platform"]
    # The input is not changed.
    assert stored["ai_platform"]["chat"] == {"host": "https://stored.invalid"}

    # A chat path that routes by use case is filled from the account, as the model provider's is.
    routed = materialize_image_analysis_config(_section(), settings=_ai_settings(ai_platform_chat_uri="/v1/{usercase}/chat/completions"))
    assert routed["ai_platform"]["chat"]["uri"] == "/v1/uc/chat/completions"


def test_materialize_is_none_when_the_connector_is_off():
    settings = _ai_settings()
    for off in (None, {}, "yes", _section(enabled=False), {"model": "gpt-5.4", "ai_platform": {"auth": ACCOUNT}}):
        assert materialize_image_analysis_config(off, settings=settings) is None
    # The switch as a form or stored JSON spells it.
    assert materialize_image_analysis_config(_section(enabled="on"), settings=settings)["enabled"] is True
    assert materialize_image_analysis_config(_section(enabled="false"), settings=settings) is None


def test_configured_needs_the_switch_the_whole_account_and_both_hosts():
    settings = _ai_settings()
    assert image_analysis_configured(materialize_image_analysis_config(_section(), settings=settings))
    # On, but something is missing: the section still reaches the runtime, which reports why.
    for key in ("username", "password", "usercase"):
        auth = {name: value for name, value in ACCOUNT.items() if name != key}
        partial = materialize_image_analysis_config(_section(auth=auth), settings=settings)
        assert partial is not None and not image_analysis_configured(partial), key
    for missing in ({"ai_platform_chat_host": ""}, {"ai_platform_ib2b_host": ""}):
        unreachable = materialize_image_analysis_config(_section(), settings=_ai_settings(**missing))
        assert unreachable is not None and not image_analysis_configured(unreachable), missing
    # A stored section carries no endpoints, so it is never "configured" before it is materialized.
    assert not image_analysis_configured(_section())
    assert not image_analysis_configured(None) and not image_analysis_configured("on")
    off = dict(materialize_image_analysis_config(_section(), settings=settings), enabled=False)
    assert not image_analysis_configured(off)


# --- projection ----------------------------------------------------------------


def test_the_canonical_config_carries_the_section_only_when_it_is_on():
    canonical = build_canonical_profile_config(_copilot_profile(), settings=_ai_settings(ai_platform_responses_uri="/v1/responses"))
    assert canonical["image_analysis"] == {
        "enabled": True,
        "model": "gpt-5.6-sol",
        "ai_platform": {
            "chat": {"host": "https://chat.int", "uri": "/v1/api/v1/chat/completions"},
            "ib2b": {"host": "https://ib2b.int", "uri": "/dsp/token"},
            "auth": {"username": "u", "password": "pw", "usercase": "uc", "trust_token_header": "X-Trust", "tracking_prefix": "EFP"},
        },
    }
    # The Copilot chat profile is as it was: no AI Platform block of its own.
    assert canonical["llm"]["provider"] == "github_copilot" and "ai_platform" not in canonical["llm"]

    off = build_canonical_profile_config(_copilot_profile(enabled=False), settings=_ai_settings())
    assert "image_analysis" not in off
    assert "image_analysis" not in build_canonical_profile_config({"llm": {"provider": "github_copilot"}}, settings=_ai_settings())
    # The per-message profile context is built the same way.
    scoped = build_canonical_profile_config(_copilot_profile(), include_portal_sections=False, settings=_ai_settings())
    assert scoped["image_analysis"]["ai_platform"]["chat"]["host"] == "https://chat.int"


def test_the_password_is_encrypted_in_the_profile_secret(monkeypatch):
    monkeypatch.setenv("EFP_CONFIG_KEY", "unit-test-config-key")
    canonical = build_canonical_profile_config(_copilot_profile(), settings=_ai_settings())
    encrypted = encrypt_sensitive_fields(canonical)
    auth = encrypted["image_analysis"]["ai_platform"]["auth"]
    assert auth["password"].startswith(ENC_PREFIX) and "pw" != auth["password"]
    assert (auth["username"], auth["usercase"]) == ("u", "uc")
    # An encrypted password still counts: the runtime decrypts it at boot.
    assert image_analysis_configured(encrypted["image_analysis"])


def test_the_instruction_text_is_the_contracts():
    assert RUNTIME_PROFILE_IMAGE_ANALYSIS_INSTRUCTIONS == CONTRACT_INSTRUCTIONS
    assert "\n" not in RUNTIME_PROFILE_IMAGE_ANALYSIS_INSTRUCTIONS
    assert RUNTIME_PROFILE_IMAGE_ANALYSIS_INSTRUCTIONS.isascii()


def test_native_projection_appends_the_instructions_only_when_configured():
    canonical = build_canonical_profile_config(_copilot_profile(), settings=_ai_settings())
    assert _has_image_analysis_config(canonical)
    native = project_canonical_for_runtime(canonical, "native")
    assert native["instruction_texts"] == [RUNTIME_PROFILE_IMAGE_ANALYSIS_INSTRUCTIONS]
    # No CLI connector is set up, so only the image text is added; with one, it follows the CLI text.
    assert RUNTIME_PROFILE_CLI_TOOL_INSTRUCTIONS not in native["instruction_texts"]
    with_cli = dict(_copilot_profile(), github={"enabled": True, "api_token": "ghp"})
    both = project_canonical_for_runtime(build_canonical_profile_config(with_cli, settings=_ai_settings()), "native")
    assert both["instruction_texts"] == [RUNTIME_PROFILE_CLI_TOOL_INSTRUCTIONS, RUNTIME_PROFILE_IMAGE_ANALYSIS_INSTRUCTIONS]
    # Projecting twice does not repeat it.
    assert project_canonical_for_runtime(native, "native")["instruction_texts"] == [RUNTIME_PROFILE_IMAGE_ANALYSIS_INSTRUCTIONS]
    # OpenCode is out of scope: it gets its instructions from the workspace files, as before.
    assert "instruction_texts" not in project_canonical_for_runtime(canonical, "opencode")


def test_native_projection_skips_the_instructions_when_inspect_image_cannot_run():
    off = build_canonical_profile_config(_copilot_profile(enabled=False), settings=_ai_settings())
    assert "instruction_texts" not in project_canonical_for_runtime(off, "native")

    # On, but this deployment names no AI Platform hosts: nothing to promise.
    no_endpoints = build_canonical_profile_config(
        _copilot_profile(), settings=_ai_settings(ai_platform_chat_host="", ai_platform_ib2b_host="")
    )
    assert "image_analysis" in no_endpoints
    assert "instruction_texts" not in project_canonical_for_runtime(no_endpoints, "native")

    # On, without the whole account (an API client can store that): the same.
    half = build_canonical_profile_config(_copilot_profile(auth={"username": "u"}), settings=_ai_settings())
    assert "instruction_texts" not in project_canonical_for_runtime(half, "native")

    # An AI Platform chat profile does not read images by itself any more: the connector decides.
    chat_only = build_canonical_profile_config(
        {"llm": {"provider": "ai_platform", "model": "gpt-5.4", "ai_platform": {"auth": dict(ACCOUNT)}}},
        settings=_ai_settings(),
    )
    assert "image_analysis" not in chat_only
    assert "instruction_texts" not in project_canonical_for_runtime(chat_only, "native")


# --- smoke test ----------------------------------------------------------------


def _recording_service(monkeypatch, settings, *, chat_ok=True):
    svc = RuntimeProfileTestService(settings)
    calls = []

    async def _fake_http_json_request(*, method, url, headers, payload, timeout, client_kwargs=None):
        calls.append({"method": method, "url": url, "headers": headers, "payload": payload, "client_kwargs": client_kwargs or {}})
        if "ib2b" in url:
            return True, "ok", {"issued_token": "JWT-123"}
        if not chat_ok:
            return False, "HTTP 400: image input is not supported", None
        return True, "ok", {"choices": [{"message": {"content": "pong"}}]}

    monkeypatch.setattr(svc, "_http_json_request", _fake_http_json_request)
    return svc, calls


def test_the_smoke_test_sends_one_image_to_chat_completions(monkeypatch):
    # A Responses path is configured for chat: inspect-image speaks chat/completions, so the test does too.
    svc, calls = _recording_service(monkeypatch, _ai_settings(ai_platform_responses_uri="/v1/responses"))
    ok, message = asyncio.run(svc.run_test("image_analysis", _copilot_profile(), runtime_type="native"))
    assert ok is True
    assert message == "Image analysis smoke test OK: ai_platform/gpt-5.6-sol."
    assert [call["url"] for call in calls] == ["https://ib2b.int/dsp/token", "https://chat.int/v1/api/v1/chat/completions"]
    exchange, chat = calls
    assert exchange["payload"]["input_token_state"] == {"token_type": "CREDENTIAL", "username": "u", "password": "pw"}
    assert chat["headers"]["X-Trust"] == "JWT-123" and "Authorization" not in chat["headers"]
    content = chat["payload"]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "ping"}
    assert content[1]["type"] == "image_url" and len(content) == 2
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert chat["payload"]["model"] == "gpt-5.6-sol" and chat["payload"]["user"] == "uc"
    # The Model provider's own test is unchanged: a plain "ping", no image.
    llm_only = {"llm": {"provider": "ai_platform", "model": "gpt-5.4", "ai_platform": {"auth": dict(ACCOUNT)}}}
    svc, calls = _recording_service(monkeypatch, _ai_settings())
    ok, message = asyncio.run(svc.run_test("llm", llm_only))
    assert ok is True and message == "LLM smoke test OK: ai_platform/gpt-5.4."
    assert calls[1]["payload"]["messages"][0]["content"] == "ping"


def test_the_smoke_test_leaves_through_the_proxy_assigned_to_image_analysis(monkeypatch):
    profile = _copilot_profile()
    profile["proxy"] = {
        "enabled": True,
        "default": "corp-a",
        "proxies": [
            {"name": "corp-a", "url": "http://proxy-a.example.test:3128"},
            {"name": "corp-b", "url": "http://proxy-b.example.test:3128"},
        ],
        # The model provider goes direct; image analysis has a proxy of its own.
        "assignments": {"llm": "none", "image_analysis": "corp-b"},
    }
    svc, calls = _recording_service(monkeypatch, _ai_settings())
    ok, _message = asyncio.run(svc.run_test("image_analysis", profile))
    assert ok is True and len(calls) == 2
    for call in calls:
        assert call["client_kwargs"]["proxy"] == "http://proxy-b.example.test:3128", call["url"]
        assert call["client_kwargs"]["trust_env"] is False

    # Left on Default it takes the default proxy, and a failure names the path it took.
    profile["proxy"]["assignments"] = {"llm": "none"}
    svc, calls = _recording_service(monkeypatch, _ai_settings(), chat_ok=False)
    ok, message = asyncio.run(svc.run_test("image_analysis", profile))
    assert ok is False
    assert calls[1]["client_kwargs"]["proxy"] == "http://proxy-a.example.test:3128"
    assert message == (
        "ai_platform/gpt-5.6-sol test failed: HTTP 400: image input is not supported"
        " through the proxy at proxy-a.example.test:3128 (the Proxy connector)"
    )
    # Assigned none, the failure says so with the connector's own name.
    profile["proxy"]["assignments"] = {"image_analysis": "none"}
    svc, calls = _recording_service(monkeypatch, _ai_settings(), chat_ok=False)
    ok, message = asyncio.run(svc.run_test("image_analysis", profile))
    assert ok is False and "proxy" not in calls[1]["client_kwargs"]
    assert message.endswith("(direct: the Proxy connector assigns none to Image analysis)")


def test_the_smoke_test_fails_fast_without_touching_the_network(monkeypatch):
    svc, calls = _recording_service(monkeypatch, _ai_settings())
    ok, message = asyncio.run(svc.run_test("image_analysis", _copilot_profile(enabled=False)))
    assert (ok, message) == (False, "Turn on Image analysis before testing it.")
    # An AI Platform chat profile does not stand in for the connector.
    ok, message = asyncio.run(svc.run_test("image_analysis", {"llm": {"provider": "ai_platform", "ai_platform": {"auth": dict(ACCOUNT)}}}))
    assert (ok, message) == (False, "Turn on Image analysis before testing it.")
    ok, message = asyncio.run(svc.run_test("image_analysis", _copilot_profile(auth={"username": "u", "password": "pw"})))
    assert ok is False and "username, password, and usercase" in message
    assert calls == []

    svc, calls = _recording_service(monkeypatch, _ai_settings(ai_platform_chat_host=""))
    ok, message = asyncio.run(svc.run_test("image_analysis", _copilot_profile()))
    assert ok is False and "chat host" in message.lower() and calls == []


# --- routes --------------------------------------------------------------------


def test_the_panel_renders_the_form_and_only_its_own_fields(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _bind_profile(env.db, env.agent, _copilot_profile())
        resp = env.client.get("/app/connectors/image_analysis/panel")
        assert resp.status_code == 200
        text = resp.text
        assert 'data-connector-type="image_analysis"' in text
        assert 'data-test-base="/app/connectors/image_analysis/test"' in text
        assert 'hx-post="/app/connectors/image_analysis/save"' in text
        assert 'id="profile-section-image_analysis"' in text and 'data-managed-section="image_analysis"' in text
        assert 'name="__touch_image_analysis"' in text
        assert 'name="image_analysis_enabled" id="image_analysis_enabled" checked' in text
        assert 'name="image_analysis_ai_platform_username" value="u"' in text
        # The stored password comes back into its password input, as on the other panels.
        assert 'type="password" name="image_analysis_ai_platform_password" value="pw"' in text
        assert 'name="image_analysis_ai_platform_usercase" value="uc"' in text
        assert '<option value="gpt-5.6-sol" selected>gpt-5.6-sol</option>' in text
        assert 'data-test-target="image_analysis"' in text and 'data-test-result="image_analysis"' in text
        assert "How to set this up" in text and 'href="#/help/connect-image_analysis"' in text
        # As few controls as possible: no provider, no endpoints, and nothing of another connector.
        for absent in (
            'name="image_analysis_provider"',
            "image_analysis_ai_platform_chat",
            "image_analysis_ai_platform_ib2b",
            'name="llm_',
            'name="__touch_llm"',
        ):
            assert absent not in text, absent
        # A managed connector: the panel offers the group default.
        assert "data-connector-mode-section" in text

        # The Model provider panel still has no image fields; it only points at this connector.
        llm = env.client.get("/app/connectors/llm/panel").text
        assert 'name="llm_vision_enabled"' not in llm and 'data-test-target="image_analysis"' not in llm
        assert "image_analysis_ai_platform" not in llm
        assert "Image analysis connector" in llm
    finally:
        env.cleanup()


def test_the_template_renders_the_stored_password_and_has_no_clear_control():
    text = Path("app/templates/partials/connectors/image_analysis.html").read_text(encoding="utf-8")
    assert "setup_guide('image_analysis')" in text
    assert "value=\"{{ image_analysis_auth.get('password', '') }}\"" in text
    assert "_clear" not in text and "Clear saved" not in text


def test_the_test_route_answers_for_the_new_target(monkeypatch):
    assert "image_analysis" in _MANAGED_TEST_TARGETS
    env = _build_env(monkeypatch)
    try:
        profile = _bind_profile(env.db, env.agent, _copilot_profile(enabled=False))
        # Off: the service fails fast, so no network is touched.
        resp = env.client.post("/app/connectors/image_analysis/test/image_analysis", data={})
        assert resp.status_code == 200
        assert resp.json() == {"ok": False, "target": "image_analysis", "message": "Turn on Image analysis before testing it."}

        # The posted (unsaved) form is what runs: switched on without a usercase, the save rule answers.
        resp = env.client.post(
            "/app/connectors/image_analysis/test/image_analysis", data=_member_form(image_analysis_ai_platform_usercase="")
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": False, "target": "image_analysis", "message": REQUIRED_MESSAGE}

        # Switched on in the form, the stored account and the posted model reach the service.
        seen = {}

        async def _fake_run_test(target, config, runtime_type=None, *, proxy_name=None):
            seen.update(target=target, section=config.get("image_analysis"))
            return True, "Image analysis smoke test OK: ai_platform/gpt-5.6-luna."

        monkeypatch.setattr("app.web.runtime_profile_test_service.run_test", _fake_run_test)
        resp = env.client.post(
            "/app/connectors/image_analysis/test/image_analysis", data=_member_form(image_analysis_model="gpt-5.6-luna")
        )
        assert resp.json() == {"ok": True, "target": "image_analysis", "message": "Image analysis smoke test OK: ai_platform/gpt-5.6-luna."}
        assert seen == {"target": "image_analysis", "section": _section(model="gpt-5.6-luna")}
        # Testing never saves.
        assert _saved(env.db, profile)["image_analysis"] == _section(enabled=False)

        # A connector only runs its own tests: this target is not the Model provider's (the old button's 404),
        # and this connector runs no other target.
        assert env.client.post("/app/connectors/llm/test/image_analysis", data={}).status_code == 404
        assert env.client.post("/app/connectors/image_analysis/test/llm", data={}).status_code == 404
    finally:
        env.cleanup()


def test_saving_the_panel_stores_the_section_and_refuses_a_half_filled_account(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        profile = _bind_profile(env.db, env.agent, {"llm": {"provider": "github_copilot", "model": "gpt-6-luna", "api_key": "ghu_token"}})
        refused = env.client.post("/app/connectors/image_analysis/save", data=_member_form(image_analysis_ai_platform_password=""))
        assert refused.status_code == 200
        assert REQUIRED_MESSAGE in refused.text and 'data-settings-status="error"' in refused.text
        assert "image_analysis" not in _saved(env.db, profile) and env.calls["apply"] == 0

        saved = env.client.post("/app/connectors/image_analysis/save", data=_member_form(image_analysis_model="gpt-6-sol"))
        assert saved.status_code == 200 and 'data-settings-status="success"' in saved.text
        assert saved.headers["HX-Trigger"] == "connectorsChanged"
        stored = _saved(env.db, profile)
        # The Copilot model the form posted was repaired to an AI Platform one; the model provider is untouched.
        assert stored["image_analysis"] == _section(model="gpt-5.4")
        assert stored["llm"] == {"provider": "github_copilot", "model": "gpt-6-luna", "api_key": "ghu_token"}
        assert env.calls["apply"] == 1

        # Another connector's save cannot rewrite this section, whatever the page posts.
        env.client.post("/app/connectors/github/save", data={"__touch_github": "1", **_member_form(image_analysis_enabled=None)})
        assert _saved(env.db, profile)["image_analysis"] == _section(model="gpt-5.4")
    finally:
        env.cleanup()


def test_a_member_who_follows_the_defaults_tests_and_sees_the_shared_account(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        shared = _section(model="gpt-5.6-terra", auth={"username": "svc", "password": "svc-pw", "usercase": "svc-uc"})
        RuntimeProfileSeedService(env.db).save_seed({"image_analysis": shared})
        profile = _bind_profile(env.db, env.agent, {"llm": {"provider": "github_copilot", "api_key": "ghu_token"}})
        # Classified, nothing customized: every managed connector follows the Default connectors.
        profile.connector_modes_json = "{}"
        env.db.add(profile)
        env.db.commit()

        panel = env.client.get("/app/connectors/image_analysis/panel").text
        assert 'data-connector-mode="system"' in panel and 'data-connector-mode-button="custom"' in panel
        assert 'name="image_analysis_ai_platform_username" value="svc"' in panel
        assert '<option value="gpt-5.6-terra" selected>gpt-5.6-terra</option>' in panel
        assert 'data-connector-state="connected"' in panel
        # Read-only: no Save, but Test still runs, on the shared values.
        assert '<button type="submit"' not in panel and 'data-test-target="image_analysis"' in panel

        seen = {}

        async def _fake_run_test(target, config, runtime_type=None, *, proxy_name=None):
            seen.update(target=target, section=config.get("image_analysis"))
            return True, "ok"

        monkeypatch.setattr("app.web.runtime_profile_test_service.run_test", _fake_run_test)
        # The read-only form posts no fields and an untouched flag.
        resp = env.client.post("/app/connectors/image_analysis/test/image_analysis", data={"__touch_image_analysis": "0"})
        assert resp.json()["ok"] is True
        assert seen == {"target": "image_analysis", "section": shared}

        # A save from a stale page is refused; the row keeps no copy of the shared account.
        refused = env.client.post("/app/connectors/image_analysis/save", data=_member_form())
        assert "follows your group&#39;s Default connectors" in refused.text
        assert "image_analysis" not in _saved(env.db, profile)

        # Customize starts from the shared values, which are then the member's to change.
        customized = env.client.post("/app/connectors/image_analysis/mode", data={"mode": "custom"})
        assert customized.status_code == 200 and 'data-connector-mode="custom"' in customized.text
        assert _saved(env.db, profile)["image_analysis"] == shared
    finally:
        env.cleanup()


# --- guidance, help, usage -------------------------------------------------------


def test_guidance_and_help_describe_the_connector():
    guidance = CONNECTION_GUIDANCE["image_analysis"]
    assert guidance["title"] and guidance["summary"] and guidance["steps"]
    assert guidance["user_fields"] == ["username", "password", "usercase"]
    topic = get_topic("connect-image_analysis")
    assert topic is not None and topic.connection_section == "image_analysis"
    assert topic.connector_type == "image_analysis" and topic.connector_label == "Image analysis"
    assert list(topic.steps) == list(guidance["steps"])
    assert topic.icon == "scan-eye"
    assert "`inspect-image`" in topic.body and "Productivity Suite" in topic.body
    for extension in ("`.doc`", "`.dot`", "`.xls`"):
        assert extension in topic.body
    assert "image_analysis" in Path("app/help/README.md").read_text(encoding="utf-8")

    # The Model provider no longer says images go to Productivity Suite alone: Image analysis first.
    llm_steps = " ".join(CONNECTION_GUIDANCE["llm"]["steps"])
    assert "Image analysis connector first" in llm_steps and "Productivity Suite" in llm_steps
    assert ".doc, .dot and .xls" in llm_steps
    llm_template = Path("app/templates/partials/connectors/llm.html").read_text(encoding="utf-8")
    assert "Image analysis connector first" in llm_template and ".doc, .dot and .xls" in llm_template
    assert "Attached images and documents are analyzed through Productivity Suite in your own browser; switch on" not in llm_template


def test_view_defaults_and_usage_know_the_section():
    assert RuntimeProfileService.default_profile_config()["image_analysis"] == {"enabled": False}
    assert ("image_analysis", "Image analysis") in CONNECTOR_SECTIONS
    view = _settings_view_payload({})
    assert view["image_analysis"] == {"enabled": False}
    assert view["image_analysis_auth"] == {}
    assert view["image_analysis_models"] == ["gpt-5.4", "gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra"]
    assert view["image_analysis_model_selected"] == "gpt-5.4"
    stored = _settings_view_payload({"image_analysis": _section()})
    assert stored["image_analysis_auth"] == ACCOUNT and stored["image_analysis_model_selected"] == "gpt-5.6-sol"


def test_the_contracts_document_the_connector():
    connectors = Path("docs/CONNECTORS_CONTRACT.md").read_text(encoding="utf-8")
    assert "| `image_analysis` | Image analysis | Model | `image_analysis` | `image_analysis` | `image_analysis` |" in connectors
    runtime = Path("docs/PORTAL_RUNTIME_CONTRACT.md").read_text(encoding="utf-8")
    assert "`llm`, `image_analysis`, `proxy`" in runtime
    assert "materialize_image_analysis_config" in runtime and "EFP_CHAT_VISION" in runtime
    assert "EFP_CHAT_VISION" in Path("docs/OPERATIONS_GUIDE.md").read_text(encoding="utf-8")


# --- the chat model has no vision unless the deployment says so ------------------


def test_chat_vision_is_a_deployment_setting_that_is_off_by_default(monkeypatch):
    monkeypatch.delenv("EFP_CHAT_VISION", raising=False)
    assert Settings(_env_file=None).chat_vision is False
    for truthy in ("1", "true", "yes", "on"):
        monkeypatch.setenv("EFP_CHAT_VISION", truthy)
        assert Settings(_env_file=None).chat_vision is True, truthy
    monkeypatch.setenv("EFP_CHAT_VISION", "0")
    assert Settings(_env_file=None).chat_vision is False


@pytest.mark.parametrize("runtime_type", ["native", "opencode"])
def test_chat_vision_reaches_the_pod_env_only_when_it_is_on(monkeypatch, runtime_type):
    from app.services.k8s_service import K8sService

    service = K8sService()
    service.enabled = False
    agent = SimpleNamespace(id="a1", runtime_type=runtime_type, mount_path="/workspace", name="A")

    def env_map():
        return {item.name: getattr(item, "value", None) for item in service._build_agent_container_env(agent)}

    monkeypatch.setattr(service.settings, "chat_vision", False)
    off = env_map()
    assert "EFP_CHAT_VISION" not in off and "EFP_COPILOT_VISION_VIA_CHAT" not in off
    assert "EFP_MAX_UPLOAD_MB" in off

    monkeypatch.setattr(service.settings, "chat_vision", True)
    on = env_map()
    assert on["EFP_CHAT_VISION"] == "1"
    names = [item.name for item in service._build_agent_container_env(agent)]
    # Next to the upload contract it qualifies, and nothing else in the pod changes.
    assert names.index("EFP_CHAT_VISION") == names.index("EFP_CHAT_UPLOAD_EXTENSIONS") + 1
    assert {name: value for name, value in on.items() if name != "EFP_CHAT_VISION"} == off
