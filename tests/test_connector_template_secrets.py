from pathlib import Path

import pytest

CONNECTOR_TEMPLATES = Path("app/templates/partials/connectors")

# Secret fields each settings connector form renders back for the signed-in member.
SECRET_VALUE_MARKERS = {
    "proxy.html": ["value=\"{{ proxy.get('password', '') }}\""],
    "llm.html": ["value=\"{{ raw_llm.get('api_key', '') }}\""],
    "github.html": ["value=\"{{ raw_github.get('api_token', '') }}\""],
    "aws.html": [
        "value=\"{{ raw_aws.get('domain', '') }}\"",
        "value=\"{{ raw_aws.get('username', '') }}\"",
        "value=\"{{ raw_aws.get('password', '') }}\"",
    ],
    "jira.html": ["value=\"{{ inst.get('password','') }}\"", "value=\"{{ inst.get('token','') }}\""],
    "confluence.html": ["value=\"{{ inst.get('password','') }}\"", "value=\"{{ inst.get('token','') }}\""],
    "jenkins.html": ["value=\"{{ inst.get('password','') }}\"", "value=\"{{ inst.get('token','') }}\""],
}

INSTANCE_TEMPLATES = ["jira.html", "confluence.html", "jenkins.html"]

CLEAR_CONTROL_MARKERS = [
    "Clear saved proxy password",
    "Clear saved API key",
    "Clear saved GitHub token",
    "Clear saved password",
    "Clear saved token",
    "proxy_password_clear",
    "llm_api_key_clear",
    "github_api_token_clear",
    'data-clear-field="password"',
    'data-clear-field="token"',
]


@pytest.mark.parametrize("template_name,markers", sorted(SECRET_VALUE_MARKERS.items()))
def test_connector_forms_render_secret_values_for_authenticated_edit(template_name, markers):
    text = (CONNECTOR_TEMPLATES / template_name).read_text(encoding="utf-8")
    for marker in markers:
        assert marker in text, (template_name, marker)


@pytest.mark.parametrize("template_name", INSTANCE_TEMPLATES)
def test_instance_cards_keep_enabled_toggle_and_original_identity_fields(template_name):
    text = (CONNECTOR_TEMPLATES / template_name).read_text(encoding="utf-8")
    assert 'data-field="enabled"' in text
    assert 'data-original-field="name"' in text
    assert 'data-original-field="url"' in text


def test_connector_templates_hide_visible_clear_controls():
    templates = sorted(CONNECTOR_TEMPLATES.glob("*.html"))
    assert templates
    for path in templates:
        text = path.read_text(encoding="utf-8")
        for marker in CLEAR_CONTROL_MARKERS:
            assert marker not in text, (path.name, marker)
