from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.proxy_service import build_portal_agent_identity_headers


def _web_source() -> str:
    return Path("app/web.py").read_text(encoding="utf-8")


def _proxy_api_source() -> str:
    return Path("app/api/proxy.py").read_text(encoding="utf-8")


def test_runtime_panel_wiring_uses_proxy_forward_with_identity_headers():
    web_source = _web_source()

    assert "async def _forward_runtime(" in web_source
    assert "proxy_service.forward(" in web_source
    assert "extra_headers=_portal_extra_headers(user, agent)" in web_source


def test_file_upload_route_contract_uses_forward_runtime_multipart_and_query_filter():
    web_source = _web_source()

    assert '@router.post("/a/{agent_id}/api/files/upload")' in web_source
    assert "query_items = _filter_runtime_file_upload_query_items(request)" in web_source
    assert "_forward_runtime_multipart(" in web_source
    assert 'subpath="api/files/upload"' in web_source
    assert "query_items=query_items" in web_source


def test_file_upload_route_contract_keeps_oversize_guard():
    web_source = _web_source()

    assert "MAX_FILE_SIZE = 10 * 1024 * 1024" in web_source
    assert "File too large. Maximum size is 10MB." in web_source


def test_server_files_upload_contract_uses_forward_runtime_multipart_with_path_data_and_passthrough():
    web_source = _web_source()

    assert '@router.post("/a/{agent_id}/api/server-files/upload")' in web_source
    assert "if not _can_write(agent, user):" in web_source
    assert "_forward_runtime_multipart(" in web_source
    assert 'subpath="api/server-files/upload"' in web_source
    assert 'data={"path": target_path}' in web_source
    assert "return Response(content=content_bytes, media_type=content_type, status_code=status_code)" in web_source


def test_identity_headers_helper_adds_portal_user_and_agent_fields():
    fake_user = SimpleNamespace(id=321, username="portal-user", nickname="Portal User", role="user")
    fake_agent = SimpleNamespace(name="Agent One")

    assert build_portal_agent_identity_headers(fake_user, fake_agent) == {
        "X-Portal-Author-Source": "portal",
        "X-Portal-User-Id": "321",
        "X-Portal-User-Name": "Portal User",
        "X-Portal-Agent-Name": "Agent One",
    }


def test_non_execution_proxy_path_contract_does_not_enrich_payload():
    proxy_source = _proxy_api_source()

    assert "def _is_direct_chat_execution_path(method: str, subpath: str) -> bool:" in proxy_source
    assert 'normalized in {"api/chat", "api/chat/stream"}' in proxy_source
    assert "if is_direct_chat_execution and request_body:" in proxy_source
    assert "parsed_payload = _enrich_chat_payload_with_runtime_metadata" in proxy_source
    assert "filtered_query_items = _filter_proxy_query_items(request.query_params.multi_items())" in proxy_source
