import json
from types import SimpleNamespace

from app.schemas.runtime_profile import sanitize_runtime_profile_config_dict, redact_runtime_profile_config_for_public_response
from app.services.runtime_profile_sync_service import _project_llm_for_runtime
from app.web import _settings_merge_payload


def test_schema_oauth_by_runtime_sanitize_and_redact():
    raw = {"llm": {"provider": "github_copilot", "oauth": {"type": "oauth", "access": "L", "refresh": "L", "expires": 0}, "oauth_by_runtime": {"native": {"type": "oauth", "access": "N", "refresh": "N", "expires": 1}, "opencode": {"type": "oauth", "access": "O", "refresh": "O", "expires": 2}, "foo": {"access": "X"}}}}
    s = sanitize_runtime_profile_config_dict(raw)
    assert set(s["llm"]["oauth_by_runtime"].keys()) == {"native", "opencode"}
    red = redact_runtime_profile_config_for_public_response(s)
    assert "access" not in red["llm"]["oauth_by_runtime"]["native"]
    assert red["llm"]["oauth_by_runtime"]["native"]["present"] is True
    assert "access" not in red["llm"]["oauth"]


def test_settings_merge_payload_legacy_fallback_only_opencode():
    merged, error = _settings_merge_payload({}, {"__touch_llm": "1", "llm_provider": "github_copilot", "llm_oauth_access": "LEG", "llm_oauth_refresh": "", "llm_oauth_expires": "0"})
    assert error is None
    llm = merged["llm"]
    assert llm["oauth_by_runtime"]["opencode"]["access"] == "LEG"
    assert "native" not in llm["oauth_by_runtime"]


def test_project_llm_for_runtime_secrets_do_not_cross():
    llm = {"provider": "github_copilot", "model": "gpt-5", "oauth_by_runtime": {"native": {"type": "oauth", "access": "NATIVE_SECRET", "refresh": "NATIVE_SECRET", "expires": 0}, "opencode": {"type": "oauth", "access": "OPENCODE_SECRET", "refresh": "OPENCODE_SECRET", "expires": 0}}}
    op = _project_llm_for_runtime(llm, "opencode")
    na = _project_llm_for_runtime(llm, "native")
    assert op["provider"] == "github-copilot"
    assert op["oauth"]["access"] == "OPENCODE_SECRET"
    assert "api_key" not in op and "oauth_by_runtime" not in op
    assert "NATIVE_SECRET" not in json.dumps(op)
    assert na["provider"] == "github_copilot"
    assert na["api_key"] == "NATIVE_SECRET"
    assert "oauth" not in na and "oauth_by_runtime" not in na
    assert "OPENCODE_SECRET" not in json.dumps(na)


def test_project_opencode_does_not_keep_api_key_when_oauth_by_runtime_present():
    llm = {"provider": "github_copilot", "api_key": "NATIVE_SECRET", "oauth_by_runtime": {"native": {"type": "oauth", "access": "NATIVE_SECRET", "refresh": "NATIVE_SECRET", "expires": 0}}}
    op = _project_llm_for_runtime(llm, "opencode")
    assert "api_key" not in op


def test_ui_static_dual_auth_markers_present():
    rp = open("app/templates/partials/runtime_profile_panel.html", encoding="utf-8").read()
    sp = open("app/templates/partials/settings_panel.html", encoding="utf-8").read()
    js = open("app/static/js/chat_ui.js", encoding="utf-8").read()
    for text in [rp, sp]:
        assert 'data-copilot-auth-button="native"' in text
        assert 'data-copilot-auth-button="opencode"' in text
        assert 'llm_oauth_native_access' in text
        assert 'llm_oauth_opencode_access' in text
        assert 'id="copilot_auth_btn"' not in text
        assert 'id="copilot_auth_status"' not in text
    assert 'runtime_type: key' in js
    assert 'JSON.stringify({ runtime_type: key })' in js
    assert 'github_base_url: githubBaseUrl' not in js
    assert 'data-copilot-auth-card' in js
    assert 'closest("#copilot_auth_btn")' not in js
    assert 'function setCopilotOAuthFields(root, runtimeType, oauth)' in js


