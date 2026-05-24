import json
from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient


class _DB:
    def close(self):
        return None


class _FakeTaskRepo:
    def __init__(self, _db, task, chain=None):
        self._task = task
        self._chain = chain or [task]

    def get_by_id(self, _task_id):
        return self._task

    def list_by_root_task_id(self, _root_task_id):
        return self._chain


def _setup_task_client(monkeypatch, task, chain=None, can_manage_task=True):
    from app.main import app
    import app.web as web_module

    user = SimpleNamespace(id=11, username="portal", nickname="Portal", role="user")
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _r: user)
    monkeypatch.setattr(web_module, "_visible_group_ids_for_user", lambda _db, _user: ["group-1"])
    monkeypatch.setattr(web_module, "_can_manage_task_for_user", lambda _db, _task, _user: can_manage_task)
    monkeypatch.setattr(web_module, "AgentTaskRepository", lambda db: _FakeTaskRepo(db, task, chain=chain))

    return TestClient(app)


def _setup_create_panel_client(monkeypatch, agents):
    from app.main import app
    import app.web as web_module

    user = SimpleNamespace(id=11, username="portal", nickname="Portal", role="user")
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _r: user)
    monkeypatch.setattr(web_module, "_list_writable_agents", lambda _db, _user: agents)
    return TestClient(app)


def _unsupported_task(status: str):
    now = datetime.utcnow()
    payload = {
        "source": "legacy",
        "summary": "Imported task payload",
        "items": ["PAY-123", "docs/spec.md"],
    }
    return SimpleNamespace(
        id="task-1",
        status=status,
        task_type="legacy_import_task",
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


def _agent_async_task(status: str):
    now = datetime.utcnow()
    result_payload = None
    if status == "done":
        result_payload = json.dumps(
            {
                "status": "success",
                "summary": "Completed",
                "final_response": "Finished the work.",
                "blockers": [],
                "next_recommendation": "Review the changes.",
            }
        )
    return SimpleNamespace(
        id="async-task-1",
        title="Review current branch",
        status=status,
        task_type="agent_async_task",
        task_family="agent_task",
        source="portal",
        assignee_agent_id="agent-1",
        skill_name="review",
        parent_task_id=None,
        root_task_id="async-task-1",
        task_session_id="agent-task:async-task-1",
        group_id=None,
        owner_user_id=11,
        created_by_user_id=11,
        runtime_request_id="req-1" if status == "running" else None,
        created_at=now,
        started_at=now if status in {"running", "done"} else None,
        finished_at=now if status == "done" else None,
        updated_at=now,
        retry_count=0,
        summary="Completed" if status == "done" else "",
        error_message="",
        input_payload_json=json.dumps(
            {
                "schema": "agent_async_task.v1",
                "user_task": "Review the current branch and report regressions.",
                "skill_name": "review",
                "task_session_id": "agent-task:async-task-1",
                "root_task_id": "async-task-1",
                "parent_task_id": None,
            }
        ),
        result_payload_json=result_payload,
    )


def test_task_create_panel_has_agent_skill_textarea_and_no_template(monkeypatch):
    agent = SimpleNamespace(id="agent-1", name="Agent One")
    client = _setup_create_panel_client(monkeypatch, [agent])
    response = client.get("/app/tasks/create/panel")
    assert response.status_code == 200
    assert 'id="create-agent-async-task-form"' in response.text
    assert 'name="assignee_agent_id"' in response.text
    assert 'name="skill_name"' in response.text
    assert 'name="task_content"' in response.text
    assert "Template" not in response.text
    assert 'name="template_id"' not in response.text


def test_task_detail_panel_renders_non_async_tasks_as_unsupported_read_only(monkeypatch):
    client = _setup_task_client(monkeypatch, _unsupported_task("done"))
    response = client.get("/app/tasks/task-1/panel")
    assert response.status_code == 200
    assert "Unsupported Task" in response.text
    assert "This task type is not supported by the background task panel" in response.text
    assert "Input Payload" in response.text
    assert "Result Payload" in response.text
    assert "Task " + "Template" not in response.text


def test_non_async_task_detail_does_not_auto_refresh(monkeypatch):
    client_running = _setup_task_client(monkeypatch, _unsupported_task("queued"))
    running_html = client_running.get("/app/tasks/task-1/panel").text
    assert 'hx-trigger="every 5s"' not in running_html


def test_agent_async_task_detail_renders_final_response_and_followup(monkeypatch):
    task = _agent_async_task("done")
    client = _setup_task_client(monkeypatch, task)
    response = client.get("/app/tasks/async-task-1/panel")
    assert response.status_code == 200
    assert "Review current branch" in response.text
    assert "Finished the work." in response.text
    assert "Review the changes." in response.text
    assert 'id="continue-agent-task-form"' in response.text
    assert 'data-rerun-task="async-task-1"' in response.text
    assert "Start Follow-up" in response.text
    assert "Raw Input" in response.text
    assert "Raw Result" in response.text


def test_read_only_agent_async_task_hides_manage_actions(monkeypatch):
    task = _agent_async_task("done")
    client = _setup_task_client(monkeypatch, task, can_manage_task=False)
    html = client.get("/app/tasks/async-task-1/panel").text
    assert 'data-rerun-task="async-task-1"' not in html
    assert 'id="continue-agent-task-form"' not in html
    assert "Start Follow-up" not in html


def test_active_agent_async_task_detail_renders_cancel_and_auto_refresh(monkeypatch):
    task = _agent_async_task("queued")
    client = _setup_task_client(monkeypatch, task)
    html = client.get("/app/tasks/async-task-1/panel").text
    assert 'hx-trigger="every 5s"' in html
    assert 'data-cancel-task="async-task-1"' in html
    assert 'data-rerun-task="async-task-1"' not in html
    assert 'id="continue-agent-task-form"' not in html
