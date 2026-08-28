"""Static assets are compressed, cacheable, and mostly off the critical path.

Portal shipped ~2MB decoded on every load: no compression at all (chat_ui.js
went out as 655KB of raw bytes even when the client offered gzip), no
Cache-Control, and ~1.2MB of render-blocking scripts in <head>.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

BASE_HTML = Path("app/templates/base.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_large_static_assets_are_served_compressed(client):
    raw = client.get("/static/js/chat_ui.js", headers={"Accept-Encoding": "identity"})
    gzipped = client.get("/static/js/chat_ui.js", headers={"Accept-Encoding": "gzip"})
    assert raw.status_code == gzipped.status_code == 200
    assert "content-encoding" not in raw.headers
    assert gzipped.headers.get("content-encoding") == "gzip"
    # Caches must not hand the compressed body to a client that cannot take it.
    assert "accept-encoding" in gzipped.headers.get("vary", "").lower()


def test_static_assets_always_revalidate_but_keep_a_validator(client):
    # Filenames are not content-hashed, so any max-age lets a deploy serve stale
    # JS until it expires. no-cache still caches the bytes; it just checks the
    # ETag first, so an unchanged asset costs a 304 rather than a full transfer.
    response = client.get("/static/css/app.css")
    assert response.status_code == 200
    assert response.headers.get("cache-control") == "no-cache, must-revalidate"
    assert response.headers.get("etag")


def test_unchanged_assets_revalidate_to_304(client):
    first = client.get("/static/css/app.css")
    again = client.get("/static/css/app.css", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304


def test_compression_threshold_is_set_so_tiny_responses_stay_raw(client):
    # Below minimum_size, compression only adds overhead. Every file currently
    # in app/static happens to exceed it, so assert the configured threshold.
    main_source = Path("app/main.py").read_text(encoding="utf-8")
    assert "minimum_size=1024" in main_source


def test_streaming_routes_are_not_wrapped_in_gzip():
    # Compression is mounted on the /static sub-app rather than app-wide so the
    # chat proxy's text/event-stream responses never sit behind the compressor.
    lines = Path("app/main.py").read_text(encoding="utf-8").splitlines()
    gzip_lines = [line.strip() for line in lines if "add_middleware(GZipMiddleware" in line]
    assert gzip_lines == ["static_app.add_middleware(GZipMiddleware, minimum_size=1024)"]


@pytest.mark.parametrize("asset", ["htmx.min.js", "highlight.min.js", "lucide.min.js"])
def test_heavy_libraries_are_deferred(asset):
    # Paths go through static_url() for cache versioning, so match the tag shape.
    assert f"""<script defer src="{{{{ static_url('lib/{asset}') }}}}"></script>""" in BASE_HTML


def test_markdown_it_stays_blocking_because_chat_ui_uses_it_at_top_level():
    # chat_ui.js builds its renderer at module scope, and chat_ui.js itself must
    # stay non-deferred so chatApp() exists before the deferred Alpine boots.
    assert """<script src="{{ static_url('lib/markdown-it.min.js') }}"></script>""" in BASE_HTML
    chat_ui = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    assert "const md = window.markdownit({" in chat_ui
    app_html = Path("app/templates/app.html").read_text(encoding="utf-8")
    assert """<script src="{{ static_url('js/chat_ui.js') }}"></script>""" in app_html
