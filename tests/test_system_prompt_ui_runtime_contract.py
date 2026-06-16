from pathlib import Path

from _js_extract_helpers import _extract_js_function


def test_system_prompt_ui_uses_single_runtime_contract():
    source = Path('app/static/js/chat_ui.js').read_text(encoding='utf-8')
    assert 'function getAgentRuntimeType(agent)' in source
    assert 'AGENTS' in source
    assert 'OpenCode runtime only supports AGENTS.md' not in source


def test_load_system_prompt_config_runtime_aware_sections():
    source = Path('app/static/js/chat_ui.js').read_text(encoding='utf-8')
    fn = _extract_js_function(source, 'loadSystemPromptConfig')
    assert 'getSystemPromptUiModel(currentAgent, config || {})' in fn
    assert 'ui.sections' in fn

    model_fn = _extract_js_function(source, 'getSystemPromptUiModel')
    assert 'Array.isArray(config.sections)' in model_fn
    assert "sections = ['agents']" in model_fn
    assert 'sectionConfig.can_disable !== false' in model_fn


def test_editor_and_save_do_not_force_runtime_specific_prompt_rules():
    source = Path('app/static/js/chat_ui.js').read_text(encoding='utf-8')
    editor_fn = _extract_js_function(source, 'showSystemPromptEditor')
    save_fn = _extract_js_function(source, 'saveSystemPromptSection')

    assert "var title = label + ' Configuration';" in editor_fn
    assert 'enabledCheckbox.checked = canDisable ? enabled !== false : true;' in editor_fn
    assert 'enabledCheckbox.disabled = !canDisable;' in editor_fn
    assert 'AGENTS.md is always active' not in editor_fn

    assert "runtimeIsOpenCode" not in save_fn
    assert 'enabledCheckbox.disabled ? true : enabledCheckbox.checked' in save_fn
