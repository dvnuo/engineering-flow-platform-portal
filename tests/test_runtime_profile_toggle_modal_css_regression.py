from pathlib import Path
import re


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
    assert '<div class="stack">' in editor_modal_html
    assert '<label class="toggle-switch">' in editor_modal_html
    assert 'id="sp-editor-enabled"' in editor_modal_html
    assert '<span class="toggle-slider"></span>' in editor_modal_html
    assert 'Enable custom prompt for this section' in editor_modal_html
    assert 'id="sp-editor-content"' in editor_modal_html
    assert 'id="sp-editor-save"' in editor_modal_html
