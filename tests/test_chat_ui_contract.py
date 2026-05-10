from pathlib import Path


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
