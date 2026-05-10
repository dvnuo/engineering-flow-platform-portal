from app.api.proxy import _requires_write_access


def test_system_prompt_paths_require_write_only_for_mutations():
    assert _requires_write_access('GET', 'api/agent/system-prompt/config') is False
    assert _requires_write_access('PUT', 'api/agent/system-prompt/config') is True
    assert _requires_write_access('GET', 'api/agent/system-prompt/agents') is False
    assert _requires_write_access('PUT', 'api/agent/system-prompt/agents') is True
    assert _requires_write_access('PATCH', 'api/agent/system-prompt/agents') is True
    assert _requires_write_access('DELETE', 'api/agent/system-prompt/agents') is True


def test_existing_sessions_and_server_files_contract_is_preserved():
    assert _requires_write_access('GET', 'api/sessions') is False
    assert _requires_write_access('PUT', 'api/sessions/abc') is True
    assert _requires_write_access('GET', 'api/server-files') is True