def test_redaction_drops_unknown_oauth_by_runtime_key():
    raw = {"llm": {"oauth_by_runtime": {"native": {"type": "oauth", "access": "N", "refresh": "N", "expires": 0}, "foo": {"type": "oauth", "access": "X", "refresh": "X", "expires": 0}}}}
    red = redact_runtime_profile_config_for_public_response(raw)
    assert "foo" not in red["llm"]["oauth_by_runtime"]
    assert "access" not in red["llm"]["oauth_by_runtime"]["native"]


def test_update_model_options_non_copilot_does_not_clear_unsaved_copilot_values_static_assert():
    js = open("app/static/js/chat_ui.js", encoding="utf-8").read()
    assert "function clearCopilotOAuthFields(root, runtimeType = null, options = {})" in js
    assert "const clearValues = options.clearValues !== false;" in js
    assert "clearCopilotOAuthFields(root, null, { markClear: false, clearValues: false })" in js
    assert "stopCopilotPolling(root);" in js

def test_project_llm_for_runtime_infers_copilot_provider_from_model_prefix():
    llm = {
        "model": "github_copilot/gpt-5",
        "oauth_by_runtime": {
            "native": {"type": "oauth", "access": "NATIVE_SECRET", "refresh": "NATIVE_SECRET", "expires": 0},
            "opencode": {"type": "oauth", "access": "OPENCODE_SECRET", "refresh": "OPENCODE_SECRET", "expires": 0},
        },
    }
    op = _project_llm_for_runtime(llm, "opencode")
    assert op["provider"] == "github-copilot"
    assert op["oauth"]["access"] == "OPENCODE_SECRET"
    assert "api_key" not in op
    assert "oauth_by_runtime" not in op
    assert "NATIVE_SECRET" not in json.dumps(op)

    native = _project_llm_for_runtime(llm, "native")
    assert native["provider"] == "github_copilot"
    assert native["api_key"] == "NATIVE_SECRET"
    assert "oauth" not in native
    assert "oauth_by_runtime" not in native
    assert "OPENCODE_SECRET" not in json.dumps(native)


def test_project_llm_for_runtime_infers_copilot_provider_from_hyphen_model_prefix():
    llm = {
        "model": "github-copilot/gpt-5",
        "oauth_by_runtime": {
            "opencode": {"type": "oauth", "access": "OPENCODE_SECRET", "refresh": "OPENCODE_SECRET", "expires": 0},
        },
    }
    op = _project_llm_for_runtime(llm, "opencode")
    assert op["provider"] == "github-copilot"
    assert op["oauth"]["access"] == "OPENCODE_SECRET"
    assert "oauth_by_runtime" not in op

def test_project_llm_for_runtime_drops_stale_oauth_for_non_copilot_provider():
    llm = {
        "provider": "openai",
        "model": "gpt-4",
        "api_key": "sk_OPENAI",
        "oauth": {
            "type": "oauth",
            "access": "COPILOT_DIRECT_SECRET",
            "refresh": "COPILOT_DIRECT_SECRET",
            "expires": 0,
        },
        "oauth_by_runtime": {
            "native": {
                "type": "oauth",
                "access": "NATIVE_SECRET",
                "refresh": "NATIVE_SECRET",
                "expires": 0,
            },
            "opencode": {
                "type": "oauth",
                "access": "OPENCODE_SECRET",
                "refresh": "OPENCODE_SECRET",
                "expires": 0,
            },
        },
    }

    for runtime_type in ("native", "opencode"):
        projected = _project_llm_for_runtime(llm, runtime_type)
        dumped = json.dumps(projected)
        assert projected["provider"] == "openai"
        assert projected["model"] == "gpt-4"
        assert projected["api_key"] == "sk_OPENAI"
        assert "oauth" not in projected
        assert "oauth_by_runtime" not in projected
        assert "COPILOT_DIRECT_SECRET" not in dumped
        assert "NATIVE_SECRET" not in dumped
        assert "OPENCODE_SECRET" not in dumped
