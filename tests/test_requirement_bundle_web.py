import json
from types import SimpleNamespace

from fastapi.testclient import TestClient


class _DB:
    def close(self):
        return None


class _FakeAgentRepo:
    def __init__(self, _db, agents):
        self._agents = agents

    def list_all(self):
        return list(self._agents)

    def get_by_id(self, agent_id):
        for agent in self._agents:
            if agent.id == agent_id:
                return agent
        return None


class _FakeTaskRepo:
    def __init__(self, _db, store):
        self._store = store

    def create(self, **kwargs):
        task = SimpleNamespace(id=f"task-{len(self._store) + 1}", **kwargs)
        self._store.append(task)
        return task


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
    fake_agent = SimpleNamespace(id="agent-1", name="Agent One", owner_user_id=11, visibility="private")
    created_tasks = []
    state = {"template_id": "requirement.v1", "artifact_exists": {"requirements": True, "test_cases": True}}

    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(web_module, "AgentRepository", lambda db: _FakeAgentRepo(db, [fake_agent]))
    monkeypatch.setattr(web_module, "AgentTaskRepository", lambda db: _FakeTaskRepo(db, created_tasks))
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _r: fake_user if logged_in else None)

    scheduled_task_ids = []
    monkeypatch.setattr(web_module.task_dispatcher_service, "dispatch_task_in_background", lambda task_id: scheduled_task_ids.append(task_id))

    def _create_bundle(form):
        state["template_id"] = form.template_id
        return SimpleNamespace(repo="octo/engineering-flow-platform-assets", path="requirement-bundles/payments/checkout-flow", branch="bundle/checkout-flow/deadbeef")

    def _inspect_bundle(_bundle_ref):
        return _detail_for(state["template_id"], artifact_exists=state["artifact_exists"])

    monkeypatch.setattr(web_module.requirement_bundle_service, "create_bundle", _create_bundle)
    monkeypatch.setattr(web_module.requirement_bundle_service, "inspect_bundle", _inspect_bundle)

    return TestClient(app), created_tasks, state, scheduled_task_ids


def test_bundle_page_title_and_modal_support_template(monkeypatch):
    client, _tasks, _state, _scheduled = _setup_client(monkeypatch, logged_in=True)
    response = client.get("/app/requirement-bundles")
    assert response.status_code == 200
    assert "Bundles" in response.text
    app_page = client.get("/app")
    assert 'name="template_id"' in app_page.text
    assert "Slug (optional, used for repo path / bundle_id / branch)" in app_page.text


def test_create_route_accepts_template_id(monkeypatch):
    client, _tasks, state, _scheduled = _setup_client(monkeypatch, logged_in=True)
    response = client.post(
        "/app/requirement-bundles/create",
        data={"template_id": "research.v1", "title": "Checkout Flow", "domain": "payments", "slug": "", "base_branch": "main"},
    )
    assert response.status_code == 200
    assert state["template_id"] == "research.v1"
    assert "Research Bundle" in response.text
    assert "research-notes.yaml" in response.text


def test_detail_panel_dynamic_actions_and_artifacts(monkeypatch):
    client, _tasks, state, _scheduled = _setup_client(monkeypatch, logged_in=True)

    for template_id, action_label in [
        ("requirement.v1", "Collect Requirements"),
        ("research.v1", "Collect Research Notes"),
        ("development.v1", "Generate Implementation Plan"),
        ("operations.v1", "Generate Runbook"),
    ]:
        state["template_id"] = template_id
        response = client.get("/app/requirement-bundles/open", params={"repo": "octo/engineering-flow-platform-assets", "path": "any", "branch": "main"})
        assert response.status_code == 200
        assert action_label in response.text
        assert "/app/requirement-bundles/task-shortcuts/run" in response.text


