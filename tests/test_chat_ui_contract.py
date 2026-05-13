from pathlib import Path
import re

from _js_extract_helpers import _extract_js_function


def test_chat_ui_contains_provider_retry_contract():
    source = Path('app/static/js/chat_ui.js').read_text(encoding='utf-8')
    assert 'provider.retry' in source
    assert 'Provider API retrying' in source
    assert 'Check Runtime Profile LLM API key/base URL/proxy' in source
    assert 'request_id' in source


def test_chat_ui_no_empty_response_literal():
    source = Path('app/static/js/chat_ui.js').read_text(encoding='utf-8')
    assert '(empty response)' not in source


def test_render_display_blocks_empty_returns_empty_html_not_placeholder():
    source = Path('app/static/js/chat_ui.js').read_text(encoding='utf-8')
    assert 'function renderDisplayBlocksToHtml' in source
    assert 'return "";' in source


def test_system_prompt_editor_close_helper_is_defined_and_cleans_up_modal():
    source = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    close_fn = _extract_js_function(source, "closeSystemPromptEditor")

    assert "system-prompt-editor-modal" in close_fn
    assert "classList.add('hidden')" in close_fn or 'classList.add("hidden")' in close_fn
    assert "aria-hidden" in close_fn
    assert "removeEventListener('keydown', modal._keyHandler)" in close_fn or 'removeEventListener("keydown", modal._keyHandler)' in close_fn
    assert "modal.dataset.keyHandlerAttached = '0'" in close_fn or 'modal.dataset.keyHandlerAttached = "0"' in close_fn
    assert "_previousActiveElement" in close_fn


def test_system_prompt_editor_close_helper_is_available_to_editor_and_save_paths():
    source = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    editor_fn = _extract_js_function(source, "showSystemPromptEditor")
    save_fn = _extract_js_function(source, "saveSystemPromptSection")

    assert "closeSystemPromptEditor" in editor_fn
    assert "closeSystemPromptEditor" in save_fn

    close_idx = source.index("function closeSystemPromptEditor(")
    save_idx = source.index("function saveSystemPromptSection(")
    assert close_idx < save_idx


def test_chat_ui_close_handler_references_are_defined():
    source = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")

    refs = set(re.findall(r"addEventListener\([^\n;]*,\s*(close[A-Z][A-Za-z0-9_]*)\)", source))
    refs |= set(re.findall(r"\b(close[A-Z][A-Za-z0-9_]*)\s*\(", source))

    defs = set(re.findall(r"\b(?:async\s+function|function)\s+(close[A-Z][A-Za-z0-9_]*)\s*\(", source))
    defs |= set(re.findall(r"\b(?:const|let|var)\s+(close[A-Z][A-Za-z0-9_]*)\s*=", source))

    missing = sorted(refs - defs)
    assert not missing
