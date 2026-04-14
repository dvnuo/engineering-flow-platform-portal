import hashlib
import hmac
import json

from fastapi import APIRouter, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.schemas.external_event_ingress import ExternalEventIngressRequest, ExternalEventIngressResponse
from app.services.external_event_router import ExternalEventRouterService
from fastapi import Depends

router = APIRouter(tags=["provider-webhooks"])
service = ExternalEventRouterService()
settings = get_settings()


def _normalize_github_review_requested(payload: dict) -> ExternalEventIngressRequest | None:
    if payload.get("action") != "review_requested":
        return None
    pull_request = payload.get("pull_request") if isinstance(payload.get("pull_request"), dict) else {}
    repo_obj = payload.get("repository") if isinstance(payload.get("repository"), dict) else {}
    owner_obj = repo_obj.get("owner") if isinstance(repo_obj.get("owner"), dict) else {}
    requested_reviewer = payload.get("requested_reviewer") if isinstance(payload.get("requested_reviewer"), dict) else {}

    owner = owner_obj.get("login")
    repo = repo_obj.get("name")
    pull_number = pull_request.get("number") or payload.get("number")
    reviewer = requested_reviewer.get("login")
    head_obj = pull_request.get("head") if isinstance(pull_request.get("head"), dict) else {}
    head_sha = head_obj.get("sha")
    if not owner or not repo or pull_number is None or not reviewer:
        return None

    dedupe_key = f"github:review:{owner}/{repo}:{pull_number}:{reviewer}:{head_sha or ''}"
    payload_json = json.dumps(
        {
            "owner": owner,
            "repo": repo,
            "pull_number": pull_number,
            "reviewer": reviewer,
            "head_sha": head_sha,
        }
    )
    return ExternalEventIngressRequest(
        source_type="github",
        event_type="pull_request_review_requested",
        external_account_id=reviewer,
        target_ref=f"{owner}/{repo}",
        dedupe_key=dedupe_key,
        payload_json=payload_json,
        metadata_json=json.dumps(
            {
                "trigger_mode": "push",
                "source_kind": "github.pull_request_review_requested",
                "binding_lookup_username": reviewer,
                "provider_reviewer_id": requested_reviewer.get("id"),
            }
        ),
    )


def _normalize_jira_workflow_requested(payload: dict) -> ExternalEventIngressRequest | None:
    issue = payload.get("issue") if isinstance(payload.get("issue"), dict) else {}
    fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
    issue_key = issue.get("key")
    project = fields.get("project") if isinstance(fields.get("project"), dict) else {}
    issue_type_obj = fields.get("issuetype") if isinstance(fields.get("issuetype"), dict) else {}
    status_obj = fields.get("status") if isinstance(fields.get("status"), dict) else {}
    assignee = fields.get("assignee") if isinstance(fields.get("assignee"), dict) else {}

    project_key = project.get("key")
    issue_type = issue_type_obj.get("name")
    trigger_status = status_obj.get("name")
    issue_assignee = (
        assignee.get("accountId")
        or assignee.get("name")
        or assignee.get("emailAddress")
        or assignee.get("displayName")
    )
    if not issue_key or not project_key or not issue_type or not trigger_status:
        return None

    payload_json = json.dumps(
        {
            "issue_key": issue_key,
            "project_key": project_key,
            "issue_type": issue_type,
            "trigger_status": trigger_status,
            "issue_assignee": issue_assignee,
        }
    )
    return ExternalEventIngressRequest(
        source_type="jira",
        event_type="workflow_review_requested",
        external_account_id=issue_assignee,
        target_ref=project_key,
        payload_json=payload_json,
        metadata_json=json.dumps({"trigger_mode": "push", "source_kind": "jira.workflow_review_requested"}),
        project_key=project_key,
        issue_type=issue_type,
        trigger_status=trigger_status,
        issue_key=issue_key,
        issue_assignee=issue_assignee,
    )


def _verify_github_signature(raw_body: bytes, signature_header: str | None) -> None:
    secret = (settings.github_webhook_secret or "").strip()
    if not secret:
        if settings.allow_insecure_provider_webhooks:
            return
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="GitHub webhook secret is not configured")
    if not signature_header:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid GitHub webhook signature")
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    expected = f"sha256={digest}"
    provided = str(signature_header).strip()
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid GitHub webhook signature")


def _verify_jira_shared_secret(shared_secret_header: str | None) -> None:
    expected = (settings.jira_webhook_shared_secret or "").strip()
    if not expected:
        if settings.allow_insecure_provider_webhooks:
            return
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Jira webhook secret is not configured")
    provided = str(shared_secret_header or "").strip()
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Jira webhook secret")


@router.post("/api/webhooks/github", response_model=ExternalEventIngressResponse)
async def ingest_github_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_hub_signature_256: str | None = Header(default=None),
):
    raw_body = await request.body()
    _verify_github_signature(raw_body, x_hub_signature_256)
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid GitHub payload") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid GitHub payload")

    ingress_payload = _normalize_github_review_requested(payload)
    if ingress_payload is None:
        return ExternalEventIngressResponse(
            accepted=False,
            matched_subscription_ids=[],
            routing_reason="unsupported_github_event",
            resolved_task_type=None,
            message="Only pull_request review_requested is handled",
        )
    return service.route_external_event(ingress_payload, db)


@router.post("/api/webhooks/jira", response_model=ExternalEventIngressResponse)
async def ingest_jira_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_efp_webhook_secret: str | None = Header(default=None),
):
    _verify_jira_shared_secret(x_efp_webhook_secret)
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Jira payload") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Jira payload")

    ingress_payload = _normalize_jira_workflow_requested(payload)
    if ingress_payload is None:
        return ExternalEventIngressResponse(
            accepted=False,
            matched_subscription_ids=[],
            routing_reason="unsupported_jira_event",
            resolved_task_type=None,
            message="Jira payload missing workflow review fields",
        )
    return service.route_external_event(ingress_payload, db)
