"""Each assistant message says which model answered, on hover at its top-right.

The runtimes stamp `model` / `requested_model` / `model_fallback_reason` on
assistant messages, on the final chat payload and on usage.updated events; the
chat UI turns that into a `.message-model` badge in the message header. No
node here: the checks are on the shipped source, in the style of
test_chat_assistant_message_actions.py.
"""
from pathlib import Path

from _js_extract_helpers import _extract_js_function, _extract_js_helper_block


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _js() -> str:
    return (_repo_root() / "app" / "static" / "js" / "chat_ui.js").read_text(encoding="utf-8")


def _css() -> str:
    return (_repo_root() / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")


def test_model_attribution_helpers_read_runtime_fields_and_render_a_badge():
    helpers = _extract_js_helper_block(_js(), "assistantModelAttribution")

    for name in ("assistantModelAttribution", "assistantModelBadgeText", "buildAssistantModelBadgeHtml", "applyAssistantModelBadge", "latestAssistantModelAttribution"):
        assert f"function {name}(" in helpers, name
    # The runtime field names, exactly as the native runner and the opencode adapter stamp them.
    for field in ("\"model\"", "\"resolved_model\"", "\"requested_model\"", "\"model_fallback_reason\"", "\"resolved_model_fallback\""):
        assert field in helpers, field
    assert "Auto \\u2192" in helpers
    assert 'class="message-model' in helpers
    assert "message-model-fallback" in helpers
    assert "inferenceModelLabel(attribution.model)" in helpers


def test_history_and_live_rows_apply_the_badge():
    src = _js()

    history = _extract_js_function(src, "renderChatHistory")
    assert "latestAssistantModelAttribution(entry.messages)" in history
    assert "buildAssistantModelBadgeHtml(" in history

    article = _extract_js_function(src, "buildAssistantMessageArticle")
    assert "buildAssistantModelBadgeHtml(assistantModelAttribution(options))" in article
    assert "${modelBadgeHtml}</div><article" in article

    group = _extract_js_function(src, "buildAssistantGroupMessageArticle")
    assert "latestAssistantModelAttribution(group?.messages || [])" in group
    assert "modelFallbackReason: attribution?.fallbackReason" in group

    finalize = _extract_js_function(src, "finalizePendingAssistantRow")
    assert "applyAssistantModelBadge(article, assistantModelAttribution(payload) || requestCtx?.modelAttribution || null)" in finalize

    update = _extract_js_function(src, "updateOrCreateAssistantRowForRequest")
    assert "applyAssistantModelBadge(article, assistantModelAttribution(payload) || requestCtx?.modelAttribution || null)" in update

    stream = _extract_js_function(src, "handleChatStreamEvent")
    assert 'if (embeddedType === "usage.updated")' in stream
    assert "requestCtx.modelAttribution = modelAttribution" in stream
    assert "applyAssistantModelBadge(findPendingAssistantArticle(" in stream


def test_canonical_history_keeps_the_model_fields_for_opencode_sessions():
    src = _js()

    canonical = _extract_js_function(src, "getCanonicalMessagesFromSessionPayload")
    assert "assistantModelAttribution(message)" in canonical
    assert "model_fallback_reason: attribution?.fallbackReason" in canonical

    legacy = _extract_js_function(src, "canonicalMessageToLegacyDisplayMessage")
    assert "model: String(message.model || info.modelID || info.model_id || \"\")" in legacy
    assert "requested_model: String(message.requested_model || \"\")" in legacy


def test_badge_shows_on_hover_at_the_top_right_of_the_bubble():
    css = _css()

    assert ".message-row-assistant .message-meta { width: min(100%, calc(var(--portal-chat-width, 980px) - 60px)); }" in css
    assert ".message-model { margin-left: auto;" in css
    assert ".message-row:hover .message-model," in css
    assert ".message-row:focus-within .message-model { opacity: 1; }" in css
    assert "@media (hover: none) { .message-model { opacity: 1; } }" in css
    assert ".message-model-fallback { border-style: dashed; }" in css