def test_dispatch_actions_create_bundle_action_task(monkeypatch):
    client, created_tasks, state, scheduled = _setup_client(monkeypatch, logged_in=True)
    matrix = [
        ("requirement.v1", "collect_requirements_to_bundle", {"jira_sources": "JIRA-1"}),
        ("requirement.v1", "design_test_cases_from_bundle", {}),
        ("research.v1", "collect_research_notes_to_bundle", {"jira_sources": "JIRA-2"}),
        ("development.v1", "generate_implementation_plan_from_bundle", {}),
        ("operations.v1", "generate_runbook_from_bundle", {}),
    ]
    for template_id, action_id, extra in matrix:
        state["template_id"] = template_id
        response = client.post(
            "/app/requirement-bundles/task-shortcuts/run",
            data={
                "template_id": template_id,
                "task_template_id": action_id,
                "action_agent_id": "agent-1",
                "bundle_repo": "octo/engineering-flow-platform-assets",
                "bundle_path": "requirement-bundles/payments/checkout-flow",
                "bundle_branch": "bundle/checkout-flow/deadbeef",
                "manifest_repo": "octo/engineering-flow-platform-assets",
                "manifest_path": "requirement-bundles/payments/checkout-flow",
                "manifest_branch": "main",
                **extra,
            },
        )
        assert response.status_code == 200

    assert len(created_tasks) == 5
    assert all(task.task_type == "bundle_action_task" for task in created_tasks)
    assert scheduled == ["task-1", "task-2", "task-3", "task-4", "task-5"]
    payload = json.loads(created_tasks[0].input_payload_json)
    assert payload["bundle_ref"]["repo"] == "octo/engineering-flow-platform-assets"
    assert payload["skill_name"] == "collect_requirements_to_bundle"


def test_legacy_wrappers_still_work_and_create_bundle_action_task(monkeypatch):
    client, created_tasks, state, _scheduled = _setup_client(monkeypatch, logged_in=True)
    state["template_id"] = "requirement.v1"

    collect = client.post(
        "/app/requirement-bundles/collect",
        data={
            "collect_agent_id": "agent-1",
            "bundle_repo": "octo/engineering-flow-platform-assets",
            "bundle_path": "requirement-bundles/payments/checkout-flow",
            "bundle_branch": "bundle/checkout-flow/deadbeef",
            "manifest_repo": "octo/engineering-flow-platform-assets",
            "manifest_path": "requirement-bundles/payments/checkout-flow",
            "manifest_branch": "main",
            "jira_sources": "JIRA-1",
        },
    )
    design = client.post(
        "/app/requirement-bundles/design-test-cases",
        data={
            "design_agent_id": "agent-1",
            "bundle_repo": "octo/engineering-flow-platform-assets",
            "bundle_path": "requirement-bundles/payments/checkout-flow",
            "bundle_branch": "bundle/checkout-flow/deadbeef",
            "manifest_repo": "octo/engineering-flow-platform-assets",
            "manifest_path": "requirement-bundles/payments/checkout-flow",
            "manifest_branch": "main",
        },
    )
    assert collect.status_code == 200
    assert design.status_code == 200
    assert all(task.task_type == "bundle_action_task" for task in created_tasks)


def test_collect_rejects_empty_and_figma_only(monkeypatch):
    client, created_tasks, state, _scheduled = _setup_client(monkeypatch, logged_in=True)
    state["template_id"] = "requirement.v1"

    empty_resp = client.post(
        "/app/requirement-bundles/task-shortcuts/run",
        data={
            "template_id": "requirement.v1",
            "task_template_id": "collect_requirements_to_bundle",
            "action_agent_id": "agent-1",
            "bundle_repo": "octo/engineering-flow-platform-assets",
            "bundle_path": "requirement-bundles/payments/checkout-flow",
            "bundle_branch": "bundle/checkout-flow/deadbeef",
            "manifest_repo": "octo/engineering-flow-platform-assets",
            "manifest_path": "requirement-bundles/payments/checkout-flow",
            "manifest_branch": "main",
        },
    )
    figma_resp = client.post(
        "/app/requirement-bundles/task-shortcuts/run",
        data={
            "template_id": "requirement.v1",
            "task_template_id": "collect_requirements_to_bundle",
            "action_agent_id": "agent-1",
            "bundle_repo": "octo/engineering-flow-platform-assets",
            "bundle_path": "requirement-bundles/payments/checkout-flow",
            "bundle_branch": "bundle/checkout-flow/deadbeef",
            "manifest_repo": "octo/engineering-flow-platform-assets",
            "manifest_path": "requirement-bundles/payments/checkout-flow",
            "manifest_branch": "main",
            "figma_sources": "https://www.figma.com/file/abc123",
        },
    )
    assert "At least one Jira, Confluence, or GitHub Docs source is required." in empty_resp.text
    assert "Figma-only collection is not supported in MVP" in figma_resp.text
    assert len(created_tasks) == 0


