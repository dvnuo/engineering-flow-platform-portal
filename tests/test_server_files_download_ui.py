from pathlib import Path


def test_chat_ui_server_files_download_uses_anchor_not_window_open():
    content = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")

    start = content.find("function downloadSelectedFiles(paths)")
    assert start != -1
    end = content.find("async function previewServerFile", start)
    assert end != -1

    function_body = content[start:end]

    assert "window.open" not in function_body
    assert "document.createElement('a')" in function_body or 'document.createElement("a")' in function_body
    assert "link.download" in function_body
    assert "url.searchParams.append('paths'" in function_body or 'url.searchParams.append("paths"' in function_body
