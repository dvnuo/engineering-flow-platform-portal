import json
from typing import Any


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _safe_json_dict(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_json_list(raw: Any) -> list:
    if isinstance(raw, list):
        return raw
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def _build_budget_from_metadata(metadata_dict: dict) -> dict:
    if not isinstance(metadata_dict, dict):
        return {}
    mapping = {
        "context_usage_percent": "usage_percent",
        "context_estimated_tokens": "estimated_tokens",
        "context_window_tokens": "context_window_tokens",
        "context_next_compaction_action": "next_compaction_action",
        "context_next_pruning_policy": "next_pruning_policy",
        "context_tokens_until_soft_threshold": "tokens_until_soft_threshold",
        "context_tokens_until_hard_threshold": "tokens_until_hard_threshold",
        "context_prompt_budget_tokens": "prompt_budget_tokens",
        "context_request_estimated_tokens": "request_estimated_tokens",
        "context_reserved_output_tokens": "reserved_output_tokens",
        "context_safety_margin_tokens": "safety_margin_tokens",
        "context_projection_chars_saved": "projection_chars_saved",
        "context_projected_old_assistant_messages": "projected_old_assistant_messages",
        "context_projected_old_tool_messages": "projected_old_tool_messages",
        "context_context_blob_refs_created": "context_blob_refs_created",
    }
    budget = {}
    for source_key, target_key in mapping.items():
        value = metadata_dict.get(source_key)
        if value is None:
            continue
        budget[target_key] = value
    return budget


def _has_meaningful_context_state(value: Any) -> bool:
    if not isinstance(value, dict) or not value:
        return False

    if _has_meaningful_context_contents(value):
        return True

    if str(value.get("compaction_level") or "").strip():
        return True

    budget = value.get("budget")
    if isinstance(budget, dict):
        for item in budget.values():
            if item is None or item == "":
                continue
            if isinstance(item, list) and len(item) == 0:
                continue
            if isinstance(item, dict) and len(item) == 0:
                continue
            return True

    return False


def _has_meaningful_context_contents(value: Any) -> bool:
    if not isinstance(value, dict) or not value:
        return False

    scalar_keys = (
        "objective",
        "summary",
        "current_state",
        "next_step",
        "recovery_context_message",
    )
    if any(str(value.get(key) or "").strip() for key in scalar_keys):
        return True

    list_keys = ("constraints", "decisions", "open_loops")
    for key in list_keys:
        items = value.get(key)
        if isinstance(items, list) and any(str(item or "").strip() for item in items):
            return True

    return False


def _pick_context_with_source(chatlog: dict, metadata: dict, events: list, metadata_dict: dict) -> tuple[dict, str]:
    candidates: list[tuple[str, Any]] = [
        ("chatlog", chatlog.get("context_state")),
        ("metadata", metadata.get("context_state")),
    ]
    for event in reversed(events):
        event_type = event.get("type") or event.get("event_type")
        if event_type != "context_snapshot":
            continue
        data = {
            **_as_dict(event.get("data")),
            **_as_dict(event.get("detail_payload")),
        }
        candidates.append(("event", data.get("context_state")))
    candidates.append(("metadata_record", metadata_dict.get("context_state")))
    preview = {}
    if metadata_dict.get("context_objective_preview"):
        preview["objective"] = metadata_dict.get("context_objective_preview")
    if metadata_dict.get("context_summary_preview"):
        preview["summary"] = metadata_dict.get("context_summary_preview")
    if metadata_dict.get("context_next_step_preview"):
        preview["next_step"] = metadata_dict.get("context_next_step_preview")
    budget = _build_budget_from_metadata(metadata_dict)
    if budget:
        preview["budget"] = budget
    candidates.append(("metadata_preview", preview))

    for source, candidate in candidates:
        if _has_meaningful_context_contents(candidate):
            return _as_dict(candidate), source
    for source, candidate in candidates:
        if _has_meaningful_context_state(candidate):
            return _as_dict(candidate), source
    return {}, "none"


def _pick_context_budget(chatlog: dict, metadata: dict, events: list, metadata_dict: dict, selected_context: dict) -> dict:
    selected_budget = _as_dict(selected_context.get("budget"))
    if selected_budget:
        return selected_budget

    candidates = [
        chatlog.get("context_state"),
        metadata.get("context_state"),
        metadata_dict.get("context_state"),
    ]
    for event in reversed(events):
        data = {
            **_as_dict(event.get("data")),
            **_as_dict(event.get("detail_payload")),
        }
        candidates.append(data.get("context_state"))
        event_budget = _as_dict(data.get("budget"))
        if event_budget:
            return event_budget

    for candidate in candidates:
        budget = _as_dict(_as_dict(candidate).get("budget"))
        if budget:
            return budget

    return _build_budget_from_metadata(metadata_dict)


def _pick_context(chatlog: dict, metadata: dict, events: list, metadata_dict: dict) -> dict:
    return _pick_context_with_source(chatlog, metadata, events, metadata_dict)[0]


def _merge_events(chatlog: dict, metadata_events: list, llm_debug: dict) -> list:
    merged = []
    seen = set()
    for source in (
        _as_list(chatlog.get("events")),
        _as_list(chatlog.get("runtime_events")),
        _as_list(chatlog.get("thinking_events")),
        _as_list(llm_debug.get("thinking_events")),
        metadata_events,
    ):
        for event in source:
            if not isinstance(event, dict):
                continue
            event_type = event.get("type") or event.get("event_type") or "event"
            data = {
                **_as_dict(event.get("data")),
                **_as_dict(event.get("detail_payload")),
            }
            context_state = _as_dict(data.get("context_state"))
            message = (
                data.get("message")
                or event.get("summary")
                or context_state.get("summary")
                or context_state.get("next_step")
                or event_type
            )
            request_id = event.get("request_id") or data.get("request_id") or ""
            session_id = event.get("session_id") or data.get("session_id") or ""
            agent_id = event.get("agent_id") or data.get("agent_id") or ""
            normalized = {
                "type": event_type,
                "event_type": event_type,
                "title": str(event.get("title") or event.get("summary") or event_type).replace("_", " ").title(),
                "message": message,
                "data": data,
                "ts": event.get("ts") or event.get("created_at") or "",
                "state": event.get("state") or data.get("state") or "",
                "request_id": request_id,
                "session_id": session_id,
                "agent_id": agent_id,
            }
            key = f"{normalized['type']}|{request_id}|{session_id}|{normalized['ts']}|{json.dumps(data, sort_keys=True, default=str)}"
            if key in seen:
                continue
            seen.add(key)
            merged.append(normalized)
    return merged


def _extract_active_skill(chatlog: dict, metadata: dict, metadata_dict: dict, events: list) -> dict:
    skill_session = _as_dict(chatlog.get("skill_session")) or _as_dict(metadata.get("active_skill_session"))
    skill = {
        "name": skill_session.get("skill_name") or skill_session.get("skill") or metadata.get("active_skill_name") or metadata_dict.get("active_skill_name"),
        "status": skill_session.get("status") or metadata.get("active_skill_status") or metadata_dict.get("active_skill_status"),
        "goal": skill_session.get("goal") or skill_session.get("original_user_request") or metadata.get("active_skill_goal") or metadata_dict.get("active_skill_goal"),
        "turn_count": skill_session.get("turn_count") if skill_session.get("turn_count") is not None else metadata.get("active_skill_turn_count") if metadata.get("active_skill_turn_count") is not None else metadata_dict.get("active_skill_turn_count"),
        "reason": skill_session.get("activation_reason") or metadata.get("active_skill_activation_reason") or metadata_dict.get("active_skill_activation_reason"),
        "hash": skill_session.get("skill_hash") or metadata.get("active_skill_hash") or metadata_dict.get("active_skill_hash"),
        "allowed_tools": skill_session.get("allowed_tools") or skill_session.get("task_tools") or metadata_dict.get("active_skill_allowed_tools") or [],
    }
    if not skill["name"]:
        for event in reversed(events):
            if event.get("type") == "skill_contract_active":
                data = _as_dict(event.get("data"))
                skill["name"] = data.get("skill") or data.get("skill_name")
                skill["reason"] = skill["reason"] or data.get("reason")
                skill["goal"] = skill["goal"] or data.get("goal")
                skill["turn_count"] = skill["turn_count"] if skill["turn_count"] is not None else data.get("turn_count")
                if isinstance(data.get("allowed_tools"), list):
                    skill["allowed_tools"] = data.get("allowed_tools")
                break
    return skill


def build_thinking_process_view(chatlog: dict | None, metadata_record=None) -> dict:
    chatlog = _as_dict(chatlog)
    metadata = _as_dict(chatlog.get("metadata"))
    llm_debug = _as_dict(chatlog.get("llm_debug"))

    metadata_dict = _safe_json_dict(getattr(metadata_record, "metadata_json", None))
    metadata_events = _safe_json_list(getattr(metadata_record, "runtime_events_json", None))

    events = _merge_events(chatlog, metadata_events, llm_debug)
    context_state, context_source = _pick_context_with_source(chatlog, metadata, events, metadata_dict)
    budget = _pick_context_budget(chatlog, metadata, events, metadata_dict, context_state)

    llm_request = _as_dict(llm_debug.get("llm_request"))
    llm_request_request = _as_dict(llm_request.get("request"))
    llm_response = _as_dict(llm_request.get("response"))

    status = str(chatlog.get("status") or metadata.get("status") or getattr(metadata_record, "latest_event_state", "") or "unknown").lower()
    if status not in {"running", "success", "error", "unknown", "failed", "completed"}:
        status = "unknown"

    active_skill = _extract_active_skill(chatlog, metadata, metadata_dict, events)
    fallback = {
        "latest_event_type": getattr(metadata_record, "latest_event_type", "") if metadata_record else "",
        "latest_event_state": getattr(metadata_record, "latest_event_state", "") if metadata_record else "",
        "last_execution_id": getattr(metadata_record, "last_execution_id", "") if metadata_record else "",
    }
    has_context = _has_meaningful_context_contents(context_state)
    if not has_context and budget:
        context_source_label = "Context window only — no context contents captured"
    else:
        context_source_label = {
            "chatlog": "Final Context Snapshot",
            "metadata": "Final Context Snapshot",
            "event": "Final Context Snapshot",
            "metadata_record": "Persisted Context Snapshot",
            "metadata_preview": "Persisted Context Preview",
            "none": "No context snapshot captured",
        }.get(context_source, "Final Context Snapshot")
    has_data = bool(
        events
        or budget
        or has_context
        or active_skill.get("name")
        or fallback.get("latest_event_type")
        or fallback.get("latest_event_state")
        or fallback.get("last_execution_id")
    )

    view = {
        "session_id": chatlog.get("session_id") or getattr(metadata_record, "session_id", "") or "",
        "timestamp": chatlog.get("timestamp") or metadata.get("timestamp") or metadata_dict.get("timestamp") or "",
        "status": status,
        "request_id": chatlog.get("request_id") or metadata.get("request_id") or getattr(metadata_record, "last_execution_id", "") or "",
        "model": llm_request_request.get("model") or metadata_dict.get("model") or "",
        "active_skill": active_skill,
        "context_source": context_source,
        "context_source_label": context_source_label,
        "has_context": has_context,
        "context": {
            "objective": context_state.get("objective") or metadata_dict.get("context_objective_preview") or "",
            "summary": context_state.get("summary") or metadata_dict.get("context_summary_preview") or "",
            "current_state": context_state.get("current_state") or "",
            "next_step": context_state.get("next_step") or metadata_dict.get("context_next_step_preview") or "",
            "constraints": _as_list(context_state.get("constraints")),
            "decisions": _as_list(context_state.get("decisions")),
            "open_loops": _as_list(context_state.get("open_loops")),
            "recovery_context_message": context_state.get("recovery_context_message") or "",
        },
        "budget": {
            "usage_percent": budget.get("usage_percent"),
            "prepared_usage_percent": budget.get("prepared_usage_percent"),
            "estimated_tokens": budget.get("estimated_tokens"),
            "prepared_tokens": budget.get("prepared_tokens"),
            "context_window_tokens": budget.get("context_window_tokens"),
            "prompt_budget_tokens": budget.get("prompt_budget_tokens") if budget.get("prompt_budget_tokens") is not None else budget.get("max_prompt_tokens"),
            "request_estimated_tokens": budget.get("request_estimated_tokens"),
            "reserved_output_tokens": budget.get("reserved_output_tokens"),
            "safety_margin_tokens": budget.get("safety_margin_tokens"),
            "projection_chars_saved": budget.get("projection_chars_saved"),
            "projected_old_assistant_messages": budget.get("projected_old_assistant_messages"),
            "projected_old_tool_messages": budget.get("projected_old_tool_messages"),
            "context_blob_refs_created": budget.get("context_blob_refs_created"),
            "soft_threshold_percent": budget.get("soft_threshold_percent"),
            "hard_threshold_percent": budget.get("hard_threshold_percent"),
            "tokens_until_soft_threshold": budget.get("tokens_until_soft_threshold"),
            "tokens_until_hard_threshold": budget.get("tokens_until_hard_threshold"),
            "next_compaction_action": budget.get("next_compaction_action"),
            "next_pruning_policy": budget.get("next_pruning_policy"),
        } if budget else {},
        "events": events,
        "debug": {
            "system_prompt": llm_request_request.get("instructions") or "",
            "request_items": _as_list(llm_request_request.get("input")),
            "available_tools": _as_list(llm_request_request.get("tools")),
            "final_response": llm_debug.get("final_response") or llm_response.get("content") or "",
            "usage": _as_dict(llm_response.get("usage")),
        },
        "warning": None,
        "fallback": fallback,
        "has_data": has_data,
    }
    return view
