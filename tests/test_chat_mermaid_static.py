"""Inline Mermaid diagrams in chat: static guards over the template, JS and CSS.

A ```mermaid fence (or a code display block with lang "mermaid") is turned into
a .message-diagram component by chat_ui.js and drawn with the vendored
mermaid.min.js, which is fetched on demand rather than with the page shell.
"""

import re
from pathlib import Path

from tests._js_extract_helpers import _extract_js_function


ROOT = Path(__file__).resolve().parents[1]
MERMAID_VERSION = "11.15.0"


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _chat_ui() -> str:
    return _read("app/static/js/chat_ui.js")


def test_mermaid_library_is_vendored_at_the_pinned_version():
    lib = ROOT / "app/static/lib/mermaid.min.js"
    assert lib.is_file(), "mermaid.min.js must be vendored like the other libs; no CDN"
    assert lib.stat().st_size > 1_000_000
    banner = lib.read_bytes()[:600].decode("utf-8", errors="ignore")
    assert f"mermaid {MERMAID_VERSION}" in banner
    assert "registry.npmjs.org/mermaid" in banner


def test_base_template_exposes_the_mermaid_url_without_loading_it_eagerly():
    base_html = _read("app/templates/base.html")
    assert 'name="portal-mermaid-src"' in base_html
    assert "static_url('lib/mermaid.min.js')" in base_html
    # 3 MB must not join the render-blocking shell; chat_ui.js injects the
    # script on the first diagram (ensureMermaidLoaded).
    assert not re.search(r"<script[^>]*mermaid", base_html)


def test_highlight_callback_routes_mermaid_and_keeps_unknown_language_classes():
    source = _chat_ui()
    start = source.index("const md = window.markdownit(")
    end = source.index("md.validateLink", start)
    block = source[start:end]
    assert "isMermaidFenceLanguage(language)" in block
    assert 'class="language-mermaid"' in block
    # Languages hljs has no grammar for used to lose their class and label as "text".
    assert 'class="hljs${languageClass}"' in block
    assert "normalizeFenceLanguage(lang)" in block


def test_render_markdown_draws_diagrams_only_on_the_highlight_pass():
    fn = _extract_js_function(_chat_ui(), "renderMarkdown")
    gate = fn.index("if (!highlight) return;")
    assert fn.index("renderMermaidDiagrams(el)") > gate, "streaming passes must not render half a fence"
    assert "isMermaidCodeElement(code)) return;" in fn, "hljs must never rewrite mermaid source"


def test_enhance_markdown_block_builds_the_diagram_component():
    fn = _extract_js_function(_chat_ui(), "enhanceMarkdownBlock")
    assert "buildDiagramComponent(code)" in fn
    assert 'code.closest(".message-diagram")' in fn


def test_code_display_block_with_mermaid_lang_uses_the_diagram_markup():
    fn = _extract_js_function(_chat_ui(), "renderCodeBlock")
    assert "isMermaidFenceLanguage(language)" in fn
    assert 'class="language-mermaid"' in fn


def test_diagram_component_starts_on_code_view_and_copies_source():
    source = _chat_ui()
    toolbar = _extract_js_function(source, "buildDiagramToolbarHtml")
    assert 'data-diagram-view="diagram" aria-pressed="false" disabled' in toolbar
    assert 'data-diagram-view="code" aria-pressed="true"' in toolbar
    assert "message-diagram-copy" in toolbar
    component = _extract_js_function(source, "buildDiagramComponent")
    assert 'component.dataset.diagramState = "pending"' in component
    assert "copyText(diagramSource(component))" in component


def test_mermaid_is_initialised_strict_without_its_own_error_graphics():
    source = _chat_ui()
    config = _extract_js_function(source, "mermaidBaseConfig")
    assert "startOnLoad: false" in config
    assert 'securityLevel: "strict"' in config
    assert "suppressErrorRendering: true" in config
    assert "mermaid.initialize(mermaidBaseConfig(theme))" in _extract_js_function(source, "configureMermaid")


def test_render_failures_fall_back_to_the_code_view():
    source = _chat_ui()
    render = _extract_js_function(source, "renderMermaidComponent")
    assert "setDiagramError(component, diagramErrorSummary(error))" in render
    error = _extract_js_function(source, "setDiagramError")
    assert 'setDiagramView(component, "code")' in error
    assert "Diagram unavailable:" in error


def test_rendered_svg_is_cached_per_theme_and_source():
    source = _chat_ui()
    render = _extract_js_function(source, "renderMermaidSvg")
    assert "mermaidSvgCache.get(key)" in render
    assert "mermaidSvgCache.set(key, entry)" in render
    key = _extract_js_function(source, "mermaidCacheKey")
    assert "theme" in key and "source" in key
    # Every insert gets its own element id so two copies never share markers.
    component = _extract_js_function(source, "renderMermaidComponent")
    assert "withFreshSvgId(entry, `portal-mermaid-view-${diagramInsertSeq}`)" in component


def test_theme_change_redraws_diagrams():
    source = _chat_ui()
    assert "rerenderMermaidDiagrams();" in _extract_js_function(source, "applyTheme")
    rerender = _extract_js_function(source, "rerenderMermaidDiagrams")
    assert 'component.dataset.diagramTheme !== theme' in rerender


