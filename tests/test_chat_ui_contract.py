from pathlib import Path


def test_chat_ui_contains_provider_retry_contract():
    source = Path('app/static/js/chat_ui.js').read_text(encoding='utf-8')
    assert 'provider.retry' in source
    assert 'Provider API retrying' in source
    assert 'Check Runtime Profile LLM API key/base URL/proxy' in source
    assert 'request_id' in source
