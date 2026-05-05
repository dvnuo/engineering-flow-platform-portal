import json
from datetime import datetime
from dataclasses import asdict, dataclass
import logging
import time
import asyncio
import threading

import httpx
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.log_context import (
    bind_log_context,
    generate_span_id,
    generate_trace_id,
    get_log_context,
    reset_log_context,
    snapshot_log_context,
)
from app.redaction import safe_preview, sanitize_exception_message
from app.repositories.agent_coordination_run_repo import AgentCoordinationRunRepository
from app.repositories.agent_delegation_repo import AgentDelegationRepository
from app.repositories.agent_repo import AgentRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.repositories.group_shared_context_snapshot_repo import GroupSharedContextSnapshotRepository
from app.services.runtime_execution_context_service import RuntimeExecutionContextService
from app.services.proxy_service import ProxyService, build_runtime_trace_headers

logger = logging.getLogger(__name__)


@dataclass
class AgentTaskDispatchResult:
    dispatched: bool
    task_id: str
    runtime_status_code: int | None
    task_status: str
    message: str
    result_payload_json: str | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class NormalizedRuntimeOutcome:
    terminal_status: str  # done | failed | stale | cancelled | pending_restart | cancel_failed | running
    result_payload_json: str
    message: str
    runtime_status_code: int | None
    is_malformed: bool = False


