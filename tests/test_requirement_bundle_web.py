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


def _setup_client(monkeypatch, logged_in=True):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=11, username="portal", nickname="Portal", role="user")
    fake_agent = SimpleNamespace(id="agent-1", name="Agent One", owner_user_id=11, visibility="private")
    created_tasks = []
    bundle_state = {"requirements_exists": True}

    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(web_module, "AgentRepository", lambda db: _FakeAgentRepo(db, [fake_agent]))
    monkeypatch.setattr(web_module, "AgentTaskRepository", lambda db: _FakeTaskRepo(db, created_tasks))
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _r: fake_user if logged_in else None)

    async def _fake_dispatch(task_id, db, user=None):
        return SimpleNamespace(dispatched=True, task_status="queued", message="queued", task_id=task_id)

    monkeypatch.setattr(web_module.task_dispatcher_service, "dispatch_task", _fake_dispatch)

    monkeypatch.setattr(
        web_module.requirement_bundle_service,
        "create_bundle",
        lambda _form: SimpleNamespace(
            repo="octo/engineering-flow-platform-assets",
            path="requirement-bundles/payments/checkout-flow",
            branch="bundle/checkout-flow/deadbeef",
        ),
    )
    monkeypatch.setattr(
        web_module.requirement_bundle_service,
        "inspect_bundle",
        lambda bundle_ref: SimpleNamespace(
            bundle_ref=bundle_ref,
            manifest={"bundle_id": "RB-checkout-flow", "title": "Checkout Flow"},
            requirements_exists=bundle_state["requirements_exists"],
            test_cases_exists=True,
            last_commit_sha="abc123",
        ),
    )

    return TestClient(app), created_tasks, bundle_state


