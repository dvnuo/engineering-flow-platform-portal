"""Portal proxy contract for workspace file management.

- server-files/download is streamed (not buffered into Portal memory) so a
  large workspace download no longer spikes the control plane (A1).
- The new B1/B2 write ops (mkdir/new-file/move) are covered by the existing
  api/server-files write-access rule, so the generic proxy forwards them
  behind a write-access check with no new routes.
"""

from pathlib import Path

import app.api.proxy as proxy_module


def _proxy_source() -> str:
    return Path("app/api/proxy.py").read_text(encoding="utf-8")


def test_download_branch_streams_instead_of_buffering():
    source = _proxy_source()
    # The download branch must open a streaming upstream and hand back a
    # StreamingResponse rather than reading the whole body into memory.
    assert "normalized_subpath == \"api/server-files/download\"" in source
    assert "download_client.stream(" in source
    assert "_select_download_response_headers(" in source
    assert "StreamingResponse(" in source
    # The old buffered form for this branch is gone.
    assert "return Response(status_code=status_code, content=content, media_type=content_type, headers=response_headers)" not in source


def test_download_headers_preserve_filename_and_length():
    headers = proxy_module._select_download_response_headers(
        {
            "content-disposition": 'attachment; filename="x.zip"',
            "content-type": "application/zip",
            "content-length": "1234",
            "set-cookie": "should-be-dropped",
        }
    )
    assert headers["content-disposition"] == 'attachment; filename="x.zip"'
    assert headers["content-length"] == "1234"
    assert headers["content-type"] == "application/zip"
    assert "set-cookie" not in headers


def test_new_write_ops_require_write_access():
    for op in ("mkdir", "new-file", "move"):
        assert proxy_module._requires_write_access("POST", f"api/server-files/{op}") is True
    # Reads stay classified as before (whole server-files namespace is write-gated).
    assert proxy_module._requires_write_access("POST", "api/server-files/move") is True