def test_css_styles_the_diagram_component_from_theme_tokens():
    css = _read("app/static/css/app.css")
    for selector in (
        ".message-diagram {",
        ".message-diagram [hidden]",
        ".message-diagram-switch-btn.is-active",
        ".message-diagram-status",
        ".message-diagram-canvas svg",
        ".message-diagram-source",
    ):
        assert selector in css, selector
    block = css[css.index(".message-diagram {"):css.index(".message-table-wrap {")]
    assert "var(--portal-codeblock-bg)" in block
    assert "var(--portal-codeblock-border)" in block
    assert "#" not in re.sub(r"/\*.*?\*/", "", block, flags=re.S), "no hard-coded colors; both themes come from tokens"


def test_render_failure_offers_a_fix_request_only_inside_the_transcript():
    source = _chat_ui()
    error = _extract_js_function(source, "setDiagramError")
    assert "buildDiagramFixButton(component, message)" in error
    button = _extract_js_function(source, "buildDiagramFixButton")
    assert "dom.messageList?.contains(component)" in button, "task and delegation views have no composer for the author"
    assert "fillComposer(composeDiagramFixRequest(message, diagramSource(component)))" in button


def test_fix_request_lands_in_the_composer_without_sending():
    source = _chat_ui()
    fill = _extract_js_function(source, "fillComposer")
    assert "dom.chatInput" in fill
    assert 'dispatchEvent(new Event("input", { bubbles: true }))' in fill
    for send_path in ("submitChatForSelectedAgent", "requestSubmit", ".submit(", "sendChatBtn.click"):
        assert send_path not in fill
    css = _read("app/static/css/app.css")
    assert ".message-diagram-fix {" in css


def test_diagram_toolbar_offers_zoom_and_expand_only_on_the_diagram_view():
    source = _chat_ui()
    toolbar = _extract_js_function(source, "buildDiagramToolbarHtml")
    assert "buildDiagramZoomControlsHtml()" in toolbar
    assert "data-diagram-expand" in toolbar
    zoom = _extract_js_function(source, "buildDiagramZoomControlsHtml")
    for action in ("out", "reset", "in", "fit"):
        assert f'data-diagram-zoom="{action}"' in zoom
    css = _read("app/static/css/app.css")
    assert '.message-diagram[data-view="code"] .message-diagram-zoom' in css
    assert '.message-diagram[data-view="code"] .message-diagram-expand { display: none; }' in css


def test_diagrams_open_fitted_to_the_column():
    source = _chat_ui()
    choice = _extract_js_function(source, "defaultDiagramScale")
    assert 'return { mode: "fit", scale: Math.min(1, fit) };' in choice, "always Fit, never enlarged"
    assert "DIAGRAM_FIT_THRESHOLD" not in source
    render = _extract_js_function(source, "renderMermaidComponent")
    assert "defaultDiagramScale(diagramFitScale(canvas))" in render
    scale = _extract_js_function(source, "applyDiagramScale")
    assert 'svg.style.maxWidth = "none"' in scale, "mermaid's max-width would otherwise cap the zoom"
    assert "svg.style.width" in scale and "svg.style.height" in scale


def test_column_resizes_refit_only_diagrams_still_in_fit_mode():
    source = _chat_ui()
    refit = _extract_js_function(source, "refitDiagramViewports")
    assert '.message-diagram-canvas[data-zoom-mode="fit"]' in refit
    observer = _extract_js_function(source, "ensureDiagramRefitObserver")
    assert "diagramRefitObserver.observe(dom.messageList)" in observer
    assert "ensureDiagramRefitObserver();" in _extract_js_function(source, "renderMermaidComponent"), "bound on the first drawn diagram, not at script load"


def test_lightbox_offers_full_screen_tab_and_exports_and_closes_on_escape():
    source = _chat_ui()
    box = _extract_js_function(source, "ensureDiagramLightbox")
    for hook in ("data-lightbox-fullscreen", "data-lightbox-open-tab", 'data-lightbox-download="svg"', 'data-lightbox-download="png"', "data-lightbox-copy-png", "data-lightbox-copy-source", "data-lightbox-close"):
        assert hook in box, hook
    assert 'event.key === "Escape"' in box
    assert "requestFullscreen" in _extract_js_function(source, "toggleDiagramFullscreen")
    assert "URL.createObjectURL" in _extract_js_function(source, "triggerDiagramDownload")
    # Exports draw with SVG text labels: HTML labels taint the canvas and travel badly.
    export = _extract_js_function(source, "renderMermaidSvg")
    assert "htmlLabels: false" in export
    png = _extract_js_function(source, "downloadDiagram")
    assert 'downloadDiagram("svg")' in png, "a failed PNG export falls back to the SVG"
    css = _read("app/static/css/app.css")
    assert ".message-diagram-lightbox { position: fixed; inset: 0; z-index: 1100; }" in css
    assert ":fullscreen .message-diagram-lightbox-panel" in css
    # Centred both ways while smaller than the viewport, scrollable from the top-left once larger.
    assert ".message-diagram-lightbox-viewport svg { max-width: none; flex: none; margin: auto; display: block; }" in css
