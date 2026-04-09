import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from app.log_context import get_log_context


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
        return SimpleNamespace(
            dispatched=True,
            task_status="queued",
            message="queued",
            task_id=task_id,
            runtime_status_code=200,
        )

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
            manifest_ref=bundle_ref,
            bundle_ref=bundle_ref,
            manifest={"bundle_id": "RB-checkout-flow", "title": "Checkout Flow"},
            requirements_file="requirements.yaml",
            test_cases_file="test-cases.yaml",
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


def test_requirement_bundles_panel_route_returns_fragment(monkeypatch):
    client, _tasks, _bundle_state = _setup_client(monkeypatch, logged_in=True)
    response = client.get("/app/requirement-bundles/panel")
    assert response.status_code == 200
    assert "Requirement Bundles" in response.text
    assert 'id="requirement-bundles-panel-root"' in response.text
    assert "Back to App" not in response.text


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


def test_create_bundle_htmx_returns_panel_fragment(monkeypatch):
    client, _tasks, _bundle_state = _setup_client(monkeypatch, logged_in=True)
    response = client.post(
        "/app/requirement-bundles/create",
        data={
            "title": "Checkout Flow",
            "domain": "payments",
            "slug": "",
            "base_branch": "main",
        },
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "Bundle created successfully" in response.text
    assert 'id="requirement-bundles-panel-root"' in response.text
    assert "Back to App" not in response.text


def test_open_existing_bundle_shows_summary_and_github_link(monkeypatch):
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
    assert "Open in GitHub" in response.text
    assert "Advanced: Open by repo/path/branch" not in response.text
    assert "bundle.yaml" not in response.text


def test_open_existing_bundle_htmx_returns_panel_fragment(monkeypatch):
    client, _tasks, _bundle_state = _setup_client(monkeypatch, logged_in=True)
    response = client.get(
        "/app/requirement-bundles/open",
        params={
            "repo": "octo/engineering-flow-platform-assets",
            "path": "requirement-bundles/payments/checkout-flow",
            "branch": "bundle/checkout-flow/deadbeef",
        },
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert 'id="requirement-bundles-panel-root"' in response.text
    assert "Bundle Detail" in response.text
    assert "Open in GitHub" in response.text
    assert "Advanced: Open by repo/path/branch" not in response.text
    assert "bundle.yaml" not in response.text
    assert "Back to App" not in response.text


def test_app_page_has_requirement_bundles_sidebar_section(monkeypatch):
    client, _tasks, _bundle_state = _setup_client(monkeypatch, logged_in=True)
    response = client.get("/app")
    assert response.status_code == 200
    assert 'id="bundle-list"' in response.text
    assert 'id="add-bundle-btn"' in response.text


def test_collect_and_design_create_and_dispatch_tasks(monkeypatch):
    client, created_tasks, _bundle_state = _setup_client(monkeypatch, logged_in=True)

    collect_response = client.post(
        "/app/requirement-bundles/collect",
        data={
            "bundle_repo": "octo/engineering-flow-platform-assets",
            "bundle_path": "requirement-bundles/payments/checkout-flow",
            "bundle_branch": "bundle/checkout-flow/deadbeef",
            "manifest_repo": "octo/engineering-flow-platform-assets",
            "manifest_path": "requirement-bundles/payments/checkout-flow",
            "manifest_branch": "bundle/checkout-flow/deadbeef",
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
            "manifest_repo": "octo/engineering-flow-platform-assets",
            "manifest_path": "requirement-bundles/payments/checkout-flow",
            "manifest_branch": "bundle/checkout-flow/deadbeef",
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
    assert collect_payload["manifest_ref"]["repo"] == "octo/engineering-flow-platform-assets"
    assert collect_payload["sources"] == {
        "jira": ["JIRA-123", "JIRA-124"],
        "confluence": ["https://confluence.local/page-1", "https://confluence.local/page-2"],
        "github_docs": ["https://github.com/org/repo/blob/main/README.md"],
        "figma": ["https://figma.com/file/abc", "https://figma.com/file/def"],
    }
    assert design_payload["bundle_ref"]["path"] == "requirement-bundles/payments/checkout-flow"
    assert design_payload["manifest_ref"]["path"] == "requirement-bundles/payments/checkout-flow"


def test_collect_and_design_use_canonical_branch_from_inspect(monkeypatch):
    client, created_tasks, _bundle_state = _setup_client(monkeypatch, logged_in=True)

    import app.web as web_module

    canonical_branch = "bundle/checkout/abcd1234"

    monkeypatch.setattr(
        web_module.requirement_bundle_service,
        "inspect_bundle",
        lambda _bundle_ref: SimpleNamespace(
            manifest_ref=SimpleNamespace(
                repo="octo/engineering-flow-platform-assets",
                path="requirement-bundles/payments/checkout-flow",
                branch="main",
            ),
            bundle_ref=SimpleNamespace(
                repo="octo/engineering-flow-platform-assets",
                path="requirement-bundles/payments/checkout-flow",
                branch=canonical_branch,
            ),
            manifest={"bundle_id": "RB-checkout-flow", "title": "Checkout Flow"},
            requirements_file="requirements.yaml",
            test_cases_file="test-cases.yaml",
            requirements_exists=True,
            test_cases_exists=True,
            last_commit_sha="abc123",
        ),
    )

    open_response = client.get(
        "/app/requirement-bundles/open",
        params={
            "repo": "octo/engineering-flow-platform-assets",
            "path": "requirement-bundles/payments/checkout-flow",
            "branch": "main",
        },
    )
    assert open_response.status_code == 200
    assert 'name="manifest_branch" value="main"' in open_response.text
    assert f'name="bundle_branch" value="{canonical_branch}"' in open_response.text

    collect_response = client.post(
        "/app/requirement-bundles/collect",
        data={
            "bundle_repo": "octo/engineering-flow-platform-assets",
            "bundle_path": "requirement-bundles/payments/checkout-flow",
            "bundle_branch": canonical_branch,
            "manifest_repo": "octo/engineering-flow-platform-assets",
            "manifest_path": "requirement-bundles/payments/checkout-flow",
            "manifest_branch": "main",
            "collect_agent_id": "agent-1",
            "jira_sources": "JIRA-123",
            "confluence_sources": "",
            "github_doc_sources": "",
            "figma_sources": "",
        },
    )
    assert collect_response.status_code == 200

    design_response = client.post(
        "/app/requirement-bundles/design-test-cases",
        data={
            "bundle_repo": "octo/engineering-flow-platform-assets",
            "bundle_path": "requirement-bundles/payments/checkout-flow",
            "bundle_branch": canonical_branch,
            "manifest_repo": "octo/engineering-flow-platform-assets",
            "manifest_path": "requirement-bundles/payments/checkout-flow",
            "manifest_branch": "main",
            "design_agent_id": "agent-1",
        },
    )
    assert design_response.status_code == 200

    collect_payload = json.loads(created_tasks[0].input_payload_json)
    design_payload = json.loads(created_tasks[1].input_payload_json)

    assert collect_payload["bundle_ref"]["branch"] == canonical_branch
    assert collect_payload["manifest_ref"]["branch"] == "main"
    assert design_payload["bundle_ref"]["branch"] == canonical_branch
    assert design_payload["manifest_ref"]["branch"] == "main"


def test_collect_task_payload_uses_canonical_ref_even_if_posted_branch_is_noncanonical(monkeypatch):
    client, created_tasks, _bundle_state = _setup_client(monkeypatch, logged_in=True)

    import app.web as web_module

    canonical_branch = "bundle/checkout/abcd1234"
    posted_branch = "main"

    monkeypatch.setattr(
        web_module.requirement_bundle_service,
        "inspect_bundle",
        lambda _bundle_ref: SimpleNamespace(
            manifest_ref=SimpleNamespace(
                repo="octo/engineering-flow-platform-assets",
                path="requirement-bundles/payments/checkout-flow",
                branch=posted_branch,
            ),
            bundle_ref=SimpleNamespace(
                repo="octo/engineering-flow-platform-assets",
                path="requirement-bundles/payments/checkout-flow",
                branch=canonical_branch,
            ),
            manifest={"bundle_id": "RB-checkout-flow", "title": "Checkout Flow"},
            requirements_file="requirements.yaml",
            test_cases_file="test-cases.yaml",
            requirements_exists=True,
            test_cases_exists=True,
            last_commit_sha="abc123",
        ),
    )

    response = client.post(
        "/app/requirement-bundles/collect",
        data={
            "bundle_repo": "octo/engineering-flow-platform-assets",
            "bundle_path": "requirement-bundles/payments/checkout-flow",
            "bundle_branch": posted_branch,
            "manifest_repo": "octo/engineering-flow-platform-assets",
            "manifest_path": "requirement-bundles/payments/checkout-flow",
            "manifest_branch": posted_branch,
            "collect_agent_id": "agent-1",
            "jira_sources": "JIRA-123",
            "confluence_sources": "",
            "github_doc_sources": "",
            "figma_sources": "",
        },
    )

    assert response.status_code == 200
    collect_payload = json.loads(created_tasks[0].input_payload_json)
    assert collect_payload["bundle_ref"]["branch"] == canonical_branch
    assert collect_payload["manifest_ref"]["branch"] == posted_branch


def test_collect_dispatch_runs_with_trace_context(monkeypatch):
    client, _created_tasks, _bundle_state = _setup_client(monkeypatch, logged_in=True)
    import app.web as web_module

    observed = {"trace_id": None}

    async def _capture_dispatch(task_id, db, user=None):
        _ = (task_id, db, user)
        observed["trace_id"] = get_log_context().get("trace_id")
        return SimpleNamespace(
            dispatched=True,
            task_status="queued",
            message="queued",
            task_id=task_id,
            runtime_status_code=200,
        )

    monkeypatch.setattr(web_module.task_dispatcher_service, "dispatch_task", _capture_dispatch)

    response = client.post(
        "/app/requirement-bundles/collect",
        data={
            "bundle_repo": "octo/engineering-flow-platform-assets",
            "bundle_path": "requirement-bundles/payments/checkout-flow",
            "bundle_branch": "bundle/checkout-flow/deadbeef",
            "manifest_repo": "octo/engineering-flow-platform-assets",
            "manifest_path": "requirement-bundles/payments/checkout-flow",
            "manifest_branch": "bundle/checkout-flow/deadbeef",
            "collect_agent_id": "agent-1",
            "jira_sources": "JIRA-123",
            "confluence_sources": "",
            "github_doc_sources": "",
            "figma_sources": "",
        },
    )
    assert response.status_code == 200
    assert observed["trace_id"]
    assert observed["trace_id"] != "-"


def test_design_task_payload_uses_canonical_ref_even_if_posted_branch_is_noncanonical(monkeypatch):
    client, created_tasks, _bundle_state = _setup_client(monkeypatch, logged_in=True)

    import app.web as web_module

    canonical_branch = "bundle/checkout/abcd1234"
    posted_branch = "main"

    monkeypatch.setattr(
        web_module.requirement_bundle_service,
        "inspect_bundle",
        lambda _bundle_ref: SimpleNamespace(
            manifest_ref=SimpleNamespace(
                repo="octo/engineering-flow-platform-assets",
                path="requirement-bundles/payments/checkout-flow",
                branch=posted_branch,
            ),
            bundle_ref=SimpleNamespace(
                repo="octo/engineering-flow-platform-assets",
                path="requirement-bundles/payments/checkout-flow",
                branch=canonical_branch,
            ),
            manifest={"bundle_id": "RB-checkout-flow", "title": "Checkout Flow"},
            requirements_file="requirements.yaml",
            test_cases_file="test-cases.yaml",
            requirements_exists=True,
            test_cases_exists=True,
            last_commit_sha="abc123",
        ),
    )

    response = client.post(
        "/app/requirement-bundles/design-test-cases",
        data={
            "bundle_repo": "octo/engineering-flow-platform-assets",
            "bundle_path": "requirement-bundles/payments/checkout-flow",
            "bundle_branch": posted_branch,
            "manifest_repo": "octo/engineering-flow-platform-assets",
            "manifest_path": "requirement-bundles/payments/checkout-flow",
            "manifest_branch": posted_branch,
            "design_agent_id": "agent-1",
        },
    )

    assert response.status_code == 200
    design_payload = json.loads(created_tasks[0].input_payload_json)
    assert design_payload["bundle_ref"]["branch"] == canonical_branch
    assert design_payload["manifest_ref"]["branch"] == posted_branch
    assert design_payload["bundle_ref"]["branch"] != posted_branch


def test_open_existing_bundle_shows_custom_linked_filenames(monkeypatch):
    client, _tasks, _bundle_state = _setup_client(monkeypatch, logged_in=True)
    import app.web as web_module

    monkeypatch.setattr(
        web_module.requirement_bundle_service,
        "inspect_bundle",
        lambda bundle_ref: SimpleNamespace(
            manifest_ref=bundle_ref,
            bundle_ref=bundle_ref,
            manifest={"bundle_id": "RB-checkout-flow", "title": "Checkout Flow"},
            requirements_file="docs/reqs.yaml",
            test_cases_file="outputs/tc.yaml",
            requirements_exists=True,
            test_cases_exists=False,
            last_commit_sha="abc123",
        ),
    )

    response = client.get(
        "/app/requirement-bundles/open",
        params={
            "repo": "octo/engineering-flow-platform-assets",
            "path": "requirement-bundles/payments/checkout-flow",
            "branch": "bundle/checkout-flow/deadbeef",
        },
    )

    assert response.status_code == 200
    assert "docs/reqs.yaml" in response.text
    assert "outputs/tc.yaml" in response.text


def test_design_missing_requirements_message_uses_custom_linked_filename(monkeypatch):
    client, _created_tasks, _bundle_state = _setup_client(monkeypatch, logged_in=True)
    import app.web as web_module

    monkeypatch.setattr(
        web_module.requirement_bundle_service,
        "inspect_bundle",
        lambda bundle_ref: SimpleNamespace(
            manifest_ref=bundle_ref,
            bundle_ref=bundle_ref,
            manifest={"bundle_id": "RB-checkout-flow", "title": "Checkout Flow"},
            requirements_file="docs/reqs.yaml",
            test_cases_file="outputs/tc.yaml",
            requirements_exists=False,
            test_cases_exists=True,
            last_commit_sha="abc123",
        ),
    )

    response = client.post(
        "/app/requirement-bundles/design-test-cases",
        data={
            "bundle_repo": "octo/engineering-flow-platform-assets",
            "bundle_path": "requirement-bundles/payments/checkout-flow",
            "bundle_branch": "main",
            "manifest_repo": "octo/engineering-flow-platform-assets",
            "manifest_path": "requirement-bundles/payments/checkout-flow",
            "manifest_branch": "main",
            "design_agent_id": "agent-1",
        },
    )

    assert response.status_code == 200
    assert "docs/reqs.yaml is missing; collect requirements first" in response.text


def test_collect_rejects_empty_sources(monkeypatch):
    client, created_tasks, _bundle_state = _setup_client(monkeypatch, logged_in=True)

    response = client.post(
        "/app/requirement-bundles/collect",
        data={
            "bundle_repo": "octo/engineering-flow-platform-assets",
            "bundle_path": "requirement-bundles/payments/checkout-flow",
            "bundle_branch": "bundle/checkout-flow/deadbeef",
            "manifest_repo": "octo/engineering-flow-platform-assets",
            "manifest_path": "requirement-bundles/payments/checkout-flow",
            "manifest_branch": "bundle/checkout-flow/deadbeef",
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
            "manifest_repo": "octo/engineering-flow-platform-assets",
            "manifest_path": "requirement-bundles/payments/checkout-flow",
            "manifest_branch": "bundle/checkout-flow/deadbeef",
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
            "manifest_repo": "octo/engineering-flow-platform-assets",
            "manifest_path": "requirement-bundles/payments/checkout-flow",
            "manifest_branch": "bundle/checkout-flow/deadbeef",
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
