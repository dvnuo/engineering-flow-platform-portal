from types import SimpleNamespace

from fastapi.testclient import TestClient


def test_git_info_injects_trusted_portal_identity_headers(monkeypatch):
    from app.main import app
    import app.api.agents as agents_module

    fake_user = SimpleNamespace(id=88, username="owner", nickname=" Owner\n", role="user")
    fake_agent = SimpleNamespace(
        id="agent-1",
        owner_user_id=88,
        visibility="private",
        status="running",
        repo_url="https://example.com/repo.git",
    )

    def _override_user():
        return fake_user

    def _override_db():
        yield object()

    app.dependency_overrides[agents_module.get_current_user] = _override_user
    app.dependency_overrides[agents_module.get_db] = _override_db

    monkeypatch.setattr(
        agents_module,
        "AgentRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
    )

    captured = {}

    async def _fake_forward(**kwargs):
        captured.update(kwargs)
        return 200, b'{"commit_id":"abc123","repo_url":""}', "application/json"

    monkeypatch.setattr(agents_module.proxy_service, "forward", _fake_forward)

    client = TestClient(app)
    response = client.get("/api/agents/agent-1/git-info")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["commit_id"] == "abc123"
    assert response.json()["repo_url"] == "https://example.com/repo.git"
    assert captured["subpath"] == "api/git-info"
    assert captured["extra_headers"] == {
        "X-Portal-Author-Source": "portal",
        "X-Portal-User-Id": "88",
        "X-Portal-User-Name": "Owner",
    }
