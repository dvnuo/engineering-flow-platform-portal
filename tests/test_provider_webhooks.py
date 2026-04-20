import json

from fastapi.testclient import TestClient

from app.api import provider_webhooks
from app.schemas.external_event_ingress import ExternalEventIngressResponse


def test_normalize_github_review_requested_user_target():
    payload = {
        "action": "review_requested",
        "pull_request": {"number": 7, "head": {"sha": "abc"}, "html_url": "https://example/pr/7", "title": "PR title"},
        "repository": {"name": "portal", "owner": {"login": "acme"}},
        "requested_reviewer": {"login": "alice", "id": 11},
    }
    req = provider_webhooks._normalize_github_review_requested(payload)
    assert req is not None
    assert req.external_account_id == "alice"
    parsed_payload = json.loads(req.payload_json or "{}")
    assert parsed_payload["review_target"] == {"type": "user", "name": "alice"}
    assert parsed_payload["reviewer"] == "alice"
    assert parsed_payload["review_team"] is None
    parsed_meta = json.loads(req.metadata_json or "{}")
    assert parsed_meta["provider_review_target_kind"] == "user"
    assert parsed_meta["provider_reviewer_id"] == 11


def test_normalize_github_review_requested_team_target():
    payload = {
        "action": "review_requested",
        "pull_request": {"number": 9, "head": {"sha": "def"}},
        "repository": {"name": "portal", "owner": {"login": "acme"}},
        "requested_team": {"slug": "reviewers", "id": 22},
    }
    req = provider_webhooks._normalize_github_review_requested(payload)
    assert req is not None
    assert req.external_account_id == "acme/reviewers"
    parsed_payload = json.loads(req.payload_json or "{}")
    assert parsed_payload["review_target"] == {"type": "team", "name": "acme/reviewers"}
    assert parsed_payload["reviewer"] is None
    assert parsed_payload["review_team"] == "acme/reviewers"
    parsed_meta = json.loads(req.metadata_json or "{}")
    assert parsed_meta["provider_review_target_kind"] == "team"
    assert parsed_meta["provider_team_id"] == 22


def test_normalize_github_review_requested_malformed_payload_returns_none():
    payload = {
        "action": "review_requested",
        "pull_request": {"number": 9},
        "repository": {"name": "portal", "owner": {"login": "acme"}},
    }
    assert provider_webhooks._normalize_github_review_requested(payload) is None


def test_github_webhook_team_payload_routes_to_external_event_router(monkeypatch):
    from app.main import app

    provider_webhooks.settings.allow_insecure_provider_webhooks = True
    captured = {}

    def _fake_route(ingress_payload, _db):
        captured["ingress_payload"] = ingress_payload
        return ExternalEventIngressResponse(
            accepted=True,
            matched_subscription_ids=[],
            routing_reason="matched_automation_rule",
            matched_agent_id="agent-1",
            created_task_id="task-1",
            matched_workflow_rule_id=None,
            resolved_task_type="github_review_task",
            deduped=False,
            message="ok",
        )

    monkeypatch.setattr(provider_webhooks.service, "route_external_event", _fake_route)
    client = TestClient(app)
    resp = client.post(
        "/api/webhooks/github",
        json={
            "action": "review_requested",
            "pull_request": {"number": 12, "head": {"sha": "xyz"}, "title": "T"},
            "repository": {"name": "portal", "owner": {"login": "acme"}},
            "requested_team": {"slug": "reviewers", "id": 22},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] is True
    assert body["routing_reason"] == "matched_automation_rule"
    ingress_payload = captured["ingress_payload"]
    parsed_payload = json.loads(ingress_payload.payload_json or "{}")
    assert ingress_payload.event_type == "pull_request_review_requested"
    assert parsed_payload["review_target"] == {"type": "team", "name": "acme/reviewers"}
