import importlib
import logging
import sys
from types import SimpleNamespace

import pytest


def test_main_calls_setup_logging_on_startup_not_import(monkeypatch):
    calls = []

    def spy_setup_logging(level=logging.INFO):
        calls.append(level)

    import app.logger

    monkeypatch.setattr(app.logger, "setup_logging", spy_setup_logging)
    sys.modules.pop("app.main", None)

    app_main = importlib.import_module("app.main")

    assert calls == []

    schema_ready_calls = []
    monkeypatch.setattr(app_main, "assert_portal_schema_ready", lambda _engine: schema_ready_calls.append(True))
    monkeypatch.setattr(app_main, "assert_phase5_schema_compatibility", lambda _engine: None)
    monkeypatch.setattr(app_main, "assert_runtime_profile_schema_compatibility", lambda _engine: None)

    class DummySession:
        def close(self):
            return None

    monkeypatch.setattr(app_main, "SessionLocal", lambda: DummySession())
    monkeypatch.setattr(
        app_main,
        "UserRepository",
        lambda db: SimpleNamespace(get_by_username=lambda username: object(), create=lambda *args, **kwargs: None),
    )
    monkeypatch.setattr(
        app_main,
        "RuntimeProfileService",
        lambda _db: SimpleNamespace(
            ensure_user_has_default_profile=lambda _user: None,
            repair_legacy_runtime_profiles=lambda _db2: None,
            ensure_defaults_for_all_users=lambda _db2: None,
        ),
    )

    app_main.on_startup()

    assert len(calls) == 1
    assert calls[0] in (logging.INFO, logging.DEBUG)
    assert schema_ready_calls == [True]


def test_main_startup_does_not_call_create_all(monkeypatch):
    import app.main as app_main

    create_all_calls = []
    monkeypatch.setattr(app_main, "assert_portal_schema_ready", lambda _engine: None)
    monkeypatch.setattr(app_main, "assert_phase5_schema_compatibility", lambda _engine: None)
    monkeypatch.setattr(app_main, "assert_runtime_profile_schema_compatibility", lambda _engine: None)
    monkeypatch.setattr(app_main, "setup_logging", lambda _level: None)
    monkeypatch.setattr(app_main, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(
        app_main,
        "UserRepository",
        lambda db: SimpleNamespace(get_by_username=lambda username: object(), create=lambda *args, **kwargs: None),
    )
    monkeypatch.setattr(
        app_main,
        "RuntimeProfileService",
        lambda _db: SimpleNamespace(
            ensure_user_has_default_profile=lambda _user: None,
            repair_legacy_runtime_profiles=lambda _db2: None,
            ensure_defaults_for_all_users=lambda _db2: None,
        ),
    )
    monkeypatch.setattr(
        app_main.engine,
        "_run_ddl_visitor",
        lambda *args, **kwargs: create_all_calls.append((args, kwargs)),
    )

    app_main.on_startup()

    assert create_all_calls == []


def test_main_startup_starts_worker_after_runtime_profile_repairs(monkeypatch):
    import app.main as app_main

    call_order: list[str] = []
    monkeypatch.setattr(app_main, "setup_logging", lambda _level: call_order.append("setup_logging"))
    monkeypatch.setattr(app_main, "assert_portal_schema_ready", lambda _engine: call_order.append("schema_guard"))
    monkeypatch.setattr(app_main, "assert_phase5_schema_compatibility", lambda _engine: None)
    monkeypatch.setattr(app_main, "assert_runtime_profile_schema_compatibility", lambda _engine: None)
    monkeypatch.setattr(app_main.settings, "automation_rules_worker_enabled", True)
    monkeypatch.setattr(app_main.worker_singleton, "start", lambda: call_order.append("worker_start"))
    monkeypatch.setattr(
        app_main,
        "UserRepository",
        lambda db: SimpleNamespace(get_by_username=lambda _username: object(), create=lambda *args, **kwargs: None),
    )

    class DummyRuntimeProfileService:
        def __init__(self, _db):
            pass

        def ensure_user_has_default_profile(self, _admin_user):
            call_order.append("ensure_default_for_admin")

        def repair_legacy_runtime_profiles(self, _db):
            call_order.append("repair")

        def ensure_defaults_for_all_users(self, _db):
            call_order.append("ensure_defaults")

    monkeypatch.setattr(app_main, "RuntimeProfileService", DummyRuntimeProfileService)
    monkeypatch.setattr(app_main, "SessionLocal", lambda: SimpleNamespace(close=lambda: call_order.append("db_close")))

    app_main.on_startup()

    assert call_order.index("schema_guard") < call_order.index("repair")
    assert call_order.index("repair") < call_order.index("ensure_defaults")
    assert call_order.index("db_close") < call_order.index("worker_start")


def test_main_startup_does_not_start_worker_when_repair_raises(monkeypatch):
    import app.main as app_main

    call_order: list[str] = []
    monkeypatch.setattr(app_main, "setup_logging", lambda _level: None)
    monkeypatch.setattr(app_main, "assert_portal_schema_ready", lambda _engine: None)
    monkeypatch.setattr(app_main, "assert_phase5_schema_compatibility", lambda _engine: None)
    monkeypatch.setattr(app_main, "assert_runtime_profile_schema_compatibility", lambda _engine: None)
    monkeypatch.setattr(app_main.settings, "automation_rules_worker_enabled", True)
    monkeypatch.setattr(app_main.worker_singleton, "start", lambda: call_order.append("worker_start"))
    monkeypatch.setattr(
        app_main,
        "UserRepository",
        lambda db: SimpleNamespace(get_by_username=lambda _username: object(), create=lambda *args, **kwargs: None),
    )

    class DummyRuntimeProfileService:
        def __init__(self, _db):
            pass

        def ensure_user_has_default_profile(self, _admin_user):
            return None

        def repair_legacy_runtime_profiles(self, _db):
            raise RuntimeError("repair boom")

        def ensure_defaults_for_all_users(self, _db):
            return None

    monkeypatch.setattr(app_main, "RuntimeProfileService", DummyRuntimeProfileService)
    monkeypatch.setattr(app_main, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))

    with pytest.raises(RuntimeError, match="repair boom"):
        app_main.on_startup()
    assert "worker_start" not in call_order
