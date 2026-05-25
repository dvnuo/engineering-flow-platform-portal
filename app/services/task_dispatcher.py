import json
from datetime import datetime
from dataclasses import asdict, dataclass
import logging
import time
import asyncio
import threading

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
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
from app.repositories.agent_repo import AgentRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.services.runtime_execution_context_service import RuntimeExecutionContextService
from app.services.proxy_service import ProxyService, build_runtime_trace_headers

logger = logging.getLogger(__name__)
AGENT_ASYNC_TASK_AUTONOMOUS_INSTRUCTION = (
    "Run as a background long-running task. Do not ask the user for more information unless truly blocked. "
    "Make reasonable assumptions and complete as much as possible."
)


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
    terminal_status: str  # done | failed | blocked | stale | cancelled | pending_restart | cancel_failed | running
    result_payload_json: str
    message: str
    runtime_status_code: int | None
    is_malformed: bool = False


class TaskDispatcherService:
    def __init__(self) -> None:
        self.proxy_service = ProxyService()
        self.runtime_execution_context_service = RuntimeExecutionContextService()

    @staticmethod
    def _coerce_min_int(value, *, default: int, minimum: int) -> int:
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            normalized = default
        return max(minimum, normalized)

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

    async def _post_cancel_to_runtime(self, url: str, metadata: dict | None = None) -> httpx.Response:
        headers = {}
        if isinstance(metadata, dict):
            headers.update(build_runtime_trace_headers(metadata))
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await client.post(url, headers=headers)

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

    @staticmethod
    def _derive_summary_from_runtime_payload(payload: dict | None) -> str | None:
        if not isinstance(payload, dict):
            return None
        output_payload = payload.get("output_payload")
        if isinstance(output_payload, dict):
            for key in ("summary", "final_response", "response", "raw_text", "review_summary", "message", "result_summary"):
                value = output_payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        for key in ("summary", "final_response", "response", "raw_text", "message"):
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
        if outcome.terminal_status in {"done", "failed", "blocked", "stale", "cancelled", "pending_restart", "cancel_failed"}:
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
        last_poll_error_class: str | None = None
        last_poll_error_message: str | None = None
        while True:
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                break

            try:
                response = await self._get_runtime_task_status(runtime_status_url, metadata)
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                last_poll_error_class = exc.__class__.__name__
                last_poll_error_message = sanitize_exception_message(exc)
                remaining_after_error = max(0.0, deadline - time.monotonic())
                logger.warning(
                    "Runtime status poll transient failure trace_id=%s portal_dispatch_id=%s runtime_status_url=%s exception_class=%s message=%s remaining_poll_seconds=%s",
                    trace_context.get("trace_id"),
                    trace_context.get("portal_dispatch_id"),
                    runtime_status_url,
                    last_poll_error_class,
                    last_poll_error_message,
                    round(remaining_after_error, 2),
                )
                sleep_seconds = min(max(0, interval_seconds), remaining_after_error)
                if sleep_seconds > 0:
                    await asyncio.sleep(sleep_seconds)
                continue

            last_poll_error_class = None
            last_poll_error_message = None
            preview = safe_preview(response.text or "", limit=800)
            phase, _payload, outcome = self._normalize_runtime_status_response(
                response,
                trace_context=trace_context,
                raw_response_preview=preview,
                allow_pending=True,
            )
            if phase == "terminal" and outcome is not None:
                return outcome
            remaining_after_poll = max(0.0, deadline - time.monotonic())
            sleep_seconds = min(max(0, interval_seconds), remaining_after_poll)
            if sleep_seconds > 0:
                await asyncio.sleep(sleep_seconds)

        timeout_message = "Runtime status polling timed out"
        if last_poll_error_class and last_poll_error_message is not None:
            timeout_message = (
                "Runtime status polling timed out after transient status poll failure: "
                f"{last_poll_error_class}: {last_poll_error_message}"
            )

        return NormalizedRuntimeOutcome(
            terminal_status="failed",
            result_payload_json=self._build_failure_payload(
                "runtime_poll_timeout",
                timeout_message,
                trace_context=trace_context,
            ),
            message=timeout_message,
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

        if normalized_status == "blocked":
            return NormalizedRuntimeOutcome(
                terminal_status="blocked",
                result_payload_json=normalized_payload_json,
                message="Runtime execution reported blockers",
                runtime_status_code=runtime_status_code,
            )

        if normalized_status in {"error", "failed"} or ok_value is False:
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

                metadata = {
                    "portal_task_id": task.id,
                    "portal_task_source": task.source,
                    "portal_task_type": task.task_type,
                    "portal_task_family": task.task_family,
                    "current_task_id": task.id,
                    "source_type": task.source or "portal",
                    "source_ref": task.id,
                }
                metadata = self.runtime_execution_context_service.build_runtime_metadata(db, agent, metadata)
                metadata["trace_id"] = trace_id
                metadata["span_id"] = dispatch_span_id
                metadata["parent_span_id"] = parent_span_id
                metadata["portal_dispatch_id"] = portal_dispatch_id
                metadata["portal_task_id"] = task.id
                source_kind = input_payload.get("source_kind")
                if source_kind:
                    metadata["source_kind"] = source_kind
                delegation_rule = input_payload.get("delegation_rule")
                delegation_rule_id = input_payload.get("delegation_rule_id")
                delegation_payload = input_payload.get("delegation")
                if isinstance(delegation_payload, dict):
                    delegation_rule_id = delegation_rule_id or delegation_payload.get("delegation_rule_id")
                    delegation_source = delegation_payload.get("source")
                    delegation_provider = delegation_payload.get("provider")
                    if delegation_source:
                        metadata["portal_delegation_source"] = str(delegation_source)
                    if delegation_provider:
                        metadata["portal_delegation_provider"] = str(delegation_provider)
                if delegation_rule:
                    metadata["portal_delegation_rule"] = delegation_rule
                if delegation_rule_id:
                    metadata["portal_delegation_rule_id"] = str(delegation_rule_id)
                if task.trigger:
                    metadata["portal_task_trigger"] = task.trigger
                head_sha = input_payload.get("head_sha")
                if head_sha:
                    metadata["portal_head_sha"] = head_sha
                execution_mode = input_payload.get("execution_mode")
                if execution_mode:
                    metadata["portal_execution_mode"] = str(execution_mode)
                dedupe_hint = task.dedupe_key or task.version_key
                if dedupe_hint:
                    metadata["portal_dedupe_hint"] = dedupe_hint
                if task.task_type == "agent_async_task":
                    metadata["portal_task_mode"] = "agent_async_task"
                    if getattr(task, "skill_name", None):
                        metadata["portal_skill_name"] = task.skill_name
                    if getattr(task, "root_task_id", None):
                        metadata["portal_root_task_id"] = task.root_task_id
                    if getattr(task, "parent_task_id", None):
                        metadata["portal_parent_task_id"] = task.parent_task_id
                    autonomous_instruction = input_payload.get("autonomous_instruction")
                    if not isinstance(autonomous_instruction, str) or not autonomous_instruction.strip():
                        autonomous_instruction = AGENT_ASYNC_TASK_AUTONOMOUS_INSTRUCTION
                    existing_system_prompt = metadata.get("system_prompt")
                    if not isinstance(existing_system_prompt, str) or not existing_system_prompt.strip():
                        metadata["system_prompt"] = autonomous_instruction.strip()

                runtime_body = {
                    "task_id": task.id,
                    "task_type": task.task_type,
                    "input_payload": input_payload,
                    "source": task.source,
                    "metadata": metadata,
                }
                explicit_task_session_id = getattr(task, "task_session_id", None)
                input_session_id = input_payload.get("session_id")
                runtime_session_id = None
                if task.task_type == "agent_async_task" and isinstance(explicit_task_session_id, str) and explicit_task_session_id.strip():
                    runtime_session_id = explicit_task_session_id.strip()
                    metadata["portal_task_session_id"] = runtime_session_id
                elif isinstance(input_session_id, str) and input_session_id.strip():
                    runtime_session_id = input_session_id.strip()
                    metadata["portal_input_session_id"] = runtime_session_id
                if runtime_session_id:
                    runtime_body["session_id"] = runtime_session_id

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
                    return AgentTaskDispatchResult(False, task.id, None, task.status, f"Runtime URL resolution failed: {error_message}", task.result_payload_json)

                logger.debug(
                    "Prepared runtime dispatch body runtime_url=%s task_id=%s task_type=%s agent_id=%s service_name=%s namespace=%s source=%s has_session_id=%s input_payload_keys=%s metadata_keys=%s",
                    runtime_url,
                    task.id,
                    task.task_type,
                    getattr(agent, "id", "-"),
                    getattr(agent, "service_name", "-"),
                    getattr(agent, "namespace", "-"),
                    task.source,
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

                    if fresh_task.status in {"stale", "cancelled"}:
                        late_status = fresh_task.status
                        return AgentTaskDispatchResult(
                            True,
                            fresh_task.id,
                            submit_outcome.runtime_status_code if submit_outcome else response.status_code,
                            late_status,
                            f"late_runtime_result_ignored_because_task_is_{late_status}",
                            fresh_task.result_payload_json,
                        )
                    if phase == "pending":
                        fresh_task.status = "running"
                        fresh_task.runtime_request_id = submit_payload.get("request_id") if isinstance(submit_payload, dict) else None
                        if fresh_task.started_at is None:
                            fresh_task.started_at = datetime.utcnow()
                        task_repo.save(fresh_task)
                        status_url = self.proxy_service.build_agent_base_url(agent).rstrip("/") + f"/api/tasks/{task.id}"
                        poll_kwargs = {
                            "runtime_status_url": status_url,
                            "metadata": metadata,
                            "trace_context": trace_context,
                        }
                        if task.task_type == "agent_async_task":
                            settings = get_settings()
                            poll_kwargs["timeout_seconds"] = self._coerce_min_int(
                                getattr(settings, "agent_task_runtime_poll_timeout_seconds", 3600),
                                default=3600,
                                minimum=60,
                            )
                            poll_kwargs["interval_seconds"] = self._coerce_min_int(
                                getattr(settings, "agent_task_runtime_poll_interval_seconds", 1),
                                default=1,
                                minimum=1,
                            )
                        outcome = await self._poll_runtime_task_until_terminal(
                            **poll_kwargs,
                        )
                    else:
                        outcome = submit_outcome
                        fresh_task.status = "running"
                        fresh_task.runtime_request_id = (submit_payload or {}).get("request_id") if isinstance(submit_payload, dict) else None
                        if fresh_task.started_at is None:
                            fresh_task.started_at = datetime.utcnow()
                        task_repo.save(fresh_task)

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
                    if fresh_task.status in {"stale", "cancelled"}:
                        late_status = fresh_task.status
                        return AgentTaskDispatchResult(
                            True,
                            fresh_task.id,
                            outcome.runtime_status_code,
                            late_status,
                            f"late_runtime_result_ignored_because_task_is_{late_status}",
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
                    if fresh_task.status in {"stale", "cancelled"}:
                        late_status = fresh_task.status
                        return AgentTaskDispatchResult(
                            True,
                            fresh_task.id,
                            None,
                            late_status,
                            f"late_runtime_result_ignored_because_task_is_{late_status}",
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

    async def cancel_task(self, task_id: str, db: Session, user=None):
        _ = user
        task_repo = AgentTaskRepository(db)
        agent_repo = AgentRepository(db)
        task = task_repo.get_by_id(task_id)
        if not task:
            raise ValueError("Task not found")

        normalized_status = (task.status or "").strip().lower()
        terminal_statuses = {"done", "failed", "blocked", "stale", "cancelled", "pending_restart", "cancel_failed"}
        if normalized_status in terminal_statuses:
            return task
        if normalized_status == "queued":
            task.status = "cancelled"
            task.summary = "Task was cancelled before it started."
            task.finished_at = datetime.utcnow()
            return task_repo.save(task)
        if normalized_status != "running":
            raise RuntimeError("Task is not cancellable")

        agent = agent_repo.get_by_id(task.assignee_agent_id)
        if not agent:
            raise RuntimeError("Assignee agent not found")

        metadata = {
            "portal_task_id": task.id,
            "portal_task_source": task.source,
            "portal_task_type": task.task_type,
            "current_task_id": task.id,
            "source_type": task.source or "portal",
            "source_ref": task.id,
        }
        if task.task_type == "agent_async_task":
            metadata["portal_task_mode"] = "agent_async_task"
            if getattr(task, "skill_name", None):
                metadata["portal_skill_name"] = task.skill_name
            if getattr(task, "root_task_id", None):
                metadata["portal_root_task_id"] = task.root_task_id
            if getattr(task, "parent_task_id", None):
                metadata["portal_parent_task_id"] = task.parent_task_id
            if getattr(task, "task_session_id", None):
                metadata["portal_task_session_id"] = task.task_session_id

        runtime_url = self.proxy_service.build_agent_base_url(agent).rstrip("/") + f"/api/tasks/{task.id}/cancel"
        response = await self._post_cancel_to_runtime(runtime_url, metadata)
        if not (200 <= response.status_code < 300):
            preview = safe_preview(response.text or "", limit=500)
            raise RuntimeError(f"Runtime cancel failed with status {response.status_code}: {preview}")

        fresh_task = task_repo.get_by_id(task.id)
        if not fresh_task:
            raise ValueError("Task not found")
        if (fresh_task.status or "").strip().lower() in terminal_statuses:
            return fresh_task
        fresh_task.status = "cancelled"
        fresh_task.summary = "Task cancellation was requested."
        fresh_task.finished_at = datetime.utcnow()
        return task_repo.save(fresh_task)

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
