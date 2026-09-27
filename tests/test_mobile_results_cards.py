"""Review, evidence, and matrix cards: parsing, the task page, the review
endpoint, the dispatcher's task id, video Range forwarding, and the JS."""
from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.models import AgentTask, AuditLog
from app.services.efp_cards import REPLY_CARDS_NOTE, extract_cards, live_matrix_path, reply_text_without_cards, review_decision_text
from tests._js_extract_helpers import _extract_js_function
from tests.test_agent_async_tasks_api import _client as _async_client
from tests.test_web_connector_settings import _build_env


# --- parsing ------------------------------------------------------------------

RESPONSE = """Ran 2 scenarios.

```efp-matrix
{"path": "mobile/runs/t1/matrix.json"}
```

Details below.

```efp-evidence
not json
```

```efp-review
{"title": "Scenarios", "items": [{"id": "S1", "title": "Buy USD"}]}
```
"""


def test_extract_cards_removes_valid_blocks_and_keeps_broken_ones():
    text, cards = extract_cards(RESPONSE)
    assert [card["kind"] for card in cards] == ["matrix", "review"]
    assert json.loads(cards[0]["payload_json"]) == {"path": "mobile/runs/t1/matrix.json"}
    assert "efp-matrix" not in text and "efp-review" not in text
    assert "```efp-evidence\nnot json\n```" in text
    assert text.startswith("Ran 2 scenarios.") and "Details below." in text


def test_extract_cards_ignores_other_fences_and_empty_text():
    assert extract_cards("```json\n{}\n```") == ("```json\n{}\n```", [])
    assert extract_cards(None) == ("", [])


def test_delegation_replies_drop_cards_and_point_at_portal():
    text = 'Drafted 4 scenarios for FX-12.\n\n```efp-review\n{"kind": "scenarios", "items": []}\n```'
    assert reply_text_without_cards(text) == f"Drafted 4 scenarios for FX-12.\n\n{REPLY_CARDS_NOTE}"
    assert reply_text_without_cards('```efp-matrix\n{"rows": []}\n```') == REPLY_CARDS_NOTE
    assert reply_text_without_cards("No cards here.") == "No cards here."


def test_review_decision_text_is_stable_and_bounded():
    text = review_decision_text(decision="approve", kind="scenarios", title="FX-101", approved=["S1", "S2", "S1"], declined=[], notes="")
    assert text == "REVIEW DECISION: approved (scenarios: FX-101)\nApproved: S1, S2\nNot approved: none"
    text = review_decision_text(decision="changes", kind="", title="", approved=[], declined=["S3"], notes="JPY has no decimals")
    assert text.splitlines()[0] == "REVIEW DECISION: changes requested (review)"
    assert text.endswith("Notes: JPY has no decimals")


# --- review endpoint --------------------------------------------------------------


