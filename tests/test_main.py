import importlib
import logging
import sys
from types import SimpleNamespace


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

    class DummySession:
        def close(self):
            return None

    monkeypatch.setattr(app_main, "SessionLocal", lambda: DummySession())
    monkeypatch.setattr(
        app_main,
        "UserRepository",
        lambda db: SimpleNamespace(get_by_username=lambda username: object(), create=lambda *args, **kwargs: None),
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
    monkeypatch.setattr(app_main, "setup_logging", lambda _level: None)
    monkeypatch.setattr(app_main, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(
        app_main,
        "UserRepository",
        lambda db: SimpleNamespace(get_by_username=lambda username: object(), create=lambda *args, **kwargs: None),
    )
    monkeypatch.setattr(
        app_main.engine,
        "_run_ddl_visitor",
        lambda *args, **kwargs: create_all_calls.append((args, kwargs)),
    )

    app_main.on_startup()

    assert create_all_calls == []
