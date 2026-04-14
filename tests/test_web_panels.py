import json
from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient


class _DB:
    def close(self):
        return None


class _FakeTaskRepo:
    def __init__(self, _db, task):
        self._task = task

    def get_by_id(self, _task_id):
        return self._task


def _setup_task_client(monkeypatch, task):
    from app.main import app
    import app.web as web_module

    user = SimpleNamespace(id=11, username="portal", nickname="Portal", role="user")
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _r: user)
    monkeypatch.setattr(web_module, "_visible_group_ids_for_user", lambda _db, _user: ["group-1"])
    monkeypatch.setattr(web_module, "AgentTaskRepository", lambda db: _FakeTaskRepo(db, task))

    return TestClient(app)


def _bundle_action_task(status: str):
    now = datetime.utcnow()
    payload = {
        "template_id": "requirement.v1",
        "action_id": "collect_requirements",
        "bundle_ref": {
            "repo": "octo/engineering-flow-platform-assets",
            "path": "requirement-bundles/payments/checkout-flow",
            "branch": "bundle/checkout-flow/deadbeef",
        },
        "manifest_ref": {
            "repo": "octo/engineering-flow-platform-assets",
            "path": "requirement-bundles/payments/checkout-flow",
            "branch": "main",
        },
        "sources": {
            "jira": ["PAY-123"],
            "confluence": [],
            "github_docs": ["docs/spec.md"],
            "figma": [],
        },
    }
    return SimpleNamespace(
        id="task-1",
        status=status,
        task_type="bundle_action_task",
        source="portal",
        assignee_agent_id="agent-1",
        group_id="group-1",
        owner_user_id=11,
        created_by_user_id=11,
        runtime_request_id="req-1",
        created_at=now,
        started_at=now,
        finished_at=now if status == "done" else None,
        updated_at=now,
        retry_count=0,
        summary="Collected requirements" if status == "done" else "",
        error_message="",
        input_payload_json=json.dumps(payload),
        result_payload_json=json.dumps({"ok": True}),
    )


def test_task_detail_panel_renders_business_context_for_bundle_action_task(monkeypatch):
    client = _setup_task_client(monkeypatch, _bundle_action_task("done"))
    response = client.get("/app/tasks/task-1/panel")
    assert response.status_code == 200
    assert "Collect Requirements" in response.text
    assert "Input Payload" in response.text
    assert "Result Payload" in response.text
    assert "Open Bundle Detail" in response.text


def test_task_detail_panel_auto_refresh_only_for_active_tasks(monkeypatch):
    client_running = _setup_task_client(monkeypatch, _bundle_action_task("queued"))
    running_html = client_running.get("/app/tasks/task-1/panel").text
    assert 'hx-trigger="every 5s"' in running_html

    client_done = _setup_task_client(monkeypatch, _bundle_action_task("done"))
    done_html = client_done.get("/app/tasks/task-1/panel").text
    assert 'hx-trigger="every 5s"' not in done_html
