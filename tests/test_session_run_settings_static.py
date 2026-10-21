"""The chat composer's Run settings follow the session.

No node here: the checks are on the shipped source, in the style of
test_chat_model_badge_static.py.
"""

from pathlib import Path

from _js_extract_helpers import _extract_js_function


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _js() -> str:
    return (_repo_root() / "app" / "static" / "js" / "chat_ui.js").read_text(encoding="utf-8")


def test_run_settings_are_saved_with_the_session_and_restored_with_it():
    src = _js()
    for name in ("composerRunSettingsPayload", "composerHasRunSettings", "persistSessionRunSettings", "restoreSessionRunSettings"):
        assert f"function {name}(" in src, name
    assert "/sessions/${encodeURIComponent(sessionId)}/run-settings`" in src

    # Opening a session restores its settings into the composer.
    load = _extract_js_function(src, "loadSessionForAgent")
    assert "void restoreSessionRunSettings(agentId, normalized);" in load

    # Every composer control saves on change.
    assert src.count("void persistSessionRunSettings(selectedAgentId);") == 1
    assert src.count("void persistSessionRunSettings(state.selectedAgentId);") == 2

    # A new chat inherits the composer's choice and saves it once the runtime
    # has named the session, before anything reloads it.
    assert "await persistSessionRunSettings(agentIdAtSend, createdSessionId);" in src

    # Restoring never overwrites a session the member has already left.
    restore = _extract_js_function(src, "restoreSessionRunSettings")
    assert "if (chatState.sessionId !== sid) return;" in restore
    assert "renderComposerModelSelectorForAgent(agentId)" in restore


def test_proxy_applies_the_sessions_settings_before_validating_the_request():
    source = (_repo_root() / "app" / "api" / "proxy.py").read_text(encoding="utf-8")
    applied = source.index("parsed_payload = apply_session_run_settings(db, agent, parsed_payload)")
    validated = source.index("_normalize_and_validate_inference_overrides_for_agent(\n                parsed_payload,")
    assert applied < validated
