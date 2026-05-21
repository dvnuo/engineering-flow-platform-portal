from pathlib import Path


MODULE_DIR = Path("app/static/js/opencode_chat")
RENDERER = MODULE_DIR / "renderer.js"
TEMPLATE = Path("app/templates/partials/opencode_chat_container.html")


def test_renderer_buttons_are_derived_from_view_state():
    src = RENDERER.read_text(encoding="utf-8")
    permission_src = (MODULE_DIR / "permission_panel.js").read_text(encoding="utf-8")

    assert "viewState.canSend ? \"\" : \"disabled\"" in src
    assert "viewState.canStop" in src
    assert "viewState.showReconnect" in src
    assert "data-opencode-stop" in src
    assert "data-opencode-reconnect" in src
    assert "data-opencode-new-chat" in src
    assert 'data-permission-decision="allow_once"' in permission_src
    assert 'data-permission-decision="deny"' in permission_src


def test_local_pending_placeholder_is_not_canonical_message():
    src = RENDERER.read_text(encoding="utf-8")

    assert 'data-local-submit="1"' in src
    pending_block_start = src.index('data-local-submit="1"')
    pending_block = src[pending_block_start:pending_block_start + 500]
    assert "data-canonical-message" not in pending_block
    assert "Waiting for OpenCode" in pending_block


def test_opencode_chat_files_do_not_reference_legacy_state_machine_symbols():
    forbidden = [
        "activeRequest",
        "inflightThinking",
        "hasActiveChatRequestForAgent",
        "shouldShowAbortChatRunButton",
        "clearStaleActiveRequest",
        "appendPortalChatRuntimeEvent",
        "handleAgentEventMessage",
        "startChatRunReconcileLoop",
        "reconcileChatRunOnce",
        "trySubmitChatStreamForSelectedAgent",
        "finalizeNonSuccessChatResponse",
        "/api/chat",
        "/api/chat/stream",
        "/api/chat/runs/",
        "/api/tasks",
        ":4096",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in MODULE_DIR.glob("*.js"))

    for marker in forbidden:
        assert marker not in combined


def test_template_exposes_opencode_thin_root():
    src = TEMPLATE.read_text(encoding="utf-8")

    assert 'id="opencode-chat-root"' in src
    assert 'data-runtime-type=""' in src
    assert 'data-chat-mode="thin"' in src
