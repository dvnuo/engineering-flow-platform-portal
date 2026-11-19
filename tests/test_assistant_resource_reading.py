"""What an assistant's pod is allowed, what it uses, and why it last stopped.

The details panel showed only the configured CPU and memory requests: not the
limit a pod can reach, not what it is using, and nothing about a pod that was
killed for running out of memory, which came back looking like an unexplained
restart. The reading pinned here comes from the pod itself. It is read-only and
best-effort: a cluster that cannot answer part of it (no metrics API, no pod, an
API error) still returns the rest, and nothing about it can fail a request or
change an assistant's status. The front-end half is pinned by static assertions
at the bottom and by a node run of the module's pure part.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.models.agent import Agent
from app.models.user import User
from app.services.k8s_service import K8sService
from app.services.runtime_status_cache import resource_reading_cache
from app.utils import k8s_quantity

REPO = Path(__file__).resolve().parents[1]
RESOURCES_JS = (REPO / "app/static/js/assistant_resources.js").read_text(encoding="utf-8")
CHAT_JS = (REPO / "app/static/js/chat_ui.js").read_text(encoding="utf-8")
APP_HTML = (REPO / "app/templates/app.html").read_text(encoding="utf-8")
CSS = (REPO / "app/static/css/app.css").read_text(encoding="utf-8")

T0 = datetime(2026, 11, 18, 6, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- quantities


def test_quantities_are_read_in_every_notation_the_cluster_uses():
    assert k8s_quantity.cpu_millicores("250m") == 250
    assert k8s_quantity.cpu_millicores("1") == 1000
    assert k8s_quantity.cpu_millicores("1.5") == 1500
    assert k8s_quantity.cpu_millicores("183240000n") == 183
    assert k8s_quantity.cpu_millicores("5000u") == 5
    assert k8s_quantity.memory_bytes("512Mi") == 512 * 1024**2
    assert k8s_quantity.memory_bytes("2Gi") == 2 * 1024**3
    assert k8s_quantity.memory_bytes("734224Ki") == 734224 * 1024
    assert k8s_quantity.memory_bytes("1G") == 10**9
    assert k8s_quantity.memory_bytes("129e6") == 129_000_000
    assert k8s_quantity.memory_bytes("128974848") == 128974848
    assert k8s_quantity.parse_quantity("1E") == Decimal("1e18")

    for unreadable in (None, "", "  ", "lots", "-1", "1Zi", "NaN", "Infinity"):
        assert k8s_quantity.parse_quantity(unreadable) is None
        assert k8s_quantity.memory_bytes(unreadable) is None


def test_readings_are_written_the_way_the_settings_write_them():
    assert k8s_quantity.format_cpu(120) == "120m"
    assert k8s_quantity.format_cpu(1000) == "1"
    assert k8s_quantity.format_cpu(1500) == "1.5"
    assert k8s_quantity.format_memory(734224 * 1024) == "717Mi"
    assert k8s_quantity.format_memory(2 * 1024**3) == "2Gi"
    assert k8s_quantity.format_memory(int(1.44 * 1024**3)) == "1.4Gi"
    assert k8s_quantity.percent_of(512, 2048) == 25
    assert k8s_quantity.percent_of(5, None) is None
    assert k8s_quantity.percent_of(None, 10) is None
    assert k8s_quantity.percent_of(5, 0) is None


# --------------------------------------------------------------- the reading


class _ApiError(Exception):
    def __init__(self, status):
        super().__init__(f"api error {status}")
        self.status = status


class _FakeCore:
    def __init__(self, pods=(), error=None):
        self.pods = list(pods)
        self.error = error
        self.calls = []

    def list_namespaced_pod(self, namespace, label_selector):
        self.calls.append((namespace, label_selector))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(items=self.pods)


class _FakeMetrics:
    def __init__(self, usage=None, error=None, payload=None):
        self.usage = usage
        self.error = error
        self.payload = payload
        self.calls = []

    def get_namespaced_custom_object(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if self.payload is not None:
            return self.payload
        return {
            "containers": [
                {"name": "sidecar", "usage": {"cpu": "1", "memory": "9Gi"}},
                {"name": "agent", "usage": self.usage},
            ]
        }


def _pod(
    name="agent-pod-new",
    *,
    created=T0,
    phase="Running",
    reason=None,
    message=None,
    deleting=False,
    requests=None,
    limits=None,
    restart_count=0,
    terminated=None,
    state_terminated_at=None,
    conditions=(),
    container_name="agent",
):
    last_state = SimpleNamespace(
        terminated=SimpleNamespace(**terminated) if terminated else None,
    )
    state = SimpleNamespace(
        terminated=SimpleNamespace(finished_at=state_terminated_at) if state_terminated_at else None,
    )
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            creation_timestamp=created,
            deletion_timestamp=T0 if deleting else None,
        ),
        spec=SimpleNamespace(
            containers=[
                SimpleNamespace(
                    name=container_name,
                    resources=SimpleNamespace(requests=requests, limits=limits),
                )
            ]
        ),
        status=SimpleNamespace(
            phase=phase,
            reason=reason,
            message=message,
            start_time=created + timedelta(seconds=5),
            conditions=list(conditions),
            container_statuses=[
                SimpleNamespace(
                    name=container_name,
                    restart_count=restart_count,
                    last_state=last_state,
                    state=state,
                )
            ],
        ),
    )


def _service(pods=(), *, metrics=None, core_error=None):
    service = K8sService()
    service.enabled = True
    service.core_api = _FakeCore(pods, error=core_error)
    service.custom_api = metrics if metrics is not None else _FakeMetrics(error=_ApiError(404))
    return service


AGENT = SimpleNamespace(id="agent-1", namespace="efp-agents", cpu="250m", memory="512Mi")
CONFIGURED_REQUESTS = {"cpu": "250m", "memory": "512Mi", "ephemeral_storage": None}
CONFIGURED_LIMITS = {"cpu": "1", "memory": "2Gi", "ephemeral_storage": None}


def test_without_a_cluster_the_reading_is_what_the_portal_would_configure():
    service = K8sService()
    service.enabled = False

    reading = service.get_agent_resource_reading(AGENT)

    assert reading == {
        "source": "configured",
        "requests": CONFIGURED_REQUESTS,
        "limits": CONFIGURED_LIMITS,
        "usage": None,
        "usage_status": "kubernetes_disabled",
        "pod_name": None,
        "pod_started_at": None,
        "restart_count": 0,
        "last_termination": None,
        "last_eviction": None,
    }


def test_a_pod_killed_for_memory_says_so_with_its_own_limits_and_live_usage():
    pod = _pod(
        requests={"cpu": "500m", "memory": "1Gi"},
        limits={"cpu": "2", "memory": "4Gi", "ephemeral-storage": "10Gi"},
        restart_count=3,
        terminated={"reason": "OOMKilled", "exit_code": 137, "finished_at": T0 + timedelta(hours=1)},
    )
    metrics = _FakeMetrics(usage={"cpu": "183240000n", "memory": "1048576Ki"})
    service = _service([pod], metrics=metrics)

    reading = service.get_agent_resource_reading(AGENT)

    assert service.core_api.calls == [("efp-agents", "app=agent,agent-id=agent-1")]
    assert metrics.calls == [
        {
            "group": "metrics.k8s.io",
            "version": "v1beta1",
            "namespace": "efp-agents",
            "plural": "pods",
            "name": "agent-pod-new",
        }
    ]
    # The pod's own amounts, not the Portal's current defaults: a pod started
    # before a settings change keeps what it was started with.
    assert reading["source"] == "pod"
    assert reading["requests"] == {"cpu": "500m", "memory": "1Gi", "ephemeral_storage": None}
    assert reading["limits"] == {"cpu": "2", "memory": "4Gi", "ephemeral_storage": "10Gi"}
    assert reading["pod_name"] == "agent-pod-new"
    assert reading["pod_started_at"] == "2026-11-18T06:00:05+00:00"
    assert reading["restart_count"] == 3
    assert reading["last_termination"] == {
        "reason": "OOMKilled",
        "exit_code": 137,
        "finished_at": "2026-11-18T07:00:00+00:00",
    }
    assert reading["last_eviction"] is None
    assert reading["usage_status"] == "ok"
    assert reading["usage"] == {
        "cpu": "183m",
        "memory": "1Gi",
        "cpu_millicores": 183,
        "memory_bytes": 1024**3,
        "cpu_percent_of_limit": 9,
        "memory_percent_of_limit": 25,
    }


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_ApiError(404), "metrics_unavailable"),
        (_ApiError(503), "metrics_unavailable"),
        (_ApiError(403), "metrics_forbidden"),
        (RuntimeError("connection reset"), "metrics_unavailable"),
    ],
)
def test_a_cluster_without_usage_still_reports_limits_and_restarts(error, expected):
    pod = _pod(
        limits={"cpu": "1", "memory": "2Gi"},
        restart_count=1,
        terminated={"reason": "Error", "exit_code": 1, "finished_at": T0},
    )
    service = _service([pod], metrics=_FakeMetrics(error=error))

    reading = service.get_agent_resource_reading(AGENT)

    assert reading["usage"] is None
    assert reading["usage_status"] == expected
    assert reading["limits"]["memory"] == "2Gi"
    assert reading["restart_count"] == 1
    assert reading["last_termination"]["reason"] == "Error"


@pytest.mark.parametrize(
    "payload",
    [
        {"containers": []},
        {"containers": [{"name": "agent", "usage": None}]},
        {"containers": [{"name": "agent", "usage": {"cpu": "?", "memory": "?"}}]},
        {"kind": "Status"},
        "not a mapping",
    ],
)
def test_an_unreadable_usage_answer_is_no_usage_rather_than_an_error(payload):
    service = _service([_pod()], metrics=_FakeMetrics(payload=payload))

    reading = service.get_agent_resource_reading(AGENT)

    assert reading["usage"] is None
    assert reading["usage_status"] == "metrics_unavailable"
    assert reading["pod_name"] == "agent-pod-new"


def test_a_stopped_assistant_has_no_pod_and_shows_the_configured_amounts():
    metrics = _FakeMetrics(usage={"cpu": "1m", "memory": "1Mi"})
    service = _service([], metrics=metrics)

    reading = service.get_agent_resource_reading(AGENT)

    assert reading["source"] == "configured"
    assert reading["usage_status"] == "no_pod"
    assert reading["requests"] == CONFIGURED_REQUESTS
    assert reading["limits"] == CONFIGURED_LIMITS
    assert metrics.calls == []


def test_the_newest_live_pod_is_read_and_one_being_removed_is_not():
    old = _pod("agent-pod-old", created=T0 - timedelta(hours=2), restart_count=9)
    leaving = _pod("agent-pod-leaving", created=T0 + timedelta(hours=1), deleting=True, restart_count=7)
    done = _pod("agent-pod-done", created=T0 + timedelta(hours=2), phase="Succeeded", restart_count=5)
    current = _pod("agent-pod-current", created=T0, restart_count=2)
    service = _service([old, leaving, done, current])

    reading = service.get_agent_resource_reading(AGENT)

    assert reading["pod_name"] == "agent-pod-current"
    assert reading["restart_count"] == 2


def test_an_eviction_is_read_from_the_pod_it_left_behind():
    evicted_long_ago = _pod(
        "agent-pod-evicted-1",
        created=T0 - timedelta(days=3),
        phase="Failed",
        reason="Evicted",
        message="The node was low on resource: ephemeral-storage.",
        state_terminated_at=T0 - timedelta(days=2),
    )
    evicted = _pod(
        "agent-pod-evicted-2",
        created=T0 - timedelta(hours=5),
        phase="Failed",
        reason="Evicted",
        message="The node was low on resource: memory. Threshold quantity: 100Mi, available: 60Mi.",
        state_terminated_at=T0 - timedelta(hours=1),
    )
    current = _pod("agent-pod-current", created=T0)
    service = _service([evicted_long_ago, current, evicted])

    reading = service.get_agent_resource_reading(AGENT)

    assert reading["pod_name"] == "agent-pod-current"
    assert reading["restart_count"] == 0
    assert reading["last_termination"] is None
    assert reading["last_eviction"] == {
        "reason": "Evicted",
        "message": "The node was low on resource: memory. Threshold quantity: 100Mi, available: 60Mi.",
        "at": "2026-11-18T05:00:00+00:00",
    }


def test_an_eviction_time_falls_back_to_the_disruption_condition_or_stays_unknown():
    condition = SimpleNamespace(type="DisruptionTarget", last_transition_time=T0 - timedelta(minutes=30))
    with_condition = _pod("evicted", phase="Failed", reason="Evicted", conditions=[condition])
    service = _service([with_condition])
    reading = service.get_agent_resource_reading(AGENT)
    assert reading["usage_status"] == "no_pod"
    assert reading["last_eviction"]["at"] == "2026-11-18T05:30:00+00:00"

    # Never the evicted pod's own start time: that is when the lost run began.
    without = _pod("evicted", phase="Failed", reason="Evicted")
    reading = _service([without]).get_agent_resource_reading(AGENT)
    assert reading["last_eviction"] == {"reason": "Evicted", "message": None, "at": None}


def test_a_cluster_that_cannot_list_pods_returns_the_configured_reading():
    service = _service(core_error=_ApiError(500))

    reading = service.get_agent_resource_reading(AGENT)

    assert reading["usage_status"] == "error"
    assert reading["source"] == "configured"
    assert reading["limits"] == CONFIGURED_LIMITS
    assert reading["restart_count"] == 0


def test_a_pod_the_portal_cannot_make_sense_of_never_raises():
    broken = SimpleNamespace(
        metadata=SimpleNamespace(name="odd", creation_timestamp="yesterday", deletion_timestamp=None),
        spec=None,
        status=SimpleNamespace(phase="Running", container_statuses="nonsense", start_time=None),
    )
    service = _service([broken])

    reading = service.get_agent_resource_reading(AGENT)

    assert reading["usage_status"] in {"error", "metrics_unavailable"}
    assert reading["limits"] == CONFIGURED_LIMITS


# ------------------------------------------------- optional local-disk bounds


def test_local_disk_bounds_are_off_unless_an_operator_sets_them(monkeypatch):
    settings = Settings()
    assert settings.default_agent_ephemeral_storage == ""
    assert settings.default_agent_ephemeral_storage_limit == ""

    monkeypatch.setenv("DEFAULT_AGENT_EPHEMERAL_STORAGE", "2Gi")
    monkeypatch.setenv("DEFAULT_AGENT_EPHEMERAL_STORAGE_LIMIT", "10Gi")
    settings = Settings()
    assert settings.default_agent_ephemeral_storage == "2Gi"
    assert settings.default_agent_ephemeral_storage_limit == "10Gi"


def test_the_pod_template_only_gains_local_disk_bounds_when_they_are_set():
    service = K8sService()
    service.enabled = False
    settings = service.settings
    original = (settings.default_agent_ephemeral_storage, settings.default_agent_ephemeral_storage_limit)
    try:
        settings.default_agent_ephemeral_storage = ""
        settings.default_agent_ephemeral_storage_limit = ""
        resources = service._build_agent_container_resources(AGENT)
        assert resources.requests == {"cpu": "250m", "memory": "512Mi"}
        assert resources.limits == {"cpu": "1", "memory": "2Gi"}

        settings.default_agent_ephemeral_storage = " 2Gi "
        settings.default_agent_ephemeral_storage_limit = "10Gi"
        resources = service._build_agent_container_resources(AGENT)
        assert resources.requests == {"cpu": "250m", "memory": "512Mi", "ephemeral-storage": "2Gi"}
        assert resources.limits == {"cpu": "1", "memory": "2Gi", "ephemeral-storage": "10Gi"}
        reading = service.get_agent_resource_reading(AGENT)
        assert reading["requests"]["ephemeral_storage"] == "2Gi"
        assert reading["limits"]["ephemeral_storage"] == "10Gi"

        settings.default_agent_ephemeral_storage = ""
        resources = service._build_agent_container_resources(SimpleNamespace(id="a", cpu=None, memory=None))
        assert resources.requests is None
        assert resources.limits == {"cpu": "1", "memory": "2Gi", "ephemeral-storage": "10Gi"}
    finally:
        settings.default_agent_ephemeral_storage, settings.default_agent_ephemeral_storage_limit = original


def test_shell_tool_overrides_reach_the_pod_only_when_an_operator_sets_them(monkeypatch):
    assert Settings().shell_memory_guard_percent == ""
    assert Settings().shell_capture_max_bytes == ""
    monkeypatch.setenv("EFP_SHELL_MEMORY_GUARD_PERCENT", "0")
    monkeypatch.setenv("EFP_SHELL_CAPTURE_MAX_BYTES", "1048576")
    assert Settings().shell_memory_guard_percent == "0"
    assert Settings().shell_capture_max_bytes == "1048576"

    service = K8sService()
    service.enabled = False
    settings = service.settings
    original = (settings.shell_memory_guard_percent, settings.shell_capture_max_bytes)
    agent = SimpleNamespace(id="a1", runtime_type="native", mount_path="/workspace")
    try:
        settings.shell_memory_guard_percent = ""
        settings.shell_capture_max_bytes = ""
        names = [item.name for item in service._build_agent_container_env(agent)]
        assert "EFP_SHELL_MEMORY_GUARD_PERCENT" not in names
        assert "EFP_SHELL_CAPTURE_MAX_BYTES" not in names

        settings.shell_memory_guard_percent = " 0 "
        settings.shell_capture_max_bytes = "1048576"
        env = {item.name: getattr(item, "value", None) for item in service._build_agent_container_env(agent)}
        assert env["EFP_SHELL_MEMORY_GUARD_PERCENT"] == "0"
        assert env["EFP_SHELL_CAPTURE_MAX_BYTES"] == "1048576"
    finally:
        settings.shell_memory_guard_percent, settings.shell_capture_max_bytes = original


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

    def _agent(name, owner_id, visibility="private"):
        return Agent(
            name=name,
            owner_user_id=owner_id,
            visibility=visibility,
            status="running",
            image="img",
            deployment_name=f"d-{name}",
            service_name=f"s-{name}",
            pvc_name=f"p-{name}",
        )

    mine = _agent("mine", owner.id)
    hidden = _agent("hidden", other.id)
    db.add_all([mine, hidden])
    db.commit()
    for agent in (mine, hidden):
        db.refresh(agent)

    app.dependency_overrides[agents_api.get_current_user] = lambda: SimpleNamespace(
        id=owner.id, role="user", username="owner", nickname="owner"
    )
    app.dependency_overrides[agents_api.get_db] = lambda: db
    resource_reading_cache.clear()

    def cleanup():
        app.dependency_overrides.clear()
        resource_reading_cache.clear()
        db.close()

    return TestClient(app), db, {"mine": mine, "hidden": hidden}, cleanup


def test_the_endpoint_returns_the_reading_to_someone_who_can_see_the_assistant(monkeypatch):
    client, db, agents, cleanup = _client()
    try:
        reads = []

        def _reading(agent):
            reads.append(agent.id)
            return {
                "source": "pod",
                "requests": {"cpu": "250m", "memory": "512Mi", "ephemeral_storage": None},
                "limits": {"cpu": "1", "memory": "2Gi", "ephemeral_storage": None},
                "usage": {
                    "cpu": "120m",
                    "memory": "734Mi",
                    "cpu_millicores": 120,
                    "memory_bytes": 769654784,
                    "cpu_percent_of_limit": 12,
                    "memory_percent_of_limit": 36,
                },
                "usage_status": "ok",
                "pod_name": "agent-pod",
                "pod_started_at": "2026-11-18T06:00:05+00:00",
                "restart_count": 2,
                "last_termination": {"reason": "OOMKilled", "exit_code": 137, "finished_at": "2026-11-18T07:00:00+00:00"},
                "last_eviction": None,
            }

        monkeypatch.setattr("app.api.agents.k8s_service.get_agent_resource_reading", _reading)
        status_reads = []
        monkeypatch.setattr(
            "app.api.agents.k8s_service.get_agent_runtime_status",
            lambda agent: status_reads.append(agent.id),
        )

        response = client.get(f"/api/agents/{agents['mine'].id}/resources")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == agents["mine"].id
        assert body["source"] == "pod"
        assert body["limits"] == {"cpu": "1", "memory": "2Gi", "ephemeral_storage": None}
        assert body["usage"]["memory_percent_of_limit"] == 36
        assert body["restart_count"] == 2
        assert body["last_termination"] == {
            "reason": "OOMKilled",
            "exit_code": 137,
            "finished_at": "2026-11-18T07:00:00+00:00",
        }
        assert body["last_eviction"] is None

        # Several tabs with the same details open share one cluster read.
        assert client.get(f"/api/agents/{agents['mine'].id}/resources").status_code == 200
        assert reads == [agents["mine"].id]
        # Reading resources never touches the assistant's status.
        assert status_reads == []
        db.expire_all()
        assert db.get(Agent, agents["mine"].id).status == "running"
    finally:
        cleanup()


def test_the_endpoint_keeps_other_peoples_assistants_private(monkeypatch):
    client, _db, agents, cleanup = _client()
    try:
        monkeypatch.setattr(
            "app.api.agents.k8s_service.get_agent_resource_reading",
            lambda agent: pytest.fail("must not read a pod the caller cannot see"),
        )

        assert client.get(f"/api/agents/{agents['hidden'].id}/resources").status_code == 403
        assert client.get("/api/agents/no-such-assistant/resources").status_code == 404
    finally:
        cleanup()


def test_the_endpoint_answers_without_a_cluster():
    client, _db, agents, cleanup = _client()
    try:
        response = client.get(f"/api/agents/{agents['mine'].id}/resources")

        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "configured"
        assert body["usage"] is None
        assert body["usage_status"] == "kubernetes_disabled"
        assert body["restart_count"] == 0
    finally:
        cleanup()


def test_the_portal_role_may_read_pod_metrics_and_nothing_more_of_that_api():
    manifest = (REPO / "k8s/service-account.yaml").read_text(encoding="utf-8")
    rule = manifest.split('apiGroups: ["metrics.k8s.io"]', 1)[1].split("---", 1)[0]
    assert 'resources: ["pods"]' in rule
    assert 'verbs: ["get", "list"]' in rule


# ---------------------------------------------------------------- front end


def test_the_panel_asks_for_the_reading_only_when_details_are_drawn():
    render = CHAT_JS.split("function renderAgentMeta(agent) {", 1)[1].split("\nasync function fetchSkillGitInfo", 1)[0]
    assert 'id="agent-resource-reading"' in render
    assert 'data-agent-id="${escapeHtmlAttr(agent.id)}" hidden' in render
    # Optional call: without the module the pills stand as they always have.
    assert "window.AssistantResources?.show(agent.id);" in render
    assert CHAT_JS.count("AssistantResources") == 1
    # The three pills are still drawn from the assistant row itself.
    assert 'portal-resource-pill is-cpu' in render
    assert 'portal-resource-pill is-memory' in render
    assert 'portal-resource-pill is-disk' in render
    # Loaded before chat_ui.js, so the first details render already finds it.
    assert APP_HTML.index("js/assistant_resources.js") < APP_HTML.index("js/chat_ui.js")
    assert ".portal-resource-reading[hidden]" in CSS
    assert ".portal-resource-line.is-warning" in CSS


def test_the_module_leaves_the_panel_alone_when_the_request_fails():
    assert "/api/agents/${encodeURIComponent(agentId)}/resources" in RESOURCES_JS
    assert "if (!response.ok) return;" in RESOURCES_JS
    assert "window.AssistantResources = { show, describe };" in RESOURCES_JS
    # A reading for another assistant never lands in the open panel.
    assert "host.dataset.agentId === String(agentId)" in RESOURCES_JS
    # Everything drawn goes through the escaper.
    draw = RESOURCES_JS.split("function draw(host, reading) {", 1)[1].split("\n  }\n", 1)[0]
    assert "esc(line.text)" in draw and "esc(line.label)" in draw and "esc(line.detail)" in draw


def _describe(reading, now_iso="2026-11-18T08:00:00Z"):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = "\n".join(
        [
            "global.window = {};",
            "global.document = { documentElement: { lang: 'en-GB' }, getElementById: () => null };",
            f"require({json.dumps((REPO / 'app/static/js/i18n.js').as_posix())});",
            f"require({json.dumps((REPO / 'app/static/js/assistant_resources.js').as_posix())});",
            f"const lines = window.AssistantResources.describe({json.dumps(reading)}, Date.parse({json.dumps(now_iso)}));",
            "console.log(JSON.stringify(lines));",
        ]
    )
    completed = subprocess.run([node, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_describe_draws_limit_usage_and_an_out_of_memory_restart():
    lines = _describe(
        {
            "requests": {"cpu": "250m", "memory": "512Mi"},
            "limits": {"cpu": "1", "memory": "2Gi", "ephemeral_storage": "10Gi"},
            "usage": {"cpu": "120m", "memory": "734Mi", "cpu_percent_of_limit": 12, "memory_percent_of_limit": 36},
            "usage_status": "ok",
            "restart_count": 3,
            "last_termination": {"reason": "OOMKilled", "exit_code": 137, "finished_at": "2026-11-18T07:00:00+00:00"},
            "last_eviction": None,
        }
    )

    assert lines[0] == {"label": "Reserved", "text": "250m CPU · 512Mi memory"}
    assert lines[1] == {"label": "Limit", "text": "1 CPU · 2Gi memory · 10Gi local disk"}
    assert lines[2] == {
        "label": "In use now",
        "text": "120m CPU (12% of the limit) · 734Mi memory (36% of the limit)",
    }
    assert lines[3] == {"text": "Restarted 3 times since this run began."}
    assert lines[4]["tone"] == "warning"
    assert lines[4]["text"].startswith("Last restart, ")
    assert "ran out of memory and was stopped at its 2Gi limit" in lines[4]["text"]
    assert len(lines) == 5


def test_describe_says_when_usage_is_not_reported_and_explains_other_stops():
    lines = _describe(
        {
            "requests": {},
            "limits": {"memory": "2Gi"},
            "usage": None,
            "usage_status": "metrics_forbidden",
            "restart_count": 1,
            "last_termination": {"reason": "Error", "exit_code": 1, "finished_at": None},
            "last_eviction": {
                "reason": "Evicted",
                "message": "The node was low on resource: memory.",
                "at": "2026-11-18T05:00:00+00:00",
            },
        }
    )

    assert lines[0] == {"label": "Limit", "text": "2Gi memory"}
    assert lines[1] == {"label": "In use now", "text": "not reported by this cluster"}
    assert lines[2] == {"text": "Restarted once since this run began."}
    assert lines[3] == {
        "tone": "warning",
        "text": "Its last restart happened because it stopped unexpectedly (Error, exit code 1).",
    }
    assert lines[4]["tone"] == "warning"
    assert lines[4]["text"].startswith("Moved to another machine, ")
    assert lines[4]["detail"] == "The node was low on resource: memory."


def test_describe_is_quiet_for_a_healthy_or_stopped_assistant():
    healthy = _describe(
        {
            "requests": {"cpu": "250m", "memory": "512Mi"},
            "limits": {"cpu": "1", "memory": "2Gi"},
            "usage": None,
            "usage_status": "no_pod",
            "restart_count": 0,
            "last_termination": {"reason": "Completed", "exit_code": 0, "finished_at": "2026-11-18T07:00:00Z"},
            # Older than a week: no longer worth a warning.
            "last_eviction": {"reason": "Evicted", "message": "old", "at": "2026-11-01T00:00:00Z"},
        }
    )

    assert [line.get("label") for line in healthy] == ["Reserved", "Limit"]
    assert _describe(None) == []
    assert _describe({}) == []