class TaskDispatcherService:
    def __init__(self) -> None:
        self.proxy_service = ProxyService()
        self.runtime_execution_context_service = RuntimeExecutionContextService()

    @staticmethod
    def _parse_input_payload(input_payload_json: str | None) -> tuple[dict | None, str | None]:
        if input_payload_json is None or not input_payload_json.strip():
            return None, "input_payload_json must be a valid JSON object"
        try:
            payload = json.loads(input_payload_json)
        except json.JSONDecodeError:
            return None, "input_payload_json must be a valid JSON object"
        if not isinstance(payload, dict):
            return None, "input_payload_json must decode to a JSON object"
        return payload, None

    async def _post_to_runtime(self, url: str, body: dict) -> httpx.Response:
        headers = {}
        metadata = body.get("metadata") if isinstance(body, dict) else None
        if isinstance(metadata, dict):
            headers.update(build_runtime_trace_headers(metadata))
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await client.post(url, json=body, headers=headers)

    async def _get_runtime_task_status(self, url: str, metadata: dict | None = None) -> httpx.Response:
        headers = {}
        if isinstance(metadata, dict):
            headers.update(build_runtime_trace_headers(metadata))
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await client.get(url, headers=headers)

    def _build_failure_payload(
        self,
        error_code: str,
        message: str,
        status_code: int | None = None,
        trace_context: dict[str, str] | None = None,
        raw_response_preview: str | None = None,
    ) -> str:
        trace_context = trace_context or {}
        payload = {
            "ok": False,
            "error_code": error_code,
            "message": message,
            "runtime_status_code": status_code,
            "trace_id": trace_context.get("trace_id"),
            "portal_dispatch_id": trace_context.get("portal_dispatch_id"),
        }
        if raw_response_preview:
            payload["raw_response_preview"] = raw_response_preview
        return json.dumps(payload)

    def _build_shared_context_not_found_payload(
        self,
        group_id: str | None,
        context_ref: str,
        trace_context: dict[str, str] | None = None,
    ) -> str:
        trace_context = trace_context or {}
        return json.dumps({
            "ok": False,
            "error_code": "shared_context_not_found",
            "message": "Shared context snapshot not found for delegation task",
            "group_id": group_id,
            "shared_context_ref": context_ref,
            "trace_id": trace_context.get("trace_id"),
            "portal_dispatch_id": trace_context.get("portal_dispatch_id"),
        })

    @staticmethod
    def _extract_delegation_result(normalized_result_payload_json: str | None) -> tuple[dict | None, str | None]:
        if not normalized_result_payload_json:
            return None, "Missing runtime result payload"
        try:
            payload = json.loads(normalized_result_payload_json)
        except json.JSONDecodeError:
            return None, "Runtime result payload is not valid JSON"
        if not isinstance(payload, dict):
            return None, "Runtime result payload must be a JSON object"

        output_payload = payload.get("output_payload")
        if not isinstance(output_payload, dict):
            return None, "Runtime response missing output_payload object"

        delegation_result = output_payload.get("delegation_result")
        if not isinstance(delegation_result, dict):
            return None, "Runtime response missing delegation_result object"
        return delegation_result, None

    @staticmethod
    def _extract_deleted_task_agent_ids_from_delegation(delegation) -> list[str]:
        audit_raw = getattr(delegation, "audit_trace_json", None)
        if not audit_raw:
            return []
        try:
            parsed = json.loads(audit_raw)
        except Exception:
            return []
        if not isinstance(parsed, dict):
            return []
        cleanup = parsed.get("cleanup")
        if not isinstance(cleanup, dict):
            return []
        ids = cleanup.get("deleted_task_agent_ids")
        if not isinstance(ids, list):
            return []
        return [item for item in ids if isinstance(item, str) and item]

    @staticmethod
    def _append_deleted_task_agent_id_to_delegation(delegation, agent_id: str) -> None:
        if not agent_id:
            return
        try:
            parsed = json.loads(delegation.audit_trace_json) if delegation.audit_trace_json else {}
        except Exception:
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        cleanup = parsed.get("cleanup")
        if not isinstance(cleanup, dict):
            cleanup = {}
        existing = cleanup.get("deleted_task_agent_ids")
        if not isinstance(existing, list):
            existing = []
        merged = list(dict.fromkeys([item for item in existing if isinstance(item, str) and item] + [agent_id]))
        cleanup["deleted_task_agent_ids"] = merged
        parsed["cleanup"] = cleanup
        delegation.audit_trace_json = json.dumps(parsed)

    def _delegation_status_from_task_status(self, terminal_status: str, runtime_status: str | None = None) -> str:
        normalized_runtime = str(runtime_status or "").lower()
        if terminal_status == "done":
            if normalized_runtime in {"", "done", "success", "completed"}:
                return "done"
            if normalized_runtime in {"failed", "error", "blocked", "stale", "cancelled", "pending_restart", "cancel_failed"}:
                return normalized_runtime
            return "failed"
        mapping = {
            "failed": "failed",
            "stale": "stale",
            "cancelled": "cancelled",
            "pending_restart": "pending_restart",
            "cancel_failed": "failed",
        }
        return mapping.get(terminal_status, "failed")

    def _sync_delegation_from_task_result(
        self,
        db: Session,
        task,
        normalized_result_payload_json: str | None,
        terminal_status: str,
    ) -> None:
        if task.task_type != "delegation_task":
            return

        delegation_repo = AgentDelegationRepository(db)
        delegation = delegation_repo.find_by_agent_task_id(task.id)
        if not delegation:
            return

        delegation_result, error = self._extract_delegation_result(normalized_result_payload_json)
        if error:
            mapped_status = self._delegation_status_from_task_status(terminal_status)
            delegation.status = mapped_status
            if terminal_status == "pending_restart":
                delegation.result_summary = "Runtime reported pending_restart; restart is required before this delegation can complete."
            elif terminal_status == "cancelled":
                delegation.result_summary = "Task was cancelled."
            elif terminal_status == "stale":
                delegation.result_summary = "Runtime reported task is stale."
            elif terminal_status == "cancel_failed":
                delegation.result_summary = f"Runtime failed to cancel task. {error}".strip()
            else:
                delegation.result_summary = error
            delegation_repo.save(delegation)
            self._sync_coordination_run_from_delegation(db, delegation)
            self._maybe_cleanup_task_agent_after_delegation(db, task, delegation, mapped_status)
            return

        runtime_status = str(delegation_result.get("status") or "done").lower()
        computed_status = self._delegation_status_from_task_status(terminal_status, runtime_status if terminal_status == "done" else None)
        delegation.status = computed_status

        summary = delegation_result.get("summary")
        if summary is None:
            summary = delegation_result.get("result_summary")
        artifacts = delegation_result.get("artifacts")
        if artifacts is None:
            artifacts = delegation_result.get("result_artifacts")
        blockers = delegation_result.get("blockers")
        audit_trace = delegation_result.get("audit_trace")

        delegation.result_summary = summary
        delegation.result_artifacts_json = json.dumps(artifacts) if artifacts is not None else None
        delegation.blockers_json = json.dumps(blockers) if blockers is not None else None
        delegation.next_recommendation = delegation_result.get("next_recommendation")
        delegation.audit_trace_json = json.dumps(audit_trace) if audit_trace is not None else None
        delegation_repo.save(delegation)
        self._sync_coordination_run_from_delegation(db, delegation)
        self._maybe_cleanup_task_agent_after_delegation(db, task, delegation, computed_status)

    def _sync_coordination_run_from_delegation(self, db: Session, delegation) -> None:
        run_id = (getattr(delegation, "coordination_run_id", None) or "").strip()
        if not run_id:
            return
        run_repo = AgentCoordinationRunRepository(db)
        run = run_repo.get_by_coordination_run_id(run_id)
        if not run:
            return

        delegations = AgentDelegationRepository(db).list_by_coordination_run_id(run_id)
        counts = {"queued": 0, "running": 0, "blocked": 0, "done": 0, "failed": 0, "stale": 0, "cancelled": 0, "pending_restart": 0, "cancel_failed": 0}
        latest_round_index = 1
        has_blockers = False
        deleted_task_agent_ids: list[str] = []
        for item in delegations:
            status = getattr(item, "status", "")
            if status in counts:
                counts[status] += 1
            latest_round_index = max(latest_round_index, getattr(item, "round_index", 1) or 1)
            blockers_raw = getattr(item, "blockers_json", None)
            if blockers_raw:
                try:
                    parsed_blockers = json.loads(blockers_raw)
                except Exception:
                    parsed_blockers = blockers_raw
                if parsed_blockers:
                    has_blockers = True
            deleted_task_agent_ids.extend(self._extract_deleted_task_agent_ids_from_delegation(item))

        deleted_task_agent_ids = list(dict.fromkeys(deleted_task_agent_ids))

        if counts["running"] > 0 or counts["queued"] > 0:
            run_status = "running"
        elif counts["pending_restart"] > 0:
            run_status = "pending_restart"
        elif counts["cancel_failed"] > 0 or counts["failed"] > 0:
            run_status = "failed"
        elif counts["stale"] > 0:
            run_status = "stale"
        elif delegations and counts["cancelled"] == len(delegations):
            run_status = "cancelled"
        elif has_blockers or counts["blocked"] > 0:
            run_status = "blocked"
        elif counts["done"] > 0 and counts["done"] == len(delegations):
            run_status = "done"
        else:
            run_status = "running"

        run.status = run_status
        run.latest_round_index = latest_round_index
        run.summary_json = json.dumps(
            {
                "total": len(delegations),
                "queued": counts["queued"],
                "running": counts["running"],
                "blocked": counts["blocked"],
                "done": counts["done"],
                "failed": counts["failed"],
                "stale": counts["stale"],
                "cancelled": counts["cancelled"],
                "pending_restart": counts["pending_restart"],
                "cancel_failed": counts["cancel_failed"],
                "deleted_task_agent_ids": deleted_task_agent_ids,
            }
        )
        if run_status in {"done", "failed", "stale", "cancelled"}:
            run.completed_at = datetime.utcnow()
        else:
            run.completed_at = None
        run_repo.save(run)

    @staticmethod
    def _should_cleanup_task_agent(cleanup_policy: str | None, delegation_status: str) -> bool:
        if cleanup_policy == "delete_on_done":
            return delegation_status == "done"
        if cleanup_policy == "delete_on_terminal":
            return delegation_status in {"done", "failed", "stale", "cancelled", "cancel_failed"}
        return False

    def _maybe_cleanup_task_agent_after_delegation(self, db: Session, task, delegation, delegation_status: str) -> None:
        input_payload, payload_error = self._parse_input_payload(task.input_payload_json)
        if payload_error or not input_payload:
            return

        cleanup_policy = input_payload.get("task_agent_cleanup_policy")
        if not isinstance(cleanup_policy, str) or not cleanup_policy.strip():
            return
        cleanup_policy = cleanup_policy.strip()
        if cleanup_policy == "retain":
            return
        if not self._should_cleanup_task_agent(cleanup_policy, delegation_status):
            return

        assignee_agent_id = input_payload.get("ephemeral_task_agent_id") or task.assignee_agent_id
        if not assignee_agent_id:
            return

        agent = AgentRepository(db).get_by_id(assignee_agent_id)
        if not agent or agent.agent_type != "task":
            return
        group_id = task.group_id or delegation.group_id
        if not group_id:
            return

        from app.services.agent_group_service import AgentGroupService

        group_service = AgentGroupService(db)
        cleaned = group_service.auto_cleanup_task_agent(
            group_id=group_id,
            agent_id=assignee_agent_id,
            delegation_id=delegation.id,
            task_id=task.id,
            cleanup_policy=cleanup_policy,
            coordination_run_id=getattr(delegation, "coordination_run_id", None),
        )
        if cleaned:
            self._append_deleted_task_agent_id_to_delegation(delegation, assignee_agent_id)
            AgentDelegationRepository(db).save(delegation)
            self._sync_coordination_run_from_delegation(db, delegation)

    @staticmethod
    def _derive_summary_from_runtime_payload(payload: dict | None) -> str | None:
        if not isinstance(payload, dict):
            return None
        output_payload = payload.get("output_payload")
        if isinstance(output_payload, dict):
            for key in ("summary", "review_summary", "message", "result_summary"):
                value = output_payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        for key in ("message", "summary"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _derive_error_message_from_runtime_payload(payload: dict | None) -> str | None:
        if not isinstance(payload, dict):
            return None
        error = payload.get("error")
        if isinstance(error, dict):
            for key in ("message", "detail", "error"):
                value = error.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        for key in ("message", "error_message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _mark_task_failed(
        self,
        *,
        task,
        task_repo: AgentTaskRepository,
        result_payload_json: str,
        error_message: str,
    ):
        task.status = "failed"
        task.result_payload_json = result_payload_json
        task.error_message = error_message
        task.summary = None
        task.finished_at = datetime.utcnow()
        return task_repo.save(task)

    def _normalize_runtime_submit_response(
        self,
        response: httpx.Response,
        trace_context: dict[str, str] | None = None,
        raw_response_preview: str | None = None,
    ) -> tuple[str, dict | None, NormalizedRuntimeOutcome | None]:
        normalized = self._normalize_runtime_status_response(
            response,
            trace_context=trace_context,
            raw_response_preview=raw_response_preview,
            allow_pending=True,
        )
        if normalized[0] == "pending":
            return normalized
        return normalized[0], normalized[1], normalized[2]

    def _normalize_runtime_status_response(
        self,
        response: httpx.Response,
        trace_context: dict[str, str] | None = None,
        raw_response_preview: str | None = None,
        allow_pending: bool = True,
    ) -> tuple[str, dict | None, NormalizedRuntimeOutcome | None]:
        outcome = self._normalize_runtime_response(
            response,
            trace_context=trace_context,
            raw_response_preview=raw_response_preview,
        )
        if outcome.terminal_status in {"done", "failed", "stale", "cancelled", "pending_restart", "cancel_failed"}:
            try:
                return "terminal", json.loads(outcome.result_payload_json), outcome
            except Exception:
                return "terminal", None, outcome

        try:
            response_json = response.json()
        except Exception:
            response_json = None
        if not isinstance(response_json, dict):
            return "terminal", None, outcome
        status_value = str(response_json.get("status") or "").lower()
        if allow_pending and 200 <= response.status_code < 300 and status_value in {"accepted", "running"}:
            return "pending", response_json, None
        return "terminal", response_json, outcome

    async def _poll_runtime_task_until_terminal(
        self,
        *,
        runtime_status_url: str,
        metadata: dict,
        trace_context: dict[str, str],
        timeout_seconds: int = 900,
        interval_seconds: int = 1,
    ) -> NormalizedRuntimeOutcome:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            response = await self._get_runtime_task_status(runtime_status_url, metadata)
            preview = safe_preview(response.text or "", limit=800)
            phase, _payload, outcome = self._normalize_runtime_status_response(
                response,
                trace_context=trace_context,
                raw_response_preview=preview,
                allow_pending=True,
            )
            if phase == "terminal" and outcome is not None:
                return outcome
            await asyncio.sleep(interval_seconds)

        return NormalizedRuntimeOutcome(
            terminal_status="failed",
            result_payload_json=self._build_failure_payload(
                "runtime_poll_timeout",
                "Runtime status polling timed out",
                trace_context=trace_context,
            ),
            message="Runtime polling timed out",
            runtime_status_code=None,
        )

    @staticmethod
    def _normalize_runtime_response(
        response: httpx.Response,
        trace_context: dict[str, str] | None = None,
        raw_response_preview: str | None = None,
    ) -> NormalizedRuntimeOutcome:
        trace_context = trace_context or {}
        runtime_status_code = response.status_code
        response_text = response.text or ""

        if not (200 <= runtime_status_code < 300):
            payload = {
                "ok": False,
                "error_code": "runtime_http_error",
                "message": f"Runtime returned non-2xx status: {runtime_status_code}",
                "runtime_status_code": runtime_status_code,
                "trace_id": trace_context.get("trace_id"),
                "portal_dispatch_id": trace_context.get("portal_dispatch_id"),
            }
            if raw_response_preview:
                payload["raw_response_preview"] = raw_response_preview
            return NormalizedRuntimeOutcome(
                terminal_status="failed",
                result_payload_json=json.dumps(payload),
                message="Runtime returned non-2xx status",
                runtime_status_code=runtime_status_code,
            )

        try:
            response_json = response.json()
        except Exception:
            response_json = None

        if not isinstance(response_json, dict):
            payload = {
                "ok": False,
                "error_code": "malformed_runtime_response",
                "message": "Runtime returned malformed 2xx response: expected JSON object",
                "runtime_status_code": runtime_status_code,
                "trace_id": trace_context.get("trace_id"),
                "portal_dispatch_id": trace_context.get("portal_dispatch_id"),
                "raw_response_preview": raw_response_preview or safe_preview(response_text, limit=800),
            }
            return NormalizedRuntimeOutcome(
                terminal_status="failed",
                result_payload_json=json.dumps(payload),
                message="Runtime returned malformed response",
                runtime_status_code=runtime_status_code,
                is_malformed=True,
            )

        status_value = response_json.get("status")
        if not isinstance(status_value, str) or not status_value.strip():
            payload = {
                "ok": False,
                "error_code": "malformed_runtime_response",
                "message": "Runtime returned malformed 2xx response: missing status",
                "runtime_status_code": runtime_status_code,
                "trace_id": trace_context.get("trace_id"),
                "portal_dispatch_id": trace_context.get("portal_dispatch_id"),
                "raw_response_preview": raw_response_preview or safe_preview(response_text, limit=800),
            }
            return NormalizedRuntimeOutcome(
                terminal_status="failed",
                result_payload_json=json.dumps(payload),
                message="Runtime returned malformed response",
                runtime_status_code=runtime_status_code,
                is_malformed=True,
            )

        normalized_payload_json = json.dumps(response_json)
        normalized_status = str(status_value).lower()
        supported_statuses = {
            "success", "done", "completed",
            "error", "failed", "blocked",
            "accepted", "running",
            "stale",
            "cancelled", "canceled",
            "pending_restart",
            "cancel_failed",
        }
        if normalized_status not in supported_statuses:
            payload = {
                "ok": False,
                "error_code": "malformed_runtime_response",
                "message": f"Runtime returned malformed 2xx response: unsupported status '{status_value}'",
                "runtime_status_code": runtime_status_code,
                "trace_id": trace_context.get("trace_id"),
                "portal_dispatch_id": trace_context.get("portal_dispatch_id"),
                "raw_response_preview": raw_response_preview or safe_preview(response_text, limit=800),
            }
            return NormalizedRuntimeOutcome(
                terminal_status="failed",
                result_payload_json=json.dumps(payload),
                message="Runtime returned malformed response",
                runtime_status_code=runtime_status_code,
                is_malformed=True,
            )

        ok_value = response_json.get("ok")
        output_payload = response_json.get("output_payload")
        if isinstance(output_payload, dict) and output_payload.get("error_code") == "superseded_by_new_head_sha":
            return NormalizedRuntimeOutcome(
                terminal_status="stale",
                result_payload_json=normalized_payload_json,
                message="Runtime reported task superseded by newer head_sha",
                runtime_status_code=runtime_status_code,
            )

        if normalized_status in {"accepted", "running"}:
            return NormalizedRuntimeOutcome(
                terminal_status="running",
                result_payload_json=normalized_payload_json,
                message="Runtime execution accepted",
                runtime_status_code=runtime_status_code,
            )

        if normalized_status in {"success", "done", "completed"} and ok_value is not False:
            return NormalizedRuntimeOutcome(
                terminal_status="done",
                result_payload_json=normalized_payload_json,
                message="Task dispatched successfully",
                runtime_status_code=runtime_status_code,
            )

        if normalized_status == "stale":
            return NormalizedRuntimeOutcome(
                terminal_status="stale",
                result_payload_json=normalized_payload_json,
                message="Runtime reported task is stale",
                runtime_status_code=runtime_status_code,
            )

        if normalized_status in {"cancelled", "canceled"}:
            return NormalizedRuntimeOutcome(
                terminal_status="cancelled",
                result_payload_json=normalized_payload_json,
                message="Task was cancelled",
                runtime_status_code=runtime_status_code,
            )

        if normalized_status == "pending_restart":
            return NormalizedRuntimeOutcome(
                terminal_status="pending_restart",
                result_payload_json=normalized_payload_json,
                message="Runtime reported pending_restart",
                runtime_status_code=runtime_status_code,
            )

        if normalized_status == "cancel_failed":
            return NormalizedRuntimeOutcome(
                terminal_status="cancel_failed",
                result_payload_json=normalized_payload_json,
                message="Runtime failed to cancel task",
                runtime_status_code=runtime_status_code,
            )

        if normalized_status in {"error", "blocked", "failed"} or ok_value is False:
            return NormalizedRuntimeOutcome(
                terminal_status="failed",
                result_payload_json=normalized_payload_json,
                message="Runtime execution reported failure",
                runtime_status_code=runtime_status_code,
            )

        return NormalizedRuntimeOutcome(
            terminal_status="failed",
            result_payload_json=normalized_payload_json,
            message="Runtime execution reported failure",
            runtime_status_code=runtime_status_code,
        )

    async def dispatch_task(self, task_id: str, db: Session, user=None) -> AgentTaskDispatchResult:
        _ = user
        incoming_context = get_log_context()
        trace_id = incoming_context.get("trace_id")
        if not trace_id or trace_id == "-":
            trace_id = generate_trace_id()
        parent_span_id = incoming_context.get("span_id", "-") or "-"
        portal_dispatch_id = generate_span_id()
        dispatch_span_id = generate_span_id()
        runtime_url = None
        dispatch_context_token = bind_log_context(
            trace_id=trace_id,
            span_id=dispatch_span_id,
            parent_span_id=parent_span_id,
            portal_dispatch_id=portal_dispatch_id,
        )
        task_repo = AgentTaskRepository(db)
        agent_repo = AgentRepository(db)
        delegation_repo = AgentDelegationRepository(db)
        context_repo = GroupSharedContextSnapshotRepository(db)

        try:
            task = task_repo.get_by_id(task_id)
            if not task:
                return AgentTaskDispatchResult(False, task_id, None, "not_found", "Task not found", None)
            trace_context = {
                "trace_id": trace_id,
                "portal_dispatch_id": portal_dispatch_id,
                "portal_task_id": task.id,
            }

            bind_task_token = bind_log_context(portal_task_id=task.id, agent_id=task.assignee_agent_id or "-")
            try:
                if task.status != "queued":
                    return AgentTaskDispatchResult(False, task.id, None, task.status, "Task is not dispatchable", task.result_payload_json)

                if not task.assignee_agent_id:
                    failure_payload = self._build_failure_payload(
                        "missing_assignee",
                        "Task has no assignee_agent_id",
                        trace_context=trace_context,
                    )
                    task = self._mark_task_failed(
                        task=task,
                        task_repo=task_repo,
                        result_payload_json=failure_payload,
                        error_message="Task has no assignee_agent_id",
                    )
                    return AgentTaskDispatchResult(False, task.id, None, task.status, "Task has no assignee_agent_id", task.result_payload_json)

                agent = agent_repo.get_by_id(task.assignee_agent_id)
                if not agent:
                    failure_payload = self._build_failure_payload(
                        "assignee_not_found",
                        "Assignee agent not found",
                        trace_context=trace_context,
                    )
                    task = self._mark_task_failed(
                        task=task,
                        task_repo=task_repo,
                        result_payload_json=failure_payload,
                        error_message="Assignee agent not found",
                    )
                    return AgentTaskDispatchResult(False, task.id, None, task.status, "Assignee agent not found", task.result_payload_json)

                input_payload, payload_error = self._parse_input_payload(task.input_payload_json)
                if payload_error:
                    failure_payload = self._build_failure_payload(
                        "invalid_input_payload",
                        payload_error,
                        trace_context=trace_context,
                    )
                    task = self._mark_task_failed(
                        task=task,
                        task_repo=task_repo,
                        result_payload_json=failure_payload,
                        error_message=payload_error,
                    )
                    return AgentTaskDispatchResult(False, task.id, None, task.status, payload_error, task.result_payload_json)

                delegation = None
                metadata = {
                    "portal_task_id": task.id,
                    "portal_task_source": task.source,
                    "portal_task_template_id": task.template_id,
                    "portal_task_type": task.task_type,
                    "portal_task_family": task.task_family,
                    "shared_context_ref": task.shared_context_ref,
                    "current_task_id": task.id,
                    "source_type": task.source or "portal",
                    "source_ref": task.id,
                }
                if task.group_id:
                    metadata["group_id"] = task.group_id
                metadata = self.runtime_execution_context_service.build_runtime_metadata(db, agent, metadata)
                metadata["trace_id"] = trace_id
                metadata["span_id"] = dispatch_span_id
                metadata["parent_span_id"] = parent_span_id
                metadata["portal_dispatch_id"] = portal_dispatch_id
                metadata["portal_task_id"] = task.id
                workflow_rule_id = input_payload.get("workflow_rule_id")
                if workflow_rule_id:
                    metadata["portal_workflow_rule_id"] = workflow_rule_id
                source_kind = input_payload.get("source_kind")
                if source_kind:
                    metadata["source_kind"] = source_kind
                binding_id = input_payload.get("binding_id")
                if binding_id:
                    metadata["portal_binding_id"] = binding_id
                automation_rule = input_payload.get("automation_rule")
                automation_rule_id = input_payload.get("automation_rule_id") or input_payload.get("rule_id")
                if automation_rule:
                    metadata["portal_automation_rule"] = automation_rule
                if automation_rule_id:
                    metadata["portal_automation_rule_id"] = str(automation_rule_id)
                if task.trigger:
                    metadata["portal_task_trigger"] = task.trigger
                head_sha = input_payload.get("head_sha")
                if head_sha:
                    metadata["portal_head_sha"] = head_sha
                execution_mode = input_payload.get("execution_mode")
                if execution_mode:
                    metadata["portal_execution_mode"] = str(execution_mode)
                dedupe_hint = task.shared_context_ref
                if dedupe_hint:
                    metadata["portal_dedupe_hint"] = dedupe_hint

                materialized_context_ref = None
                if task.task_type == "delegation_task":
                    delegation = delegation_repo.find_by_agent_task_id(task.id)
                    if delegation:
                        metadata["portal_delegation_id"] = delegation.id
                        metadata["current_delegation_id"] = delegation.id
                        metadata["portal_group_id"] = delegation.group_id
                        if delegation.group_id:
                            metadata["group_id"] = delegation.group_id
                        metadata["portal_leader_agent_id"] = delegation.leader_agent_id
                        metadata["portal_assignee_agent_id"] = delegation.assignee_agent_id
                        metadata["portal_delegation_reply_target"] = getattr(delegation, "reply_target_type", None) or "leader"
                        if getattr(delegation, "coordination_run_id", None):
                            metadata["portal_coordination_run_id"] = delegation.coordination_run_id
                            metadata["current_coordination_run_id"] = delegation.coordination_run_id
                        metadata["portal_coordination_round_index"] = getattr(delegation, "round_index", 1) or 1
                    else:
                        if input_payload.get("coordination_run_id"):
                            metadata["portal_coordination_run_id"] = input_payload.get("coordination_run_id")
                            metadata["current_coordination_run_id"] = input_payload.get("coordination_run_id")
                        if input_payload.get("round_index") is not None:
                            metadata["portal_coordination_round_index"] = input_payload.get("round_index")
                    if input_payload.get("strict_delegation_result") is True:
                        metadata["strict_delegation_result"] = True
                    if input_payload.get("agent_mode") in {"task", "specialist"}:
                        metadata["agent_mode"] = input_payload.get("agent_mode")
                    if input_payload.get("ephemeral_task_agent_id"):
                        metadata["ephemeral_task_agent_id"] = input_payload.get("ephemeral_task_agent_id")
                    if input_payload.get("task_agent_template_id"):
                        metadata["task_agent_template_id"] = input_payload.get("task_agent_template_id")
                    if input_payload.get("task_agent_scope"):
                        metadata["task_agent_scope"] = input_payload.get("task_agent_scope")
                    if input_payload.get("task_agent_cleanup_policy"):
                        metadata["task_agent_cleanup_policy"] = input_payload.get("task_agent_cleanup_policy")

                    if task.shared_context_ref:
                        snapshot = context_repo.get_by_group_and_ref(task.group_id or "", task.shared_context_ref) if task.group_id else None
                        if not snapshot:
                            failure_payload = self._build_shared_context_not_found_payload(
                                task.group_id,
                                task.shared_context_ref,
                                trace_context=trace_context,
                            )
                            task = self._mark_task_failed(
                                task=task,
                                task_repo=task_repo,
                                result_payload_json=failure_payload,
                                error_message="Shared context snapshot not found",
                            )
                            if delegation:
                                delegation.status = "failed"
                                delegation_repo.save(delegation)
                            return AgentTaskDispatchResult(False, task.id, None, task.status, "Shared context snapshot not found", task.result_payload_json)
                        try:
                            parsed_payload = json.loads(snapshot.payload_json)
                        except json.JSONDecodeError:
                            parsed_payload = None
                        if not isinstance(parsed_payload, dict):
                            failure_payload = self._build_failure_payload(
                                "invalid_shared_context_payload",
                                "Persisted shared context payload must be a JSON object",
                                trace_context=trace_context,
                            )
                            task = self._mark_task_failed(
                                task=task,
                                task_repo=task_repo,
                                result_payload_json=failure_payload,
                                error_message="Persisted shared context payload must be a JSON object",
                            )
                            if delegation:
                                delegation.status = "failed"
                                delegation_repo.save(delegation)
                            return AgentTaskDispatchResult(False, task.id, None, task.status, "Invalid shared context payload", task.result_payload_json)
                        materialized_context_ref = parsed_payload

                runtime_body = {
                    "task_id": task.id,
                    "task_type": task.task_type,
                    "input_payload": input_payload,
                    "source": task.source,
                    "shared_context_ref": task.shared_context_ref,
                    "context_ref": materialized_context_ref,
                    "metadata": metadata,
                }
                leader_session_id = (getattr(delegation, "origin_session_id", None) if delegation else None) or input_payload.get("leader_session_id")
                if isinstance(leader_session_id, str) and leader_session_id.strip():
                    runtime_body["session_id"] = leader_session_id.strip()
                    metadata["portal_leader_session_id"] = leader_session_id.strip()

                try:
                    runtime_url = self.proxy_service.build_agent_base_url(agent).rstrip("/") + "/api/tasks/execute"
                except Exception as exc:
                    error_message = sanitize_exception_message(exc)
                    failure_payload = self._build_failure_payload(
                        "runtime_url_error",
                        error_message,
                        trace_context=trace_context,
                    )
                    task = self._mark_task_failed(
                        task=task,
                        task_repo=task_repo,
                        result_payload_json=failure_payload,
                        error_message=f"Runtime URL resolution failed: {error_message}",
                    )
                    if delegation:
                        delegation.status = "failed"
                        delegation_repo.save(delegation)
                    return AgentTaskDispatchResult(False, task.id, None, task.status, f"Runtime URL resolution failed: {error_message}", task.result_payload_json)

                logger.debug(
                    "Prepared runtime dispatch body runtime_url=%s task_id=%s task_type=%s agent_id=%s service_name=%s namespace=%s source=%s shared_context_ref=%s has_session_id=%s input_payload_keys=%s metadata_keys=%s",
                    runtime_url,
                    task.id,
                    task.task_type,
                    getattr(agent, "id", "-"),
                    getattr(agent, "service_name", "-"),
                    getattr(agent, "namespace", "-"),
                    task.source,
                    task.shared_context_ref,
                    "session_id" in runtime_body,
                    sorted(input_payload.keys()),
                    sorted(metadata.keys()),
                )
                try:
                    start = time.monotonic()
                    logger.debug("Dispatch HTTP call start task_id=%s runtime_url=%s", task.id, runtime_url)
                    response = await self._post_to_runtime(runtime_url, runtime_body)
                    duration_ms = round((time.monotonic() - start) * 1000, 2)
                    response_preview = safe_preview(response.text or "", limit=800)
                    logger.info(
                        "Dispatch HTTP call end task_id=%s runtime_url=%s runtime_status_code=%s duration_ms=%s",
                        task.id,
                        runtime_url,
                        response.status_code,
                        duration_ms,
                    )
                    if not (200 <= response.status_code < 300):
                        logger.warning(
                            "Runtime returned non-2xx task_id=%s runtime_status_code=%s runtime_url=%s raw_response_preview=%s",
                            task.id,
                            response.status_code,
                            runtime_url,
                            response_preview,
                        )
                    phase, submit_payload, submit_outcome = self._normalize_runtime_submit_response(
                        response,
                        trace_context=trace_context,
                        raw_response_preview=response_preview,
                    )
                    if submit_outcome and submit_outcome.is_malformed:
                        logger.warning(
                            "Runtime returned malformed response task_id=%s runtime_status_code=%s runtime_url=%s raw_response_preview=%s",
                            task.id,
                            response.status_code,
                            runtime_url,
                            response_preview,
                        )
                    fresh_task = task_repo.get_by_id(task.id)
                    if not fresh_task:
                        runtime_status_code = submit_outcome.runtime_status_code if submit_outcome else response.status_code
                        return AgentTaskDispatchResult(True, task.id, runtime_status_code, "not_found", "Task disappeared during dispatch", None)

                    if fresh_task.status == "stale":
                        return AgentTaskDispatchResult(
                            True,
                            fresh_task.id,
                            submit_outcome.runtime_status_code if submit_outcome else response.status_code,
                            "stale",
                            "late_runtime_result_ignored_because_task_is_stale",
                            fresh_task.result_payload_json,
                        )
                    if phase == "pending":
                        fresh_task.status = "running"
                        fresh_task.runtime_request_id = submit_payload.get("request_id") if isinstance(submit_payload, dict) else None
                        if fresh_task.started_at is None:
                            fresh_task.started_at = datetime.utcnow()
                        task_repo.save(fresh_task)
                        if delegation:
                            delegation.status = "running"
                            delegation_repo.save(delegation)
                        status_url = self.proxy_service.build_agent_base_url(agent).rstrip("/") + f"/api/tasks/{task.id}"
                        outcome = await self._poll_runtime_task_until_terminal(
                            runtime_status_url=status_url,
                            metadata=metadata,
                            trace_context=trace_context,
                        )
                    else:
                        outcome = submit_outcome
                        fresh_task.status = "running"
                        fresh_task.runtime_request_id = (submit_payload or {}).get("request_id") if isinstance(submit_payload, dict) else None
                        if fresh_task.started_at is None:
                            fresh_task.started_at = datetime.utcnow()
                        task_repo.save(fresh_task)
                        if delegation:
                            delegation.status = "running"
                            delegation_repo.save(delegation)

                    if not outcome:
                        outcome = NormalizedRuntimeOutcome(
                            terminal_status="failed",
                            result_payload_json=self._build_failure_payload(
                                "runtime_response_error",
                                "Runtime response normalization failed",
                                trace_context=trace_context,
                            ),
                            message="Runtime response normalization failed",
                            runtime_status_code=response.status_code,
                        )

                    fresh_task = task_repo.get_by_id(task.id)
                    if not fresh_task:
                        return AgentTaskDispatchResult(True, task.id, outcome.runtime_status_code, "not_found", "Task disappeared during dispatch", None)
                    if fresh_task.status == "stale":
                        return AgentTaskDispatchResult(
                            True,
                            fresh_task.id,
                            outcome.runtime_status_code,
                            "stale",
                            "late_runtime_result_ignored_because_task_is_stale",
                            fresh_task.result_payload_json,
                        )

                    try:
                        parsed_outcome_payload = json.loads(outcome.result_payload_json)
                    except Exception:
                        parsed_outcome_payload = None
                    fresh_task.status = outcome.terminal_status
                    fresh_task.result_payload_json = outcome.result_payload_json
                    fresh_task.summary = self._derive_summary_from_runtime_payload(parsed_outcome_payload)
                    fresh_task.error_message = None
                    if outcome.terminal_status in {"failed", "stale", "cancel_failed"}:
                        fresh_task.error_message = self._derive_error_message_from_runtime_payload(parsed_outcome_payload)
                        if outcome.terminal_status == "cancel_failed" and not fresh_task.error_message:
                            fresh_task.error_message = outcome.message or "Runtime failed to cancel task."
                    if outcome.terminal_status == "pending_restart" and not fresh_task.summary:
                        fresh_task.summary = "Runtime reported pending_restart; restart is required before this task can complete."
                    if outcome.terminal_status == "cancelled" and not fresh_task.summary:
                        fresh_task.summary = "Task was cancelled."
                    fresh_task.finished_at = datetime.utcnow()
                    task_repo.save(fresh_task)
                    if outcome.terminal_status in {"done", "failed", "stale", "cancelled", "pending_restart", "cancel_failed"}:
                        self._sync_delegation_from_task_result(db, fresh_task, outcome.result_payload_json, outcome.terminal_status)
                    logger.info(
                        "Dispatch normalization outcome task_id=%s runtime_status_code=%s task_status=%s message=%s",
                        fresh_task.id,
                        outcome.runtime_status_code,
                        fresh_task.status,
                        outcome.message,
                    )
                    return AgentTaskDispatchResult(
                        True,
                        fresh_task.id,
                        outcome.runtime_status_code,
                        fresh_task.status,
                        outcome.message,
                        fresh_task.result_payload_json,
                    )
                except Exception as exc:
                    logger.exception(
                        "Runtime dispatch exception trace_id=%s portal_dispatch_id=%s task_id=%s runtime_url=%s exception_class=%s message=%s",
                        trace_id,
                        portal_dispatch_id,
                        task.id,
                        runtime_url or "-",
                        exc.__class__.__name__,
                        sanitize_exception_message(exc),
                    )
                    fresh_task = task_repo.get_by_id(task.id)
                    if not fresh_task:
                        return AgentTaskDispatchResult(
                            True,
                            task.id,
                            None,
                            "not_found",
                            f"Runtime dispatch request failed: {sanitize_exception_message(exc)}",
                            None,
                        )
                    if fresh_task.status == "stale":
                        return AgentTaskDispatchResult(
                            True,
                            fresh_task.id,
                            None,
                            "stale",
                            "late_runtime_result_ignored_because_task_is_stale",
                            fresh_task.result_payload_json,
                        )
                    error_message = sanitize_exception_message(exc)
                    failure_payload = self._build_failure_payload(
                        "runtime_request_error",
                        error_message,
                        trace_context=trace_context,
                    )
                    fresh_task = self._mark_task_failed(
                        task=fresh_task,
                        task_repo=task_repo,
                        result_payload_json=failure_payload,
                        error_message=f"Runtime dispatch request failed: {error_message}",
                    )
                    self._sync_delegation_from_task_result(db, fresh_task, fresh_task.result_payload_json, "failed")
                    return AgentTaskDispatchResult(
                        True,
                        fresh_task.id,
                        None,
                        fresh_task.status,
                        f"Runtime dispatch request failed: {error_message}",
                        fresh_task.result_payload_json,
                    )
            finally:
                reset_log_context(bind_task_token)
        finally:
            reset_log_context(dispatch_context_token)

    def dispatch_task_in_background(self, task_id: str) -> None:
        parent_context = snapshot_log_context()

        def _runner() -> None:
            inherited_context_token = None
            db_session = SessionLocal()
            try:
                if parent_context:
                    inherited_context_token = bind_log_context(**parent_context)
                asyncio.run(self.dispatch_task(task_id, db_session))
            finally:
                if inherited_context_token is not None:
                    reset_log_context(inherited_context_token)
                db_session.close()

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()
