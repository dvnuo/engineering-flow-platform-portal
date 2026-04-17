from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_repo_root() / relative_path).read_text(encoding="utf-8")


def test_base_template_does_not_apply_dark_highlight_css_after_app_css():
    base_html = _read("app/templates/base.html")
    app_css = '/static/css/app.css'
    dark_hl = '/static/lib/github-dark.min.css'

    assert app_css in base_html
    if dark_hl in base_html:
        assert base_html.index(app_css) > base_html.index(dark_hl)
    else:
        assert dark_hl not in base_html


def test_app_css_defines_code_theme_tokens_for_light_and_dark_modes():
    css = _read("app/static/css/app.css")

    required_tokens = [
        "--portal-code-inline-bg",
        "--portal-code-inline-text",
        "--portal-code-inline-border",
        "--portal-codeblock-bg",
        "--portal-codeblock-border",
        "--portal-codeblock-toolbar-bg",
        "--portal-codeblock-text",
        "--portal-code-token-keyword",
        "--portal-code-token-string",
        "--portal-code-token-number",
        "--portal-code-token-title",
        "--portal-code-token-comment",
        "--portal-code-token-meta",
        "--portal-code-token-variable",
        "--portal-code-token-type",
        "--portal-code-token-attr",
        "--portal-code-token-built-in",
    ]

    for token in required_tokens:
        assert css.count(token) >= 2


def test_inline_code_rule_uses_theme_aware_color_background_and_border_tokens():
    css = _read("app/static/css/app.css")

    assert ".message-markdown code" in css
    assert "color: var(--portal-code-inline-text);" in css
    assert "background: var(--portal-code-inline-bg);" in css
    assert "border: 1px solid var(--portal-code-inline-border);" in css


def test_codeblock_highlight_styles_are_scoped_to_message_codeblock_hljs_tokens():
    css = _read("app/static/css/app.css")

    expected_selectors = [
        ".message-codeblock .hljs",
        ".message-codeblock .hljs-keyword",
        ".message-codeblock .hljs-string",
        ".message-codeblock .hljs-number",
        ".message-codeblock .hljs-title",
        ".message-codeblock .hljs-comment",
        ".message-codeblock .hljs-meta",
        ".message-codeblock .hljs-variable",
        ".message-codeblock .hljs-type",
        ".message-codeblock .hljs-attr",
        ".message-codeblock .hljs-built_in",
        ".message-codeblock .hljs-literal",
        ".message-codeblock .hljs-params",
        ".message-codeblock .hljs-subst",
    ]

    for selector in expected_selectors:
        assert selector in css


def test_chat_renderer_codeblock_structure_is_preserved():
    js_source = _read("app/static/js/chat_ui.js")

    assert "function renderCodeBlock(" in js_source
    assert ".message-codeblock" in js_source
    assert "message-codeblock-toolbar" in js_source
    assert "message-codeblock-copy" in js_source
