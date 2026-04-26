"""Lightweight source-contract tests for proxy API routes."""

from pathlib import Path


def _proxy_source() -> str:
    return Path("app/api/proxy.py").read_text(encoding="utf-8")


def _web_source() -> str:
    return Path("app/web.py").read_text(encoding="utf-8")


def test_proxy_chat_contract_wiring_exists():
    source = _proxy_source()

    assert '@router.api_route("/a/{agent_id}/{subpath:path}"' in source
    assert 'normalized in {"api/chat", "api/chat/stream"}' in source
    assert "proxy_service.forward(" in source


def test_proxy_events_websocket_contract_exists():
    source = _proxy_source()

    assert '@router.websocket("/a/{agent_id}/api/events")' in source
    assert 'upstream_url = f"{ws_base}/api/events"' in source


def test_proxy_files_upload_contract_uses_multipart_forwarding_in_web_route():
    source = _web_source()

    assert '@router.post("/a/{agent_id}/api/files/upload")' in source
    assert "_forward_runtime_multipart(" in source
    assert 'subpath="api/files/upload"' in source


def test_proxy_files_preview_contract_is_present():
    source = _web_source()

    assert '@router.get("/a/{agent_id}/api/files/{file_id}/preview")' in source
    assert 'subpath=f"api/files/{file_id}/preview"' in source


def test_proxy_git_info_contract_is_covered_by_generic_proxy_route():
    source = _proxy_source()

    assert '@router.api_route("/a/{agent_id}/{subpath:path}"' in source
    assert "method=request.method" in source
    assert "subpath=subpath" in source
