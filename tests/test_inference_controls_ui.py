from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_assistant_composer_uses_compact_inference_settings_popover():
    template = (ROOT / "app/templates/app.html").read_text(encoding="utf-8")
    css = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")

    assert '<details id="composer-inference-settings"' in template
    assert 'id="composer-inference-summary-text"' in template
    assert 'id="composer-reasoning-select"' in template
    assert 'id="composer-context-select"' in template
    assert ".composer-inference-popover" in css
    assert "position: absolute" in css


def test_task_and_delegation_forms_share_collapsed_run_settings():
    task_template = (ROOT / "app/templates/partials/task_create_panel.html").read_text(encoding="utf-8")
    script = (ROOT / "app/static/js/chat_ui.js").read_text(encoding="utf-8")

    assert '<details class="portal-inference-settings"' in task_template
    assert 'name="model_override"' in task_template
    assert 'name="reasoning_effort"' in task_template
    assert 'name="max_context_tokens"' in task_template
    assert "function inferenceSettingsFieldsHtml()" in script
    assert "...collectInferenceSettings(formEl)" in script
    assert "populateInferenceSettingsForForm(form" in script
