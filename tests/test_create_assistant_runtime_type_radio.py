from pathlib import Path
import re


def _create_form_block() -> str:
    html = Path("app/templates/app.html").read_text(encoding="utf-8")
    assert '<form id="create-form"' in html
    return html.split('<form id="create-form"', 1)[1].split("</form>", 1)[0]


def test_create_assistant_runtime_type_is_radio_group_not_select():
    block = _create_form_block()
    assert '<select name="runtime_type" id="create-runtime-type-select">' not in block
    assert 'id="create-runtime-type-select"' in block
    assert 'role="radiogroup"' in block
    assert 'type="radio"' in block
    assert 'name="runtime_type"' in block
    assert 'value="opencode"' in block
    assert 'value="native"' in block


def test_create_assistant_opencode_radio_is_static_default():
    block = _create_form_block()
    assert re.search(
        r'<input[^>]+type="radio"[^>]+name="runtime_type"[^>]+value="opencode"[^>]+checked',
        block,
        re.S,
    ) or re.search(
        r'<input[^>]+type="radio"[^>]+value="opencode"[^>]+name="runtime_type"[^>]+checked',
        block,
        re.S,
    )


def test_create_assistant_static_runtime_order_is_opencode_first():
    block = _create_form_block()
    assert block.index('value="opencode"') < block.index('value="native"')
    assert block.index("OpenCode Runtime") < block.index("EFP Native Runtime")


def test_create_runtime_type_radio_js_helpers_exist():
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    assert "function populateRuntimeTypeRadioGroup(" in js
    assert "function getCreateDefaultRuntimeType(" in js
    assert "function getCreateRuntimeTypes(" in js
    assert "function isRuntimeTypeAvailable(" in js
    assert 'formData.get("runtime_type")' in js
    assert "runtime_type: runtimeType" in js


def test_create_runtime_type_dynamic_helpers_force_opencode_first_and_default():
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    assert "function getCreateRuntimeTypes(" in js
    assert "function isRuntimeTypeAvailable(" in js
    assert 'isRuntimeTypeAvailable(defaults, "opencode")' in js
    assert 'return "opencode"' in js
    assert "getCreateRuntimeTypes(defaults)" in js
    assert "selectedValue || getCreateDefaultRuntimeType(defaults)" in js
    assert 'runtimeTypeValue = runtimeTypeControl?.value || getCreateDefaultRuntimeType(defaults)' in js


def test_create_runtime_type_radio_population_does_not_default_to_backend_native():
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    start = js.index("function populateRuntimeTypeRadioGroup(")
    end = js.index("function updateCreateRuntimeTypeHint", start)
    block = js[start:end]
    assert 'defaults?.default_runtime_type || "opencode"' not in block
    assert "getCreateDefaultRuntimeType(defaults)" in block


def test_create_runtime_type_default_falls_back_to_first_available_when_opencode_missing():
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    start = js.index("function getCreateDefaultRuntimeType(")
    end = js.index("function runtimeTypeDescription", start)
    block = js[start:end]
    assert 'isRuntimeTypeAvailable(defaults, "opencode")' in block
    assert "isRuntimeTypeAvailable(defaults, normalized)" in block
    assert "const firstRuntimeType = getCreateRuntimeTypes(defaults)[0]?.value" in block
    assert "return normalizeRuntimeTypeValue(firstRuntimeType || normalized, defaults)" in block


def test_runtime_type_radio_css_exists():
    css = Path("app/static/css/app.css").read_text(encoding="utf-8")
    assert ".runtime-type-radio-group" in css
    assert ".runtime-type-radio-card" in css
    assert ".runtime-type-radio-input:checked + .runtime-type-radio-card" in css
    assert ".runtime-type-radio-input:focus-visible + .runtime-type-radio-card" in css


def test_config_default_runtime_type_is_opencode_but_contract_legacy_default_stays_native():
    config = Path("app/config.py").read_text(encoding="utf-8")
    contract = Path("app/contracts/runtime_types.py").read_text(encoding="utf-8")
    schema = Path("app/schemas/agent.py").read_text(encoding="utf-8")
    assert 'default_runtime_type: str = Field(default="opencode"' in config
    assert 'DEFAULT_RUNTIME_TYPE = "native"' in contract
    assert 'runtime_type: str = "native"' in schema


def test_k8s_manifests_set_default_runtime_type_opencode():
    for path in [
        "k8s/efp-portal-deployment.yaml",
        "k8s/portal-git-clone/efp-portal-deployment.yaml",
    ]:
        text = Path(path).read_text(encoding="utf-8")
        assert "name: DEFAULT_RUNTIME_TYPE" in text
        assert 'value: "opencode"' in text
