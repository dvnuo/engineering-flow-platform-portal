from pathlib import Path
import re


def _source() -> str:
    return Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")


def test_chat_ui_does_not_success_from_streamed_text_only():
    src = _source()
    assert "if (requestCtx.streamedText) { requestCtx.streamCompleted = true; await handleAgentChatSuccess" not in src
    danger = re.search(r"if\s*\(\s*requestCtx\.streamedText\s*\)[\s\S]{0,800}handleAgentChatSuccess", src)
    assert danger is None


def test_chat_ui_has_incomplete_stream_handler():
    src = _source()
    assert "handleIncompleteChatStream" in src
    assert "completion_state" in src or "completionState" in src
    assert "missing_final" in src
    assert "Stream ended before a final assistant response." in src


def test_chat_ui_final_success_requires_final_event():
    src = _source()
    assert "isCompletedFinalPayload" in src
    assert "getCompletionState" in src
    assert "isChatStreamFinalEventName" in src
