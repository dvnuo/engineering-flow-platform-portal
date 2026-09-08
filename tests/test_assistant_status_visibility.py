"""The selected assistant's lifecycle must be visible wherever the member is.

Before this, the startup card (phases, failure cause, Retry) was drawn inside
the chat transcript, and the chat view is hidden whenever the assistant is not
running -- so the card was only ever visible once it had nothing to say. The
sidebar showed an 8px dot with no text and no motion, and the header showed
nothing at all. These tests pin the three fixes: a banner outside both views,
a labelled and animated sidebar row, and a header badge.
"""

from pathlib import Path

from app.services.agent_startup_status import startup_view
from tests._js_extract_helpers import _extract_js_function

TEMPLATE = Path("app/templates/app.html").read_text(encoding="utf-8")
CHAT_JS = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
STARTUP_JS = Path("app/static/js/assistant_startup.js").read_text(encoding="utf-8")
CSS = Path("app/static/css/app.css").read_text(encoding="utf-8")


# ------------------------------------------------------------------ banner


def test_startup_banner_sits_between_header_and_both_main_views():
    header_end = TEMPLATE.index("</header>")
    banner = TEMPLATE.index('id="assistant-status-banner"')
    home = TEMPLATE.index('id="center-placeholder"')
    chat = TEMPLATE.index('id="agent-chat-app"')
    assert header_end < banner < home < chat
    # Non-assistant sections start with it hidden; syncMainHeader flips it.
    assert 'id="assistant-status-banner" class="portal-assistant-status-banner hidden"' in TEMPLATE


def test_startup_card_mounts_in_banner_not_in_transcript():
    assert 'const BANNER_ID = "assistant-status-banner"' in STARTUP_JS
    assert 'document.getElementById("message-list")' not in STARTUP_JS
    assert "message-row-assistant" not in STARTUP_JS
    assert "banner.replaceChildren(card)" in STARTUP_JS


def test_banner_follows_lifecycle_actions_and_reports_status_back():
    # chat_ui -> banner: selection, lifecycle actions, and the initial load sync
    assert 'document.addEventListener("portal:agent-selected"' in STARTUP_JS
    assert 'document.addEventListener("portal:agent-lifecycle"' in STARTUP_JS
    assert 'lifecycleAction === "sync"' in STARTUP_JS
    # banner -> chat_ui: every reading, so the dot, badge and view follow along
    assert 'new CustomEvent("portal:agent-status"' in STARTUP_JS
    assert 'document.addEventListener("portal:agent-status"' in _extract_js_function(CHAT_JS, "bindEvents")

    action_fn = _extract_js_function(CHAT_JS, "action")
    assert 'new CustomEvent("portal:agent-lifecycle"' in action_fn
    # Start is not in parseAgentLifecycleAction's stop|restart regex; it must
    # still announce itself.
    assert "/start$/" in action_fn

    sync_state = _extract_js_function(CHAT_JS, "syncSelectedAgentState")
    assert "announceStartupWatch(agent)" in sync_state
    assert "announceStartupWatch(null)" in sync_state


def test_external_status_switches_view_but_leaves_restart_poll_alone():
    handler = _extract_js_function(CHAT_JS, "handleExternalAgentStatus")
    assert 'applyAgentStatusSnapshot([{ agentId, payload }], { source: "banner" })' in handler
    snapshot = _extract_js_function(CHAT_JS, "applyAgentStatusSnapshot")
    assert "updateAgentRuntimeStatusCache(agentId, payload, { render: false })" in snapshot
    assert 'if (selectedPrevious === "restarting") return;' in snapshot
    assert "await syncSelectedAgentState();" in snapshot


def test_banner_hides_write_actions_from_readers():
    assert 'WRITE_ACTIONS = new Set(["start", "retry", "open_connections"])' in STARTUP_JS
    assert "if (WRITE_ACTIONS.has(startup.action) && !canWrite) return" in STARTUP_JS
    assert "canWrite: canWriteAgent(getSelectedAgent())" in CHAT_JS


# ------------------------------------------------------------ server copy


def test_stopped_offers_start_instead_of_promising_a_wake_up():
    # Chat is disabled while stopped, so "it wakes up when you send a message"
    # was false. The card now carries the Start button itself.
    view = startup_view("stopped")
    assert view["action"] == "start"
    assert view["action_label"] == "Start"
    assert "wakes up" not in view["detail"]


def test_restarting_says_restarting():
    assert startup_view("restarting")["headline"].startswith("Restarting")
    assert startup_view("creating")["headline"].startswith("Starting")


# ----------------------------------------------------------------- sidebar


def test_sidebar_row_has_a_status_label_and_pulses_while_in_transition():
    render = _extract_js_function(CHAT_JS, "renderAgentList")
    assert 'portal-agent-status-label is-${safe(health.tone)}' in render
    assert '${health.busy ? " is-pulsing" : ""}' in render

    health = _extract_js_function(CHAT_JS, "agentHealth")
    assert 'label: "Restarting"' in health
    assert 'label: "Starting"' in health
    assert 'label: "Deleting"' in health
    # A locally noted "Restart requested" must not paint the row red while the
    # restart is in progress.
    assert "(lastError && !inTransition)" in health


def test_sidebar_dot_styles_distinguish_stopped_and_animate_transitions():
    assert ".portal-agent-status-dot.status-stopped { background: transparent;" in CSS
    assert ".portal-agent-status-dot.is-pulsing { animation: portal-status-pulse" in CSS
    assert "@keyframes portal-status-pulse" in CSS
    # Listed in one of the reduced-motion blocks (the sidebar one, next to the row).
    reduced_blocks = CSS.split("@media (prefers-reduced-motion: reduce)")[1:]
    assert any(
        ".portal-agent-status-dot.is-pulsing" in block and ".portal-header-status-badge.is-pulsing::before" in block
        for block in reduced_blocks
    )
    assert "grid-template-columns: auto minmax(0, 1fr) auto" in CSS.split(".portal-agent-row-head {", 1)[1].split("}", 1)[0]


# ------------------------------------------------------------------ header


def test_header_badge_exists_and_renders_health_not_raw_status():
    assert 'id="selected-status" class="portal-status-badge portal-header-status-badge hidden"' in TEMPLATE
    header_copy = TEMPLATE.split('class="portal-main-header-copy"', 1)[1].split("</div>\n        </div>", 1)[0]
    assert 'id="embed-title"' in header_copy
    assert 'id="selected-status"' in header_copy

    setter = _extract_js_function(CHAT_JS, "setSelectedStatusText")
    assert "agentHealth(agent)" in setter
    assert "dom.selectedStatus.textContent = health.label" in setter
    assert 'portal-header-status-badge is-${health.tone}' in setter
    assert "dom.selectedStatus.textContent = status" not in setter


def test_non_assistant_sections_hide_badge_and_banner():
    body = _extract_js_function(CHAT_JS, "syncMainHeader")
    assert 'document.getElementById("assistant-status-banner")?.classList.toggle("hidden", !assistantMode)' in body
    else_branch = body.split("} else {", 1)[1]
    assert 'setSelectedStatusText("idle")' in else_branch


def test_startup_banner_card_is_centred_on_wide_screens():
    rule = CSS.split(".portal-assistant-status-banner .portal-startup-progress {", 1)[1].split("}", 1)[0]
    assert "margin: 0 auto" in rule
