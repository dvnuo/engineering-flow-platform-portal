"""Keeping assistant status honest after the page has loaded.

Before this the sidebar read status once, on load, and again only after a
lifecycle click. Idle auto-stop, evictions and crash loops were invisible until
a reload. The batch endpoint below backs a periodic refresh; the cache keeps
that refresh from turning into a Kubernetes call per assistant per tab per
half minute. The front-end half (polling, optimistic states, wake-on-send,
slow-start escalation) is pinned by static assertions at the bottom.
"""

from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models.agent import Agent
from app.models.user import User
from app.services.runtime_status_cache import RuntimeStatusCache, runtime_status_cache
from tests._js_extract_helpers import _extract_js_function

CHAT_JS = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
STARTUP_JS = Path("app/static/js/assistant_startup.js").read_text(encoding="utf-8")
CSS = Path("app/static/css/app.css").read_text(encoding="utf-8")


# ------------------------------------------------------------------- cache


def test_cache_expires_and_invalidates():
    now = [100.0]
    cache = RuntimeStatusCache(ttl_seconds=10, clock=lambda: now[0])

    assert cache.get("a") is None
    cache.put("a", "running")
    assert cache.get("a") == "running"
    now[0] = 109.9
    assert cache.get("a") == "running"
    now[0] = 110.0
    assert cache.get("a") is None

    cache.put("a", "running")
    cache.invalidate("a")
    assert cache.get("a") is None


# ---------------------------------------------------------------- endpoint


def _client():
    from fastapi.testclient import TestClient
    from app.main import app
    import app.api.agents as agents_api

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    owner = User(username="owner", password_hash="x", role="user", is_active=True)
    other = User(username="other", password_hash="x", role="user", is_active=True)
    db.add_all([owner, other])
    db.commit()
    db.refresh(owner)
    db.refresh(other)

    def _agent(name, owner_id, status, visibility="private"):
        return Agent(
            name=name,
            owner_user_id=owner_id,
            visibility=visibility,
            status=status,
            image="img",
            deployment_name=f"d-{name}",
            service_name=f"s-{name}",
            pvc_name=f"p-{name}",
        )

    mine = _agent("mine", owner.id, "running")
    public = _agent("public", other.id, "stopped", visibility="public")
    hidden = _agent("hidden", other.id, "running")
    db.add_all([mine, public, hidden])
    db.commit()
    for agent in (mine, public, hidden):
        db.refresh(agent)

    app.dependency_overrides[agents_api.get_current_user] = lambda: SimpleNamespace(
        id=owner.id, role="user", username="owner", nickname="owner"
    )
    app.dependency_overrides[agents_api.get_db] = lambda: db
    runtime_status_cache.clear()

    def cleanup():
        app.dependency_overrides.clear()
        runtime_status_cache.clear()
        db.close()

    return TestClient(app), db, {"mine": mine, "public": public, "hidden": hidden}, cleanup


def test_batch_status_covers_mine_and_public_only(monkeypatch):
    client, db, agents, cleanup = _client()
    try:
        reads = []

        def _status(agent):
            reads.append(agent.id)
            return SimpleNamespace(status="running", message=None, cpu_usage="1", memory_usage="2")

        monkeypatch.setattr("app.api.agents.k8s_service.get_agent_runtime_status", _status)

        response = client.get("/api/agents/status")
        assert response.status_code == 200
        by_id = {row["id"]: row for row in response.json()["statuses"]}
        assert set(by_id) == {agents["mine"].id, agents["public"].id}
        assert by_id[agents["public"].id]["status"] == "running"
        assert by_id[agents["public"].id]["startup"]["phase"] == "ready"
        # The reading is persisted, as the single endpoint always did.
        db.expire_all()
        assert db.get(Agent, agents["public"].id).status == "running"
    finally:
        cleanup()


def test_batch_status_reads_through_cache_but_actions_invalidate(monkeypatch):
    client, db, agents, cleanup = _client()
    try:
        reads = []

        def _status(agent):
            reads.append(agent.id)
            return SimpleNamespace(status="running", message=None, cpu_usage=None, memory_usage=None)

        monkeypatch.setattr("app.api.agents.k8s_service.get_agent_runtime_status", _status)
        monkeypatch.setattr(
            "app.api.agents.k8s_service.stop_agent",
            lambda _agent: SimpleNamespace(status="stopped", message=None),
        )

        client.get("/api/agents/status")
        client.get("/api/agents/status")
        assert reads.count(agents["mine"].id) == 1, "second batch within the TTL must not hit the cluster"

        # The single endpoint stays live (someone is watching it start)...
        client.get(f"/api/agents/{agents['mine'].id}/status")
        assert reads.count(agents["mine"].id) == 2

        # ...and a lifecycle action drops the cached reading, so the refresh
        # right after Stop cannot report the "running" from a moment ago.
        stop = client.post(f"/api/agents/{agents['mine'].id}/stop")
        assert stop.status_code == 200
        monkeypatch.setattr(
            "app.api.agents.k8s_service.get_agent_runtime_status",
            lambda agent: SimpleNamespace(status="stopped", message=None, cpu_usage=None, memory_usage=None),
        )
        after = client.get("/api/agents/status").json()["statuses"]
        assert {row["id"]: row["status"] for row in after}[agents["mine"].id] == "stopped"
    finally:
        cleanup()


# ---------------------------------------------------------- front-end: poll


