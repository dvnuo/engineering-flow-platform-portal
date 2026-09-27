from pathlib import Path
import re

import pytest


CONNECTOR_TEMPLATES = Path("app/templates/partials/connectors")

# (template, managed section, enable input, condition that checks it)
TARGET_SECTIONS = [
    ("proxy.html", "proxy", "proxy_enabled", "proxy.get('enabled')"),
    ("jira.html", "jira", "jira_enabled", "jira.get('enabled')"),
    ("confluence.html", "confluence", "confluence_enabled", "confluence.get('enabled')"),
    ("github.html", "github", "github_enabled", "github.get('enabled')"),
    ("aws.html", "aws", "aws_enabled", "aws.get('enabled')"),
    ("jenkins.html", "jenkins", "jenkins_enabled", "jenkins.get('enabled')"),
    ("nexus.html", "nexus", "nexus_enabled", "nexus.get('enabled')"),
    ("splunk.html", "splunk", "splunk_enabled", "splunk.get('enabled')"),
    ("pgsql.html", "pgsql", "pgsql_enabled", "pgsql.get('enabled')"),
    ("browserstack.html", "mobile", "mobile_enabled", "mobile.get('enabled')"),
]


def _section_html(template_html: str, section_name: str) -> str:
    marker = f'data-managed-section="{section_name}"'
    start = template_html.index(marker)
    rest = template_html[start:]
    end_match = re.search(r"</section>", rest)
    assert end_match, f"section {section_name} should close"
    return rest[: end_match.end()]


@pytest.mark.parametrize("template_name,section_name,input_name,checked_condition", TARGET_SECTIONS)
def test_connector_enable_toggle_is_a_plain_toggle_switch(template_name, section_name, input_name, checked_condition):
    html = (CONNECTOR_TEMPLATES / template_name).read_text(encoding="utf-8")
    section = _section_html(html, section_name)

    assert "portal-settings-title-with-toggle" not in section
    assert "portal-section-enable-switch" not in section
    assert "portal-section-enable-text" not in section

    assert section.count(f'name="{input_name}"') == 1
    assert checked_condition in section

    input_pos = section.index(f'name="{input_name}"')
    label_start = section.rfind("<label", 0, input_pos)
    assert label_start != -1
    label_open = section[label_start : section.find(">", label_start) + 1]
    assert "toggle-switch" in label_open
    assert "portal-section-enable-switch" not in label_open
    assert "<span>Enabled</span>" in section


def test_connector_title_renders_above_the_form_fields():
    # The connector's name is the panel heading; each form partial is included
    # below it, so the enable toggle sits under the title.
    panel = (CONNECTOR_TEMPLATES / "panel.html").read_text(encoding="utf-8")
    assert "<h5>{{ connector.label }}</h5>" in panel
    assert panel.index("<h5>{{ connector.label }}</h5>") < panel.index("{% include connector_template %}")


def test_leading_toggle_css_classes_exist():
    css = Path("app/static/css/app.css").read_text(encoding="utf-8")

    assert ".portal-settings-section-head--leading-toggle" in css
    assert ".portal-settings-title-with-toggle" in css
    assert ".portal-section-enable-switch" in css
    assert ".portal-section-enable-text" in css


def test_stack_selectors_exclude_toggle_internals_and_slider_has_display_block():
    css = Path("app/static/css/app.css").read_text(encoding="utf-8")

    assert ".stack label:not(.toggle-switch)" in css
    assert (
        '.stack input:not([type="checkbox"]):not([type="radio"]), .stack textarea'
        in css
    )

    slider_block = re.search(r"\.toggle-slider\s*\{(?P<body>[\s\S]*?)\}", css)
    assert slider_block, ".toggle-slider rule should exist"
    assert "display: block;" in slider_block.group("body")


def test_system_prompt_editor_markup_still_uses_shared_toggle_classes():
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")

    editor_modal_match = re.search(
        r"modal\.innerHTML\s*=\s*'(?P<html>[\s\S]*?id=\"sp-editor-title\"[\s\S]*?)';",
        js,
    )
    assert editor_modal_match, "System prompt editor modal.innerHTML assignment should exist"

    editor_modal_html = editor_modal_match.group("html")
    assert 'class="modal-backdrop"' not in editor_modal_html
    assert 'id="sp-editor-backdrop"' not in editor_modal_html
    assert '<div class="stack">' in editor_modal_html
    assert '<label class="toggle-switch">' in editor_modal_html
    assert 'id="sp-editor-enabled"' in editor_modal_html
    assert '<span class="toggle-slider"></span>' in editor_modal_html
    assert 'Enable custom prompt for this section' in editor_modal_html
    assert 'id="sp-editor-content"' in editor_modal_html
    assert 'id="sp-editor-cancel"' in editor_modal_html
    assert 'class="portal-btn is-secondary"' in editor_modal_html
    assert 'id="sp-editor-save"' in editor_modal_html
    assert 'class="portal-btn is-primary"' in editor_modal_html


def test_stack_button_selectors_are_narrowed_to_avoid_portal_button_overrides():
    css = Path("app/static/css/app.css").read_text(encoding="utf-8")

    assert ".stack > button:not(.portal-btn):not(.portal-modal-close)" in css
    assert ".stack button {" not in css
    assert ".stack button:disabled" not in css
