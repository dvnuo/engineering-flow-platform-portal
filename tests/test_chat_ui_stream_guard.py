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


def test_non_success_final_calls_incomplete_handler_inside_final_branch():
    src = _source()
    final_start = src.find("if (isChatStreamFinalEventName(outerType) || isDirectCompletionEventName(outerType)) {")
    assert final_start != -1
    final_snippet = src[final_start:final_start + 1800]
    assert "localIsNonSuccessFinalPayload(eventData)" in final_snippet
    assert "await localHandleIncompleteChatStream(" in final_snippet or "await handleIncompleteChatStream(" in final_snippet
    assert 'return "final_non_success"' in final_snippet


def test_stream_final_candidate_can_still_complete_legacy_final():
    src = _source()
    candidate_start = src.find("const candidate = requestCtx.streamFinalCandidate;")
    assert candidate_start != -1
    candidate_snippet = src[candidate_start:candidate_start + 2200]
    assert "isCompletedFinalPayload(candidate)" in candidate_snippet
    assert "finalResponseText(candidate)" in candidate_snippet
    assert "await handleAgentChatSuccess(agentIdAtSend, requestCtx" in candidate_snippet
    assert 'await handleIncompleteChatStream(agentIdAtSend, requestCtx, "runtime_incomplete", candidate);' in candidate_snippet


def test_non_stream_chat_ok_false_not_success():
    src = _source()
    chat_api_start = src.find('const resp = await fetch(`/a/${agentIdAtSend}/api/chat`')
    assert chat_api_start != -1
    chat_api_snippet = src[chat_api_start:chat_api_start + 1800]
    assert "payload?.ok === false" in chat_api_snippet
    assert "isNonSuccessFinalPayload(payload)" in chat_api_snippet
    assert 'await handleIncompleteChatStream(agentIdAtSend, requestCtx, "runtime_error_or_incomplete", payload);' in chat_api_snippet


def test_non_stream_completed_path_normalizes_response_before_success():
    src = _source()
    chat_api_start = src.find('const resp = await fetch(`/a/${agentIdAtSend}/api/chat`')
    assert chat_api_start != -1
    chat_api_snippet = src[chat_api_start:chat_api_start + 2200]
    assert "await handleAgentChatSuccess(agentIdAtSend, requestCtx, {" in chat_api_snippet
    assert "response: responseText" in chat_api_snippet
    assert "session_id: payload?.session_id || requestCtx.sessionIdAtSend || \"\"" in chat_api_snippet
    assert "await handleAgentChatSuccess(agentIdAtSend, requestCtx, payload);" not in chat_api_snippet


def test_non_success_states_include_empty_final():
    src = _source()
    assert "empty_final" in src
