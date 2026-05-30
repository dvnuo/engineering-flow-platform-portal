from pathlib import Path


def _create_form_block() -> str:
    html = Path("app/templates/app.html").read_text(encoding="utf-8")
    assert '<form id="create-form"' in html
    return html.split('<form id="create-form"', 1)[1].split("</form>", 1)[0]


def _edit_form_block() -> str:
    html = Path("app/templates/app.html").read_text(encoding="utf-8")
    assert '<form id="edit-form"' in html
    return html.split('<form id="edit-form"', 1)[1].split("</form>", 1)[0]


def test_create_assistant_has_no_runtime_type_control():
    block = _create_form_block()
    assert 'name="runtime_type"' not in block
    assert "create-runtime-type-select" not in block
    assert "runtime-type-radio" not in block
    assert "Runtime Type" not in block


def test_edit_assistant_has_no_runtime_type_control():
    block = _edit_form_block()
    assert 'name="runtime_type"' not in block
    assert "edit-runtime-type-select" not in block
    assert "Runtime Type" not in block
    assert "Changing runtime type" not in block


def test_create_runtime_type_js_helpers_removed():
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    assert "function populateRuntimeTypeRadioGroup(" not in js
    assert "function getCreateDefaultRuntimeType(" not in js
    assert "function getCreateRuntimeTypes(" not in js
    assert "function isRuntimeTypeAvailable(" not in js
    assert 'formData.get("runtime_type")' not in js
    assert "runtime_type: runtimeType" not in js


def test_config_and_schema_use_single_native_runtime_default():
    config = Path("app/config.py").read_text(encoding="utf-8")
    contract = Path("app/contracts/runtime_type.py").read_text(encoding="utf-8")
    schema = Path("app/schemas/agent.py").read_text(encoding="utf-8")
    assert 'default_runtime_type: str = Field(default="native"' in config
    assert 'DEFAULT_RUNTIME_TYPE = "native"' in contract
    assert 'runtime_type: str = "native"' in schema


def test_k8s_manifests_no_longer_default_to_legacy_runtime_choice():
    for path in [
        "k8s/efp-portal-deployment.yaml",
        "k8s/portal-git-clone/efp-portal-deployment.yaml",
    ]:
        text = Path(path).read_text(encoding="utf-8")
        assert 'value: "opencode"' not in text
