from types import SimpleNamespace

from fastapi.testclient import TestClient


class _StubRequirementBundleService:
    def __init__(self):
        self.force_refresh_calls = []

    def list_bundles(self, *, force_refresh: bool = False):
        self.force_refresh_calls.append(force_refresh)
        return [
            {
                "bundle_id": "RB-checkout",
                "title": "Checkout",
                "domain": "payments",
                "status": "draft",
                "bundle_label": "Requirement Bundle",
                "artifacts": None,
                "bundle_ref": {"repo": "octo/assets", "path": "requirement-bundles/payments/checkout", "branch": "bundle/checkout/abc123"},
                "manifest_ref": {"repo": "octo/assets", "path": "requirement-bundles/payments/checkout", "branch": "bundle/checkout/abc123"},
                "requirements_exists": True,
                "test_cases_exists": False,
                "last_commit_sha": "abc123",
            }
        ]

    def create_bundle(self, payload):
        assert payload.title == "Checkout"
        assert payload.domain == "payments"
        return SimpleNamespace(repo="octo/assets", path="requirement-bundles/payments/checkout", branch="bundle/checkout/abc123")

    def inspect_bundle(self, bundle_ref):
        return {
            "manifest_ref": {"repo": bundle_ref.repo, "path": bundle_ref.path, "branch": bundle_ref.branch},
            "bundle_ref": {"repo": bundle_ref.repo, "path": bundle_ref.path, "branch": bundle_ref.branch},
            "manifest": {"bundle_id": "RB-checkout", "title": "Checkout", "status": "draft", "scope": {"domain": "payments"}},
            "bundle_label": "Requirement Bundle",
            "artifacts": [
                {"artifact_key": "requirements", "file_path": "requirements.yaml", "exists": True},
                {"artifact_key": "test_cases", "file_path": "test-cases.yaml", "exists": False},
            ],
            "requirements_file": "requirements.yaml",
            "test_cases_file": "test-cases.yaml",
            "requirements_exists": True,
            "test_cases_exists": False,
            "last_commit_sha": "abc123",
        }


def test_requirement_bundles_api_requires_auth_for_get():
    from app.main import app

    client = TestClient(app)
    response = client.get("/api/requirement-bundles")
    assert response.status_code == 401


def test_requirement_bundles_api_get_returns_bundle_fields(monkeypatch):
    from app.main import app
    import app.api.requirement_bundles as requirement_bundles_api

    stub = _StubRequirementBundleService()
    monkeypatch.setattr(requirement_bundles_api, "requirement_bundle_service", stub)
    app.dependency_overrides[requirement_bundles_api.get_current_user] = lambda: SimpleNamespace(id=7, role="user")

    try:
        client = TestClient(app)
        response = client.get("/api/requirement-bundles")
        assert response.status_code == 200
        data = response.json()
        assert data[0]["bundle_label"] == "Requirement Bundle"
        assert data[0]["requirements_exists"] is True
        assert stub.force_refresh_calls == [False]
    finally:
        app.dependency_overrides.clear()


def test_requirement_bundles_api_get_refresh_forces_service_refresh(monkeypatch):
    from app.main import app
    import app.api.requirement_bundles as requirement_bundles_api

    stub = _StubRequirementBundleService()
    monkeypatch.setattr(requirement_bundles_api, "requirement_bundle_service", stub)
    app.dependency_overrides[requirement_bundles_api.get_current_user] = lambda: SimpleNamespace(id=7, role="user")

    try:
        client = TestClient(app)
        response = client.get("/api/requirement-bundles?refresh=1")
        assert response.status_code == 200
        assert stub.force_refresh_calls == [True]
    finally:
        app.dependency_overrides.clear()


def test_requirement_bundles_api_post_creates_bundle(monkeypatch):
    from app.main import app
    import app.api.requirement_bundles as requirement_bundles_api

    monkeypatch.setattr(requirement_bundles_api, "requirement_bundle_service", _StubRequirementBundleService())
    app.dependency_overrides[requirement_bundles_api.get_current_user] = lambda: SimpleNamespace(id=7, role="user")

    try:
        client = TestClient(app)
        response = client.post(
            "/api/requirement-bundles",
            json={"title": "Checkout", "domain": "payments", "slug": "checkout", "base_branch": "main"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["bundle_label"] == "Requirement Bundle"
        assert "artifacts" in data
        assert "requirements_file" in data
        assert "test_cases_file" in data
    finally:
        app.dependency_overrides.clear()
