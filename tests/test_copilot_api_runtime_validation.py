from fastapi.testclient import TestClient

from app.main import app


def test_copilot_start_invalid_runtime_returns_400(monkeypatch):
    client = TestClient(app)

    class U:
        id = 1

    from app.api import copilot as mod
    app.dependency_overrides[mod.get_current_user] = lambda: U()
    try:
        r = client.post('/api/copilot/auth/start', json={"runtime_type": "bad_runtime"})
        assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()
