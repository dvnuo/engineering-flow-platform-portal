"""Lightweight file-route contract tests (no app.main boot)."""

from pathlib import Path


def _web_source() -> str:
    return Path("app/web.py").read_text(encoding="utf-8")


def _proxy_source() -> str:
    return Path("app/api/proxy.py").read_text(encoding="utf-8")


def _app_template_source() -> str:
    return Path("app/templates/app.html").read_text(encoding="utf-8")


def test_agent_file_upload_route_contract_exists_and_is_guarded():
    source = _web_source()

    assert '@router.post("/a/{agent_id}/api/files/upload")' in source
    assert "MAX_FILE_SIZE = 10 * 1024 * 1024" in source
    assert "_forward_runtime_multipart(" in source
    assert 'subpath="api/files/upload"' in source


def test_agent_file_preview_route_contract_exists():
    source = _web_source()

    assert '@router.get("/a/{agent_id}/api/files/{file_id}/preview")' in source
    assert 'subpath=f"api/files/{file_id}/preview"' in source
    assert "query_items=[(\"max_chars\", str(max_chars))]" in source


def test_agent_file_download_route_contract_exists():
    source = _web_source()

    assert '@router.get("/a/{agent_id}/api/files/download")' in source
    assert 'subpath="api/files/download"' in source
    assert 'query_items = [("paths", p) for p in file_paths]' in source


def test_files_panel_route_contract_uses_runtime_files_list_subpath():
    source = _web_source()

    assert '@router.get("/app/agents/{agent_id}/files/panel")' in source
    assert 'subpath="api/files/list"' in source


def test_proxy_route_contract_covers_runtime_file_subpaths():
    source = _proxy_source()

    assert '@router.api_route("/a/{agent_id}/{subpath:path}"' in source
    assert "_filter_proxy_query_items(request.query_params.multi_items())" in source
    assert "proxy_service.forward(" in source


def test_composer_upload_input_accept_contract_is_present_in_template():
    html = _app_template_source()

    assert 'id="upload-input"' in html
    assert 'type="file"' in html
    assert "multiple" in html
    assert 'accept="image/jpeg,image/png,image/webp,image/gif,.pdf,.docx,.xlsx,.csv,.txt"' in html