def test_sidebar_polls_statuses_and_refreshes_on_return_to_tab():
    poll = _extract_js_function(CHAT_JS, "pollAgentStatuses")
    assert 'api("/api/agents/status")' in poll
    assert "if (document.hidden && !force) return false;" in poll
    assert 'applyAgentStatusSnapshot(entries, { source: "poll" })' in poll

    start = _extract_js_function(CHAT_JS, "startAgentStatusPolling")
    assert "setInterval" in start
    assert 'document.addEventListener("visibilitychange"' in start
    assert 'window.addEventListener("focus"' in start
    boot = CHAT_JS.split("await refreshAll({ preserveLayout: true, skipRouteApply: true });", 1)[1][:300]
    assert "startAgentStatusPolling();" in boot

    refresh_all = _extract_js_function(CHAT_JS, "refreshAll")
    assert 'api("/api/agents/status")' in refresh_all
    # Older servers without the batch endpoint still get a sidebar.
    assert "api(`/api/agents/${agent.id}/status`)" in refresh_all


def test_snapshot_renders_once_and_nudges_the_banner():
    snapshot = _extract_js_function(CHAT_JS, "applyAgentStatusSnapshot")
    assert "{ render: false }" in snapshot
    assert snapshot.count("renderAgentList();") == 1
    assert 'action: "refresh"' in snapshot
    assert 'if (source !== "banner" && selectedPrevious !== selectedCurrent)' in snapshot
    assert "wakeInFlight" in snapshot
    # The banner keeps the elapsed clock on a refresh; only actions reset it.
    assert 'if (lifecycleAction === "refresh") {' in STARTUP_JS
    assert "watch(agentId, { keepElapsed: true });" in STARTUP_JS


# ---------------------------------------------- front-end: optimistic state


def test_lifecycle_actions_show_their_transition_immediately_and_revert_on_failure():
    action_fn = _extract_js_function(CHAT_JS, "action")
    assert 'const OPTIMISTIC_STATUS = { start: "starting", stop: "stopping", restart: "restarting" };' in action_fn
    assert "applyLocalAgentStatus(lifecycleAgentId, OPTIMISTIC_STATUS[lifecycleAction], \"\");" in action_fn
    assert "if (previousLocal) applyLocalAgentStatus(lifecycleAgentId, previousLocal.status, previousLocal.lastError);" in action_fn
    # Start answers "running" before the pod exists; it must not flash Ready.
    assert 'result?.status || "creating"' not in action_fn
    assert 'if (lifecycleAction === "stop") applyLocalAgentStatus(lifecycleAgentId, result?.status || "stopped"' in action_fn

    health = _extract_js_function(CHAT_JS, "agentHealth")
    assert 'label: "Stopping"' in health
    assert ".portal-agent-status-dot.status-stopping" in CSS


def test_start_is_reachable_from_the_home_card_and_the_banner():
    sync_state = _extract_js_function(CHAT_JS, "syncSelectedAgentState")
    assert "data-home-start-agent=" in sync_state
    assert '["stopped", "failed"].includes(status)' in sync_state
    bind_events = _extract_js_function(CHAT_JS, "bindEvents")
    assert 'document.addEventListener("portal:agent-action"' in bind_events
    assert "[data-home-start-agent]" in CHAT_JS
    # The banner delegates rather than calling the API itself, so restart
    # polling, toasts and refreshes are the same whichever button was pressed.
    assert 'new CustomEvent("portal:agent-action", { detail: { agentId, action: "start" } })' in STARTUP_JS
    assert "fetch(`/api/agents/${encodeURIComponent(agentId)}/start`" not in STARTUP_JS


# ----------------------------------------------- front-end: wake on send


def test_sending_to_a_paused_assistant_wakes_it_first():
    submit = _extract_js_function(CHAT_JS, "submitChatForSelectedAgent")
    assert "if (!(await prepareSelectedAssistantForSend(agentIdAtSend))) return;" in submit
    assert "chatState.wakeInFlight" in submit

    prepare = _extract_js_function(CHAT_JS, "prepareSelectedAssistantForSend")
    assert "const hadSession = Boolean(chatState?.sessionId);" in prepare
    assert "if (!hadSession) await startNewChatForSelectedAgent();" in prepare

    wake = _extract_js_function(CHAT_JS, "wakeSelectedAssistant")
    assert 'if (status === "stopped") {' in wake
    assert "await waitForAgentRuntimeStatus(agentId, {" in wake
    assert 'failureStatuses: ["failed", "deleting"]' in wake

    # Failed is not wakeable: the banner explains and offers Retry instead.
    assert 'WAKEABLE_AGENT_STATUSES = new Set(["stopped", "starting", "creating", "pending", "restarting"])' in CHAT_JS

    controls = _extract_js_function(CHAT_JS, "syncSelectedAgentChatActionControls")
    assert "dom.sendChatBtn.disabled = busy || (needsStart && !wakeable)" in controls
    assert '"Start & chat"' in controls

    sync_state = _extract_js_function(CHAT_JS, "syncSelectedAgentState")
    assert "const showChat = running || canWakeSelectedAssistant(agent, status);" in sync_state
    assert "if (!running && state.eventWsAgentId === agent.id) disconnectEventSocket();" in sync_state

    placeholder = _extract_js_function(CHAT_JS, "updateChatInputPlaceholder")
    assert "Paused. Sending a message starts it first" in placeholder


# ------------------------------------------ front-end: slow-start escalation


def test_slow_start_changes_the_card_without_promising_a_restart():
    assert "const SLOW_START_FACTOR = 2.5;" in STARTUP_JS
    assert "const SUPPORT_AFTER_SECONDS = 600;" in STARTUP_JS
    assert 'headline: "Startup is taking longer than usual"' in STARTUP_JS
    assert 'view.action = "contact_support";' in STARTUP_JS
    assert "const startup = escalate(rawStartup);" in STARTUP_JS
    assert ".portal-startup-progress.is-slow" in CSS
