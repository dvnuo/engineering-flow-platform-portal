from pathlib import Path


def test_templates_include_clear_controls_and_do_not_render_secret_values():
    for rel in ["app/templates/partials/settings_panel.html", "app/templates/partials/runtime_profile_panel.html"]:
        text = Path(rel).read_text()
        assert "llm_api_key_clear" in text
        assert "proxy_password_clear" in text
        assert "github_api_token_clear" in text
        assert 'data-field="enabled"' in text
        assert 'data-original-field="name"' in text
        assert 'data-original-field="url"' in text
        assert 'data-clear-field="password"' in text
        assert 'data-clear-field="token"' in text
        assert "value=\"{{ proxy.get('password', '') }}\"" not in text
        assert "value=\"{{ raw_llm.get('api_key', '') }}\"" not in text
        assert "value=\"{{ inst.get('password','') }}\"" not in text
        assert "value=\"{{ inst.get('token','') }}\"" not in text
        assert "value=\"{{ raw_github.get('api_token', '') }}\"" not in text
