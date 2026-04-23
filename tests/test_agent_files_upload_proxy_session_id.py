from types import SimpleNamespace

from fastapi.testclient import TestClient


class _DB:
    def close(self):
        return None


def test_agent_files_upload_forwards_session_id_query_item(monkeypatch):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=321, username="portal-user", nickname="Portal User", role="user")
    fake_agent = SimpleNamespace(id="agent-1", owner_user_id=321, visibility="private", status="running", name="Agent One")

    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: fake_user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(
        web_module,
        "AgentRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
    )

    captured = {}

    async def _fake_forward_multipart(**kwargs):
        captured.update(kwargs)
        return 200, b'{"ok": true}', "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward_multipart", _fake_forward_multipart)

    client = TestClient(app)
    response = client.post(
        "/a/agent-1/api/files/upload?session_id=webchat_20260423_010203_abcd1234",
        files={"file": ("note.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 200
    assert captured["subpath"] == "api/files/upload"
    assert ("session_id", "webchat_20260423_010203_abcd1234") in captured["query_items"]
