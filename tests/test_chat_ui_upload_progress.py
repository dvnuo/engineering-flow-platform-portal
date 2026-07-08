"""B5: the attachment upload wires real progress into the pending-file card."""

from pathlib import Path


def _chat_ui_source() -> str:
    return Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")


def test_upload_wires_xhr_progress():
    source = _chat_ui_source()
    # Progress is tracked from the XHR upload and reflected in the UI.
    assert "xhr.upload.addEventListener('progress'" in source
    assert "pf.uploadProgress" in source
    # The handler re-renders the preview so the bar updates live.
    assert "renderInputPreview();" in source


def test_pending_card_renders_progress_bar():
    source = _chat_ui_source()
    assert "input-preview-progress" in source
    # The bar width is driven by the tracked percentage.
    assert "width:${pct}%" in source
