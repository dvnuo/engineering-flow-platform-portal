from pathlib import Path
import sys
from types import SimpleNamespace

from starlette.datastructures import QueryParams

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.web import _filter_runtime_file_upload_query_items


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


def test_agent_files_upload_route_uses_query_filter_helper():
    web_source = Path("app/web.py").read_text(encoding="utf-8")

    assert "def _filter_runtime_file_upload_query_items(request: Request)" in web_source
    assert "query_items = _filter_runtime_file_upload_query_items(request)" in web_source
