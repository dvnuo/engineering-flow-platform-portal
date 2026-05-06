from pathlib import Path

from fastapi.testclient import TestClient


def test_http_middleware_generates_portal_trace_id_not_browser_supplied():
    source = Path("app/main.py").read_text(encoding="utf-8")
    start = source.index("async def bind_request_log_context")
    end = source.index("\n\n@app.on_event", start)
    fn_source = source[start:end]
    assert "trace_id = generate_trace_id()" in fn_source
    assert 'request.headers.get("X-Trace-Id")' not in fn_source
    assert 'request.headers.get("X-Request-Id")' not in fn_source


def test_http_middleware_response_trace_id_not_browser_supplied():
    from app.main import app

    client = TestClient(app)
    response = client.get("/health", headers={"X-Trace-Id": "browser-spoof"})
    assert response.status_code == 200
    assert response.headers["X-Trace-Id"]
    assert response.headers["X-Trace-Id"] != "browser-spoof"
