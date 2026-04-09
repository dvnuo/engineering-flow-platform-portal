from types import SimpleNamespace

from fastapi.testclient import TestClient


class _StubRequirementBundleService:
    def list_bundles(self):
        return [
            {
                "bundle_id": "RB-checkout",
                "title": "Checkout",
                "domain": "payments",
                "status": "draft",
                "bundle_ref": {
                    "repo": "octo/assets",
                    "path": "requirement-bundles/payments/checkout",
                    "branch": "bundle/checkout/abc123",
                },
                "manifest_ref": {
                    "repo": "octo/assets",
                    "path": "requirement-bundles/payments/checkout",
                    "branch": "bundle/checkout/abc123",
                },
                "requirements_exists": True,
                "test_cases_exists": False,
                "last_commit_sha": "abc123",
            }
        ]

    def create_bundle(self, _payload):
        return SimpleNamespace(
            repo="octo/assets",
            path="requirement-bundles/payments/checkout",
            branch="bundle/checkout/abc123",
        )

    def inspect_bundle(self, bundle_ref):
        return {
            "manifest_ref": {
                "repo": bundle_ref.repo,
                "path": bundle_ref.path,
                "branch": bundle_ref.branch,
            },
            "bundle_ref": {
                "repo": bundle_ref.repo,
                "path": bundle_ref.path,
                "branch": bundle_ref.branch,
            },
            "manifest": {
                "bundle_id": "RB-checkout",
                "title": "Checkout",
                "status": "draft",
                "scope": {"domain": "payments"},
            },
            "requirements_file": "requirements.yaml",
            "test_cases_file": "test-cases.yaml",
            "requirements_exists": True,
            "test_cases_exists": False,
            "last_commit_sha": "abc123",
        }


def test_requirement_bundles_api_requires_auth_for_get(monkeypatch):
    from app.main import app

    client = TestClient(app)
    response = client.get("/api/requirement-bundles")
    assert response.status_code == 401


def test_requirement_bundles_api_get_returns_list_items(monkeypatch):
    from app.main import app
    import app.api.requirement_bundles as requirement_bundles_api

    monkeypatch.setattr(requirement_bundles_api, "requirement_bundle_service", _StubRequirementBundleService())
    app.dependency_overrides[requirement_bundles_api.get_current_user] = lambda: SimpleNamespace(id=7, role="user")

    try:
        client = TestClient(app)
        response = client.get("/api/requirement-bundles")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["bundle_id"] == "RB-checkout"
        assert data[0]["bundle_ref"]["path"] == "requirement-bundles/payments/checkout"
    finally:
        app.dependency_overrides.clear()


def test_requirement_bundles_api_post_creates_and_returns_detail(monkeypatch):
    from app.main import app
    import app.api.requirement_bundles as requirement_bundles_api

    monkeypatch.setattr(requirement_bundles_api, "requirement_bundle_service", _StubRequirementBundleService())
    app.dependency_overrides[requirement_bundles_api.get_current_user] = lambda: SimpleNamespace(id=7, role="user")

    try:
        client = TestClient(app)
        response = client.post(
            "/api/requirement-bundles",
            json={
                "title": "Checkout",
                "domain": "payments",
                "slug": "checkout",
                "base_branch": "main",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["bundle_ref"]["repo"] == "octo/assets"
        assert data["manifest"]["bundle_id"] == "RB-checkout"
    finally:
        app.dependency_overrides.clear()