def test_requirement_bundles_requires_login(monkeypatch):
    client, _tasks, _bundle_state = _setup_client(monkeypatch, logged_in=False)
    response = client.get("/app/requirement-bundles", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_requirement_bundles_page_logged_in(monkeypatch):
    client, _tasks, _bundle_state = _setup_client(monkeypatch, logged_in=True)
    response = client.get("/app/requirement-bundles")
    assert response.status_code == 200
    assert "Requirement Bundles" in response.text


def test_create_new_bundle_renders_bundle_ref(monkeypatch):
    client, _tasks, _bundle_state = _setup_client(monkeypatch, logged_in=True)
    page = client.get("/app/requirement-bundles")
    assert page.status_code == 200
    assert "Collect Agent (optional)" not in page.text
    assert "Design Agent (optional)" not in page.text

    response = client.post(
        "/app/requirement-bundles/create",
        data={
            "title": "Checkout Flow",
            "domain": "payments",
            "slug": "",
            "base_branch": "main",
        },
    )
    assert response.status_code == 200
    assert "Bundle created successfully" in response.text
    assert "requirement-bundles/payments/checkout-flow" in response.text


def test_open_existing_bundle_shows_manifest(monkeypatch):
    client, _tasks, _bundle_state = _setup_client(monkeypatch, logged_in=True)
    response = client.get(
        "/app/requirement-bundles/open",
        params={
            "repo": "octo/engineering-flow-platform-assets",
            "path": "requirement-bundles/payments/checkout-flow",
            "branch": "bundle/checkout-flow/deadbeef",
        },
    )
    assert response.status_code == 200
    assert "Bundle Detail" in response.text
    assert "RB-checkout-flow" in response.text


def test_collect_and_design_create_and_dispatch_tasks(monkeypatch):
    client, created_tasks, _bundle_state = _setup_client(monkeypatch, logged_in=True)

    collect_response = client.post(
        "/app/requirement-bundles/collect",
        data={
            "bundle_repo": "octo/engineering-flow-platform-assets",
            "bundle_path": "requirement-bundles/payments/checkout-flow",
            "bundle_branch": "bundle/checkout-flow/deadbeef",
            "collect_agent_id": "agent-1",
            "jira_sources": "JIRA-123, JIRA-124",
            "confluence_sources": "https://confluence.local/page-1\nhttps://confluence.local/page-2",
            "github_doc_sources": "https://github.com/org/repo/blob/main/README.md",
            "figma_sources": "https://figma.com/file/abc, https://figma.com/file/def",
        },
    )
    assert collect_response.status_code == 200
    assert "Created task task-1" in collect_response.text

    design_response = client.post(
        "/app/requirement-bundles/design-test-cases",
        data={
            "bundle_repo": "octo/engineering-flow-platform-assets",
            "bundle_path": "requirement-bundles/payments/checkout-flow",
            "bundle_branch": "bundle/checkout-flow/deadbeef",
            "design_agent_id": "agent-1",
        },
    )
    assert design_response.status_code == 200
    assert "Created task task-2" in design_response.text

    assert len(created_tasks) == 2
    assert created_tasks[0].task_type == "requirement_bundle_collect_task"
    assert created_tasks[1].task_type == "requirement_bundle_design_test_cases_task"

    collect_payload = json.loads(created_tasks[0].input_payload_json)
    design_payload = json.loads(created_tasks[1].input_payload_json)

    assert collect_payload["bundle_ref"]["repo"] == "octo/engineering-flow-platform-assets"
    assert collect_payload["sources"] == {
        "jira": ["JIRA-123", "JIRA-124"],
        "confluence": ["https://confluence.local/page-1", "https://confluence.local/page-2"],
        "github_docs": ["https://github.com/org/repo/blob/main/README.md"],
        "figma": ["https://figma.com/file/abc", "https://figma.com/file/def"],
    }
    assert design_payload["bundle_ref"]["path"] == "requirement-bundles/payments/checkout-flow"


def test_collect_rejects_empty_sources(monkeypatch):
    client, created_tasks, _bundle_state = _setup_client(monkeypatch, logged_in=True)

    response = client.post(
        "/app/requirement-bundles/collect",
        data={
            "bundle_repo": "octo/engineering-flow-platform-assets",
            "bundle_path": "requirement-bundles/payments/checkout-flow",
            "bundle_branch": "bundle/checkout-flow/deadbeef",
            "collect_agent_id": "agent-1",
            "jira_sources": "",
            "confluence_sources": "",
            "github_doc_sources": "",
            "figma_sources": "",
        },
    )

    assert response.status_code == 200
    assert "At least one Jira, Confluence, or GitHub Docs source is required." in response.text
    assert len(created_tasks) == 0


def test_collect_rejects_figma_only_sources(monkeypatch):
    client, created_tasks, _bundle_state = _setup_client(monkeypatch, logged_in=True)

    response = client.post(
        "/app/requirement-bundles/collect",
        data={
            "bundle_repo": "octo/engineering-flow-platform-assets",
            "bundle_path": "requirement-bundles/payments/checkout-flow",
            "bundle_branch": "bundle/checkout-flow/deadbeef",
            "collect_agent_id": "agent-1",
            "jira_sources": "",
            "confluence_sources": "",
            "github_doc_sources": "",
            "figma_sources": "https://www.figma.com/file/abc123",
        },
    )

    assert response.status_code == 200
    assert "Figma-only collection is not supported in MVP" in response.text
    assert len(created_tasks) == 0


def test_design_rejects_missing_requirements_yaml(monkeypatch):
    client, created_tasks, bundle_state = _setup_client(monkeypatch, logged_in=True)
    bundle_state["requirements_exists"] = False

    response = client.post(
        "/app/requirement-bundles/design-test-cases",
        data={
            "bundle_repo": "octo/engineering-flow-platform-assets",
            "bundle_path": "requirement-bundles/payments/checkout-flow",
            "bundle_branch": "bundle/checkout-flow/deadbeef",
            "design_agent_id": "agent-1",
        },
    )

    assert response.status_code == 200
    assert "requirements.yaml is missing; collect requirements first" in response.text
    assert len(created_tasks) == 0


def test_requirement_bundle_page_has_source_format_guidance(monkeypatch):
    client, _tasks, _bundle_state = _setup_client(monkeypatch, logged_in=True)
    response = client.get(
        "/app/requirement-bundles/open",
        params={
            "repo": "octo/engineering-flow-platform-assets",
            "path": "requirement-bundles/payments/checkout-flow",
            "branch": "bundle/checkout-flow/deadbeef",
        },
    )

    assert response.status_code == 200
    assert "Jira Sources (issue keys or browse URLs)" in response.text
    assert "Confluence Sources (page IDs or page URLs)" in response.text
    assert "GitHub Docs Sources (repo-relative paths or blob URLs)" in response.text
    assert "Figma Sources (ignored in MVP)" in response.text
    assert "stored only; not processed in MVP" in response.text


def test_design_button_disabled_when_requirements_missing(monkeypatch):
    client, _tasks, bundle_state = _setup_client(monkeypatch, logged_in=True)
    bundle_state["requirements_exists"] = False

    response = client.get(
        "/app/requirement-bundles/open",
        params={
            "repo": "octo/engineering-flow-platform-assets",
            "path": "requirement-bundles/payments/checkout-flow",
            "branch": "bundle/checkout-flow/deadbeef",
        },
    )

    assert response.status_code == 200
    assert "requirements.yaml not found — run Collect Requirements first" in response.text
    assert "disabled" in response.text
