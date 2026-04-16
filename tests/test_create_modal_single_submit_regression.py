from pathlib import Path


def test_single_submit_helpers_exist_in_chat_ui_js():
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")

    assert "function getFormSubmitButton(" in js
    assert "function beginSingleSubmit(" in js
    assert "function endSingleSubmit(" in js
    assert 'form.dataset.submitting === "true"' in js


def test_create_modal_submit_handlers_use_begin_single_submit():
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")

    assert "beginSingleSubmit(form, { pendingText: \"Creating...\", closeButton: dom.closeCreateRuntimeProfileModal })" in js
    assert "beginSingleSubmit(form, { pendingText: \"Creating...\", closeButton: document.getElementById(\"close-create-modal\") })" in js
    assert "beginSingleSubmit(form, { pendingText: \"Creating...\", closeButton: dom.closeCreateBundleModal })" in js


def test_create_modal_close_handlers_check_submitting_guard():
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")

    assert 'if (dom.createRuntimeProfileForm?.dataset.submitting === "true") return;' in js
    assert 'if (dom.createBundleForm?.dataset.submitting === "true") return;' in js
    assert 'if (document.getElementById("create-form")?.dataset.submitting === "true") return;' in js


def test_disabled_styles_for_modal_buttons_exist():
    css = Path("app/static/css/app.css").read_text(encoding="utf-8")

    assert ".portal-btn:disabled" in css
    assert ".portal-modal-close:disabled" in css
    assert ".stack button:disabled" in css
