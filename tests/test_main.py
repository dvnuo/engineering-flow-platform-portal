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

    monkeypatch.setattr(app_main.Base.metadata, "create_all", lambda **kwargs: None)

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
