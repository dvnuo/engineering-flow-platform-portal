from pathlib import Path
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


def test_agent_files_upload_query_contract_keeps_only_session_id_with_noise_and_route_wiring():
    # Keep this lightweight: no app.web import (avoids DB/sqlalchemy dependency in minimal test envs).
    request = SimpleNamespace(
        query_params=QueryParams(
            [
                ("session_id", "webchat_keep_me"),
                ("token", "drop-me"),
                ("debug", "1"),
            ]
        )
    )
    assert _filter_runtime_file_upload_query_items(request) == [("session_id", "webchat_keep_me")]

    web_source = Path("app/web.py").read_text(encoding="utf-8")
    assert '@router.post("/a/{agent_id}/api/files/upload")' in web_source
    assert "query_items = _filter_runtime_file_upload_query_items(request)" in web_source
    assert "query_items=query_items" in web_source


def test_agent_files_upload_query_contract_preserves_multiple_session_ids_order_and_route_wiring():
    # Keep this lightweight: lock helper behavior + source-level route wiring contract.
    request = SimpleNamespace(
        query_params=QueryParams(
            [
                ("debug", "1"),
                ("session_id", "webchat_keep_primary"),
                ("token", "drop-me"),
                ("session_id", "webchat_keep_secondary"),
                ("x-trace-id", "drop-me-too"),
            ]
        )
    )
    assert _filter_runtime_file_upload_query_items(request) == [
        ("session_id", "webchat_keep_primary"),
        ("session_id", "webchat_keep_secondary"),
    ]

    web_source = Path("app/web.py").read_text(encoding="utf-8")
    assert '@router.post("/a/{agent_id}/api/files/upload")' in web_source
    assert "query_items = _filter_runtime_file_upload_query_items(request)" in web_source
    assert "query_items=query_items" in web_source
    assert "_forward_runtime_multipart(" in web_source
