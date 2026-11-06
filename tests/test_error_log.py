"""The error log behind Administration > Errors (app/services/error_log_service.py).

What gets recorded (runs, assistants, connector tests, Portal responses, the
Portal's own log), how it is redacted, grouped and pruned, and what the admin
API and the panel show.
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models import Agent, ErrorEvent, User
from app.repositories.agent_execution_repo import AgentExecutionRepository
from app.services import error_log_service as error_log
from app.services.error_log_service import (
    ErrorCaptureMiddleware,
    ErrorLogHandler,
    ErrorLogService,
    fingerprint_for,
    install_error_log_handler,
    record_error,
    uninstall_error_log_handler,
)
from tests.test_user_admin_management import _database, _override_db

NOW = datetime(2026, 11, 6, 12, 0, 0)


def _seed(db):
    admin = User(username="admin", password_hash="hash", role="admin", is_active=True)
    member = User(username="12345678", nickname="Alex", password_hash="hash", role="user", is_active=True)
    db.add_all([admin, member])
    db.commit()
    db.refresh(admin)
    db.refresh(member)
    agent = Agent(
        name="Release assistant", owner_user_id=member.id, visibility="private", status="running",
        image="img", deployment_name="d", service_name="s", pvc_name="p",
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return admin, member, agent


def _event(db, **fields):
    values = dict(source="log", message="something broke", severity="error", occurred_at=NOW)
    values.update(fields)
    return record_error(db, commit=True, **values)


def _count(db) -> int:
    return int(db.scalar(select(func.count(ErrorEvent.id))) or 0)


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def test_record_error_redacts_truncates_and_groups_alike_messages():
    db = _database()
    try:
        first = _event(db, source="execution", code="chat_failed", message="Runtime pod-a returned 502 for session 123 token=abc123")
        assert "token=[REDACTED]" in first.message and "abc123" not in first.message
        second = _event(db, source="execution", code="chat_failed", message="Runtime pod-a returned 503 for session 456 token=zzz999")
        assert first.fingerprint == second.fingerprint and first.id != second.id, "numbers do not split a group"
        other = _event(db, source="execution", code="chat_incomplete", message=first.message)
        assert other.fingerprint != first.fingerprint, "a different code is a different problem"

        long = _event(db, message="x" * 5000, detail="y" * 20000)
        assert len(long.message) == error_log.MESSAGE_LIMIT and long.message.endswith("...")
        assert len(long.detail) == error_log.DETAIL_LIMIT

        one = fingerprint_for("portal", "http_502", "HTTP 502 on POST /a/0f8fad5b-d9cb-469f-a165-70867728950e/api/chat")
        two = fingerprint_for("portal", "http_502", "HTTP 502 on POST /a/7c9e6679-7425-40de-944b-e07fc1f90ae7/api/chat")
        assert one == two, "assistant ids do not split a group"
        assert _event(db, source="nonsense", message="").source == "portal"
        assert _event(db, message=None, code="boom").message == "boom"
    finally:
        db.close()


def test_repeats_within_the_window_bump_the_count_for_the_same_member_and_path_only():
    db = _database()
    try:
        kwargs = dict(source="portal", code="http_502", message="HTTP 502 on POST /x", request_path="POST /x", dedup_window_seconds=300)
        first = _event(db, **kwargs)
        again = _event(db, occurred_at=NOW + timedelta(seconds=30), detail="later detail", **kwargs)
        assert again.id == first.id and again.count == 2
        assert again.last_seen_at == NOW + timedelta(seconds=30) and again.occurred_at == NOW
        assert again.detail == "later detail", "the first detail wins when there is one; here there was none"

        other_member = _event(db, user_id=7, **kwargs)
        assert other_member.id != first.id, "another member's repeat is its own row"
        late = _event(db, occurred_at=NOW + timedelta(minutes=10), **kwargs)
        assert late.id != first.id and late.count == 1, "after the window it is a new row"
        plain = dict(kwargs, dedup_window_seconds=0)
        assert _event(db, **plain).id != first.id, "without a window every call is a row"
        assert _count(db) == 4
    finally:
        db.close()


def test_a_run_that_ends_failed_or_stale_is_logged_once_with_its_context():
    db = _database()
    try:
        _admin, member, agent = _seed(db)
        repo = AgentExecutionRepository(db)
        execution = repo.create(
            agent_id=agent.id, kind="chat", status="running", runtime_type="native", owner_user_id=member.id,
            created_by_user_id=member.id, session_id="s-1", model="gpt-6.1-sol",
        )
        repo.mark_status(execution, status="failed", error_code="chat_failed", error_message="Copilot returned 429: rate limited", runtime_status_code=429)
        repo.mark_status(execution, status="failed", error_message="Copilot returned 429: rate limited")
        events = db.scalars(select(ErrorEvent)).all()
        assert len(events) == 1, "a second failed write is not a second failure"
        event = events[0]
        assert (event.source, event.severity, event.code) == ("execution", "error", "chat_failed")
        assert event.message == "Copilot returned 429: rate limited"
        assert (event.agent_id, event.user_id, event.execution_id, event.session_id) == (agent.id, member.id, execution.id, "s-1")
        assert (event.model, event.kind, event.runtime_type) == ("gpt-6.1-sol", "chat", "native")
        assert "runtime_status_code: 429" in event.detail

        stale = repo.create(agent_id=agent.id, kind="async_task", status="running", runtime_type="native", owner_user_id=member.id, created_by_user_id=member.id)
        repo.mark_status(stale, status="stale", error_code="runtime_task_missing", error_message="Runtime no longer has this task")
        warning = db.scalars(select(ErrorEvent).where(ErrorEvent.execution_id == stale.id)).one()
        assert warning.severity == "warning" and warning.code == "runtime_task_missing"

        fine = repo.create(agent_id=agent.id, kind="chat", status="running", runtime_type="native", owner_user_id=member.id, created_by_user_id=member.id)
        repo.mark_status(fine, status="succeeded")
        assert _count(db) == 2
    finally:
        db.close()


def test_an_assistant_whose_runtime_failed_is_logged_with_its_owner():
    from app.api.agents import _record_assistant_failure

    db = _database()
    try:
        _admin, member, agent = _seed(db)
        _record_assistant_failure(db, agent, SimpleNamespace(status="running", message=None), action="start")
        assert _count(db) == 0
        _record_assistant_failure(db, agent, SimpleNamespace(status="failed", message="ImagePullBackOff: registry refused"), action="start")
        db.commit()
        event = db.scalars(select(ErrorEvent)).one()
        assert (event.source, event.code, event.message) == ("assistant", "start", "ImagePullBackOff: registry refused")
        assert (event.agent_id, event.agent_name, event.user_id, event.runtime_type) == (agent.id, "Release assistant", member.id, "native")
    finally:
        db.close()


def test_middleware_records_5xx_responses_and_unhandled_exceptions_then_lets_them_through():
    db = _database()
    try:
        api = FastAPI()
        api.add_middleware(ErrorCaptureMiddleware, session_factory=lambda: db)

        @api.get("/boom")
        def boom():
            raise RuntimeError("secret=abc kaput")

        @api.get("/bad")
        def bad():
            return JSONResponse({"detail": "Runtime unreachable"}, status_code=502)

        @api.get("/fine")
        def fine():
            return {"ok": True}

        client = TestClient(api, raise_server_exceptions=False)
        assert client.get("/fine").status_code == 200
        assert client.get("/bad").status_code == 502
        assert client.get("/bad").status_code == 502
        assert client.get("/boom").status_code == 500

        events = db.scalars(select(ErrorEvent).order_by(ErrorEvent.id)).all()
        assert [event.code for event in events] == ["http_502", "RuntimeError"]
        bad_event, boom_event = events
        assert (bad_event.source, bad_event.count, bad_event.request_path) == ("portal", 2, "GET /bad")
        assert bad_event.message == "HTTP 502 on GET /bad: Runtime unreachable"
        assert boom_event.message == "RuntimeError: secret=[REDACTED] kaput"
        assert "Traceback" in boom_event.detail and "abc" not in boom_event.detail
        assert _count(db) == 2
    finally:
        db.close()


def test_the_handler_keeps_warnings_of_the_portal_loggers_with_their_traceback():
    db = _database()
    try:
        handler = ErrorLogHandler(session_factory=lambda: db, asynchronous=False, max_per_minute=3)
        logger = logging.getLogger("app.tests.error_log_sample")
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        try:
            logger.warning("connector save restart failed agent_id=%s", "a-1")
            try:
                raise ValueError("bad token=xyz")
            except ValueError:
                logger.exception("delegation poll failed")
            logger.info("not a problem")
            foreign = logging.getLogger("uvicorn.error")
            record = foreign.makeRecord("uvicorn.error", logging.ERROR, __file__, 1, "Exception in ASGI application", (), None)
            handler.emit(record)
            logger.warning("third")
            logger.warning("fourth is over budget")
        finally:
            logger.removeHandler(handler)
            handler.close()

        events = db.scalars(select(ErrorEvent).order_by(ErrorEvent.id)).all()
        assert [event.message for event in events] == ["connector save restart failed agent_id=a-1", "delegation poll failed", "third"]
        warning, error, _third = events
        assert (warning.source, warning.severity, warning.logger_name) == ("log", "warning", "app.tests.error_log_sample")
        assert warning.detail.endswith("in test_the_handler_keeps_warnings_of_the_portal_loggers_with_their_traceback")
        assert (error.severity, error.code) == ("error", "ValueError")
        assert "Traceback" in error.detail and "token=[REDACTED]" in error.detail and "xyz" not in error.detail
        assert handler.dropped == 1 and handler.written == 3
    finally:
        db.close()


def test_install_attaches_one_handler_to_the_app_logger_and_uninstall_removes_it():
    db = _database()
    settings = SimpleNamespace(error_log_capture_level="ERROR", error_log_retention_days=30, error_log_max_rows=100)
    uninstall_error_log_handler()
    handler = install_error_log_handler(settings, session_factory=lambda: db, asynchronous=False)
    try:
        assert install_error_log_handler(settings) is handler
        assert handler in logging.getLogger("app").handlers and handler.level == logging.ERROR
        logging.getLogger("app.services.sample").warning("below the capture level")
        logging.getLogger("app.services.sample").error("pod crashed")
        assert [event.message for event in db.scalars(select(ErrorEvent)).all()] == ["pod crashed"]
        assert handler._maybe_prune(force=True) == {"deleted_old": 0, "deleted_over_cap": 0}
    finally:
        uninstall_error_log_handler()
        db.close()
    assert handler not in logging.getLogger("app").handlers


# ---------------------------------------------------------------------------
# Reading and pruning
# ---------------------------------------------------------------------------


def test_overview_groups_the_same_problem_filters_and_lists_recent_then_prune_bounds_the_table():
    db = _database()
    try:
        _admin, member, agent = _seed(db)
        for minute in range(3):
            _event(db, source="execution", code="chat_failed", message="Copilot returned 429", user_id=member.id, agent_id=agent.id, occurred_at=NOW - timedelta(minutes=minute))
        _event(db, source="connector_test", severity="warning", code="jira:jira", message="Jira answered 401", user_id=member.id, kind="jira", occurred_at=NOW - timedelta(hours=1))
        _event(db, source="portal", code="http_502", message="HTTP 502 on POST /x", request_path="POST /x", occurred_at=NOW - timedelta(days=2))
        _event(db, source="log", message="ancient", occurred_at=NOW - timedelta(days=20))
        service = ErrorLogService(db, now=NOW)

        overview = service.overview(7)
        totals = overview["totals"]
        assert (totals["rows"], totals["events"], totals["errors"], totals["warnings"]) == (5, 5, 4, 1)
        assert (totals["failed_runs"], totals["members"], totals["assistants"]) == (3, 1, 1)
        assert totals["last_seen_at"] == "2026-11-06T12:00:00"
        groups = overview["groups"]
        assert [(group["source"], group["events"]) for group in groups] == [("execution", 3), ("connector_test", 1), ("portal", 1)]
        assert groups[0]["message"] == "Copilot returned 429" and groups[0]["members"] == 1 and groups[0]["assistants"] == 1
        assert [row["source"] for row in overview["recent"]] == ["execution", "execution", "execution", "connector_test", "portal"]
        assert overview["recent"][0]["member"] == "Alex" and overview["recent"][0]["agent_name"] == "Release assistant"
        assert {item["source"]: item["events"] for item in overview["by_source"]} == {"execution": 3, "assistant": 0, "connector_test": 1, "portal": 1, "log": 0}

        assert service.overview(1)["totals"]["rows"] == 4, "24 hours: the Portal row is two days old"
        assert [row["message"] for row in service.overview(7, source="connector_test")["recent"]] == ["Jira answered 401"]
        assert service.overview(7, query="alex")["totals"]["rows"] == 4, "a member's nickname finds their rows"
        assert service.overview(7, query="502")["totals"]["rows"] == 1
        assert service.overview(7, fingerprint=groups[0]["fingerprint"])["totals"]["rows"] == 3
        page = service.overview(7, limit=2)
        assert len(page["recent"]) == 2 and page["has_more"] is True and overview["has_more"] is False

        detail = service.get(overview["recent"][3]["id"])
        assert detail["source"] == "connector_test" and detail["member"] == "Alex" and detail["kind"] == "jira"
        text = ErrorLogService.describe(detail)
        assert text.startswith("connector_test jira:jira") and "Jira answered 401" in text and "Member: Alex" in text
        assert service.get(999999) is None

        rows = service.export_rows(7)
        assert len(rows) == 5 and list(rows[0].keys()) == list(error_log.EXPORT_COLUMNS)
        assert rows[0]["member"] == "Alex" and rows[0]["agent_name"] == "Release assistant"

        assert service.prune(retention_days=10, max_rows=0) == {"deleted_old": 1, "deleted_over_cap": 0}
        assert service.prune(retention_days=0, max_rows=2) == {"deleted_old": 0, "deleted_over_cap": 3}
        assert [row["source"] for row in service.overview(7)["recent"]] == ["execution", "execution"]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# API and panel
# ---------------------------------------------------------------------------


def test_admin_error_api_is_admin_only_and_serves_overview_detail_and_csv():
    import app.api.admin as admin_api
    import app.deps as deps_module
    from app.main import app

    db = _database()
    admin, member, agent = _seed(db)
    event = _event(db, source="execution", code="chat_failed", message="Copilot returned 429", user_id=member.id, agent_id=agent.id, occurred_at=datetime.utcnow())
    try:
        app.dependency_overrides[admin_api.get_db] = _override_db(db)
        app.dependency_overrides[deps_module.get_current_user] = lambda: SimpleNamespace(id=member.id, username="12345678", role="user")
        client = TestClient(app)
        assert client.get("/api/admin/errors").status_code == 403

        app.dependency_overrides[deps_module.get_current_user] = lambda: SimpleNamespace(id=admin.id, username="admin", role="admin")
        response = client.get("/api/admin/errors?days=7&source=execution")
        assert response.status_code == 200
        body = response.json()
        assert body["totals"]["events"] == 1 and body["groups"][0]["code"] == "chat_failed" and body["recent"][0]["member"] == "Alex"
        assert client.get("/api/admin/errors?days=5").status_code == 422

        detail = client.get("/api/admin/errors/%s" % event.id)
        assert detail.status_code == 200 and detail.json()["agent_name"] == "Release assistant"
        assert client.get("/api/admin/errors/999999").status_code == 404

        exported = client.get("/api/admin/errors/export.csv?days=7")
        assert exported.status_code == 200 and exported.headers["content-type"].startswith("text/csv")
        assert exported.text.startswith(chr(0xFEFF) + "id,occurred_at") and "Copilot returned 429" in exported.text
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_errors_panel_renders_filters_groups_recent_and_the_detail_card(monkeypatch):
    import app.web as web_module
    from app.main import app

    db = _database()
    admin, member, agent = _seed(db)
    # The routes commit and close the session, which detaches the seeded rows: keep the id.
    member_id = member.id
    _event(db, source="execution", code="chat_failed", message="Copilot returned 429", user_id=member.id, agent_id=agent.id, model="gpt-6.1-sol", occurred_at=datetime.utcnow())
    _event(db, source="log", severity="warning", message="connector save restart failed", logger_name="app.services.runtime_profile_secret_service", detail="app/services/x.py:12 in restart", occurred_at=datetime.utcnow())
    current = SimpleNamespace(id=admin.id, username="admin", nickname="admin", role="admin")
    monkeypatch.setattr(web_module, "SessionLocal", lambda: db)
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: current)
    client = TestClient(app)
    try:
        response = client.get("/app/admin/errors/panel")
        assert response.status_code == 200
        html = response.text
        assert 'id="admin-errors-panel"' in html and 'data-period-days="7"' in html
        for marker in ("Most frequent", "Recent", "Copilot returned 429", "connector save restart failed", "Alex", "Release assistant", "chat_failed", "Kept for 90 days."):
            assert marker in html, marker
        assert 'hx-get="/app/admin/errors/panel"' in html and '<option value="7" selected>' in html
        assert 'href="/api/admin/errors/export.csv?days=7&amp;source=&amp;q=&amp;fingerprint="' in html
        assert html.count('hx-target="#admin-error-detail"') == 2, "one Details button per recent row"
        assert 'id="admin-error-detail"' in html

        filtered = client.get("/app/admin/errors/panel?days=30&source=log&q=restart").text
        assert "connector save restart failed" in filtered and "Copilot returned 429" not in filtered
        assert '<option value="log" selected>' in filtered and 'value="restart"' in filtered
        assert "No errors recorded in this period." in client.get("/app/admin/errors/panel?source=assistant").text

        event_id = db.scalar(select(ErrorEvent.id).where(ErrorEvent.source == "execution"))
        detail = client.get("/app/admin/errors/%s/panel" % event_id)
        assert detail.status_code == 200
        for marker in ('data-error-detail="%s"' % event_id, "Copy details", "Release assistant", "gpt-6.1-sol", "data-error-copy-text"):
            assert marker in detail.text, marker
        assert client.get("/app/admin/errors/999999/panel").status_code == 404

        monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: SimpleNamespace(id=member_id, username="12345678", role="user"))
        assert client.get("/app/admin/errors/panel").status_code == 403
    finally:
        db.close()


def test_the_panel_is_wired_into_administration_and_the_start_up():
    html = Path("app/templates/app.html").read_text(encoding="utf-8")
    assert 'data-admin-panel="errors"' in html and 'data-admin-panel-url="/app/admin/errors/panel"' in html
    assert "js/admin_errors.js" in html
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    assert '"errors": {' in js and 't("Failures and warnings collected for troubleshooting")' in js
    errors_js = Path("app/static/js/admin_errors.js").read_text(encoding="utf-8")
    for marker in ("htmx:afterSwap", "data-close-error-detail", "data-copy-error-detail", "navigator.clipboard"):
        assert marker in errors_js, marker
    main_py = Path("app/main.py").read_text(encoding="utf-8")
    assert "app.add_middleware(ErrorCaptureMiddleware" in main_py
    assert "install_error_log_handler(settings)" in main_py and "uninstall_error_log_handler()" in main_py
    web_py = Path("app/web.py").read_text(encoding="utf-8")
    assert 'source="connector_test"' in web_py, "a failed connector Test is recorded"
    css = Path("app/static/css/app.css").read_text(encoding="utf-8")
    for selector in (".portal-admin-error-detail", ".portal-admin-error-pre", ".portal-error-sev", ".portal-error-open"):
        assert selector in css, selector
