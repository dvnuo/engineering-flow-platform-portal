"""Chat attachments served through the generic proxy never run under the Portal's origin."""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.proxy import _harden_attachment_response_headers


def test_hardening_keeps_runtime_disposition_for_plain_content():
    headers = _harden_attachment_response_headers(
        "text/plain",
        {"Content-Disposition": 'inline; filename="notes.txt"'},
    )
    assert headers["Content-Disposition"] == 'inline; filename="notes.txt"'
    assert headers["X-Content-Type-Options"] == "nosniff"


def test_hardening_forces_download_for_active_markup():
    inline_html = _harden_attachment_response_headers(
        "text/html; charset=utf-8",
        {"Content-Disposition": "inline; filename=\"page.html\"; filename*=UTF-8''page.html"},
    )
    assert inline_html["Content-Disposition"] == "attachment; filename=\"page.html\"; filename*=UTF-8''page.html"

    svg_without_disposition = _harden_attachment_response_headers("image/svg+xml", {})
    assert svg_without_disposition["Content-Disposition"] == 'attachment; filename="download"'

    already_download = _harden_attachment_response_headers(
        "application/xml", {"Content-Disposition": 'attachment; filename="a.xml"'}
    )
    assert already_download["Content-Disposition"] == 'attachment; filename="a.xml"'


def test_hardening_ignores_other_upstream_headers():
    headers = _harden_attachment_response_headers(
        "application/pdf",
        {"Set-Cookie": "x=1", "content-disposition": 'inline; filename="a.pdf"'},
    )
    assert headers == {"Content-Disposition": 'inline; filename="a.pdf"', "X-Content-Type-Options": "nosniff"}


def test_proxy_serves_uploaded_html_as_download(monkeypatch):
    from app.main import app
    import app.api.proxy as proxy_module

    fake_user = SimpleNamespace(id=88, username="owner", nickname="Owner", role="user")
    fake_agent = SimpleNamespace(id="agent-1", owner_user_id=88, visibility="private", status="running", name="Agent One")

    def _override_user():
        return fake_user

    def _override_db():
        yield object()

    app.dependency_overrides[proxy_module.get_current_user] = _override_user
    app.dependency_overrides[proxy_module.get_db] = _override_db
    try:
        monkeypatch.setattr(
            proxy_module,
            "AgentRepository",
            lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
        )
        captured = {}

        async def _fake_forward(**kwargs):
            captured.update(kwargs)
            if kwargs.get("return_response_headers"):
                return 200, b"<script>alert(1)</script>", "text/html", {"Content-Disposition": 'inline; filename="page.html"'}
            return 200, b"{}", "application/json"

        monkeypatch.setattr(proxy_module.proxy_service, "forward", _fake_forward)

        client = TestClient(app)
        response = client.get("/a/agent-1/api/files/abc123?session_id=s1")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert captured["return_response_headers"] is True
    assert response.headers["content-disposition"] == 'attachment; filename="page.html"'
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.content == b"<script>alert(1)</script>"
