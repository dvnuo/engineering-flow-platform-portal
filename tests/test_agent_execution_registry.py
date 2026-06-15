import json
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, AgentExecution, AgentTask, User
from app.repositories.agent_execution_repo import AgentExecutionRepository
from app.services.agent_execution_registry import (
    ChatStreamExecutionObserver,
    finish_chat_response_best_effort,
    record_chat_started_best_effort,
    upsert_task_execution_queued_best_effort,
)
from app.services.auth_service import hash_password


def _db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    user = User(username="owner", password_hash=hash_password("pw"), role="admin", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    agent = Agent(
        name="Registry Agent",
        description="registry",
        owner_user_id=user.id,
        visibility="private",
        status="running",
        image="example/image:latest",
        repo_url="https://example.com/repo.git",
        branch="main",
        cpu="500m",
        memory="1Gi",
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp-agents",
        deployment_name="dep-registry",
        service_name="svc-registry",
        pvc_name="pvc-registry",
        endpoint_path="/",
        agent_type="workspace",
        runtime_type="opencode",
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return db, TestingSessionLocal, user, agent


def test_chat_execution_registry_records_start_and_terminal_without_prompt_text():
    db, _session_factory, user, agent = _db_session()
    try:
        execution = record_chat_started_best_effort(
            db,
            agent=agent,
            user=SimpleNamespace(id=user.id),
            payload={
                "message": "sensitive prompt text",
                "session_id": "session-1",
                "request_id": "req-1",
                "metadata": {"runtime_profile_id": "rp-1", "provider": "github-copilot"},
            },
            execution_path="/api/chat",
        )
        assert execution is not None
        assert execution.status == "running"
        assert execution.kind == "chat"
        assert execution.runtime_type == "opencode"
        assert "sensitive prompt text" not in (execution.metadata_json or "")

        finish_chat_response_best_effort(
            db,
            execution_id=execution.id,
            status_code=200,
            content=b'{"ok": true, "completion_state": "completed", "response": "done"}',
        )

        db.refresh(execution)
        assert execution.status == "succeeded"
        assert execution.finished_at is not None
        assert execution.runtime_status_code == 200
        assert execution.result_summary == "done"
    finally:
        db.close()


def test_chat_stream_observer_marks_stale_without_terminal_event(monkeypatch):
    db, session_factory, _user, agent = _db_session()
    try:
        execution = AgentExecutionRepository(db).create(
            agent_id=agent.id,
            session_id="session-1",
            request_id="req-stream",
            kind="chat",
            status="running",
            source="portal",
            runtime_type="opencode",
        )
        import app.services.agent_execution_registry as registry_module

        monkeypatch.setattr(registry_module, "SessionLocal", session_factory)
        observer = ChatStreamExecutionObserver(execution.id)
        observer.feed(b"event: runtime_event\ndata: {\"type\":\"llm_thinking\"}\n\n")
        observer.finish(status_code=200)

        db.refresh(execution)
        updated = db.get(AgentExecution, execution.id)
        assert updated.status == "stale"
        assert updated.error_code == "stream_closed_without_terminal_event"
        metadata = json.loads(updated.metadata_json)
        assert metadata["stream_event_count"] == 1
        assert metadata["saw_final_event"] is False
    finally:
        db.close()


def test_task_execution_upsert_does_not_count_existing_row_as_conflict():
    db, _session_factory, user, agent = _db_session()
    try:
        task = AgentTask(
            assignee_agent_id=agent.id,
            source="portal",
            task_type="agent_async",
            title="Long task",
            task_session_id="session-task",
            input_payload_json="{}",
            task_family="manual",
            trigger="manual",
            status="queued",
            owner_user_id=user.id,
            created_by_user_id=user.id,
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        created = upsert_task_execution_queued_best_effort(db, task=task, agent=agent, user=SimpleNamespace(id=user.id))
        updated = upsert_task_execution_queued_best_effort(db, task=task, agent=agent, user=SimpleNamespace(id=user.id))

        assert created is not None
        assert updated is not None
        assert updated.id == created.id
        assert updated.would_conflict_same_session is False
        metadata = json.loads(updated.metadata_json)
        assert metadata["active_same_session_count"] == 0
        assert metadata["would_conflict_same_session"] is False
    finally:
        db.close()
