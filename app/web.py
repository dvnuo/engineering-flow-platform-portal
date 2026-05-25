import markupsafe
import app.logger  # Ensure logging is configured (intentional side-effect import)  # noqa: F401
import json
import logging
from datetime import datetime
from urllib.parse import quote
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request, Response, status, Query
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.db import SessionLocal
from app.repositories.agent_repo import AgentRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.repositories.agent_session_metadata_repo import AgentSessionMetadataRepository
from app.repositories.runtime_capability_catalog_snapshot_repo import RuntimeCapabilityCatalogSnapshotRepository
from app.repositories.user_repo import UserRepository
from app.repositories.runtime_profile_repo import RuntimeProfileRepository
from app.schemas.requirement_bundle import BundleRef, RequirementBundleCreateForm
from app.schemas.runtime_profile import (
    dump_runtime_profile_config_json,
    normalize_runtime_profile_llm_tools,
    parse_runtime_profile_config_json,
    sanitize_runtime_profile_config_dict,
    runtime_profile_model_supports_temperature,
)
from app.services.requirement_bundle_github_service import (
    RequirementBundleGithubService,
    RequirementBundleGithubServiceError,
)
from app.services.auth_service import parse_session_token
from app.services.proxy_service import ProxyService, build_portal_agent_headers, build_runtime_trace_headers
from app.services.runtime_execution_context_service import RuntimeExecutionContextService
from app.services.runtime_profile_sync_service import RuntimeProfileSyncService
from app.services.runtime_profile_sync_queue_service import RuntimeProfileSyncQueueService
from app.services.runtime_profile_service import RuntimeProfileService
from app.services.runtime_profile_config_policy import canonicalize_portal_runtime_profile_config
from app.services.runtime_capability_catalog import build_runtime_capability_catalog_provider_from_settings, RuntimeCapabilityCatalogProvider
from app.services.runtime_profile_test_service import RuntimeProfileTestService
from app.services.session_context_preview import merge_runtime_sessions_with_metadata
from app.services.thinking_process_view import build_thinking_process_view
from app.utils.runtime_proxy_query import _filter_runtime_file_upload_query_items
from app.log_context import get_log_context
from app.chat_payloads import normalize_assistant_chat_payload

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)

def escape_data_attr(s):
    """Escape string for safe embedding in HTML data-* attributes using markupsafe."""
    if s is None:
        return ''
    return markupsafe.escape(str(s))

templates.env.filters['data_attr'] = escape_data_attr
settings = get_settings()
proxy_service = ProxyService()
runtime_execution_context_service = RuntimeExecutionContextService()
requirement_bundle_service = RequirementBundleGithubService()
runtime_profile_sync_service = RuntimeProfileSyncService(proxy_service=proxy_service)
runtime_profile_sync_queue_service = RuntimeProfileSyncQueueService(runtime_profile_sync_service=runtime_profile_sync_service)
runtime_profile_test_service = RuntimeProfileTestService()
base_uri = settings.base_uri


def _current_user_from_cookie(request: Request):
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None
    user_id = parse_session_token(token)
    if not user_id:
        return None

    db = SessionLocal()
    try:
        user = UserRepository(db).get_by_id(user_id)
        if not user or not user.is_active:
            return None
        return user
    finally:
        db.close()


def _can_access(agent, user) -> bool:
    return user.role == "admin" or agent.owner_user_id == user.id or agent.visibility == "public"


def _can_write(agent, user) -> bool:
    return user.role == "admin" or agent.owner_user_id == user.id


def _session_id_is_task_session(session_id) -> bool:
    if not isinstance(session_id, str):
        return False
    return session_id.strip().startswith("agent-task:")


