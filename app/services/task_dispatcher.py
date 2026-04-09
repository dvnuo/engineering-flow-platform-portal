import json
from datetime import datetime
from dataclasses import asdict, dataclass
import logging
import time

import httpx
from sqlalchemy.orm import Session

from app.log_context import (
    bind_log_context,
    generate_span_id,
    generate_trace_id,
    get_log_context,
    reset_log_context,
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
    terminal_status: str  # done | failed | stale
    result_payload_json: str
    message: str
    runtime_status_code: int | None


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
        headers = self.proxy_service.build_runtime_internal_headers()
        metadata = body.get("metadata") if isinstance(body, dict) else None
        if isinstance(metadata, dict):
            headers.update(build_runtime_trace_headers(metadata))
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await client.post(url, json=body, headers=headers)

    def _build_failure_payload(self, error_code: str, message: str, status_code: int | None = None) -> str:
        return json.dumps({"ok": False, "error_code": error_code, "message": message, "runtime_status_code": status_code})

    def _build_shared_context_not_found_payload(self, group_id: str | None, context_ref: str) -> str:
        return json.dumps({
            "ok": False,
            "error_code": "shared_context_not_found",
            "message": "Shared context snapshot not found for delegation task",
            "group_id": group_id,
            "shared_context_ref": context_ref,
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

    def _sync_delegation_from_task_result(
        self,
        db: Session,
        task,
        normalized_result_payload_json: str | None,
        task_execution_succeeded: bool,
    ) -> None:
        if task.task_type != "delegation_task":
            return

        delegation_repo = AgentDelegationRepository(db)
        delegation = delegation_repo.find_by_agent_task_id(task.id)
        if not delegation:
            return

        delegation_result, error = self._extract_delegation_result(normalized_result_payload_json)
        if error:
            delegation.status = "failed"
            delegation.result_summary = error
            delegation_repo.save(delegation)
            self._sync_coordination_run_from_delegation(db, delegation)
            self._maybe_cleanup_task_agent_after_delegation(db, task, delegation, "failed")
            return

        runtime_status = str(delegation_result.get("status") or "done").lower()
        computed_status = "done" if runtime_status in {"done", "success", "completed"} else "failed"
        if not task_execution_succeeded:
            computed_status = "failed"
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
        counts = {"queued": 0, "running": 0, "blocked": 0, "done": 0, "failed": 0}
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
        elif counts["failed"] > 0:
            run_status = "failed"
        elif has_blockers:
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
                "deleted_task_agent_ids": deleted_task_agent_ids,
            }
        )
        if run_status in {"done", "failed"}:
            run.completed_at = datetime.utcnow()
        else:
            run.completed_at = None
        run_repo.save(run)

    @staticmethod
    def _should_cleanup_task_agent(cleanup_policy: str | None, delegation_status: str) -> bool:
        if cleanup_policy == "delete_on_done":
            return delegation_status == "done"
        if cleanup_policy == "delete_on_terminal":
            return delegation_status in {"done", "failed"}
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
            )

        normalized_payload_json = json.dumps(response_json)
        status_value = response_json.get("status")
        if status_value is None:
            return NormalizedRuntimeOutcome(
                terminal_status="failed",
                result_payload_json=normalized_payload_json,
                message="Runtime returned malformed response",
                runtime_status_code=runtime_status_code,
            )

        normalized_status = str(status_value).lower()
        ok_value = response_json.get("ok")
        output_payload = response_json.get("output_payload")
        if isinstance(output_payload, dict) and output_payload.get("error_code") == "superseded_by_new_head_sha":
            return NormalizedRuntimeOutcome(
                terminal_status="stale",
                result_payload_json=normalized_payload_json,
                message="Runtime reported task superseded by newer head_sha",
                runtime_status_code=runtime_status_code,
            )

        if normalized_status == "success" and ok_value is not False:
            return NormalizedRuntimeOutcome(
                terminal_status="done",
                result_payload_json=normalized_payload_json,
                message="Task dispatched successfully",
                runtime_status_code=runtime_status_code,
            )

        if normalized_status in {"error", "blocked"} or ok_value is False:
            return NormalizedRuntimeOutcome(
                terminal_status="failed",
                result_payload_json=normalized_payload_json,
                message="Runtime execution reported failure",
                runtime_status_code=runtime_status_code,
            )

        return NormalizedRuntimeOutcome(
            terminal_status="failed",
            result_payload_json=normalized_payload_json,
            message="Runtime returned malformed response",
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

            bind_task_token = bind_log_context(portal_task_id=task.id, agent_id=task.assignee_agent_id or "-")
            try:
                if task.status != "queued":
                    return AgentTaskDispatchResult(False, task.id, None, task.status, "Task is not dispatchable", task.result_payload_json)

                if not task.assignee_agent_id:
                    task.status = "failed"
                    task.result_payload_json = self._build_failure_payload("missing_assignee", "Task has no assignee_agent_id")
                    task_repo.save(task)
                    return AgentTaskDispatchResult(False, task.id, None, task.status, "Task has no assignee_agent_id", task.result_payload_json)

                agent = agent_repo.get_by_id(task.assignee_agent_id)
                if not agent:
                    task.status = "failed"
                    task.result_payload_json = self._build_failure_payload("assignee_not_found", "Assignee agent not found")
                    task_repo.save(task)
                    return AgentTaskDispatchResult(False, task.id, None, task.status, "Assignee agent not found", task.result_payload_json)

                input_payload, payload_error = self._parse_input_payload(task.input_payload_json)
                if payload_error:
                    task.status = "failed"
                    task.result_payload_json = self._build_failure_payload("invalid_input_payload", payload_error)
                    task_repo.save(task)
                    return AgentTaskDispatchResult(False, task.id, None, task.status, payload_error, task.result_payload_json)

                delegation = None
                metadata = {
                    "portal_task_id": task.id,
                    "portal_task_source": task.source,
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
                subscription_id = input_payload.get("subscription_id")
                if subscription_id:
                    metadata["portal_subscription_id"] = subscription_id
                head_sha = input_payload.get("head_sha")
                if head_sha:
                    metadata["portal_head_sha"] = head_sha
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
                            task.status = "failed"
                            task.result_payload_json = self._build_shared_context_not_found_payload(task.group_id, task.shared_context_ref)
                            task_repo.save(task)
                            if delegation:
                                delegation.status = "failed"
                                delegation_repo.save(delegation)
                            return AgentTaskDispatchResult(False, task.id, None, task.status, "Shared context snapshot not found", task.result_payload_json)
                        try:
                            parsed_payload = json.loads(snapshot.payload_json)
                        except json.JSONDecodeError:
                            parsed_payload = None
                        if not isinstance(parsed_payload, dict):
                            task.status = "failed"
                            task.result_payload_json = self._build_failure_payload("invalid_shared_context_payload", "Persisted shared context payload must be a JSON object")
                            task_repo.save(task)
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
                    task.status = "failed"
                    task.result_payload_json = self._build_failure_payload("runtime_url_error", sanitize_exception_message(exc))
                    task_repo.save(task)
                    if delegation:
                        delegation.status = "failed"
                        delegation_repo.save(delegation)
                    return AgentTaskDispatchResult(False, task.id, None, task.status, f"Runtime URL resolution failed: {sanitize_exception_message(exc)}", task.result_payload_json)

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
                task.status = "running"
                task_repo.save(task)

                if delegation:
                    delegation.status = "running"
                    delegation_repo.save(delegation)

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
                    outcome = self._normalize_runtime_response(
                        response,
                        trace_context={
                            "trace_id": trace_id,
                            "portal_dispatch_id": portal_dispatch_id,
                        },
                        raw_response_preview=response_preview,
                    )
                    if outcome.message == "Runtime returned malformed response":
                        logger.warning(
                            "Runtime returned malformed response task_id=%s runtime_status_code=%s runtime_url=%s raw_response_preview=%s",
                            task.id,
                            response.status_code,
                            runtime_url,
                            response_preview,
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

                    fresh_task.status = outcome.terminal_status
                    fresh_task.result_payload_json = outcome.result_payload_json
                    task_repo.save(fresh_task)
                    if outcome.terminal_status in {"done", "failed"}:
                        self._sync_delegation_from_task_result(
                            db,
                            fresh_task,
                            outcome.result_payload_json,
                            outcome.terminal_status == "done",
                        )
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
                    fresh_task.status = "failed"
                    fresh_task.result_payload_json = self._build_failure_payload(
                        "runtime_request_error",
                        sanitize_exception_message(exc),
                    )
                    task_repo.save(fresh_task)
                    self._sync_delegation_from_task_result(db, fresh_task, fresh_task.result_payload_json, False)
                    return AgentTaskDispatchResult(
                        True,
                        fresh_task.id,
                        None,
                        fresh_task.status,
                        f"Runtime dispatch request failed: {sanitize_exception_message(exc)}",
                        fresh_task.result_payload_json,
                    )
            finally:
                reset_log_context(bind_task_token)
        finally:
            reset_log_context(dispatch_context_token)
