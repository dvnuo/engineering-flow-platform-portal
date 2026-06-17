from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadCronError, croniter


DEFAULT_INTERVAL_SECONDS = 60
DEFAULT_TIMEZONE = "UTC"
SUPPORTED_SCHEDULE_TYPES = {"interval", "cron"}
SUPPORTED_MISFIRE_POLICIES = {"fire_once", "skip"}
SUPPORTED_OVERLAP_POLICIES = {"skip_if_running", "allow"}
WEEKDAY_LABELS = {
    "0": "Sunday",
    "7": "Sunday",
    "SUN": "Sunday",
    "1": "Monday",
    "MON": "Monday",
    "2": "Tuesday",
    "TUE": "Tuesday",
    "3": "Wednesday",
    "WED": "Wednesday",
    "4": "Thursday",
    "THU": "Thursday",
    "5": "Friday",
    "FRI": "Friday",
    "6": "Saturday",
    "SAT": "Saturday",
}


@dataclass(frozen=True)
class SchedulePreview:
    valid: bool
    schedule: dict[str, Any]
    summary: str
    next_run_at: datetime | None
    next_run_local: str | None
    timezone: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        next_run_at = None
        if self.next_run_at:
            next_run_at = _aware_utc(self.next_run_at).isoformat().replace("+00:00", "Z")
        return {
            "valid": self.valid,
            "schedule": self.schedule,
            "summary": self.summary,
            "next_run_at": next_run_at,
            "next_run_local": self.next_run_local,
            "timezone": self.timezone,
            "error": self.error,
        }


def utc_now_naive() -> datetime:
    return datetime.utcnow()


def _aware_utc(value: datetime | None) -> datetime:
    base = value or utc_now_naive()
    if base.tzinfo is None:
        return base.replace(tzinfo=timezone.utc)
    return base.astimezone(timezone.utc)