def _async_task(db, agent, *, status="done", skill="design-mobile-scenarios", response="Drafted."):
    task = AgentTask(
        id=f"task-{status}",
        assignee_agent_id=agent.id,
        owner_user_id=agent.owner_user_id,
        created_by_user_id=agent.owner_user_id,
        source="portal",
        task_type="agent_async_task",
        task_family="agent_task",
        title="FX-101 scenarios",
        skill_name=skill,
        root_task_id=f"task-{status}",
        task_session_id=f"agent-task:task-{status}",
        input_payload_json=json.dumps({"schema": "agent_async_task.v1", "user_task": "Draft scenarios for FX-101", "skill_name": skill}),
        result_payload_json=json.dumps({"final_response": response}),
        status=status,
        retry_count=0,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def test_review_approval_continues_the_task_and_is_audited(monkeypatch):
    client, db, agent, cleanup = _async_client()
    scheduled = []
    monkeypatch.setattr("app.api.agent_tasks.task_dispatcher_service.dispatch_task_in_background", lambda task_id: scheduled.append(task_id))
    try:
        task = _async_task(db, agent)
        resp = client.post(f"/api/agent-tasks/{task.id}/review", json={
            "decision": "approve", "kind": "scenarios", "title": "FX-101", "approved": ["S1", "S2"], "declined": ["S3"], "notes": "S3 later",
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "queued"
        db.refresh(task)
        followup = json.loads(task.input_payload_json)["followup_task"]
        assert followup.startswith("REVIEW DECISION: approved (scenarios: FX-101)")
        assert "Not approved: S3" in followup and "Notes: S3 later" in followup
        assert scheduled == [task.id]
        audit = db.query(AuditLog).filter_by(action="task_review_decision").one()
        details = json.loads(audit.details_json)
        assert details["decision"] == "approve" and details["declined"] == ["S3"] and details["has_notes"] is True
    finally:
        cleanup()


def test_review_rejects_bad_decisions_and_running_tasks(monkeypatch):
    client, db, agent, cleanup = _async_client()
    monkeypatch.setattr("app.api.agent_tasks.task_dispatcher_service.dispatch_task_in_background", lambda task_id: None)
    try:
        task = _async_task(db, agent)
        assert client.post(f"/api/agent-tasks/{task.id}/review", json={"decision": "maybe"}).status_code == 400
        resp = client.post(f"/api/agent-tasks/{task.id}/review", json={"decision": "changes", "approved": ["S1"]})
        assert resp.status_code == 400 and "what to change" in resp.json()["detail"]
        running = _async_task(db, agent, status="running")
        assert client.post(f"/api/agent-tasks/{running.id}/review", json={"decision": "approve"}).status_code == 409
        assert db.query(AuditLog).filter_by(action="task_review_decision").count() == 0
    finally:
        cleanup()


# --- task page ----------------------------------------------------------------------


def _task_page_env(monkeypatch):
    import app.web as web_module

    env = _build_env(monkeypatch)
    owner = SimpleNamespace(id=env.owner.id, role=env.owner.role, username=env.owner.username, nickname=env.owner.username)
    monkeypatch.setattr(web_module, "_authorized_web_user", lambda _request: (owner, None))
    return env


def test_task_page_shows_cards_instead_of_their_json(monkeypatch):
    env = _task_page_env(monkeypatch)
    try:
        task = _async_task(env.db, env.agent, response=RESPONSE)
        html = env.client.get(f"/app/tasks/{task.id}/panel").text
        assert 'data-efp-card="matrix"' in html and 'data-efp-card="review"' in html
        assert f'data-agent-id="{env.agent.id}"' in html and f'data-task-id="{task.id}"' in html
        assert "mobile/runs/t1/matrix.json" in html
        assert "```efp-matrix" not in html
        # A finished task the viewer can continue gets an answerable review.
        review = html[html.index('data-efp-card="review"'):]
        assert 'data-readonly="1"' not in review[: review.index(">")]
    finally:
        env.cleanup()


def test_running_mobile_run_task_shows_the_live_matrix(monkeypatch):
    env = _task_page_env(monkeypatch)
    try:
        task = _async_task(env.db, env.agent, status="running", skill="run-mobile-scenarios", response="")
        html = env.client.get(f"/app/tasks/{task.id}/panel").text
        assert f'data-path="{live_matrix_path(task.id)}"' in html
        assert 'data-quiet-missing="1"' in html
        other = _async_task(env.db, env.agent, status="queued", skill="code-review", response="")
        assert "data-efp-card" not in env.client.get(f"/app/tasks/{other.id}/panel").text
    finally:
        env.cleanup()


# --- video Range ---------------------------------------------------------------------


def test_download_stream_forwards_range_and_returns_206(monkeypatch):
    from app.main import app
    import app.api.proxy as proxy_module

    captured = {}

    class _Upstream:
        status_code = 206
        headers = {"content-type": "video/mp4", "content-range": "bytes 0-99/5000", "accept-ranges": "bytes", "content-length": "100"}

        async def aiter_raw(self):
            yield b"x" * 100

    class _StreamCM:
        async def __aenter__(self):
            return _Upstream()

        async def __aexit__(self, *exc):
            return False

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def stream(self, method, url, params=None, headers=None):
            captured["headers"] = dict(headers or {})
            return _StreamCM()

        async def aclose(self):
            pass

    fake_user = SimpleNamespace(id=55, username="u", nickname="u", role="user")
    fake_agent = SimpleNamespace(id="agent-1", owner_user_id=55, visibility="private", status="running")
    app.dependency_overrides[proxy_module.get_current_user] = lambda: fake_user
    app.dependency_overrides[proxy_module.get_db] = lambda: iter([object()])
    try:
        monkeypatch.setattr(proxy_module, "AgentRepository", lambda _db: SimpleNamespace(get_by_id=lambda _id: fake_agent))
        monkeypatch.setattr(proxy_module.httpx, "AsyncClient", _Client)
        monkeypatch.setattr(proxy_module.proxy_service, "build_agent_base_url", lambda agent: "http://runtime.local:8000")
        resp = TestClient(app).get("/a/agent-1/api/server-files/download?paths=mobile/runs/t1/video.mp4", headers={"Range": "bytes=0-99"})
    finally:
        app.dependency_overrides.clear()
    assert captured["headers"].get("Range") == "bytes=0-99"
    assert resp.status_code == 206
    assert resp.headers["content-range"] == "bytes 0-99/5000"


# --- JS ----------------------------------------------------------------------------------


def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    return node


def _run_node(script: str) -> None:
    # A file, not node -e: an inlined module can pass Windows' command-line limit.
    import tempfile

    node = _node()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "script.js"
        path.write_text(script, encoding="utf-8")
        result = subprocess.run([node, str(path)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


CARDS_SHIM = """
globalThis.window = globalThis;
globalThis.document = { addEventListener() {}, querySelectorAll() { return []; }, getElementById() { return null; } };
globalThis.Event = class { constructor(type) { this.type = type; } };
"""


def test_efp_cards_paths_and_decision_text_in_node():
    source = Path("app/static/js/efp_cards.js").read_text(encoding="utf-8")
    script = CARDS_SHIM + source + textwrap.dedent(
        r"""
        const assert = require("node:assert/strict");
        const C = window.EfpCards;
        assert.equal(C.normalizePath("./mobile//runs/../runs/t1/"), "mobile/runs/t1");
        assert.equal(C.normalizePath("../../etc/passwd"), "");
        assert.equal(C.resolvePath("mobile/runs/t1/cases/a", "01-login.png"), "mobile/runs/t1/cases/a/01-login.png");
        assert.equal(C.resolvePath("mobile/runs/t1", "https://evil.example/x.png"), "");
        assert.equal(C.dirname("mobile/runs/t1/matrix.json"), "mobile/runs/t1");
        assert.equal(C.contentUrl("agent 1", "a b.png"), "/a/agent%201/api/server-files/content?path=a%20b.png");
        assert.equal(C.isCardLanguage("efp-matrix"), true);
        assert.equal(C.isCardLanguage("json"), false);
        assert.equal(C.parsePayload("{oops"), null);

        const ev = C.normalizeEvidence({
          scenario: "buy-currency", example: "JPY", status: "failed", platform: "android", device: "Pixel 8",
          video: "video.mp4", screenshots: [{ label: "confirm", file: "01-confirm.png" }], failure_screenshot: "screenshot.png",
          session_url: "javascript:alert(1)", error: { code: "assertion_failed", message: "total <b>wrong</b>" }, fallback_hits: [{}],
          script: "../../../../scripts/FX-12/android/buy-currency_JPY.yaml",
        }, "mobile/runs/t1/cases/buy-currency_JPY");
        assert.equal(ev.video, "mobile/runs/t1/cases/buy-currency_JPY/video.mp4");
        assert.equal(ev.script, "mobile/scripts/FX-12/android/buy-currency_JPY.yaml");
        assert.equal(ev.screenshots[0].path, "mobile/runs/t1/cases/buy-currency_JPY/screenshot.png");
        assert.equal(ev.sessionUrl, "");
        const html = C.evidenceCardHtml(ev, "agent-1");
        assert.ok(html.includes("buy-currency · JPY"));
        assert.ok(html.includes("&lt;b&gt;wrong&lt;/b&gt;"), "error text must be escaped");
        assert.ok(!html.includes("javascript:"), "non-http links are dropped");
        assert.ok(html.includes('<video class="efp-evidence-video"'));
        assert.ok(html.includes("drifted"));
        assert.ok(html.includes(">Script</a>") && html.includes("path=mobile%2Fscripts%2FFX-12%2Fandroid%2Fbuy-currency_JPY.yaml"));

        const summary = C.matrixSummary([{ status: "passed" }, { status: "failed" }, { status: "running" }, { status: "queued" }]);
        assert.deepEqual(summary, { total: 4, passed: 1, failed: 1, running: 1, queued: 1 });
        const matrix = C.matrixHtml({ suite: "fx-buy", rows: [{ id: "a#USD", case: "a", example: "USD", status: "passed", evidence: "cases/a_USD/evidence.json", classification: "drift", script: "../../scripts/FX-12/android/a_USD.yaml" }] }, { agentId: "agent-1" }, "mobile/runs/t1");
        assert.ok(matrix.includes('data-efp-evidence-toggle="mobile/runs/t1/cases/a_USD/evidence.json"'));
        assert.ok(matrix.includes("path=mobile%2Fscripts%2FFX-12%2Fandroid%2Fa_USD.yaml"));
        assert.ok(matrix.includes("Script drift"));

        const review = C.reviewHtml({ title: "FX <1>", items: [{ id: "S1", title: "Buy", type: "negative", warnings: ["no expected fee"] }, { id: "S2", selected: false }] }, {});
        assert.ok(review.includes("FX &lt;1&gt;"));
        assert.ok(review.includes('data-efp-review-item="S1" checked'));
        assert.ok(!review.includes('data-efp-review-item="S2" checked'));
        assert.equal(
          C.reviewDecisionText({ kind: "scenarios", title: "FX-101" }, "approve", ["S1"], ["S2"], "later"),
          "REVIEW DECISION: approved (scenarios: FX-101)\nApproved: S1\nNot approved: S2\nNotes: later",
        );
        """
    )
    _run_node(script)


def test_chat_ui_routes_card_fences_and_previews_media_in_node():
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    assert 'isEfpCardCodeElement(code) && window.EfpCards && window.EfpCards.buildFromCode(code)' in js
    helpers = "\n".join(
        _extract_js_function(js, name)
        for name in ("normalizeFenceLanguage", "isEfpCardFenceLanguage", "buildWorkspaceFileContentUrl", "buildWorkspaceFileDownloadUrl", "fileBlockPreviewHtml")
    )
    script = helpers + textwrap.dedent(
        r"""
        const assert = require("node:assert/strict");
        const escapeHtmlAttr = (value) => String(value ?? "").replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
        assert.equal(isEfpCardFenceLanguage("efp-evidence"), true);
        assert.equal(isEfpCardFenceLanguage("EFP-Review extra"), true);
        assert.equal(isEfpCardFenceLanguage("mermaid"), false);
        const img = fileBlockPreviewHtml("agent-1", "output/shot.PNG", "shot");
        assert.ok(img.includes('<img src="/a/agent-1/api/server-files/content?path=output%2Fshot.PNG"'));
        const video = fileBlockPreviewHtml("agent-1", "mobile/runs/t1/video.mp4", "video");
        assert.ok(video.includes('<video class="message-file-video"') && video.includes("server-files/download?paths="));
        assert.equal(fileBlockPreviewHtml("agent-1", "report.pdf", "report"), "");
        assert.equal(fileBlockPreviewHtml("", "shot.png", "shot"), "");
        """
    )
    _run_node(script)


def test_scripts_are_loaded_in_order():
    html = Path("app/templates/app.html").read_text(encoding="utf-8")
    assert html.index("js/efp_cards.js") < html.index("js/chat_ui.js")
    assert "js/mobile_testing.js" in html


def test_task_list_api_adds_scenario_progress_to_run_tasks(monkeypatch):
    client, db, agent, cleanup = _async_client()
    try:
        agent_id = agent.id
        owner_id = agent.owner_user_id
        payload = json.dumps({"output_payload": {"final_response": 'Done.\n\n```efp-matrix\n{"path": "mobile/runs/t/matrix.json", "summary": {"total": 4, "passed": 3, "failed": 1, "running": 0, "queued": 0}}\n```'}})
        db.add(AgentTask(id="task-run-progress", assignee_agent_id=agent_id, source="portal", task_type="agent_async_task", skill_name="/run-mobile-scenarios", status="done", owner_user_id=owner_id, created_by_user_id=owner_id, result_payload_json=payload))
        db.add(AgentTask(id="task-other", assignee_agent_id=agent_id, source="portal", task_type="agent_async_task", skill_name="write-product-requirements", status="done", owner_user_id=owner_id, created_by_user_id=owner_id, result_payload_json=payload))
        db.commit()
        rows = {row["id"]: row for row in client.get("/api/my/tasks").json()}
        assert rows["task-run-progress"]["scenario_progress"] == {"total": 4, "passed": 3, "failed": 1, "running": 0, "queued": 0}
        assert rows["task-other"]["scenario_progress"] is None
    finally:
        cleanup()


def test_task_nav_rows_render_scenario_progress():
    source = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    assert "${taskNavScenarioProgressHtml(task.scenario_progress)}" in source
    assert "function taskNavScenarioProgressHtml(progress)" in source


def test_scenario_progress_for_task_cards(monkeypatch):
    from app.services.efp_cards import scenario_progress

    with_summary = json.dumps({"final_response": 'Done.\n```efp-matrix\n{"path": "mobile/runs/t/matrix.json", "summary": {"total": 12, "passed": 9, "failed": 2, "running": 1}}\n```'})
    assert scenario_progress(with_summary) == {"total": 12, "passed": 9, "failed": 2, "running": 1, "queued": 0}
    inline = json.dumps({"output_payload": {"final_response": '```efp-matrix\n{"rows": [{"status": "passed"}, {"status": "failed"}]}\n```'}})
    assert scenario_progress(inline) == {"total": 2, "passed": 1, "failed": 1, "running": 0, "queued": 0}
    assert scenario_progress(json.dumps({"final_response": "no cards"})) is None
    assert scenario_progress("not json") is None

    env = _task_page_env(monkeypatch)
    try:
        task = _async_task(env.db, env.agent, skill="run-mobile-scenarios", response=json.loads(with_summary)["final_response"])
        html = env.client.get("/app/tasks/list").text
        assert "9/12 scenarios passed, 2 failed" in html
    finally:
        env.cleanup()
