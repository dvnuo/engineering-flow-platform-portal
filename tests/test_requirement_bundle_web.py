from types import SimpleNamespace

from fastapi.testclient import TestClient


class _DB:
    def close(self):
        return None


def _detail(artifact_exists: dict[str, bool] | None = None):
    if artifact_exists is None:
        artifact_exists = {}
    artifacts = [
        SimpleNamespace(artifact_key="requirements", file_path="requirements.yaml", exists=artifact_exists.get("requirements", True)),
        SimpleNamespace(artifact_key="test_cases", file_path="test-cases.yaml", exists=artifact_exists.get("test_cases", True)),
    ]
    return SimpleNamespace(
        manifest_ref=SimpleNamespace(repo="octo/engineering-flow-platform-assets", path="requirement-bundles/payments/checkout-flow", branch="main"),
        bundle_ref=SimpleNamespace(repo="octo/engineering-flow-platform-assets", path="requirement-bundles/payments/checkout-flow", branch="bundle/checkout-flow/deadbeef"),
        manifest={"bundle_id": "RB-checkout-flow", "title": "Checkout Flow", "status": "draft", "scope": {"domain": "payments"}},
        bundle_label="Requirement Bundle",
        artifacts=artifacts,
        requirements_file="requirements.yaml",
        test_cases_file="test-cases.yaml",
        requirements_exists=artifact_exists.get("requirements", True),
        test_cases_exists=artifact_exists.get("test_cases", True),
        last_commit_sha="abc123",
    )


def _setup_client(monkeypatch, logged_in=True):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=11, username="portal", nickname="Portal", role="user")
    state = {"artifact_exists": {"requirements": True, "test_cases": True}, "created_title": ""}

    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _r: fake_user if logged_in else None)

    def _create_bundle(form):
        state["created_title"] = form.title
        return SimpleNamespace(repo="octo/engineering-flow-platform-assets", path="requirement-bundles/payments/checkout-flow", branch="bundle/checkout-flow/deadbeef")

    def _inspect_bundle(_bundle_ref):
        return _detail(artifact_exists=state["artifact_exists"])

    monkeypatch.setattr(web_module.requirement_bundle_service, "create_bundle", _create_bundle)
    monkeypatch.setattr(web_module.requirement_bundle_service, "inspect_bundle", _inspect_bundle)

    return TestClient(app), state


def test_bundle_page_title_and_modal(monkeypatch):
    client, _state = _setup_client(monkeypatch, logged_in=True)
    response = client.get("/app/requirement-bundles")
    assert response.status_code == 200
    assert "Bundles" in response.text
    app_page = client.get("/app")
    assert "Slug (optional, used for repo path / branch)" in app_page.text


def test_create_route_creates_bundle(monkeypatch):
    client, state = _setup_client(monkeypatch, logged_in=True)
    response = client.post(
        "/app/requirement-bundles/create",
        data={"title": "Checkout Flow", "domain": "payments", "slug": "", "base_branch": "main"},
    )
    assert response.status_code == 200
    assert state["created_title"] == "Checkout Flow"
    assert "Requirement Bundle" in response.text
    assert "requirements.yaml" in response.text


def test_detail_panel_renders_artifacts_without_task_forms(monkeypatch):
    client, _state = _setup_client(monkeypatch, logged_in=True)
    response = client.get("/app/requirement-bundles/open", params={"repo": "octo/engineering-flow-platform-assets", "path": "any", "branch": "main"})
    assert response.status_code == 200
    assert "requirements.yaml" in response.text
    assert "test-cases.yaml" in response.text
    assert "<form" not in response.text
    assert "Create + Dispatch Task" not in response.text


def test_open_route_does_not_show_opened_success_banner(monkeypatch):
    client, _state = _setup_client(monkeypatch, logged_in=True)
    response = client.get(
        "/app/requirement-bundles/open",
        params={"repo": "octo/engineering-flow-platform-assets", "path": "any", "branch": "main"},
    )
    assert response.status_code == 200
    assert "Bundle opened successfully." not in response.text


def test_missing_artifact_renders_missing_status(monkeypatch):
    client, state = _setup_client(monkeypatch, logged_in=True)
    state["artifact_exists"] = {"requirements": False, "test_cases": True}

    response = client.get("/app/requirement-bundles/open", params={"repo": "octo/engineering-flow-platform-assets", "path": "any", "branch": "main"})
    assert response.status_code == 200
    assert "requirements.yaml" in response.text
    assert "Missing" in response.text


def test_complete_bundle_renders_artifact_summary_only(monkeypatch):
    client, state = _setup_client(monkeypatch, logged_in=True)
    state["artifact_exists"] = {"requirements": True, "test_cases": True}

    response = client.get(
        "/app/requirement-bundles/open",
        params={"repo": "octo/engineering-flow-platform-assets", "path": "any", "branch": "main"},
    )
    assert response.status_code == 200
    assert "2 / 2 artifacts ready" in response.text
    assert "requirements.yaml" in response.text
    assert "test-cases.yaml" in response.text


def test_detail_view_model_contains_metadata_and_artifacts_only():
    import app.web as web_module

    detail = _detail(artifact_exists={"requirements": True, "test_cases": True})
    vm = web_module._build_bundle_detail_view_model(detail)

    assert vm["title"] == "Checkout Flow"
    assert vm["bundle_ref_label"] == "Checkout Flow"
    assert "requirement-bundles/payments/checkout-flow" in vm["subtitle"]
    assert "Requirement Bundle" in vm["subtitle"]
    assert "draft" in vm["subtitle"]
    assert "RB-checkout-flow" not in vm["subtitle"]
    assert vm["domain"] == "payments"
    assert vm["artifact_ready_count"] == 2
    assert [item["file_path"] for item in vm["artifacts"]] == ["requirements.yaml", "test-cases.yaml"]
