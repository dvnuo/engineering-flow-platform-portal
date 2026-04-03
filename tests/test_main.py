import importlib
import logging
import sys


def test_main_calls_setup_logging(monkeypatch):
    calls = []

    def spy_setup_logging(level=logging.INFO):
        calls.append(level)

    import app.logger

    monkeypatch.setattr(app.logger, "setup_logging", spy_setup_logging)
    sys.modules.pop("app.main", None)

    importlib.import_module("app.main")

    assert len(calls) == 1
    assert calls[0] in (logging.INFO, logging.DEBUG)
