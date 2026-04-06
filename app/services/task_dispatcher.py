import json
from dataclasses import asdict, dataclass

import httpx
from sqlalchemy.orm import Session

from app.repositories.agent_repo import AgentRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.services.proxy_service import ProxyService


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


class TaskDispatcherService:
    def __init__(self) -> None:
        self.proxy_service = ProxyService()

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
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await client.post(url, json=body)

    def _build_failure_payload(self, error_code: str, message: str, status_code: int | None = None) -> str:
        return json.dumps({"ok": False, "error_code": error_code, "message": message, "runtime_status_code": status_code})

    @staticmethod
    def _normalize_runtime_response(response: httpx.Response) -> tuple[bool, str, str]:
        runtime_status_code = response.status_code
        response_text = response.text or ""

        if not (200 <= runtime_status_code < 300):
            payload = {
                "ok": False,
                "error_code": "runtime_http_error",
                "message": f"Runtime returned non-2xx status: {runtime_status_code}",
                "runtime_status_code": runtime_status_code,
            }
            return False, json.dumps(payload), "Runtime returned non-2xx status"

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
                "raw_response": response_text,
            }
            return False, json.dumps(payload), "Runtime returned malformed response"

        normalized_payload_json = json.dumps(response_json)
        status_value = response_json.get("status")
        if status_value is None:
            return False, normalized_payload_json, "Runtime returned malformed response"

        normalized_status = str(status_value).lower()
        ok_value = response_json.get("ok")

        if normalized_status == "success" and ok_value is not False:
            return True, normalized_payload_json, "Task dispatched successfully"

        if normalized_status in {"error", "blocked"} or ok_value is False:
            return False, normalized_payload_json, "Runtime execution reported failure"

        return False, normalized_payload_json, "Runtime returned malformed response"

    async def dispatch_task(self, task_id: str, db: Session, user=None) -> AgentTaskDispatchResult:
        _ = user
        task_repo = AgentTaskRepository(db)
        agent_repo = AgentRepository(db)

        task = task_repo.get_by_id(task_id)
        if not task:
            return AgentTaskDispatchResult(False, task_id, None, "not_found", "Task not found", None)

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

        metadata = {
            "portal_task_id": task.id,
            "portal_task_source": task.source,
            "shared_context_ref": task.shared_context_ref,
        }
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

        runtime_body = {
            "task_id": task.id,
            "task_type": task.task_type,
            "input_payload": input_payload,
            "source": task.source,
            "shared_context_ref": task.shared_context_ref,
            "metadata": metadata,
        }

        try:
            runtime_url = self.proxy_service.build_agent_base_url(agent).rstrip("/") + "/api/tasks/execute"
        except Exception as exc:
            task.status = "failed"
            task.result_payload_json = self._build_failure_payload("runtime_url_error", str(exc))
            task_repo.save(task)
            return AgentTaskDispatchResult(False, task.id, None, task.status, f"Runtime URL resolution failed: {exc}", task.result_payload_json)

        task.status = "running"
        task_repo.save(task)

        try:
            response = await self._post_to_runtime(runtime_url, runtime_body)
            runtime_status_code = response.status_code
            execution_succeeded, normalized_result_payload_json, dispatch_message = self._normalize_runtime_response(response)
            task.status = "done" if execution_succeeded else "failed"
            task.result_payload_json = normalized_result_payload_json
            task_repo.save(task)
            return AgentTaskDispatchResult(
                True,
                task.id,
                runtime_status_code,
                task.status,
                dispatch_message,
                task.result_payload_json,
            )
        except Exception as exc:
            task.status = "failed"
            task.result_payload_json = self._build_failure_payload("runtime_request_error", str(exc))
            task_repo.save(task)
            return AgentTaskDispatchResult(True, task.id, None, task.status, f"Runtime dispatch request failed: {exc}", task.result_payload_json)
