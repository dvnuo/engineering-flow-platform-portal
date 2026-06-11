from pathlib import Path


def test_templates_hide_visible_clear_controls_and_render_secret_values_for_authenticated_edit_forms():
    for rel in ["app/templates/partials/settings_panel.html", "app/templates/partials/runtime_profile_panel.html"]:
        text = Path(rel).read_text(encoding="utf-8")
        assert 'data-field="enabled"' in text
        assert 'data-original-field="name"' in text
        assert 'data-original-field="url"' in text
        assert "value=\"{{ proxy.get('password', '') }}\"" in text
        assert "value=\"{{ raw_llm.get('api_key', '') }}\"" in text
        assert "value=\"{{ raw_github.get('api_token', '') }}\"" in text
        assert "value=\"{{ raw_aws.get('access_key_id', '') }}\"" in text
        assert "value=\"{{ raw_aws.get('secret_access_key', '') }}\"" in text
        assert "value=\"{{ raw_aws.get('session_token', '') }}\"" in text
        assert "value=\"{{ inst.get('password','') }}\"" in text or "value=\"{{ inst.get('password', '') }}\"" in text
        assert "value=\"{{ inst.get('token','') }}\"" in text or "value=\"{{ inst.get('token', '') }}\"" in text
        assert "Clear saved proxy password" not in text
        assert "Clear saved API key" not in text
        assert "Clear saved GitHub token" not in text
        assert "Clear saved AWS access key" not in text
        assert "Clear saved password" not in text
        assert "Clear saved token" not in text
        assert "proxy_password_clear" not in text
        assert "llm_api_key_clear" not in text
        assert "github_api_token_clear" not in text
        assert "aws_secret_access_key_clear" not in text
        assert "aws_session_token_clear" not in text
        assert 'data-clear-field="password"' not in text
        assert 'data-clear-field="token"' not in text