def test_open_route_does_not_show_opened_success_banner(monkeypatch):
    client, _tasks, state, _scheduled = _setup_client(monkeypatch, logged_in=True)
    state["template_id"] = "requirement.v1"
    response = client.get(
        "/app/requirement-bundles/open",
        params={"repo": "octo/engineering-flow-platform-assets", "path": "any", "branch": "main"},
    )
    assert response.status_code == 200
    assert "Bundle opened successfully." not in response.text


def test_collect_validation_error_preserves_form_state(monkeypatch):
    client, _tasks, state, _scheduled = _setup_client(monkeypatch, logged_in=True)
    state["template_id"] = "requirement.v1"
    figma_url = "https://www.figma.com/file/abc123"
    response = client.post(
        "/app/requirement-bundles/task-shortcuts/run",
        data={
            "template_id": "requirement.v1",
            "task_template_id": "collect_requirements_to_bundle",
            "action_agent_id": "agent-1",
            "bundle_repo": "octo/engineering-flow-platform-assets",
            "bundle_path": "requirement-bundles/payments/checkout-flow",
            "bundle_branch": "bundle/checkout-flow/deadbeef",
            "manifest_repo": "octo/engineering-flow-platform-assets",
            "manifest_path": "requirement-bundles/payments/checkout-flow",
            "manifest_branch": "main",
            "figma_sources": figma_url,
        },
    )
    assert response.status_code == 200
    assert "Figma-only collection is not supported in MVP" in response.text
    assert figma_url in response.text
    assert 'value="agent-1" selected' in response.text


def test_missing_action_agent_rerenders_panel_instead_of_http_400(monkeypatch):
    client, _tasks, state, _scheduled = _setup_client(monkeypatch, logged_in=True)
    state["template_id"] = "requirement.v1"
    response = client.post(
        "/app/requirement-bundles/task-shortcuts/run",
        data={
            "template_id": "requirement.v1",
            "task_template_id": "collect_requirements_to_bundle",
            "bundle_repo": "octo/engineering-flow-platform-assets",
            "bundle_path": "requirement-bundles/payments/checkout-flow",
            "bundle_branch": "bundle/checkout-flow/deadbeef",
            "manifest_repo": "octo/engineering-flow-platform-assets",
            "manifest_path": "requirement-bundles/payments/checkout-flow",
            "manifest_branch": "main",
            "jira_sources": "PAY-1",
        },
    )
    assert response.status_code == 200
    assert "Action agent is required." in response.text


def test_design_disabled_and_message_when_requirements_missing(monkeypatch):
    client, _tasks, state, _scheduled = _setup_client(monkeypatch, logged_in=True)
    state["template_id"] = "requirement.v1"
    state["artifact_exists"] = {"requirements": False, "test_cases": True}

    response = client.get("/app/requirement-bundles/open", params={"repo": "octo/engineering-flow-platform-assets", "path": "any", "branch": "main"})
    assert response.status_code == 200
    assert "Required artifacts missing: requirements" in response.text
    assert "disabled" in response.text


def test_completed_bundle_shows_available_actions_without_recommended_heading(monkeypatch):
    client, _tasks, state, _scheduled = _setup_client(monkeypatch, logged_in=True)
    state["template_id"] = "requirement.v1"
    state["artifact_exists"] = {"requirements": True, "test_cases": True}

    response = client.get(
        "/app/requirement-bundles/open",
        params={"repo": "octo/engineering-flow-platform-assets", "path": "any", "branch": "main"},
    )
    assert response.status_code == 200
    assert "Recommended Next Step" not in response.text
    assert "Available Task Shortcuts" in response.text
    assert "Collect Requirements" in response.text
    assert "Design Test Cases" in response.text
    assert response.text.count('name="task_template_id"') >= 2


def test_form_state_only_expands_action_and_does_not_force_recommended_action():
    import app.web as web_module

    detail = _detail_for("requirement.v1", artifact_exists={"requirements": True, "test_cases": True})
    vm = web_module._build_bundle_detail_view_model(
        detail,
        web_module.list_bundle_templates(),
        [],
        form_state={"task_template_id": "design_test_cases_from_bundle", "action_agent_id": "agent-1"},
    )

    assert vm["recommended_action"] is None
    actions_by_id = {item["task_template_id"]: item for item in vm["actions"]}
    assert actions_by_id["design_test_cases_from_bundle"]["expanded"] is True
    assert actions_by_id["design_test_cases_from_bundle"]["is_recommended"] is False
    assert actions_by_id["collect_requirements_to_bundle"]["is_recommended"] is False
