from types import SimpleNamespace

from fastapi.testclient import TestClient


class _DB:
    def close(self):
        return None


def _detail_for(template_id: str, artifact_exists: dict[str, bool] | None = None):
    if artifact_exists is None:
        artifact_exists = {}
    mapping = {
        "requirement.v1": {
            "label": "Requirement Bundle",
            "path": "requirement-bundles/payments/checkout-flow",
            "artifacts": [
                ("requirements", "requirements.yaml"),
                ("test_cases", "test-cases.yaml"),
            ],
        },
        "research.v1": {
            "label": "Research Bundle",
            "path": "requirement-bundles/research/payments/checkout-flow",
            "artifacts": [("research_notes", "research-notes.yaml")],
        },
        "development.v1": {
            "label": "Development Bundle",
            "path": "requirement-bundles/development/payments/checkout-flow",
            "artifacts": [("implementation_plan", "implementation-plan.yaml")],
        },
        "operations.v1": {
            "label": "Operations Bundle",
            "path": "requirement-bundles/operations/payments/checkout-flow",
            "artifacts": [("runbook", "runbook.yaml")],
        },
    }[template_id]
    artifacts = [
        SimpleNamespace(artifact_key=k, file_path=f, exists=artifact_exists.get(k, True))
        for k, f in mapping["artifacts"]
    ]
    return SimpleNamespace(
        manifest_ref=SimpleNamespace(repo="octo/engineering-flow-platform-assets", path=mapping["path"], branch="main"),
        bundle_ref=SimpleNamespace(repo="octo/engineering-flow-platform-assets", path=mapping["path"], branch="bundle/checkout-flow/deadbeef"),
        manifest={"bundle_id": "RB-checkout-flow", "title": "Checkout Flow", "status": "draft", "scope": {"domain": "payments"}},
        template_id=template_id,
        template_label=mapping["label"],
        template_version=1,
        artifacts=artifacts,
        requirements_file="requirements.yaml" if template_id == "requirement.v1" else None,
        test_cases_file="test-cases.yaml" if template_id == "requirement.v1" else None,
        requirements_exists=artifact_exists.get("requirements", True) if template_id == "requirement.v1" else None,
        test_cases_exists=artifact_exists.get("test_cases", True) if template_id == "requirement.v1" else None,
        last_commit_sha="abc123",
    )


def _setup_client(monkeypatch, logged_in=True):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=11, username="portal", nickname="Portal", role="user")
    state = {"template_id": "requirement.v1", "artifact_exists": {"requirements": True, "test_cases": True}}

    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _r: fake_user if logged_in else None)

    def _create_bundle(form):
        state["template_id"] = form.template_id
        return SimpleNamespace(repo="octo/engineering-flow-platform-assets", path="requirement-bundles/payments/checkout-flow", branch="bundle/checkout-flow/deadbeef")

    def _inspect_bundle(_bundle_ref):
        return _detail_for(state["template_id"], artifact_exists=state["artifact_exists"])

    monkeypatch.setattr(web_module.requirement_bundle_service, "create_bundle", _create_bundle)
    monkeypatch.setattr(web_module.requirement_bundle_service, "inspect_bundle", _inspect_bundle)

    return TestClient(app), state


def test_bundle_page_title_and_modal_support_template(monkeypatch):
    client, _state = _setup_client(monkeypatch, logged_in=True)
    response = client.get("/app/requirement-bundles")
    assert response.status_code == 200
    assert "Bundles" in response.text
    app_page = client.get("/app")
    assert 'name="template_id"' in app_page.text
    assert "Slug (optional, used for repo path / bundle_id / branch)" in app_page.text


def test_create_route_accepts_template_id(monkeypatch):
    client, state = _setup_client(monkeypatch, logged_in=True)
    response = client.post(
        "/app/requirement-bundles/create",
        data={"template_id": "research.v1", "title": "Checkout Flow", "domain": "payments", "slug": "", "base_branch": "main"},
    )
    assert response.status_code == 200
    assert state["template_id"] == "research.v1"
    assert "Research Bundle" in response.text
    assert "research-notes.yaml" in response.text


def test_detail_panel_renders_template_artifacts_without_task_forms(monkeypatch):
    client, state = _setup_client(monkeypatch, logged_in=True)

    for template_id, expected_artifact in [
        ("requirement.v1", "requirements.yaml"),
        ("research.v1", "research-notes.yaml"),
        ("development.v1", "implementation-plan.yaml"),
        ("operations.v1", "runbook.yaml"),
    ]:
        state["template_id"] = template_id
        response = client.get("/app/requirement-bundles/open", params={"repo": "octo/engineering-flow-platform-assets", "path": "any", "branch": "main"})
        assert response.status_code == 200
        assert expected_artifact in response.text
        assert "<form" not in response.text
        assert "Create + Dispatch Task" not in response.text


def test_open_route_does_not_show_opened_success_banner(monkeypatch):
    client, state = _setup_client(monkeypatch, logged_in=True)
    state["template_id"] = "requirement.v1"
    response = client.get(
        "/app/requirement-bundles/open",
        params={"repo": "octo/engineering-flow-platform-assets", "path": "any", "branch": "main"},
    )
    assert response.status_code == 200
    assert "Bundle opened successfully." not in response.text


def test_missing_artifact_renders_missing_status(monkeypatch):
    client, state = _setup_client(monkeypatch, logged_in=True)
    state["template_id"] = "requirement.v1"
    state["artifact_exists"] = {"requirements": False, "test_cases": True}

    response = client.get("/app/requirement-bundles/open", params={"repo": "octo/engineering-flow-platform-assets", "path": "any", "branch": "main"})
    assert response.status_code == 200
    assert "requirements.yaml" in response.text
    assert "Missing" in response.text


def test_complete_bundle_renders_artifact_summary_only(monkeypatch):
    client, state = _setup_client(monkeypatch, logged_in=True)
    state["template_id"] = "requirement.v1"
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

    detail = _detail_for("requirement.v1", artifact_exists={"requirements": True, "test_cases": True})
    vm = web_module._build_bundle_detail_view_model(
        detail,
        web_module.list_bundle_templates(),
    )

    assert vm["title"] == "Checkout Flow"
    assert vm["domain"] == "payments"
    assert vm["artifact_ready_count"] == 2
    assert [item["file_path"] for item in vm["artifacts"]] == ["requirements.yaml", "test-cases.yaml"]
