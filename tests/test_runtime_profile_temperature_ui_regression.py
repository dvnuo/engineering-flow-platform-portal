from pathlib import Path


def test_temperature_gating_hooks_present_in_chat_ui_js():
    source = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    assert "function managedModelSupportsTemperature(value)" in source
    assert "function updateTemperatureInputState(root)" in source
    assert 'if (event.target?.id === "llm_model") updateTemperatureInputState(root);' in source


def test_runtime_profile_temperature_input_has_data_hook():
    source = Path("app/templates/partials/runtime_profile_panel.html").read_text(encoding="utf-8")
    assert "data-llm-temperature-input" in source
