from app.schemas.runtime_profile import PORTAL_MANAGED_FIELD_TREE, sanitize_runtime_profile_config_dict
from app.services.runtime_profile_context_projection import build_runtime_profile_context_config
from app.services.runtime_profile_llm_projection import project_llm_for_runtime
from app.web import _settings_merge_payload


def test_schema_drops_oauth_by_runtime_without_migrating_to_api_key():
    raw = {
        "llm": {
            "provider": "github_copilot",
            "oauth_by_runtime": {
                "native": {"type": "oauth", "access": "N", "refresh": "N", "expires": 0},
                "opencode": {"type": "oauth", "access": "O", "refresh": "O", "expires": 0},
            },
        }
    }
    s = sanitize_runtime_profile_config_dict(raw)
    assert s["llm"] == {"provider": "github_copilot"}
    assert "oauth" not in s["llm"]
    assert "oauth_by_runtime" not in s["llm"]


def test_project_llm_for_runtime_uses_single_api_key():
    llm = {"provider": "github_copilot", "api_key": "TOKEN", "model": "gpt-5.4-mini"}
    native = project_llm_for_runtime(llm, "native")
    assert native["provider"] == "github_copilot"
    assert native["api_key"] == "TOKEN"
    assert "oauth" not in native
    assert "oauth_by_runtime" not in native

    opencode = project_llm_for_runtime(llm, "opencode")
    assert opencode["provider"] == "github-copilot"
    assert opencode["model"] == "github-copilot/gpt-5.4-mini"
    assert opencode["api_key"] == "TOKEN"
    assert "oauth" not in opencode
    assert "oauth_by_runtime" not in opencode


def test_settings_merge_copilot_uses_llm_api_key_only():
    merged, error = _settings_merge_payload({}, {"__touch_llm": "1", "llm_provider": "github_copilot", "llm_api_key": "TOKEN"})
    assert error is None
    assert merged["llm"]["api_key"] == "TOKEN"
    assert "oauth" not in merged["llm"]
    assert "oauth_by_runtime" not in merged["llm"]


def test_ui_and_js_static_single_key_auth_flow_markers():
    rp = open("app/templates/partials/runtime_profile_panel.html", encoding="utf-8").read()
    sp = open("app/templates/partials/settings_panel.html", encoding="utf-8").read()
    js = open("app/static/js/chat_ui.js", encoding="utf-8").read()
    for text in [rp, sp]:
        assert 'data-copilot-auth-button="native"' not in text
        assert 'data-copilot-auth-button="opencode"' not in text
        assert text.count("data-copilot-auth-button") == 1
        assert 'name="llm_api_key"' in text
        for banned in ["data-copilot-auth-status", "data-copilot-auth-card", "llm_oauth_native", "llm_oauth_opencode", "oauth_by_runtime", "Authorized", "Not authorized", "Saved OAuth credential present", "EFP Runtime", "OpenCode " + "Runtime", "Choose one authorization"]:
            assert banned not in text
    assert "setCopilotApiKeyField" in js
    assert "querySelectorAll(\"[data-copilot-auth-button]\")" in js
    assert "button.classList.toggle(\"hidden\", !isCopilot)" in js
    assert "JSON.stringify({})" in js
    assert "runtime_type" not in js[js.index("async function startCopilotAuth"):js.index("function initializeManagedSettingsRoot")]
    assert "stopCopilotPolling(root);" in js
    assert "Authorization completed, but no token was returned" in js
    assert "const updated = setCopilotApiKeyField(root, token)" in js
    assert "stopCopilotPolling(root);" in js
    for banned in ["setCopilotOAuthFields", "clearCopilotOAuthFields", "llm_oauth_native", "llm_oauth_opencode", "data-copilot-auth-card", "data-copilot-auth-status", "normalizeCopilotRuntimeType", "copilotRuntimeLabel"]:
        assert banned not in js


def test_runtime_profile_field_tree_no_oauth_by_runtime_managed_key():
    llm_tree = PORTAL_MANAGED_FIELD_TREE.get("llm", {})
    assert "oauth_by_runtime" not in llm_tree
    assert "oauth" not in llm_tree
    assert "timeout" not in llm_tree
    assert "timeout_ms" not in llm_tree
    assert "chunk_timeout_ms" not in llm_tree
    assert "chunkTimeout" not in llm_tree


def test_opencode_projection_omits_native_efp_instruction_texts():
    projected = build_runtime_profile_context_config(
        {
            "jira": {
                "enabled": True,
                "instances": [{"name": "Jira", "url": "https://jira.example", "enabled": True}],
            },
            "confluence": {
                "enabled": True,
                "instances": [{"name": "Docs", "url": "https://conf.example/wiki", "enabled": True}],
            },
            "github": {"enabled": True, "api_token": "ghp"},
            "git": {"user": {"name": "Bot", "email": "bot@example.com"}},
        },
        runtime_type="opencode",
    )

    assert "instruction_texts" not in projected
    assert projected["jira"]["enabled"] is True
    assert projected["confluence"]["enabled"] is True
    assert projected["github"]["api_token"] == "ghp"
    assert projected["git"]["user"]["email"] == "bot@example.com"
