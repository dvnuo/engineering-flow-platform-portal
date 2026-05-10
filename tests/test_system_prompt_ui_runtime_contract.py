from pathlib import Path

from _js_extract_helpers import _extract_js_function


def test_system_prompt_ui_runtime_helpers_and_opencode_contract():
    source = Path('app/static/js/chat_ui.js').read_text(encoding='utf-8')
    assert 'function isOpenCodeAgent(agent)' in source
    assert 'function getAgentRuntimeType(agent)' in source
    assert 'OpenCode Rules' in source
    assert 'AGENTS.md' in source
    assert 'OpenCode runtime only supports AGENTS.md' in source


def test_load_system_prompt_config_runtime_aware_sections():
    source = Path('app/static/js/chat_ui.js').read_text(encoding='utf-8')
    fn = _extract_js_function(source, 'loadSystemPromptConfig')
    assert 'getSystemPromptUiModel(currentAgent, config || {})' in fn
    assert 'ui.sections' in fn

    model_fn = _extract_js_function(source, 'getSystemPromptUiModel')
    assert "['soul', 'user', 'agents', 'memory', 'daily_notes']" in model_fn
    assert "sections: ['agents']" in model_fn


def test_editor_and_save_force_enabled_for_opencode_agents():
    source = Path('app/static/js/chat_ui.js').read_text(encoding='utf-8')
    editor_fn = _extract_js_function(source, 'showSystemPromptEditor')
    save_fn = _extract_js_function(source, 'saveSystemPromptSection')

    assert 'AGENTS.md Configuration' in editor_fn
    assert 'enabledCheckbox.checked = true;' in editor_fn
    assert 'enabledCheckbox.disabled = true;' in editor_fn
    assert 'AGENTS.md is always active for OpenCode.' in editor_fn

    assert "if (runtimeIsOpenCode) enabled = true;" in save_fn
    assert "if (runtimeIsOpenCode && section !== 'agents')" in save_fn
