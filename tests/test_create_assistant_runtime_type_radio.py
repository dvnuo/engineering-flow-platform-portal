from pathlib import Path


def _create_form_block() -> str:
    html = Path("app/templates/app.html").read_text(encoding="utf-8")
    assert '<form id="create-form"' in html
    return html.split('<form id="create-form"', 1)[1].split("</form>", 1)[0]


def _edit_form_block() -> str:
    html = Path("app/templates/app.html").read_text(encoding="utf-8")
    assert '<form id="edit-form"' in html
    return html.split('<form id="edit-form"', 1)[1].split("</form>", 1)[0]


def test_create_assistant_has_runtime_type_control():
    block = _create_form_block()
    assert 'name="runtime_type"' in block
    assert "create-runtime-type-select" in block
    assert "runtime-type-radio" in block
    # Labelled "Engine" for members; the field name stays runtime_type.
    assert '<legend>{{ t("Engine") }}</legend>' in block
    # The cards come from the server so ENABLED_RUNTIME_TYPES decides what a
    # member can pick, even before /api/agents/defaults has answered.
    assert "{% for option in create_runtime_type_options %}" in block
    assert 'value="{{ option.value }}"' in block
    assert 'value="native"' not in block
    assert 'value="opencode"' not in block


def test_edit_assistant_shows_runtime_type_as_readonly_wizard_field():
    block = _edit_form_block()
    assert 'name="runtime_type"' not in block
    assert "edit-runtime-type-select" not in block
    assert "edit-runtime-type-display" in block
    assert '<label>{{ t("Engine") }}</label>' in block
    assert "The engine is fixed after creation." in block
    assert 'data-edit-step-panel="runtime"' in block
    # Connector settings are per member now, so the wizard has no profile step.
    assert 'data-edit-step-panel="profile"' not in block
    assert "edit-runtime-profile-select" not in block
    assert 'data-edit-step-panel="instructions"' in block
    assert 'data-edit-step-panel="skills"' in block
    assert 'data-edit-step-panel="review"' in block
    assert 'data-edit-load-branches="agent-settings"' in block
    assert 'data-edit-load-branches="skills"' in block
    assert "edit-agent-review" in block


def test_wizard_engine_copy_is_the_contract_text_in_english():
    # web.runtime_type_label() / runtime_type_description() spell their t()
    # keys out per marker so scripts/i18n_check.py can read them; in English
    # they must still be the constants /api/agents/defaults serves.
    from app.contracts.runtime_type import ALLOWED_RUNTIME_TYPES, RUNTIME_TYPE_DESCRIPTIONS, RUNTIME_TYPE_LABELS
    from app.web import runtime_type_description, runtime_type_label

    for marker in ALLOWED_RUNTIME_TYPES:
        assert runtime_type_label(marker) == RUNTIME_TYPE_LABELS[marker]
        assert runtime_type_description(marker) == RUNTIME_TYPE_DESCRIPTIONS[marker]
    assert runtime_type_label("other") == "other"
    assert runtime_type_description("other") == ""


def test_create_runtime_type_js_helpers_present():
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    assert "function populateRuntimeTypeRadioGroup(" in js
    assert "function getCreateDefaultRuntimeType(" in js
    assert "function getCreateRuntimeTypes(" in js
    # The wizard renders only the engines the defaults payload marks enabled.
    assert "item?.enabled !== false" in js
    assert 'formData.get("runtime_type")' in js
    assert "runtime_type: runtimeType" in js
    assert "form.dataset.runtimeType" in js
    assert "updates.runtime_type" not in js


def test_config_and_schema_use_single_native_runtime_default():
    config = Path("app/config.py").read_text(encoding="utf-8")
    contract = Path("app/contracts/runtime_type.py").read_text(encoding="utf-8")
    schema = Path("app/schemas/agent.py").read_text(encoding="utf-8")
    assert "DEFAULT_RUNTIME_TYPE" in config
    assert "ENABLED_RUNTIME_TYPES" in config
    assert 'enabled_runtime_types: str = Field(default="native"' in config
    assert 'DEFAULT_RUNTIME_TYPE = "native"' in contract
    assert 'ALLOWED_RUNTIME_TYPES = ("native", "opencode")' in contract
    assert 'runtime_type: str = "native"' in schema


def test_k8s_manifests_no_longer_default_to_legacy_runtime_choice():
    for path in [
        "k8s/efp-portal-deployment.yaml",
        "k8s/portal-git-clone/efp-portal-deployment.yaml",
    ]:
        text = Path(path).read_text(encoding="utf-8")
        assert 'value: "opencode"' not in text
