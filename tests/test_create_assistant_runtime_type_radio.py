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


def test_create_runtime_type_radio_js_helpers_exist():
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    assert "function populateRuntimeTypeRadioGroup(" in js
    assert "function getCreateDefaultRuntimeType(" in js
    assert 'defaults?.default_runtime_type || "opencode"' in js
    assert 'formData.get("runtime_type")' in js
    assert "runtime_type: runtimeType" in js


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
