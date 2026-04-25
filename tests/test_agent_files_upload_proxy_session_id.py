from pathlib import Path
import asyncio
import sys
from types import SimpleNamespace

from starlette.datastructures import QueryParams

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.utils.runtime_proxy_query import _filter_runtime_file_upload_query_items


def test_filter_runtime_file_upload_query_items_keeps_only_session_id():
    request = SimpleNamespace(
        query_params=QueryParams(
            [
                ("session_id", "webchat_20260423_010203_abcd1234"),
                ("token", "secret"),
                ("limit", "10"),
            ]
        )
    )

    assert _filter_runtime_file_upload_query_items(request) == [
        ("session_id", "webchat_20260423_010203_abcd1234")
    ]


def test_filter_runtime_file_upload_query_items_drops_unknown_noise_keys():
    request = SimpleNamespace(
        query_params=QueryParams(
            [
                ("foo", "1"),
                ("session_id", "webchat_20260424_111111_keepme"),
                ("x-session", "shadow"),
                ("bar", "2"),
            ]
        )
    )

    assert _filter_runtime_file_upload_query_items(request) == [
        ("session_id", "webchat_20260424_111111_keepme")
    ]


def test_filter_runtime_file_upload_query_items_keeps_session_value_unchanged_with_noise():
    request = SimpleNamespace(
        query_params=QueryParams(
            [
                ("token", "secret"),
                ("session_id", "webchat_20260424_235959_xyz98765"),
                ("session_id", "webchat_20260424_235959_xyz98765_dup"),
                ("limit", "20"),
            ]
        )
    )

    assert _filter_runtime_file_upload_query_items(request) == [
        ("session_id", "webchat_20260424_235959_xyz98765"),
        ("session_id", "webchat_20260424_235959_xyz98765_dup"),
    ]


def test_agent_files_upload_route_uses_query_filter_helper():
    web_source = Path("app/web.py").read_text(encoding="utf-8")

    assert '@router.post("/a/{agent_id}/api/files/upload")' in web_source
    assert "from app.utils.runtime_proxy_query import _filter_runtime_file_upload_query_items" in web_source
    assert "query_items = _filter_runtime_file_upload_query_items(request)" in web_source


def test_runtime_upload_query_allowlist_contains_session_id():
    query_source = Path("app/utils/runtime_proxy_query.py").read_text(encoding="utf-8")

    assert 'allowlisted_query = {"session_id"}' in query_source


def test_filter_runtime_file_upload_query_items_returns_empty_for_empty_query():
    request = SimpleNamespace(query_params=QueryParams([]))

    assert _filter_runtime_file_upload_query_items(request) == []


def test_agent_files_upload_forwards_only_session_id_query_items(monkeypatch):
    import app.web as web_module

    class _FakeUploadFile:
        filename = "doc.txt"
        content_type = "text/plain"

        async def read(self):
            return b"hello"

    class _FakeRequest:
        query_params = QueryParams(
            [
                ("session_id", "webchat_keep_me"),
                ("token", "drop-me"),
                ("debug", "1"),
            ]
        )

        async def form(self):
            return {"file": _FakeUploadFile()}

    class _FakeDB:
        def close(self):
            return None

    class _FakeAgentRepo:
        def __init__(self, db):
            self.db = db

        def get_by_id(self, _agent_id):
            return SimpleNamespace(id="agent-1", owner_user_id=1, visibility="private")

    captured_forward_call: dict = {}

    async def _fake_forward_runtime_multipart(**kwargs):
        captured_forward_call.update(kwargs)
        return 200, b'{"ok":true}', "application/json"

    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda request: SimpleNamespace(id=1, role="admin"))
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _FakeDB())
    monkeypatch.setattr(web_module, "AgentRepository", _FakeAgentRepo)
    monkeypatch.setattr(web_module, "_can_access", lambda agent, user: True)
    monkeypatch.setattr(web_module, "_forward_runtime_multipart", _fake_forward_runtime_multipart)

    response = asyncio.run(web_module.agent_files_upload("agent-1", _FakeRequest()))

    assert response.status_code == 200
    assert response.body == b'{"ok":true}'
    assert captured_forward_call["query_items"] == [("session_id", "webchat_keep_me")]


def test_agent_files_upload_forwards_only_allowlisted_session_ids_even_with_noise(monkeypatch):
    import app.web as web_module

    class _FakeUploadFile:
        filename = "notes.txt"
        content_type = "text/plain"

        async def read(self):
            return b"hello"

    class _FakeRequest:
        query_params = QueryParams(
            [
                ("debug", "1"),
                ("session_id", "webchat_keep_primary"),
                ("token", "drop-me"),
                ("session_id", "webchat_keep_secondary"),
                ("x-trace-id", "drop-me-too"),
            ]
        )

        async def form(self):
            return {"file": _FakeUploadFile()}

    class _FakeDB:
        def close(self):
            return None

    class _FakeAgentRepo:
        def __init__(self, db):
            self.db = db

        def get_by_id(self, _agent_id):
            return SimpleNamespace(id="agent-2", owner_user_id=1, visibility="private")

    captured_forward_call: dict = {}

    async def _fake_forward_runtime_multipart(**kwargs):
        captured_forward_call.update(kwargs)
        return 200, b'{"ok":true}', "application/json"

    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda request: SimpleNamespace(id=1, role="admin"))
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _FakeDB())
    monkeypatch.setattr(web_module, "AgentRepository", _FakeAgentRepo)
    monkeypatch.setattr(web_module, "_can_access", lambda agent, user: True)
    monkeypatch.setattr(web_module, "_forward_runtime_multipart", _fake_forward_runtime_multipart)

    response = asyncio.run(web_module.agent_files_upload("agent-2", _FakeRequest()))

    assert response.status_code == 200
    assert captured_forward_call["query_items"] == [
        ("session_id", "webchat_keep_primary"),
        ("session_id", "webchat_keep_secondary"),
    ]