def _metadata_value_is_present(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _parse_metadata_json_object(metadata_json) -> dict:
    if isinstance(metadata_json, dict):
        return metadata_json
    if isinstance(metadata_json, bytes):
        try:
            metadata_json = metadata_json.decode("utf-8")
        except UnicodeDecodeError:
            return {}
    if not metadata_json:
        return {}
    try:
        parsed = json.loads(metadata_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _metadata_object_is_task_session(metadata: dict) -> bool:
    if not isinstance(metadata, dict):
        return False
    return (
        _metadata_value_is_present(metadata.get("current_task_id"))
        or "portal_task_id" in metadata
        or "portal_task_session_id" in metadata
    )


def _metadata_record_is_task_session(record) -> bool:
    if not record:
        return False
    if _session_id_is_task_session(getattr(record, "session_id", None)):
        return True
    if _metadata_value_is_present(getattr(record, "current_task_id", None)):
        return True
    metadata = _parse_metadata_json_object(getattr(record, "metadata_json", None))
    return _metadata_object_is_task_session(metadata)


def _runtime_session_is_task_session(session: dict, metadata_record=None) -> bool:
    if not isinstance(session, dict):
        return False
    if _session_id_is_task_session(session.get("session_id")):
        return True
    if _metadata_value_is_present(session.get("current_task_id")):
        return True
    for metadata_field in ("metadata", "metadata_json"):
        if _metadata_object_is_task_session(_parse_metadata_json_object(session.get(metadata_field))):
            return True
    return _metadata_record_is_task_session(metadata_record)


def _filter_agent_visible_sessions(runtime_sessions: list[dict], metadata_records: list) -> tuple[list[dict], list]:
    metadata_by_session_id = {
        getattr(record, "session_id", None): record
        for record in metadata_records
        if getattr(record, "session_id", None)
    }
    visible_runtime_sessions = [
        session
        for session in runtime_sessions
        if not _runtime_session_is_task_session(
            session,
            metadata_by_session_id.get(session.get("session_id") if isinstance(session, dict) else None),
        )
    ]
    visible_metadata_records = [record for record in metadata_records if not _metadata_record_is_task_session(record)]
    return visible_runtime_sessions, visible_metadata_records


def _portal_extra_headers(user, agent) -> dict[str, str]:
    return {
        **build_runtime_trace_headers(get_log_context()),
        **build_portal_agent_headers(user, agent),
    }


def _list_writable_agents(db, user) -> list:
    agents = AgentRepository(db).list_all()
    return [agent for agent in agents if _can_write(agent, user)]


def _status_tone_from_value(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {"done", "completed", "ready", "success"}:
        return "success"
    if normalized in {"queued", "running", "draft", "in_progress", "stale", "pending_restart"}:
        return "warning"
    if normalized in {"failed", "blocked", "missing", "error", "cancel_failed"}:
        return "error"
    if normalized in {"cancelled", "canceled"}:
        return "info"
    if normalized in {"", "unknown", "none", "null"}:
        return "neutral"
    return "info"


def _has_thinking_view_data(view: dict) -> bool:
    if not isinstance(view, dict):
        return False
    if "has_data" in view:
        return bool(view.get("has_data"))
    context = view.get("context") if isinstance(view.get("context"), dict) else {}
    budget = view.get("budget") if isinstance(view.get("budget"), dict) else {}
    active_skill = view.get("active_skill") if isinstance(view.get("active_skill"), dict) else {}
    fallback = view.get("fallback") if isinstance(view.get("fallback"), dict) else {}
    return bool(
        view.get("events")
        or budget
        or active_skill.get("name")
        or any(context.get(key) for key in ("objective", "summary", "current_state", "next_step"))
        or fallback.get("latest_event_type")
        or fallback.get("latest_event_state")
        or fallback.get("last_execution_id")
    )


def _safe_json_object(raw: str | None) -> dict | list | None:
    if raw is None or not str(raw).strip():
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def _pretty_json_text(raw: str | None) -> str:
    if raw is None or not str(raw).strip():
        return "{}"
    parsed = _safe_json_object(raw)
    if parsed is None:
        return raw
    return json.dumps(parsed, indent=2, ensure_ascii=False)


def _parse_json_textarea(raw: str | None, *, field_name: str) -> tuple[str | None, str | None]:
    cleaned = (raw or "").strip()
    if not cleaned:
        return None, None
    try:
        parsed = json.loads(cleaned)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, f"{field_name} must be valid JSON"
    if not isinstance(parsed, dict):
        return None, f"{field_name} must be a JSON object"
    return json.dumps(parsed, ensure_ascii=False), None


def _parse_form_bool(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "on", "yes"}

def _append_error_code(parts: list[str], code, error_type) -> None:
    code_text = str(code or "").strip()
    if code_text:
        parts.append(f"code={code_text}")
    error_type_text = str(error_type or "").strip()
    if error_type_text:
        parts.append(f"error_type={error_type_text}")


def _append_allowlisted_error_details(parts: list[str], details) -> None:
    if not isinstance(details, dict):
        return
    allowlist = (
        "legacy_max_chat_output_chars_ignored",
        "configured_max_chat_output_chars",
        "legacy_max_tokens_ignored",
        "configured_max_tokens",
        "effective_max_tokens",
        "budget_max_chat_output_chars_ignored",
        "configured_budget_max_chat_output_chars",
        "arg_max_chat_output_chars_ignored",
        "configured_arg_max_chat_output_chars",
        "file_context_budget_status",
        "file_context_estimated_tokens",
        "file_context_threshold_source",
        "output_boundary_source",
        "max_context_window_tokens",
        "max_prompt_tokens",
        "max_output_tokens",
        "max_chat_output_tokens",
        "max_chat_output_chars",
        "chars_per_token_estimate",
    )
    for key in allowlist:
        if key not in details:
            continue
        value = details.get(key)
        if value is None and key != "max_chat_output_chars":
            continue
        parts.append(f"{key}={value}")


def _merge_error_details(top_level_details, nested_details) -> dict:
    merged: dict = {}
    if isinstance(top_level_details, dict):
        merged.update(top_level_details)
    if isinstance(nested_details, dict):
        merged.update(nested_details)
    return merged


def _normalize_runtime_error_detail(content: bytes) -> str:
    decoded = content.decode("utf-8", errors="replace")
    preview = decoded[:1000]
    try:
        parsed = json.loads(decoded)
    except (TypeError, ValueError, json.JSONDecodeError):
        return f"Runtime error: {preview}"

    if not isinstance(parsed, dict):
        return f"Runtime error: {preview}"

    error = parsed.get("error")
    parts: list[str] = []

    if isinstance(error, str):
        message = error.strip()
        if message:
            parts.append(message)
        _append_error_code(parts, parsed.get("code"), parsed.get("error_type"))
        _append_allowlisted_error_details(parts, parsed.get("details"))
    elif isinstance(error, dict):
        message = str(error.get("message") or "").strip()
        if message:
            parts.append(message)
        _append_error_code(parts, error.get("code") or parsed.get("code"), error.get("error_type") or parsed.get("error_type"))
        details = _merge_error_details(parsed.get("details"), error.get("details"))
        _append_allowlisted_error_details(parts, details)
    else:
        message = str(parsed.get("message") or "").strip()
        if message:
            parts.append(message)
        _append_error_code(parts, parsed.get("code"), parsed.get("error_type"))
        _append_allowlisted_error_details(parts, parsed.get("details"))

    if not parts:
        parts.append(preview)
    return f"Runtime error: {' '.join(parts)}"


def _short_sha(value: str | None) -> str:
    cleaned = (value or "").strip()
    return cleaned[:7] if cleaned else "-"


def _format_duration_label(started_at, finished_at) -> str:
    if not started_at:
        return "-"
    end_time = finished_at or datetime.utcnow()
    total_seconds = max(0, int((end_time - started_at).total_seconds()))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def _humanize_artifact_label(value: str | None) -> str:
    cleaned = (value or "").strip()
    return cleaned.replace("_", " ").title() if cleaned else "Artifact"


def _build_bundle_detail_view_model(bundle_detail) -> dict:
    manifest = bundle_detail.manifest if isinstance(bundle_detail.manifest, dict) else {}
    scope = manifest.get("scope") if isinstance(manifest.get("scope"), dict) else {}
    bundle_path = (bundle_detail.bundle_ref.path or "").strip()
    fallback_title = bundle_path.split("/")[-1] if bundle_path else "Bundle"
    title = manifest.get("title") or fallback_title or "Bundle"
    bundle_ref_label = title or fallback_title or "-"
    status_label = manifest.get("status") or "unknown"
    bundle_label = getattr(bundle_detail, "bundle_label", None) or "Requirement Bundle"
    subtitle = " · ".join(part for part in (bundle_path or "-", bundle_label, status_label) if part)
    repo = bundle_detail.bundle_ref.repo or "-"
    branch = bundle_detail.bundle_ref.branch or "-"
    github_url = f"https://github.com/{repo}/tree/{branch}/{bundle_detail.bundle_ref.path}"

    artifacts = []
    for artifact in (bundle_detail.artifacts or []):
        exists = bool(artifact.exists)
        artifacts.append(
            {
                "artifact_key": artifact.artifact_key,
                "label": _humanize_artifact_label(artifact.artifact_key),
                "file_path": artifact.file_path or "-",
                "exists": exists,
                "status_label": "Ready" if exists else "Missing",
                "status_tone": "success" if exists else "error",
                "github_url": f"https://github.com/{repo}/blob/{branch}/{bundle_detail.bundle_ref.path}/{artifact.file_path}"
                if artifact.file_path
                else None,
            }
        )

    return {
        "title": title,
        "subtitle": subtitle,
        "bundle_ref_label": bundle_ref_label,
        "status_label": status_label,
        "status_tone": _status_tone_from_value(status_label),
        "domain": scope.get("domain") or "-",
        "bundle_label": bundle_label,
        "repo": repo,
        "branch": branch,
        "path": bundle_path or "-",
        "github_url": github_url,
        "last_commit_short": _short_sha(bundle_detail.last_commit_sha),
        "last_commit_full": bundle_detail.last_commit_sha or "-",
        "artifact_ready_count": sum(1 for item in artifacts if item["exists"]),
        "artifact_total_count": len(artifacts),
        "artifacts": artifacts,
    }


def _extract_text_field(payload: dict | None, keys: tuple[str, ...]) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_async_result_payload(result_payload: dict | list | None) -> dict:
    if not isinstance(result_payload, dict):
        return {}
    output_payload = result_payload.get("output_payload")
    if isinstance(output_payload, dict):
        merged = dict(output_payload)
        for key, value in result_payload.items():
            if key not in merged:
                merged[key] = value
        return merged
    return result_payload


def _stringify_blocker(item) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for key in ("message", "summary", "reason", "detail"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return json.dumps(item, ensure_ascii=False)
    return str(item).strip() if item is not None else ""


def _normalize_blockers(value) -> list[str]:
    if isinstance(value, list):
        return [text for text in (_stringify_blocker(item) for item in value) if text]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, dict):
        text = _stringify_blocker(value)
        return [text] if text else []
    return []


def _task_content_from_input(input_obj: dict) -> tuple[str, str]:
    if isinstance(input_obj.get("followup_task"), str) and input_obj.get("followup_task").strip():
        return input_obj.get("followup_task").strip(), "Latest Follow-up"
    if isinstance(input_obj.get("user_task"), str) and input_obj.get("user_task").strip():
        return input_obj.get("user_task").strip(), "Original Task"
    return "", "Task"


def _fallback_title_from_content(content: str) -> str:
    first_line = next((line.strip() for line in (content or "").splitlines() if line.strip()), "")
    title = " ".join((first_line or content or "Agent background task").split())
    if len(title) > 96:
        return title[:93].rstrip() + "..."
    return title


def _agent_label_for_task(db, task) -> str:
    agent_id = getattr(task, "assignee_agent_id", None) or "-"
    if not db or agent_id == "-":
        return agent_id
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
    except Exception:
        agent = None
    if not agent:
        return agent_id
    agent_name = (getattr(agent, "name", None) or "").strip()
    return f"{agent_name} ({agent_id})" if agent_name else agent_id


def _build_agent_async_task_detail_view_model(task, db=None) -> dict:
    return _build_agent_async_task_detail_view_model_for_user(task, db=db, user=None)


def _can_manage_task_for_user(db, task, user) -> bool:
    if user is None:
        return True
    if getattr(user, "role", None) == "admin":
        return True
    user_id = getattr(user, "id", None)
    if getattr(task, "owner_user_id", None) == user_id or getattr(task, "created_by_user_id", None) == user_id:
        return True
    agent = AgentRepository(db).get_by_id(getattr(task, "assignee_agent_id", None))
    return bool(agent and _can_write(agent, user))


def _build_agent_async_task_detail_view_model_for_user(task, db=None, user=None) -> dict:
    repo = AgentTaskRepository(db) if db else None
    root_task_id = (getattr(task, "root_task_id", None) or getattr(task, "id", "") or "").strip()
    chain = []
    if repo and root_task_id:
        try:
            chain = repo.list_by_root_task_id(root_task_id)
        except Exception:
            chain = []
    if not chain:
        chain = [task]
    if all(getattr(item, "id", None) != getattr(task, "id", None) for item in chain):
        chain.append(task)
        chain.sort(key=lambda item: (getattr(item, "created_at", None) or datetime.min, getattr(item, "id", "")))

    input_payload = _safe_json_object(getattr(task, "input_payload_json", None))
    input_obj = input_payload if isinstance(input_payload, dict) else {}
    result_payload = _safe_json_object(getattr(task, "result_payload_json", None))
    result_obj = _extract_async_result_payload(result_payload)
    task_content, task_content_label = _task_content_from_input(input_obj)
    original_task = ""
    if task_content_label == "Latest Follow-up":
        original_task = _extract_text_field(input_obj, ("original_task", "user_task"))
    skill_name = (getattr(task, "skill_name", None) or input_obj.get("skill_name") or "").strip().lstrip("/")
    final_response = _extract_text_field(result_obj, ("final_response", "response", "summary", "raw_text", "message"))
    blockers = _normalize_blockers(result_obj.get("blockers"))
    next_recommendation = _extract_text_field(result_obj, ("next_recommendation", "recommendation", "next_step"))
    status_label = getattr(task, "status", None) or "unknown"
    chain_has_active = any((getattr(item, "status", "") or "").strip().lower() in {"queued", "running"} for item in chain)
    can_manage_task = _can_manage_task_for_user(db, task, user) if db else user is None

    timeline = []
    for item in chain:
        item_input_payload = _safe_json_object(getattr(item, "input_payload_json", None))
        item_input_obj = item_input_payload if isinstance(item_input_payload, dict) else {}
        item_content, item_kind = _task_content_from_input(item_input_obj)
        timeline.append(
            {
                "id": getattr(item, "id", ""),
                "title": getattr(item, "title", None) or _fallback_title_from_content(item_content),
                "status": getattr(item, "status", None) or "unknown",
                "status_tone": _status_tone_from_value(getattr(item, "status", None)),
                "created_at": getattr(item, "created_at", None) or "-",
                "kind": item_kind,
                "is_current": getattr(item, "id", None) == getattr(task, "id", None),
            }
        )

    return {
        "is_agent_async": True,
        "display_title": getattr(task, "title", None) or _fallback_title_from_content(task_content),
        "display_subtitle": "Agent background task",
        "status_label": status_label,
        "status_tone": _status_tone_from_value(status_label),
        "is_active": status_label in {"queued", "running"},
        "chain_has_active": chain_has_active,
        "can_cancel": can_manage_task and status_label in {"queued", "running"},
        "can_rerun": can_manage_task and not chain_has_active and status_label not in {"queued", "running"},
        "show_followup_form": can_manage_task and not chain_has_active and status_label not in {"queued", "running"},
        "summary_text": getattr(task, "summary", None) or "",
        "error_text": getattr(task, "error_message", None) or "",
        "duration_label": _format_duration_label(getattr(task, "started_at", None), getattr(task, "finished_at", None)),
        "assignee_agent_id": getattr(task, "assignee_agent_id", None) or "-",
        "assignee_agent_label": _agent_label_for_task(db, task),
        "skill_name": skill_name or "-",
        "task_session_id": getattr(task, "task_session_id", None) or input_obj.get("task_session_id") or "-",
        "root_task_id": root_task_id or "-",
        "parent_task_id": getattr(task, "parent_task_id", None) or input_obj.get("parent_task_id") or "-",
        "task_content": task_content,
        "task_content_label": task_content_label,
        "original_task": original_task,
        "final_response": final_response,
        "blockers": blockers,
        "next_recommendation": next_recommendation,
        "timeline": timeline,
        "created_at": getattr(task, "created_at", None) or "-",
        "started_at": getattr(task, "started_at", None) or "-",
        "finished_at": getattr(task, "finished_at", None) or "-",
        "updated_at": getattr(task, "updated_at", None) or "-",
        "input_payload_pretty": _pretty_json_text(getattr(task, "input_payload_json", None)),
        "result_payload_pretty": _pretty_json_text(getattr(task, "result_payload_json", None)),
    }


def _build_task_detail_view_model(task, db=None, user=None) -> dict:
    if getattr(task, "task_type", None) == "agent_async_task":
        return _build_agent_async_task_detail_view_model_for_user(task, db=db, user=user)

    status_label = getattr(task, "status", None) or "unknown"
    context_items = [
        ("Task ID", getattr(task, "id", "-")),
        ("Task Type", getattr(task, "task_type", None) or "-"),
        ("Task Family", getattr(task, "task_family", None) or "-"),
        ("Provider", getattr(task, "provider", None) or "-"),
        ("Trigger", getattr(task, "trigger", None) or "-"),
        ("Version Key", getattr(task, "version_key", None) or "-"),
        ("Dedupe Key", getattr(task, "dedupe_key", None) or "-"),
        ("Runtime Request ID", getattr(task, "runtime_request_id", None) or "-"),
        ("Owner User ID", getattr(task, "owner_user_id", None) or "-"),
        ("Created By User ID", getattr(task, "created_by_user_id", None) or "-"),
        ("Updated At", getattr(task, "updated_at", None) or "-"),
    ]

    return {
        "is_agent_async": False,
        "display_title": getattr(task, "title", None) or "Unsupported Task",
        "display_subtitle": "Read-only task type",
        "status_label": status_label,
        "status_tone": _status_tone_from_value(status_label),
        "is_active": False,
        "can_cancel": False,
        "can_rerun": False,
        "show_followup_form": False,
        "unsupported_message": "This task type is not supported by the background task panel. Raw payloads are available for inspection.",
        "timeline": [],
        "summary_text": getattr(task, "summary", None) or "",
        "error_text": getattr(task, "error_message", None) or "",
        "duration_label": _format_duration_label(getattr(task, "started_at", None), getattr(task, "finished_at", None)),
        "assignee_agent_id": getattr(task, "assignee_agent_id", None) or "-",
        "owner_user_id": getattr(task, "owner_user_id", None) or "-",
        "created_by_user_id": getattr(task, "created_by_user_id", None) or "-",
        "runtime_request_id": getattr(task, "runtime_request_id", None) or "-",
        "task_type": getattr(task, "task_type", None) or "-",
        "task_family": getattr(task, "task_family", None) or "-",
        "provider": getattr(task, "provider", None) or "-",
        "trigger": getattr(task, "trigger", None) or "-",
        "version_key": getattr(task, "version_key", None) or "-",
        "dedupe_key": getattr(task, "dedupe_key", None) or "-",
        "source": getattr(task, "source", None) or "-",
        "created_at": getattr(task, "created_at", None) or "-",
        "started_at": getattr(task, "started_at", None) or "-",
        "finished_at": getattr(task, "finished_at", None) or "-",
        "updated_at": getattr(task, "updated_at", None) or "-",
        "retry_count": getattr(task, "retry_count", 0) or 0,
        "context_items": context_items,
        "input_payload_pretty": _pretty_json_text(getattr(task, "input_payload_json", None)),
        "result_payload_pretty": _pretty_json_text(getattr(task, "result_payload_json", None)),
    }


async def _forward_runtime(
    *,
    user,
    agent,
    method: str,
    subpath: str,
    query_items,
    body,
    headers: Optional[dict[str, str]] = None,
):
    return await proxy_service.forward(
        agent=agent,
        method=method,
        subpath=subpath,
        query_items=query_items,
        body=body,
        headers=headers or {},
        extra_headers=_portal_extra_headers(user, agent),
    )


async def _forward_runtime_multipart(
    *,
    user,
    agent,
    method: str,
    subpath: str,
    query_items,
    files,
    data=None,
    headers: Optional[dict[str, str]] = None,
):
    return await proxy_service.forward_multipart(
        agent=agent,
        method=method,
        subpath=subpath,
        query_items=query_items,
        files=files,
        data=data,
        headers=headers or {},
        extra_headers=_portal_extra_headers(user, agent),
    )


def _settings_view_payload(raw_config_data: dict, effective_config_data: dict | None = None) -> dict:
    raw_config = dict(raw_config_data or {})
    raw_config.pop("ssh", None)
    effective_config = dict(effective_config_data or RuntimeProfileService.merge_with_managed_defaults(raw_config))
    effective_config.pop("ssh", None)

    llm = dict(effective_config.get("llm")) if isinstance(effective_config.get("llm"), dict) else {}
    raw_llm = dict(raw_config.get("llm")) if isinstance(raw_config.get("llm"), dict) else {}
    if raw_llm.get("provider"):
        raw_llm["provider"] = RuntimeProfileService.normalize_managed_llm_provider(raw_llm.get("provider"))
    if llm.get("provider"):
        llm["provider"] = RuntimeProfileService.normalize_managed_llm_provider(llm.get("provider"))
    jira = effective_config.get("jira") if isinstance(effective_config.get("jira"), dict) else {}
    confluence = effective_config.get("confluence") if isinstance(effective_config.get("confluence"), dict) else {}
    jira_instances = jira.get("instances") if isinstance(jira.get("instances"), list) else []
    confluence_instances = confluence.get("instances") if isinstance(confluence.get("instances"), list) else []
    llm_tools_mode, llm_tools_patterns = _settings_llm_tools_view(llm)
    raw_llm_tools_mode, raw_llm_tools_patterns = _settings_llm_tools_view(raw_llm)
    raw_github = raw_config.get("github") if isinstance(raw_config.get("github"), dict) else {}
    raw_git = raw_config.get("git") if isinstance(raw_config.get("git"), dict) else {}
    raw_proxy = raw_config.get("proxy") if isinstance(raw_config.get("proxy"), dict) else {}

    return {
        "config": effective_config,
        "raw_config": raw_config,
        "llm": llm,
        "raw_llm": raw_llm,
        "effective_llm": llm,
        "llm_temperature_allowed": runtime_profile_model_supports_temperature(raw_llm.get("model")),
        "llm_tools_mode": llm_tools_mode,
        "llm_tools_patterns": llm_tools_patterns,
        "raw_llm_tools_mode": raw_llm_tools_mode,
        "raw_llm_tools_patterns": raw_llm_tools_patterns,
        "jira": jira,
        "jira_instances": jira_instances,
        "confluence": confluence,
        "confluence_instances": confluence_instances,
        "github": effective_config.get("github") if isinstance(effective_config.get("github"), dict) else {},
        "raw_github": raw_github,
        "git": effective_config.get("git") if isinstance(effective_config.get("git"), dict) else {},
        "raw_git": raw_git,
        "proxy": effective_config.get("proxy") if isinstance(effective_config.get("proxy"), dict) else {},
        "raw_proxy": raw_proxy,
        "debug": effective_config.get("debug") if isinstance(effective_config.get("debug"), dict) else {},
    }


def _settings_llm_tools_view(llm: dict) -> tuple[str, list[str]]:
    if not isinstance(llm, dict) or "tools" not in llm:
        return "inherit", []
    normalized = normalize_runtime_profile_llm_tools(llm.get("tools"))
    if normalized == ["*"]:
        return "all", []
    if not normalized:
        return "none", []
    return "custom", normalized


def _settings_error_response(
    request: Request,
    db,
    agent_id: str,
    config_payload: dict,
    message: str,
    *,
    profile_name: str | None = None,
    profile_revision: int | None = None,
    profile_bound_agent_count: int = 0,
    read_only: bool = False,
):
    base = config_payload if isinstance(config_payload, dict) else {}
    view_data = _settings_view_payload(base, RuntimeProfileService.merge_with_managed_defaults(base))
    summary = _settings_settings_panel_summary(db, agent_id)
    return templates.TemplateResponse(
        "partials/settings_panel.html",
        {
            "request": request,
            "agent_id": agent_id,
            "status_type": "error",
            "status_message": message,
            "profile_missing_message": "",
            "profile_name": profile_name,
            "profile_revision": profile_revision,
            "profile_bound_agent_count": profile_bound_agent_count,
            "read_only": read_only,
            **summary,
            **view_data,
        },
    )


def _format_utc_timestamp(value) -> str | None:
    if not value:
        return None
    return f"{value.strftime('%Y-%m-%d %H:%M')} UTC"


def _is_external_trigger_task(task) -> bool:
    source = (task.source or "").strip().lower()
    task_type = (task.task_type or "").strip().lower()
    external_sources = {"github", "jira", "confluence", "cron", "internal", "automation", "automation_rule"}
    return source in external_sources or task_type == "agent_async_task"


def _task_activity_time(task):
    return task.finished_at or task.started_at or task.updated_at or task.created_at


def _settings_automation_activity_summary(db, agent_id: str) -> dict[str, str]:
    tasks = [task for task in AgentTaskRepository(db).list_by_agent(agent_id) if _is_external_trigger_task(task)]
    if not tasks:
        return {
            "last_triggered_task_at_text": "No automation activity yet.",
            "last_automation_task_created_at_text": "No automation activity yet.",
            "recent_failed_trigger_summary": "No recent failed triggers.",
        }

    latest_task = max(tasks, key=lambda task: _task_activity_time(task) or datetime.min)
    latest_accepted_task = max(tasks, key=lambda task: task.created_at or datetime.min)

    last_triggered_text = _format_utc_timestamp(_task_activity_time(latest_task)) or "No automation activity yet."
    last_accepted_text = _format_utc_timestamp(latest_accepted_task.created_at) or "No automation activity yet."

    failed_tasks = [task for task in tasks if (task.status or "").strip().lower() == "failed" or bool((task.error_message or "").strip())]
    recent_failed_trigger_summary = "No recent failed triggers."
    if failed_tasks:
        latest_failed = max(failed_tasks, key=lambda task: _task_activity_time(task) or datetime.min)
        summary_text = (
            (latest_failed.error_message or "").strip()
            or (latest_failed.summary or "").strip()
            or f"{latest_failed.task_type} ({latest_failed.status})"
        )
        recent_failed_trigger_summary = summary_text

    return {
        "last_triggered_task_at_text": last_triggered_text,
        "last_automation_task_created_at_text": last_accepted_text,
        "recent_failed_trigger_summary": recent_failed_trigger_summary,
    }


def _settings_settings_panel_summary(db, agent_id: str) -> dict:
    return _settings_automation_activity_summary(db, agent_id)


def _runtime_profile_panel_context(
    request: Request,
    profile,
    profile_repo: RuntimeProfileRepository,
    *,
    status_type: str = "",
    status_message: str = "",
) -> dict:
    bound_count = profile_repo.count_bound_agents(profile.id)
    raw_config_data = parse_runtime_profile_config_json(profile.config_json, fallback_to_empty=True)
    config_data = RuntimeProfileService.merge_with_managed_defaults(raw_config_data)
    view_data = _settings_view_payload(raw_config_data, config_data)
    return {
        "request": request,
        "profile_id": profile.id,
        "status_type": status_type,
        "status_message": status_message,
        "profile_name": profile.name,
        "profile_description": profile.description or "",
        "profile_revision": profile.revision,
        "profile_is_default": bool(profile.is_default),
        "profile_bound_agent_count": bound_count,
        **view_data,
    }


def _settings_parse_instances(
    form,
    prefix: str,
    fields: list[str],
    existing_instances: Optional[list] = None,
    preserve_blank_fields: Optional[set[str]] = None,
    clearable_fields: Optional[set[str]] = None,
) -> list[dict]:
    def as_bool(value) -> bool:
        return str(value or "").lower() in {"1", "true", "on", "yes"}
    def _norm_identity(value) -> str:
        return str(value or "").strip().rstrip("/").lower()
    def _build_unique_index(items: list[dict], key_fn):
        index = {}
        duplicates = set()
        for row in items:
            if not isinstance(row, dict):
                continue
            key = key_fn(row)
            if not key:
                continue
            if key in index:
                duplicates.add(key)
            else:
                index[key] = row
        for key in duplicates:
            index.pop(key, None)
        return index
    count_text = (form.get(f"{prefix}_instance_count") or "0").strip()
    try:
        count = max(0, int(count_text))
    except ValueError:
        count = 0

    instances = []
    existing_instances = existing_instances if isinstance(existing_instances, list) else []
    by_name_url = _build_unique_index(
        existing_instances,
        lambda item: (_norm_identity(item.get("name")), _norm_identity(item.get("url")))
        if _norm_identity(item.get("name")) or _norm_identity(item.get("url")) else None,
    )
    by_name = _build_unique_index(existing_instances, lambda item: _norm_identity(item.get("name")))
    by_url = _build_unique_index(existing_instances, lambda item: _norm_identity(item.get("url")))

    def _find_existing_for_row(original_name, original_url, current_name, current_url) -> dict:
        original_pair = (_norm_identity(original_name), _norm_identity(original_url))
        has_original_identity = bool(original_pair[0] or original_pair[1])
        if not has_original_identity:
            return {}

        found = by_name_url.get(original_pair)
        if isinstance(found, dict):
            return found

        if original_pair[0]:
            found = by_name.get(original_pair[0])
            if isinstance(found, dict):
                return found

        if original_pair[1]:
            found = by_url.get(original_pair[1])
            if isinstance(found, dict):
                return found
        return {}

    preserve_blank_fields = preserve_blank_fields or set()
    clearable_fields = clearable_fields or set()
    for i in range(count):
        item = {}
        original_name = (form.get(f"{prefix}_instances_{i}_original_name") or "").strip()
        original_url = (form.get(f"{prefix}_instances_{i}_original_url") or "").strip()
        current_name = (form.get(f"{prefix}_instances_{i}_name") or "").strip()
        current_url = (form.get(f"{prefix}_instances_{i}_url") or "").strip()
        existing_item = _find_existing_for_row(original_name, original_url, current_name, current_url)
        for field in fields:
            field_name = f"{prefix}_instances_{i}_{field}"
            if field == "enabled":
                item[field] = as_bool(form.get(field_name))
                continue
            clear_flag = as_bool(form.get(f"{prefix}_instances_{i}_{field}_clear")) if field in clearable_fields else False
            value = (form.get(field_name) or "").strip()
            if clear_flag:
                value = ""
            elif field_name not in form and not value and field in preserve_blank_fields:
                value = existing_item.get(field) or ""
            item[field] = value
        if item.get("name") or item.get("url"):
            instances.append(item)
    return instances




def _parse_multiline_csv_list(raw: str | None) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    normalized = (raw or "").replace(",", "\n")
    for item in normalized.splitlines():
        cleaned = item.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        values.append(cleaned)
    return values


def _settings_parse_llm_tools_patterns(form) -> list[str]:
    count_text = (form.get("llm_tools_count") or "0").strip()
    try:
        count = max(0, int(count_text))
    except ValueError:
        count = 0

    patterns: list[str] = []
    seen_lower: set[str] = set()
    for i in range(count):
        value = (form.get(f"llm_tools_{i}_pattern") or "").strip()
        if not value:
            continue
        dedupe_key = value.lower()
        if dedupe_key in seen_lower:
            continue
        seen_lower.add(dedupe_key)
        patterns.append(value)
    return patterns


def _settings_parse_response_flow_select(form, field_name: str, allowed: set[str]) -> str | None:
    value = (form.get(field_name) or "").strip()
    if not value:
        return None
    return value if value in allowed else None


def _settings_parse_response_flow_ratio(form, field_name: str) -> tuple[float | None, str | None]:
    value = (form.get(field_name) or "").strip()
    if not value:
        return None, None
    try:
        parsed = float(value)
    except ValueError:
        return None, "Response flow complexity ratio must be a number between 0 and 1."
    if not (0 < parsed <= 1):
        return None, "Response flow complexity ratio must be a number between 0 and 1."
    return parsed, None


def _settings_parse_response_flow_min_tokens(form, field_name: str) -> tuple[int | None, str | None]:
    value = (form.get(field_name) or "").strip()
    if not value:
        return None, None
    try:
        parsed = int(value)
    except ValueError:
        return None, "Response flow complexity minimum tokens must be a positive integer."
    if parsed <= 0:
        return None, "Response flow complexity minimum tokens must be a positive integer."
    return parsed, None



def _settings_merge_payload(config_payload: dict, form) -> tuple[dict, Optional[str]]:
    def as_bool(value) -> bool:
        return str(value or "").lower() in {"1", "true", "on", "yes"}
    def is_clear(field_name: str) -> bool:
        return as_bool(form.get(field_name))

    def is_section_touched(section: str) -> bool:
        return str(form.get(f"__touch_{section}") or "0").strip() == "1"
    def is_github_copilot_provider(provider_value: str) -> bool:
        return (provider_value or "").strip().lower() in {"github_copilot", "github-copilot", "copilot", "github"}

    config_payload = config_payload if isinstance(config_payload, dict) else {}
    config_payload.pop("ssh", None)

    existing_proxy_password = None
    if "proxy" in config_payload and isinstance(config_payload["proxy"], dict):
        existing_proxy_password = config_payload["proxy"].get("password")

    jira_config = config_payload.get("jira")
    if isinstance(jira_config, dict):
        jira_instances = jira_config.get("instances", [])
        existing_jira_instances = jira_instances if isinstance(jira_instances, list) else []
    else:
        existing_jira_instances = []

    confluence_config = config_payload.get("confluence")
    if isinstance(confluence_config, dict):
        confluence_instances = confluence_config.get("instances", [])
        existing_confluence_instances = confluence_instances if isinstance(confluence_instances, list) else []
    else:
        existing_confluence_instances = []

    llm = (config_payload.get("llm") if isinstance(config_payload.get("llm"), dict) else {}).copy()
    if is_section_touched("llm"):
        provider_value = (form.get("llm_provider") or "").strip()
        model_value = (form.get("llm_model") or "").strip()
        api_key_value = (form.get("llm_api_key") or "").strip()
        if provider_value:
            llm["provider"] = provider_value
        else:
            llm.pop("provider", None)
        if model_value:
            llm["model"] = model_value
        else:
            llm.pop("model", None)
        if api_key_value:
            llm["api_key"] = api_key_value
        elif "llm_api_key" in form or is_clear("llm_api_key_clear"):
            llm.pop("api_key", None)
        elif str(llm.get("api_key") or "").strip():
            llm["api_key"] = llm.get("api_key")
        else:
            llm.pop("api_key", None)
        llm.pop("oauth", None)
        llm.pop("oauth_by_runtime", None)

        max_tokens_text = (form.get("llm_max_tokens") or "").strip()
        if "llm_max_tokens" in form:
            if not max_tokens_text:
                llm.pop("max_tokens", None)
            else:
                try:
                    llm["max_tokens"] = int(max_tokens_text)
                except ValueError:
                    return config_payload, "Max tokens must be an integer."

        if llm:
            config_payload["llm"] = llm
        else:
            config_payload.pop("llm", None)

    if is_section_touched("jira"):
        jira = (config_payload.get("jira") if isinstance(config_payload.get("jira"), dict) else {}).copy()
        jira["enabled"] = as_bool(form.get("jira_enabled"))
        if "jira_instance_count" in form:
            jira["instances"] = _settings_parse_instances(
                form,
                "jira",
                ["enabled", "name", "url", "username", "password", "token", "project", "api_version"],
                existing_instances=existing_jira_instances,
                preserve_blank_fields={"password", "token"},
                clearable_fields={"password", "token"},
            )
        jira.pop("automation", None)
        config_payload["jira"] = jira

    if is_section_touched("confluence"):
        confluence = (config_payload.get("confluence") if isinstance(config_payload.get("confluence"), dict) else {}).copy()
        confluence["enabled"] = as_bool(form.get("confluence_enabled"))
        if "confluence_instance_count" in form:
            confluence["instances"] = _settings_parse_instances(
                form,
                "confluence",
                ["enabled", "name", "url", "username", "password", "token", "space"],
                existing_instances=existing_confluence_instances,
                preserve_blank_fields={"password", "token"},
                clearable_fields={"password", "token"},
            )
        confluence.pop("automation", None)
        config_payload["confluence"] = confluence

    if is_section_touched("github"):
        github_cfg = (config_payload.get("github") if isinstance(config_payload.get("github"), dict) else {}).copy()
        github_cfg["enabled"] = as_bool(form.get("github_enabled"))
        github_token_value = (form.get("github_api_token") or "").strip()
        github_base_url_value = (form.get("github_base_url") or "").strip()
        if "github_api_token" in form:
            if github_token_value:
                github_cfg["api_token"] = github_token_value
            elif "github_api_token" in form or is_clear("github_api_token_clear"):
                github_cfg.pop("api_token", None)
            else:
                existing_token = str(github_cfg.get("api_token") or "").strip()
                if existing_token:
                    github_cfg["api_token"] = existing_token
                else:
                    github_cfg.pop("api_token", None)
        if "github_base_url" in form:
            if github_base_url_value:
                github_cfg["base_url"] = github_base_url_value
            else:
                github_cfg.pop("base_url", None)
        github_cfg.pop("automation", None)
        config_payload["github"] = github_cfg

    if is_section_touched("git"):
        git_cfg = (config_payload.get("git") if isinstance(config_payload.get("git"), dict) else {}).copy()
        git_user = (git_cfg.get("user") if isinstance(git_cfg.get("user"), dict) else {}).copy()
        git_name_value = (form.get("git_user_name") or "").strip()
        git_email_value = (form.get("git_user_email") or "").strip()
        if "git_user_name" in form:
            if git_name_value:
                git_user["name"] = git_name_value
            else:
                git_user.pop("name", None)
        if "git_user_email" in form:
            if git_email_value:
                git_user["email"] = git_email_value
            else:
                git_user.pop("email", None)
        if git_user:
            git_cfg["user"] = git_user
        else:
            git_cfg.pop("user", None)
        if git_cfg:
            config_payload["git"] = git_cfg
        else:
            config_payload.pop("git", None)

    if is_section_touched("proxy"):
        proxy_cfg = (config_payload.get("proxy") if isinstance(config_payload.get("proxy"), dict) else {}).copy()
        proxy_cfg["enabled"] = as_bool(form.get("proxy_enabled"))
        proxy_url_value = (form.get("proxy_url") or "").strip()
        proxy_username_value = (form.get("proxy_username") or "").strip()
        if "proxy_url" in form:
            if proxy_url_value:
                proxy_cfg["url"] = proxy_url_value
            else:
                proxy_cfg.pop("url", None)
        if "proxy_username" in form:
            if proxy_username_value:
                proxy_cfg["username"] = proxy_username_value
            else:
                proxy_cfg.pop("username", None)
        if "proxy_password" in form:
            new_password = (form.get("proxy_password") or "").strip()
            if new_password:
                proxy_cfg["password"] = new_password
            elif "proxy_password" in form or is_clear("proxy_password_clear"):
                proxy_cfg.pop("password", None)
            elif existing_proxy_password:
                proxy_cfg["password"] = existing_proxy_password
            else:
                proxy_cfg.pop("password", None)
        elif existing_proxy_password:
            proxy_cfg["password"] = existing_proxy_password
        config_payload["proxy"] = proxy_cfg

    if is_section_touched("debug"):
        debug_cfg = (config_payload.get("debug") if isinstance(config_payload.get("debug"), dict) else {}).copy()
        debug_cfg["enabled"] = as_bool(form.get("debug_enabled"))
        valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR"]
        log_level = (form.get("debug_log_level") or "").strip()
        if log_level in valid_log_levels:
            debug_cfg["log_level"] = log_level
        config_payload["debug"] = debug_cfg

    config_payload.pop("ssh", None)
    config_payload = canonicalize_portal_runtime_profile_config(config_payload)
    return config_payload, None


@router.get("/")
def index(request: Request) -> RedirectResponse:
    user = _current_user_from_cookie(request)
    return RedirectResponse(url="/app" if user else "/login", status_code=302)


@router.get("/login")
def login_page(request: Request):
    if _current_user_from_cookie(request):
        return RedirectResponse(url="/app", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "title": "Portal Login"})


@router.get("/register")
def register_page(request: Request):
    if _current_user_from_cookie(request):
        return RedirectResponse(url="/app", status_code=302)
    return templates.TemplateResponse("register.html", {"request": request, "title": "Create Account"})


@router.get("/app")
def app_page(request: Request):
    user = _current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    return templates.TemplateResponse(
        "app.html",
        {
            "request": request,
            "title": "Engineering Portal",
            "username": user.username,
            "nickname": user.nickname or user.username,
            "user_id": user.id,
            "role": user.role,
            "bundle_base_branch": settings.assets_default_base_branch,
        },
    )


@router.get("/app/requirement-bundles")
def requirement_bundles_page(request: Request):
    user = _current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    db = SessionLocal()
    try:
        return _render_requirement_bundles_view(request, user, db)
    finally:
        db.close()


@router.get("/app/requirement-bundles/panel")
def requirement_bundles_panel(request: Request):
    user = _current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    db = SessionLocal()
    try:
        return _render_requirement_bundles_view(request, user, db, panel_mode=True)
    finally:
        db.close()




def _content_target_from_request(request: Request, default: str = "#tool-panel-body") -> str:
    hx_target = (request.headers.get("HX-Target") or "").strip()
    if hx_target:
        return hx_target if hx_target.startswith("#") else f"#{hx_target}"
    return default

def _is_htmx_request(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def _requirement_bundles_context(request: Request, user, db, **kwargs) -> dict:
    context = {
        "request": request,
        "title": "Bundles",
        "username": user.username,
        "nickname": user.nickname or user.username,
        "bundle_defaults": {
            "repo": settings.assets_repo_full_name,
            "base_branch": settings.assets_default_base_branch,
            "root_dir": settings.assets_bundle_root_dir,
        },
        "bundle_result": None,
        "bundle_detail": None,
        "status_type": "",
        "status_message": "",
        "bundle_view_model": None,
    }
    context.update(kwargs)
    return context


def _render_requirement_bundles_view(request: Request, user, db, *, panel_mode: bool = False, **kwargs):
    context = _requirement_bundles_context(request, user, db, **kwargs)
    if context.get("bundle_detail"):
        context["bundle_view_model"] = _build_bundle_detail_view_model(context["bundle_detail"])
    context["content_target"] = _content_target_from_request(
        request,
        default="#tool-panel-body" if panel_mode else "#requirement-bundles-page-content",
    )
    template_name = "partials/requirement_bundles_panel.html" if panel_mode else "requirement_bundles.html"
    return templates.TemplateResponse(template_name, context)


@router.get("/app/tasks/panel")
def my_tasks_panel(request: Request):
    user = _current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    db = SessionLocal()
    try:
        tasks = AgentTaskRepository(db).list_visible_to_user(user_id=user.id)
        summary = {"queued": 0, "running": 0, "done": 0, "blocked": 0, "failed": 0, "stale": 0, "cancelled": 0, "pending_restart": 0, "cancel_failed": 0}
        for task in tasks:
            if task.status in summary:
                summary[task.status] += 1
        return templates.TemplateResponse(
            "partials/my_tasks_panel.html",
            {"request": request, "tasks": tasks, "summary": summary, "content_target": _content_target_from_request(request)},
        )
    finally:
        db.close()




@router.get("/app/tasks/create/panel")
def task_create_panel(request: Request):
    user = _current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    db = SessionLocal()
    try:
        return templates.TemplateResponse(
            "partials/task_create_panel.html",
            {
                "request": request,
                "agents": _list_writable_agents(db, user),
            },
        )
    finally:
        db.close()

@router.get("/app/tasks/{task_id}/panel")
def task_detail_panel(request: Request, task_id: str):
    user = _current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    db = SessionLocal()
    try:
        task = AgentTaskRepository(db).get_by_id(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if user.role != "admin":
            is_visible = task.owner_user_id == user.id or task.created_by_user_id == user.id
            if not is_visible:
                raise HTTPException(status_code=404, detail="Task not found")
        return templates.TemplateResponse(
            "partials/task_detail_panel.html",
            {
                "request": request,
                "task": task,
                "task_view_model": _build_task_detail_view_model(task, db=db, user=user),
                "content_target": _content_target_from_request(request),
            },
        )
    finally:
        db.close()


@router.post("/app/requirement-bundles/create")
async def requirement_bundle_create(request: Request):
    user = _current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    panel_mode = _is_htmx_request(request)
    db = SessionLocal()
    try:
        form_data = await request.form()
        create_form = RequirementBundleCreateForm(
            title=str(form_data.get("title") or ""),
            domain=str(form_data.get("domain") or ""),
            slug=(str(form_data.get("slug") or "").strip() or None),
            base_branch=str(form_data.get("base_branch") or settings.assets_default_base_branch),
        )
        bundle_ref = requirement_bundle_service.create_bundle(create_form)
        bundle_detail = requirement_bundle_service.inspect_bundle(bundle_ref)
        return _render_requirement_bundles_view(
            request,
            user,
            db,
            panel_mode=panel_mode,
            bundle_result=bundle_ref,
            bundle_detail=bundle_detail,
            status_type="success",
            status_message="Bundle created successfully.",
        )
    except RequirementBundleGithubServiceError as exc:
        return _render_requirement_bundles_view(
            request,
            user,
            db,
            panel_mode=panel_mode,
            status_type="error",
            status_message=str(exc),
        )
    finally:
        db.close()


@router.get("/app/requirement-bundles/open")
def requirement_bundle_open(request: Request, repo: str = Query(""), path: str = Query(""), branch: str = Query("")):
    user = _current_user_from_cookie(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    target_repo = (repo or settings.assets_repo_full_name).strip()
    target_path = (path or "").strip()
    target_branch = (branch or settings.assets_default_base_branch).strip()
    panel_mode = _is_htmx_request(request)

    db = SessionLocal()
    try:
        detail = requirement_bundle_service.inspect_bundle(
            BundleRef(repo=target_repo, path=target_path, branch=target_branch)
        )
        return _render_requirement_bundles_view(
            request,
            user,
            db,
            panel_mode=panel_mode,
            bundle_detail=detail,
        )
    except RequirementBundleGithubServiceError as exc:
        return _render_requirement_bundles_view(
            request,
            user,
            db,
            panel_mode=panel_mode,
            status_type="error",
            status_message=str(exc),
        )
    finally:
        db.close()


@router.get("/app/users/panel")
async def app_users_panel(request: Request):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")

    db = SessionLocal()
    try:
        users = UserRepository(db).list_all()[:100]  # Limit to 100 users
        return templates.TemplateResponse(
            "partials/users_panel.html",
            {
                "request": request,
                "users": [{"id": u.id, "username": u.username, "role": u.role, "is_active": u.is_active, "created_at": u.created_at} for u in users],
            },
        )
    finally:
        db.close()



@router.delete("/app/agents/{agent_id}/sessions/{session_id}")
async def app_agent_delete_session(request: Request, agent_id: str, session_id: str):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    db = SessionLocal()
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent or not _can_access(agent, user):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
        if not _can_write(agent, user):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

        runtime_deleted = False
        runtime_missing = False
        runtime_skipped = False
        runtime_status = None
        runtime_response_json = True

        if not settings.k8s_enabled:
            runtime_skipped = True
        else:
            try:
                quoted_session_id = quote(session_id, safe="")
                runtime_status, content, _ = await _forward_runtime(user=user, agent=agent, method="DELETE", subpath=f"api/sessions/{quoted_session_id}", query_items=[], body=None)
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"Runtime delete failed: {exc}")
            if runtime_status == 404:
                runtime_missing = True
            elif runtime_status >= 400:
                raise HTTPException(status_code=502, detail=_normalize_runtime_error_detail(content))
            else:
                payload = {}
                if content:
                    try:
                        payload = json.loads(content.decode("utf-8"))
                        runtime_response_json = isinstance(payload, dict)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        runtime_response_json = False
                if isinstance(payload, dict) and payload.get("success") is False:
                    raise HTTPException(status_code=502, detail="Runtime delete returned success=false")
                runtime_deleted = True

        record, already_deleted = AgentSessionMetadataRepository(db).mark_deleted(agent_id=agent_id, session_id=session_id)
        return {"success": True, "agent_id": agent_id, "session_id": session_id, "runtime_deleted": runtime_deleted, "runtime_missing": runtime_missing, "runtime_skipped": runtime_skipped, "runtime_status": runtime_status, "runtime_response_json": runtime_response_json, "metadata_deleted": bool(record.deleted_at), "already_deleted": already_deleted}
    finally:
        db.close()


@router.get("/app/agents/{agent_id}/sessions/panel")
async def app_agent_sessions_panel(request: Request, agent_id: str):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    current_session_id = (request.query_params.get("current_session_id") or "").strip()
    limit = (request.query_params.get("limit") or "10").strip()

    db = SessionLocal()
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_access(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")
        can_manage_sessions = _can_write(agent, user)

        # When K8s is disabled, return empty sessions
        if not settings.k8s_enabled:
            metadata_repo = AgentSessionMetadataRepository(db)
            try:
                metadata_fallback_limit = max(1, int(limit))
            except (TypeError, ValueError):
                metadata_fallback_limit = 10
            recent_metadata_records = [
                record
                for record in metadata_repo.list_by_agent(agent_id)
                if not _metadata_record_is_task_session(record)
            ][:metadata_fallback_limit]
            sessions = merge_runtime_sessions_with_metadata(
                [],
                recent_metadata_records,
                include_metadata_only=True,
            )
            return templates.TemplateResponse(
                "partials/sessions_panel.html",
                {
                    "request": request,
                    "agent_id": agent_id,
                    "sessions": sessions,
                    "current_session_id": current_session_id,
                    "can_manage_sessions": can_manage_sessions,
                },
            )

        status_code, content, _ = await _forward_runtime(
            user=user,
            agent=agent,
            method="GET",
            subpath="api/sessions",
            query_items=[("limit", limit)],
            body=None,
        )

        if status_code >= 400:
            raise HTTPException(status_code=502, detail=_normalize_runtime_error_detail(content))

        payload = json.loads(content.decode("utf-8"))
        runtime_sessions = payload.get("sessions") or []
        session_ids = [session.get("session_id") for session in runtime_sessions if session.get("session_id")]
        metadata_repo = AgentSessionMetadataRepository(db)
        metadata_records = metadata_repo.list_by_agent_and_session_ids(
            agent_id=agent_id,
            session_ids=session_ids,
        )
        try:
            metadata_fallback_limit = max(1, int(limit))
        except (TypeError, ValueError):
            metadata_fallback_limit = 10
        recent_metadata_records = [
            record
            for record in metadata_repo.list_by_agent(agent_id)
            if not _metadata_record_is_task_session(record)
        ][:metadata_fallback_limit]
        all_metadata_records: list = []
        seen_session_ids: set[str] = set()
        for record in [*metadata_records, *recent_metadata_records]:
            record_session_id = getattr(record, "session_id", None)
            if not record_session_id or record_session_id in seen_session_ids:
                continue
            seen_session_ids.add(record_session_id)
            all_metadata_records.append(record)
        runtime_sessions, all_metadata_records = _filter_agent_visible_sessions(runtime_sessions, all_metadata_records)
        sessions = merge_runtime_sessions_with_metadata(
            runtime_sessions,
            all_metadata_records,
            include_metadata_only=True,
        )
        return templates.TemplateResponse(
            "partials/sessions_panel.html",
            {
                "request": request,
                "sessions": sessions,
                "current_session_id": current_session_id,
                "can_manage_sessions": can_manage_sessions,
            },
        )
    finally:
        db.close()





def _normalize_skill_payload(raw_skill) -> dict:
    if isinstance(raw_skill, dict):
        return dict(raw_skill)
    name = str(raw_skill or "").strip()
    return {"name": name}


def _skill_field(payload, *keys):
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return value
    return None

def _normalize_permission_state(value) -> str:
    normalized = str(value or "unknown").strip().lower()
    aliases = {
        "deny": "denied",
        "blocked": "denied",
        "disallowed": "denied",
        "allow": "allowed",
        "ask_user": "ask",
    }
    return aliases.get(normalized, normalized or "unknown")


def _normalize_runtime_compatibility(value) -> str:
    normalized = str(value or "unknown").strip().lower()
    aliases = {
        "not_supported": "unsupported",
        "not-supported": "unsupported",
        "disabled": "unsupported",
        "prompt-only": "prompt_only",
        "prompt": "prompt_only",
        "full_support": "full",
    }
    return aliases.get(normalized, normalized or "unknown")


def _runtime_catalog_provider_for_panel(db, agent) -> RuntimeCapabilityCatalogProvider:
    repo = RuntimeCapabilityCatalogSnapshotRepository(db)
    latest = repo.get_latest_for_agent(getattr(agent, "id", "")) or repo.get_latest()
    if latest:
        try:
            payload = json.loads(latest.payload_json)
        except Exception:
            payload = None
        if isinstance(payload, (dict, list)):
            return RuntimeCapabilityCatalogProvider.from_runtime_catalog_payload(
                payload,
                source=latest.catalog_source or "runtime_api",
            )
    return build_runtime_capability_catalog_provider_from_settings()


def _runtime_skill_detail_for_panel(db, agent, skill_name: str | None) -> dict:
    provider = _runtime_catalog_provider_for_panel(db, agent)
    candidates = [skill_name, (skill_name or "").replace("_", "-"), (skill_name or "").replace("-", "_")]
    for candidate in candidates:
        detail = provider.get_skill_detail(candidate)
        if detail:
            return detail
    return {}


def _annotate_skill_for_panel(db, agent, raw_skill) -> dict:
    payload = _normalize_skill_payload(raw_skill)
    name = str(_skill_field(payload, "name", "id") or "").strip().lstrip('/')
    runtime_detail = _runtime_skill_detail_for_panel(db, agent, name)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    permission_state = _normalize_permission_state(_skill_field(payload, "permission_state") or metadata.get("permission_state") or runtime_detail.get("permission_state") or "unknown")
    runtime_compatibility = _normalize_runtime_compatibility(
        _skill_field(payload, "runtime_compatibility", "compatibility", "opencode_compatibility")
        or metadata.get("runtime_compatibility")
        or metadata.get("compatibility")
        or metadata.get("opencode_compatibility")
        or runtime_detail.get("runtime_compatibility")
        or "unknown"
    )
    disabled_reasons = []
    if permission_state in {"denied", "blocked"}:
        disabled_reasons.append("Denied by runtime permission")
    if runtime_compatibility == "unsupported":
        disabled_reasons.append("Unsupported by this runtime")
    description = str(_skill_field(payload, "description") or metadata.get("description") or "").strip()
    tool_mappings = (
        payload.get("tool_mappings")
        if isinstance(payload.get("tool_mappings"), dict)
        else metadata.get("tool_mappings")
        if isinstance(metadata.get("tool_mappings"), dict)
        else runtime_detail.get("tool_mappings")
        or {}
    )
    catalog_available = bool(runtime_detail) or runtime_compatibility != "unknown"
    return {**payload, "name": name, "description": description, "catalog_available": catalog_available, "permission_state": permission_state, "runtime_compatibility": runtime_compatibility, "tool_mappings": tool_mappings, "disabled": bool(disabled_reasons), "disabled_reason": "; ".join(disabled_reasons), "prompt_only": runtime_compatibility == "prompt_only"}

@router.get("/app/agents/{agent_id}/skills/panel")
async def app_agent_skills_panel(request: Request, agent_id: str):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    db = SessionLocal()
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_access(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")

        # When K8s is disabled, return empty skills
        if not settings.k8s_enabled:
            return templates.TemplateResponse(
                "partials/skills_panel.html",
                {"request": request, "agent_id": agent_id, "skills": []},
            )

        status_code, content, _ = await _forward_runtime(
            user=user,
            agent=agent,
            method="GET",
            subpath="api/skills",
            query_items=[],
            body=None,
        )

        if status_code >= 400:
            raise HTTPException(status_code=502, detail=_normalize_runtime_error_detail(content))

        payload = json.loads(content.decode("utf-8"))
        return templates.TemplateResponse(
            "partials/skills_panel.html",
            {
                "request": request,
                "skills": [_annotate_skill_for_panel(db, agent, skill) for skill in (payload.get("skills") or [])],
            },
        )
    finally:
        db.close()


@router.get("/app/agents/{agent_id}/usage/panel")
async def app_agent_usage_panel(request: Request, agent_id: str):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    days = (request.query_params.get("days") or "30").strip()

    db = SessionLocal()
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_access(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")

        # When K8s is disabled, return empty usage
        if not settings.k8s_enabled:
            return templates.TemplateResponse(
                "partials/usage_panel.html",
                {"request": request, "agent_id": agent_id, "usage": [], "total_messages": 0, "total_cost": 0},
            )

        status_code, content, _ = await _forward_runtime(
            user=user,
            agent=agent,
            method="GET",
            subpath="api/usage",
            query_items=[("days", days)],
            body=None,
        )

        if status_code >= 400:
            raise HTTPException(status_code=502, detail=_normalize_runtime_error_detail(content))

        payload = json.loads(content.decode("utf-8"))
        return templates.TemplateResponse(
            "partials/usage_panel.html",
            {
                "request": request,
                "usage": payload if isinstance(payload, dict) else {},
            },
        )
    finally:
        db.close()

@router.post("/a/{agent_id}/api/files/upload")
async def agent_files_upload(agent_id: str, request: Request):
    """Proxy file upload to EFP agent"""
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    db = SessionLocal()
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_access(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")

        # Read the multipart form data
        form = await request.form()
        file_field = form.get("file")
        if not file_field:
            raise HTTPException(status_code=400, detail="No file provided")

        # Read file content
        content = await file_field.read()
        
        # Limit file size to 10MB to prevent memory issues
        MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail=f"File too large. Maximum size is 10MB.")
        
        # Prepare files for upload
        files = {"file": (file_field.filename, content, file_field.content_type)}
        
        query_items = _filter_runtime_file_upload_query_items(request)

        status_code, content, content_type = await _forward_runtime_multipart(
            user=user,
            agent=agent,
            method="POST",
            subpath="api/files/upload",
            query_items=query_items,
            files=files,
        )

        if status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Upload failed: {content.decode('utf-8', errors='ignore')}")

        return Response(content=content, media_type=content_type, status_code=status_code)
    finally:
        db.close()


@router.post("/a/{agent_id}/api/server-files/upload")
async def agent_server_files_upload(agent_id: str, request: Request):
    """Proxy server files upload to EFP agent."""
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    db = SessionLocal()
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_write(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")

        form = await request.form()
        file_field = form.get("file")
        target_path = form.get("path") or ""
        if not file_field:
            raise HTTPException(status_code=400, detail="No file provided")

        content = await file_field.read()
        files = {"file": (file_field.filename, content, file_field.content_type)}

        status_code, content_bytes, content_type = await _forward_runtime_multipart(
            user=user,
            agent=agent,
            method="POST",
            subpath="api/server-files/upload",
            query_items=[],
            files=files,
            data={"path": target_path},
        )

        return Response(content=content_bytes, media_type=content_type, status_code=status_code)
    finally:
        db.close()


@router.get("/a/{agent_id}/api/files/{file_id}/preview")
async def agent_files_preview(request: Request, agent_id: str, file_id: str, max_chars: int = 5000):
    """Proxy file preview to EFP agent"""
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    
    # Validate and cap max_chars
    max_chars = max(0, min(max_chars, 20000))  # Clamp between 0 and 20000

    db = SessionLocal()
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_access(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")

        # Use proxy_service.forward for consistent proxy behavior
        status_code, content, content_type = await _forward_runtime(
            user=user,
            agent=agent,
            method="GET",
            subpath=f"api/files/{file_id}/preview",
            query_items=[("max_chars", str(max_chars))],
            body=None,
        )
        
        if status_code >= 400:
            raise HTTPException(status_code=502, detail="Preview failed")
        
        return Response(content=content, media_type=content_type, status_code=status_code)
    finally:
        db.close()


@router.get("/a/{agent_id}/api/files/download")
async def agent_files_download(agent_id: str, request: Request, path: str = "", paths: Optional[List[str]] = Query(default=None)):
    """Proxy download file request to agent."""
    # Support both 'path' and 'paths' parameter (frontend uses 'paths')
    # paths can be a list for multiple files
    if paths is None:
        # Fallback to single 'path' param
        file_paths = [path] if path else []
    else:
        file_paths = paths
    
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    db = SessionLocal()
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_access(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")

        # Use proxy_service.forward for consistent proxy behavior (frontend uses 'paths')
        query_items = [("paths", p) for p in file_paths]
        status_code, content, content_type = await _forward_runtime(
            user=user,
            agent=agent,
            method="GET",
            subpath="api/files/download",
            query_items=query_items,
            body=None,
        )
        
        if status_code >= 400:
            raise HTTPException(status_code=502, detail="Download failed")
        
        # Extract filename from path (use first for single, zip for multiple)
        if len(file_paths) > 1:
            filename = "files.zip"
        elif content_type == 'application/zip':
            # Agent already determined it's a ZIP (e.g., folder download)
            filename = file_paths[0].split("/")[-1] + ".zip" if file_paths else "download.zip"
        else:
            filename = file_paths[0].split("/")[-1] if file_paths else "download"
        
        return Response(
            content=content, 
            media_type=content_type, 
            status_code=status_code,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    finally:
        db.close()


@router.get("/app/agents/{agent_id}/thinking/panel")
async def app_agent_thinking_panel(request: Request, agent_id: str, session_id: str = ""):
    """Backend-rendered thinking process panel"""
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    if not session_id:
        return templates.TemplateResponse(
            "partials/thinking_process_panel.html",
            {"request": request, "agent_id": agent_id, "session_id": "", "chatlog": None, "view": {}, "error": "No session selected"},
        )

    db = SessionLocal()
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_access(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")
        metadata_record = AgentSessionMetadataRepository(db).get_by_agent_and_session(agent_id, session_id)

        if not settings.k8s_enabled:
            view = build_thinking_process_view(None, metadata_record)
            return templates.TemplateResponse(
                "partials/thinking_process_panel.html",
                {
                    "request": request,
                    "agent_id": agent_id,
                    "session_id": session_id,
                    "chatlog": None,
                    "view": view,
                    "error": None if _has_thinking_view_data(view) else "Agent not running",
                },
            )

        status_code, content, _ = await _forward_runtime(
            user=user,
            agent=agent,
            method="GET",
            subpath=f"api/sessions/{session_id}/chatlog",
            query_items=[],
            body=None,
        )

        if status_code >= 400:
            if metadata_record:
                view = build_thinking_process_view(None, metadata_record)
                view["warning"] = f"Runtime unavailable ({status_code}), showing last metadata snapshot."
                return templates.TemplateResponse(
                    "partials/thinking_process_panel.html",
                    {"request": request, "agent_id": agent_id, "session_id": session_id, "chatlog": None, "view": view, "error": None},
                )
            return templates.TemplateResponse(
                "partials/thinking_process_panel.html",
                {"request": request, "agent_id": agent_id, "session_id": session_id, "chatlog": None, "view": {}, "error": f"Error: {status_code}"},
            )

        chatlog = json.loads(content.decode("utf-8"))
        view = build_thinking_process_view(chatlog, metadata_record)
        return templates.TemplateResponse(
            "partials/thinking_process_panel.html",
            {"request": request, "agent_id": agent_id, "session_id": session_id, "chatlog": chatlog, "view": view, "error": None},
        )
    finally:
        db.close()


@router.get("/app/agents/{agent_id}/settings/panel")
async def app_agent_settings_panel(request: Request, agent_id: str):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    db = SessionLocal()
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_access(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")
        read_only = not _can_write(agent, user)
        summary = _settings_settings_panel_summary(db, agent_id)

        runtime_profile = None
        bound_agent_count = 0
        if agent.runtime_profile_id:
            profile_repo = RuntimeProfileRepository(db)
            runtime_profile = profile_repo.get_by_id(agent.runtime_profile_id)
            if runtime_profile and runtime_profile.owner_user_id in {user.id, agent.owner_user_id}:
                bound_agent_count = profile_repo.count_bound_agents(runtime_profile.id)
            else:
                runtime_profile = None

        if not runtime_profile:
            return templates.TemplateResponse(
                "partials/settings_panel.html",
                {
                    "request": request,
                    "agent_id": agent_id,
                    "status_type": "",
                    "status_message": "",
                    "profile_missing_message": "This agent has no runtime profile. Runtime settings are unavailable until one is assigned.",
                    "profile_name": None,
                    "profile_revision": None,
                    "profile_bound_agent_count": 0,
                    "config": {},
                    "read_only": read_only,
                    **summary,
                },
            )

        raw_config_data = parse_runtime_profile_config_json(runtime_profile.config_json, fallback_to_empty=True)
        config_data = RuntimeProfileService.merge_with_managed_defaults(raw_config_data)
        view_data = _settings_view_payload(raw_config_data, config_data)
        return templates.TemplateResponse(
            "partials/settings_panel.html",
            {
                "request": request,
                "agent_id": agent_id,
                "status_type": "",
                "status_message": "",
                "profile_missing_message": "",
                "profile_name": runtime_profile.name,
                "profile_revision": runtime_profile.revision,
                "profile_bound_agent_count": bound_agent_count,
                "read_only": read_only,
                **summary,
                **view_data,
            },
        )
    finally:
        db.close()


@router.post("/app/agents/{agent_id}/settings/save")
async def app_agent_settings_save(request: Request, agent_id: str):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    form = await request.form()

    db = SessionLocal()
    status_type = "success"
    status_message = "Runtime profile updated. Changes are shared across bound agents."
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_write(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")
        summary = _settings_settings_panel_summary(db, agent_id)

        if not agent.runtime_profile_id:
            return templates.TemplateResponse(
                "partials/settings_panel.html",
                {
                    "request": request,
                    "agent_id": agent_id,
                    "status_type": "error",
                    "status_message": "This agent has no runtime profile. Runtime settings are unavailable until one is assigned.",
                    "profile_missing_message": "This agent has no runtime profile. Runtime settings are unavailable until one is assigned.",
                    "profile_name": None,
                    "profile_revision": None,
                    "profile_bound_agent_count": 0,
                    "config": {},
                    "read_only": False,
                    **summary,
                },
            )

        profile_repo = RuntimeProfileRepository(db)
        runtime_profile = profile_repo.get_by_id(agent.runtime_profile_id)
        if not runtime_profile or runtime_profile.owner_user_id not in {user.id, agent.owner_user_id}:
            return templates.TemplateResponse(
                "partials/settings_panel.html",
                {
                    "request": request,
                    "agent_id": agent_id,
                    "status_type": "error",
                    "status_message": "Assigned runtime profile was not found.",
                    "profile_missing_message": "This agent has no runtime profile. Runtime settings are unavailable until one is assigned.",
                    "profile_name": None,
                    "profile_revision": None,
                    "profile_bound_agent_count": 0,
                    "config": {},
                    "read_only": False,
                    **summary,
                },
            )

        profile_bound_agent_count = profile_repo.count_bound_agents(runtime_profile.id)
        config_base = parse_runtime_profile_config_json(runtime_profile.config_json, fallback_to_empty=True)
        config_payload, merge_error = _settings_merge_payload(config_base, form)
        if merge_error:
            return _settings_error_response(
                request,
                db,
                agent_id,
                config_payload,
                merge_error,
                profile_name=runtime_profile.name,
                profile_revision=runtime_profile.revision,
                profile_bound_agent_count=profile_bound_agent_count,
                read_only=False,
            )

        sanitized_config = sanitize_runtime_profile_config_dict(config_payload)
        runtime_profile.config_json = dump_runtime_profile_config_json(sanitized_config)
        runtime_profile.revision = (runtime_profile.revision or 0) + 1
        runtime_profile = profile_repo.save(runtime_profile)

        try:
            sync_result = runtime_profile_sync_queue_service.enqueue_profile_to_bound_agents(db, runtime_profile, reason="runtime_profile_settings_save")
            status_message = (
                "Runtime profile saved. "
                f"Sync queued for {sync_result.get('queued_agent_count', 0)} bound agents. "
                "Running agents will apply soon; starting agents will apply after they become running."
            )
        except Exception:
            db.rollback()
            logger.exception("runtime profile fan-out sync failed after settings save profile_id=%s", runtime_profile.id)
            status_type = "error"
            status_message = (
                "Runtime profile was saved, but sync enqueue failed this time. Runtime profile sync can be retried later."
            )

        view_data = _settings_view_payload(sanitized_config, RuntimeProfileService.merge_with_managed_defaults(sanitized_config))
        return templates.TemplateResponse(
            "partials/settings_panel.html",
            {
                "request": request,
                "agent_id": agent_id,
                "status_type": status_type,
                "status_message": status_message,
                "profile_missing_message": "",
                "profile_name": runtime_profile.name,
                "profile_revision": runtime_profile.revision,
                "profile_bound_agent_count": profile_bound_agent_count,
                "read_only": False,
                **summary,
                **view_data,
            },
        )
    finally:
        db.close()


_MANAGED_TEST_TARGETS = {"proxy", "llm", "jira", "confluence", "github"}


def _validate_managed_test_target(target: str) -> str:
    clean_target = (target or "").strip().lower()
    if clean_target not in _MANAGED_TEST_TARGETS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown test target")
    return clean_target


@router.post("/app/agents/{agent_id}/settings/test/{target}")
async def app_agent_settings_test(request: Request, agent_id: str, target: str):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    target = _validate_managed_test_target(target)
    form = await request.form()
    db = SessionLocal()
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_write(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")
        if not agent.runtime_profile_id:
            raise HTTPException(status_code=404, detail="RuntimeProfile not found")

        runtime_profile = RuntimeProfileRepository(db).get_by_id(agent.runtime_profile_id)
        if not runtime_profile or runtime_profile.owner_user_id not in {user.id, agent.owner_user_id}:
            raise HTTPException(status_code=404, detail="RuntimeProfile not found")

        config_base = parse_runtime_profile_config_json(runtime_profile.config_json, fallback_to_empty=True)
        config_payload, merge_error = _settings_merge_payload(config_base, form)
        if merge_error:
            return JSONResponse({"ok": False, "target": target, "message": merge_error})
        ok, message = await runtime_profile_test_service.run_test(target, config_payload, runtime_type=getattr(agent, "runtime_type", None))
        return JSONResponse({"ok": bool(ok), "target": target, "message": message})
    finally:
        db.close()


@router.get("/app/runtime-profiles/{profile_id}/panel")
async def app_runtime_profile_panel(request: Request, profile_id: str):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    db = SessionLocal()
    try:
        service = RuntimeProfileService(db)
        profile = service.get_for_user(user, profile_id)
        if not profile:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RuntimeProfile not found")
        profile_repo = RuntimeProfileRepository(db)
        return templates.TemplateResponse(
            "partials/runtime_profile_panel.html",
            _runtime_profile_panel_context(request, profile, profile_repo),
        )
    finally:
        db.close()


@router.post("/app/runtime-profiles/{profile_id}/test/{target}")
async def app_runtime_profile_test(request: Request, profile_id: str, target: str):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    target = _validate_managed_test_target(target)
    form = await request.form()
    db = SessionLocal()
    try:
        service = RuntimeProfileService(db)
        profile = service.get_for_user(user, profile_id)
        if not profile:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RuntimeProfile not found")

        config_base = parse_runtime_profile_config_json(profile.config_json, fallback_to_empty=True)
        config_payload, merge_error = _settings_merge_payload(config_base, form)
        if merge_error:
            return JSONResponse({"ok": False, "target": target, "message": merge_error})

        ok, message = await runtime_profile_test_service.run_test(target, config_payload, runtime_type=(form.get("test_runtime_type") or "native"))
        return JSONResponse({"ok": bool(ok), "target": target, "message": message})
    finally:
        db.close()


@router.post("/app/runtime-profiles/{profile_id}/save")
async def app_runtime_profile_save(request: Request, profile_id: str):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    form = await request.form()
    db = SessionLocal()
    try:
        service = RuntimeProfileService(db)
        profile = service.get_for_user(user, profile_id)
        if not profile:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RuntimeProfile not found")

        profile_repo = RuntimeProfileRepository(db)
        config_base = parse_runtime_profile_config_json(profile.config_json, fallback_to_empty=True)
        config_payload, merge_error = _settings_merge_payload(config_base, form)
        if merge_error:
            return templates.TemplateResponse(
                "partials/runtime_profile_panel.html",
                _runtime_profile_panel_context(request, profile, profile_repo, status_type="error", status_message=merge_error),
            )

        sanitized_config = sanitize_runtime_profile_config_dict(config_payload)
        is_default = str(form.get("is_default") or "").lower() in {"1", "true", "on", "yes"}
        updated, config_changed = service.update_for_user(
            user,
            profile_id,
            name=(form.get("name") or profile.name).strip(),
            description=(form.get("description") or "").strip() or None,
            config_json=dump_runtime_profile_config_json(sanitized_config),
            is_default=is_default,
        )

        status_type = "success"
        status_message = "Runtime profile saved."
        if config_changed:
            try:
                sync_result = runtime_profile_sync_queue_service.enqueue_profile_to_bound_agents(db, updated, reason="runtime_profile_panel_save")
                status_message = (
                    "Runtime profile saved. "
                    f"Sync queued for {sync_result.get('queued_agent_count', 0)} bound agents. "
                    "Running agents will apply soon; starting agents will apply after they become running."
                )
            except Exception:
                db.rollback()
                logger.exception("runtime profile fan-out sync failed after profile save profile_id=%s", updated.id)
                status_type = "error"
                status_message = "Runtime profile saved, but sync enqueue failed."

        response = templates.TemplateResponse(
            "partials/runtime_profile_panel.html",
            _runtime_profile_panel_context(request, updated, profile_repo, status_type=status_type, status_message=status_message),
        )
        response.headers["HX-Trigger"] = "runtimeProfilesChanged"
        return response
    finally:
        db.close()


@router.post("/app/chat/send")
async def app_chat_send(request: Request):
    user = _current_user_from_cookie(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    form = await request.form()
    agent_id = (form.get("agent_id") or "").strip()
    message = (form.get("message") or "").strip()
    session_id = (form.get("session_id") or "").strip() or None
    attachments_str = (form.get("attachments") or "").strip()

    # Parse attachments from JSON
    attachments = []
    if attachments_str:
        try:
            parsed = json.loads(attachments_str)
            if isinstance(parsed, list):
                attachments = parsed
        except json.JSONDecodeError:
            pass  # Invalid JSON, ignore attachments

    if not agent_id:
        raise HTTPException(status_code=400, detail="Agent not selected")
    if not message and not attachments:
        raise HTTPException(status_code=400, detail="Message or attachment required")
    request_message = message or "[attachment]"
    display_message = message or "📎 Attachment"

    db = SessionLocal()
    try:
        agent = AgentRepository(db).get_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not _can_access(agent, user):
            raise HTTPException(status_code=403, detail="Forbidden")
        if agent.status != "running":
            raise HTTPException(status_code=409, detail="Agent not running")

        metadata = runtime_execution_context_service.build_runtime_metadata(db, agent)
        payload = {
            "message": request_message,
            "metadata": metadata,
        }
        if session_id:
            payload["session_id"] = session_id
        if attachments:
            payload["attachments"] = attachments

        status_code, content, _ = await proxy_service.forward(
            agent=agent,
            method="POST",
            subpath="api/chat",
            query_items=[],
            body=json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json"},
            extra_headers=_portal_extra_headers(user, agent),
        )

        if status_code >= 400:
            raise HTTPException(status_code=502, detail=_normalize_runtime_error_detail(content))

        data = json.loads(content.decode("utf-8"))

        normalized_payload = normalize_assistant_chat_payload(
            data,
            fallback_session_id=session_id or "",
        )

        return templates.TemplateResponse(
            "partials/chat_response.html",
            {
                "request": request,
                "user_message": display_message,
                "assistant_message": normalized_payload["assistant_message"],
                "session_id": normalized_payload["session_id"],
                "agent_name": agent.name if agent else "Assistant",
                "user_message_id": normalized_payload["user_message_id"],
                "events": normalized_payload["events"],
                "display_blocks": normalized_payload["display_blocks"],
                "timestamp": datetime.now().strftime("%H:%M"),
            },
        )
    finally:
        db.close()
