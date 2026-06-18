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


class _FakeTaskListRepo:
    def __init__(self, _db, tasks):
        self._tasks = tasks

    def list_visible_to_user(self, *, user_id, limit=None, offset=0):
        _ = user_id
        window = self._tasks[offset:]
        if limit is not None:
            window = window[:limit]
        return window

    def count_by_status(self):
        counts = {}
        for task in self._tasks:
            counts[task.status] = counts.get(task.status, 0) + 1
        return counts


def _setup_task_client(monkeypatch, task, chain=None, can_manage_task=True):
    from app.main import app
    import app.web as web_module

    user = SimpleNamespace(id=11, username="portal", nickname="Portal", role="user")
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _r: user)
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
    assert 'class="modal portal-workspace-wizard-modal"' in response.text
    assert 'aria-modal="true"' in response.text
    assert 'class="modal-card panel create-agent-wizard-card"' in response.text
    assert "data-close-task-create-modal" in response.text
    assert 'id="create-agent-async-task-form"' in response.text
    assert 'class="stack portal-task-create-form portal-step-wizard"' in response.text
    assert 'name="assignee_agent_id"' in response.text
    assert 'name="skill_name"' in response.text
    assert 'name="task_content"' in response.text
    assert 'data-wizard-steps="agent,skill,content,review"' in response.text
    assert 'data-wizard-step-panel="agent"' in response.text
    assert 'data-wizard-step-panel="skill"' in response.text
    assert 'data-wizard-step-panel="content"' in response.text
    assert 'data-wizard-step-panel="review"' in response.text
    assert 'id="create-task-review"' in response.text
    assert 'data-wizard-next' in response.text
    assert "Template" not in response.text


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
    assert "data-open-task-followup-modal" in response.text
    assert 'id="task-followup-modal"' in response.text
    assert 'class="modal portal-workspace-wizard-modal hidden"' in response.text
    assert 'class="modal-card panel create-agent-wizard-card portal-task-followup-card"' in response.text
    assert "data-close-task-followup-modal" in response.text
    assert 'id="continue-agent-task-form"' in response.text
    assert 'class="stack portal-task-followup portal-step-wizard"' in response.text
    assert 'data-wizard-steps="followup,review"' in response.text
    assert 'id="continue-task-review"' in response.text
    assert 'data-wizard-next' in response.text
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


def test_agent_async_task_detail_renders_runtime_execution_context(monkeypatch):
    import app.web as web_module

    task = _agent_async_task("running")

    class FakeExecutionRepo:
        def __init__(self, _db):
            return None

        def get_latest_by_task_id(self, task_id):
            assert task_id == "async-task-1"
            return SimpleNamespace(
                status="running",
                runtime_type="opencode",
                runtime_task_id="async-task-1",
                request_id="req-runtime",
                heartbeat_at="2026-06-14 12:00:00",
                last_event_at="2026-06-14 12:00:01",
                runtime_status_code=200,
                error_code=None,
                would_conflict_same_session=False,
            )

    monkeypatch.setattr(web_module, "AgentExecutionRepository", FakeExecutionRepo)
    client = _setup_task_client(monkeypatch, task)
    html = client.get("/app/tasks/async-task-1/panel").text

    assert "Runtime Execution" in html
    assert "Execution Status" in html
    assert "opencode" in html
    assert "req-runtime" in html


def test_tasks_panel_uses_incremental_task_cards(monkeypatch):
    from app.main import app
    import app.web as web_module

    user = SimpleNamespace(id=11, username="portal", nickname="Portal", role="user")
    now = datetime.utcnow()
    tasks = [
        SimpleNamespace(
            id=f"task-{index:02d}",
            title=f"Task {index:02d}",
            status="done",
            task_type="agent_async_task",
            source="portal",
            skill_name="review",
            summary="",
            error_message="",
            owner_user_id=11,
            created_at=now,
        )
        for index in range(55)
    ]

    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _r: user)
    monkeypatch.setattr(web_module, "AgentTaskRepository", lambda db: _FakeTaskListRepo(db, tasks))
    client = TestClient(app)

    html = client.get("/app/tasks/panel?content_target=%23workspace-detail-content").text
    assert html.count('<article class="portal-task-card">') == 20
    assert "Task 00" in html
    assert "Task 20" not in html
    assert 'hx-get="/app/tasks/list?offset=20&limit=20&content_target=%23workspace-detail-content"' in html

    next_html = client.get("/app/tasks/list?offset=20&limit=20&content_target=%23workspace-detail-content").text
    assert next_html.count('<article class="portal-task-card">') == 20
    assert "Task 20" in next_html
    assert "Task 40" not in next_html
    assert 'hx-get="/app/tasks/list?offset=40&limit=20&content_target=%23workspace-detail-content"' in next_html
