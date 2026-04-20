from pathlib import Path
import re

import pytest


TARGET_SECTIONS = [
    ("proxy", "proxy_enabled", "Proxy", "proxy.get('enabled')"),
    ("jira", "jira_enabled", "Jira", "jira.get('enabled')"),
    ("confluence", "confluence_enabled", "Confluence", "confluence.get('enabled')"),
    ("github", "github_enabled", "GitHub", "github.get('enabled')"),
    ("debug", "debug_enabled", "Debug", "debug.get('enabled')"),
]


def _section_html(template_html: str, section_name: str) -> str:
    marker = f'data-managed-section="{section_name}"'
    start = template_html.index(marker)
    rest = template_html[start:]
    end_match = re.search(r"</section>", rest)
    assert end_match, f"section {section_name} should close"
    return rest[: end_match.end()]


@pytest.mark.parametrize(
    "template_path",
    [
        "app/templates/partials/runtime_profile_panel.html",
        "app/templates/partials/settings_panel.html",
    ],
)
def test_top_level_runtime_provider_enabled_toggles_are_left_of_titles(template_path):
    html = Path(template_path).read_text(encoding="utf-8")

    for section_name, input_name, title, checked_condition in TARGET_SECTIONS:
        section = _section_html(html, section_name)

        assert "portal-settings-section-head--leading-toggle" in section
        assert "portal-settings-title-with-toggle" in section
        assert "portal-section-enable-switch" in section
        assert "portal-section-enable-text" in section

        assert section.count(f'name="{input_name}"') == 1
        assert f"<h6>{title}</h6>" in section
        assert section.index(f'name="{input_name}"') < section.index(f"<h6>{title}</h6>")
        assert checked_condition in section

        old_right_side_toggle = (
            f'<div class="portal-checkbox-row"><label class="toggle-switch">'
            f'<input type="checkbox" name="{input_name}"'
        )
        assert old_right_side_toggle not in section

        input_pos = section.index(f'name="{input_name}"')
        label_start = section.rfind("<label", 0, input_pos)
        label_open = section[label_start : section.find(">", label_start) + 1]
        assert "toggle-switch portal-section-enable-switch" in label_open


def test_leading_toggle_css_classes_exist():
    css = Path("app/static/css/app.css").read_text(encoding="utf-8")

    assert ".portal-settings-section-head--leading-toggle" in css
    assert ".portal-settings-title-with-toggle" in css
    assert ".portal-section-enable-switch" in css
    assert ".portal-section-enable-text" in css


def test_create_runtime_profile_modal_retains_toggle_markup():
    template = Path("app/templates/app.html").read_text(encoding="utf-8")
    form_match = re.search(
        r'<form\s+id="create-runtime-profile-form"[^>]*>(?P<body>[\s\S]*?)</form>',
        template,
    )
    assert form_match, "Create Runtime Profile form should exist"

    form_html = form_match.group("body")
    assert '<label class="toggle-switch">' in form_html
    assert '<input type="checkbox" name="is_default" />' in form_html
    assert '<span class="toggle-slider"></span>' in form_html


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