def _naive_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _positive_interval(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("interval_seconds must be an integer") from exc
    if parsed <= 0:
        raise ValueError("interval_seconds must be greater than 0")
    return parsed


def _normalize_timezone(value: Any) -> str:
    timezone_name = _clean_text(value) or DEFAULT_TIMEZONE
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unsupported timezone: {timezone_name}") from exc
    return timezone_name


def _normalize_cron_expression(value: Any) -> str:
    expression = " ".join(_clean_text(value).split())
    if not expression:
        raise ValueError("cron expression is required")
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("cron expression must use 5 fields: minute hour day month weekday")
    if any("?" in field for field in fields):
        raise ValueError("cron expression must use standard 5-field syntax; '?' is not supported")
    try:
        is_valid = croniter.is_valid(expression)
    except Exception as exc:
        raise ValueError("invalid cron expression") from exc
    if not is_valid:
        raise ValueError("invalid cron expression")
    return expression


def normalize_delegation_schedule(raw: Any, *, interval_seconds: int | None = None) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    schedule_type = _clean_text(data.get("type")).lower()
    if not schedule_type and _clean_text(data.get("expression")):
        schedule_type = "cron"
    if not schedule_type:
        schedule_type = "interval"
    if schedule_type not in SUPPORTED_SCHEDULE_TYPES:
        raise ValueError("schedule.type must be one of: cron, interval")

    if schedule_type == "cron":
        expression = _normalize_cron_expression(data.get("expression"))
        timezone_name = _normalize_timezone(data.get("timezone"))
        misfire_policy = _clean_text(data.get("misfire_policy")).lower() or "fire_once"
        overlap_policy = _clean_text(data.get("overlap_policy")).lower() or "skip_if_running"
        if misfire_policy not in SUPPORTED_MISFIRE_POLICIES:
            raise ValueError("schedule.misfire_policy must be one of: fire_once, skip")
        if overlap_policy not in SUPPORTED_OVERLAP_POLICIES:
            raise ValueError("schedule.overlap_policy must be one of: skip_if_running, allow")
        return {
            "type": "cron",
            "expression": expression,
            "timezone": timezone_name,
            "misfire_policy": misfire_policy,
            "catchup": bool(data.get("catchup", False)),
            "overlap_policy": overlap_policy,
        }

    interval = _positive_interval(data.get("interval_seconds", interval_seconds or DEFAULT_INTERVAL_SECONDS))
    return {"type": "interval", "interval_seconds": interval}


def delegation_schedule_interval_seconds(schedule: dict[str, Any] | None) -> int:
    data = schedule if isinstance(schedule, dict) else {}
    if data.get("type") == "cron":
        return DEFAULT_INTERVAL_SECONDS
    return _positive_interval(data.get("interval_seconds", DEFAULT_INTERVAL_SECONDS))


def compute_next_run_at(schedule: dict[str, Any] | None, *, after: datetime | None = None) -> datetime:
    normalized = normalize_delegation_schedule(schedule or {})
    reference_utc = _aware_utc(after)
    if normalized["type"] == "interval":
        from datetime import timedelta

        return _naive_utc(reference_utc + timedelta(seconds=int(normalized["interval_seconds"])))

    timezone_name = normalized["timezone"]
    local_reference = reference_utc.astimezone(ZoneInfo(timezone_name))
    try:
        next_local = croniter(normalized["expression"], local_reference).get_next(datetime)
    except CroniterBadCronError as exc:
        raise ValueError("invalid cron expression") from exc
    if next_local.tzinfo is None:
        next_local = next_local.replace(tzinfo=ZoneInfo(timezone_name))
    return _naive_utc(next_local)


def build_timer_source_item(rule, schedule: dict[str, Any], *, scheduled_for: datetime | None = None) -> dict[str, Any]:
    due_at = _aware_utc(scheduled_for or getattr(rule, "next_run_at", None) or utc_now_naive())
    due_iso = due_at.isoformat().replace("+00:00", "Z")
    task_config = {}
    try:
        import json

        parsed = json.loads(getattr(rule, "task_config_json", "{}") or "{}")
        if isinstance(parsed, dict):
            task_config = parsed
    except Exception:
        task_config = {}
    task_prompt = _clean_text(task_config.get("task_prompt")) or _clean_text(task_config.get("prompt"))
    if not task_prompt:
        task_prompt = "Run this scheduled delegation task."
    source_payload = {
        "scheduled_for": due_iso,
        "schedule": schedule,
    }
    if schedule.get("type") == "cron":
        source_payload["timezone"] = schedule.get("timezone")
        source_payload["cron_expression"] = schedule.get("expression")
    return {
        "source": "timer",
        "provider": "timer",
        "dedupe_key": f"timer:{due_iso}",
        "version_key": due_iso,
        "source_url": "",
        "task_content": task_prompt,
        "represented_identity": "Portal timer",
        "source_payload": source_payload,
        "reply_target": {},
    }


def summarize_delegation_schedule(schedule: dict[str, Any] | None) -> str:
    normalized = normalize_delegation_schedule(schedule or {})
    if normalized["type"] == "interval":
        seconds = int(normalized["interval_seconds"])
        if seconds % 3600 == 0:
            return f"Every {seconds // 3600} hour" + ("" if seconds == 3600 else "s")
        if seconds % 60 == 0:
            return f"Every {seconds // 60} minute" + ("" if seconds == 60 else "s")
        return f"Every {seconds} second" + ("" if seconds == 1 else "s")

    expression = normalized["expression"]
    minute, hour, day, month, weekday = expression.split()
    time_label = _time_summary(hour, minute)
    if day == "*" and month == "*" and weekday == "*":
        return f"Every day at {time_label} ({normalized['timezone']})"
    if day == "*" and month == "*" and weekday.upper() in {"1-5", "MON-FRI"}:
        return f"Every weekday at {time_label} ({normalized['timezone']})"
    if day == "*" and month == "*" and _simple_weekday_summary(weekday):
        return f"Every {_simple_weekday_summary(weekday)} at {time_label} ({normalized['timezone']})"
    return f"{expression} ({normalized['timezone']})"


def _time_summary(hour: str, minute: str) -> str:
    try:
        return f"{int(hour):02d}:{int(minute):02d}"
    except Exception:
        return f"{hour}:{minute}"


def _simple_weekday_summary(value: str) -> str:
    parts = [part.strip().upper() for part in value.split(",") if part.strip()]
    if not parts:
        return ""
    labels = [WEEKDAY_LABELS.get(part) for part in parts]
    if any(label is None for label in labels):
        return ""
    return ", ".join(label for label in labels if label)


def preview_delegation_schedule(raw: Any, *, after: datetime | None = None) -> SchedulePreview:
    try:
        normalized = normalize_delegation_schedule(raw or {})
        next_run = compute_next_run_at(normalized, after=after)
        timezone_name = normalized.get("timezone") or DEFAULT_TIMEZONE
        next_local = _aware_utc(next_run).astimezone(ZoneInfo(timezone_name))
        return SchedulePreview(
            valid=True,
            schedule=normalized,
            summary=summarize_delegation_schedule(normalized),
            next_run_at=next_run,
            next_run_local=next_local.strftime("%Y-%m-%d %H:%M"),
            timezone=timezone_name,
            error=None,
        )
    except Exception as exc:
        timezone_name = DEFAULT_TIMEZONE
        if isinstance(raw, dict) and _clean_text(raw.get("timezone")):
            timezone_name = _clean_text(raw.get("timezone"))
        return SchedulePreview(
            valid=False,
            schedule={},
            summary="",
            next_run_at=None,
            next_run_local=None,
            timezone=timezone_name,
            error=str(exc),
        )
