from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.repositories.agent_repo import AgentRepository
from app.repositories.runtime_profile_repo import RuntimeProfileRepository


GITHUB_DELEGATION_SOURCES = {"github_pr_review", "github_pr_mention"}
JIRA_DELEGATION_SOURCES = {"jira_assignee", "jira_mention"}
TIMER_DELEGATION_SOURCES = {"timer"}
DELEGATION_SOURCE_PROVIDER = {
    "github_pr_review": "github",
    "github_pr_mention": "github",
    "jira_assignee": "jira",
    "jira_mention": "jira",
    "timer": "timer",
}


@dataclass
class DelegationSourcePreview:
    provider: str
    source: str
    runtime_profile_id: str | None = None
    runtime_profile_name: str | None = None
    account_summary: str = ""
    condition_summary: str = ""
    status: str = "ok"
    warning: str | None = None
    options: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "source": self.source,
            "runtime_profile_id": self.runtime_profile_id,
            "runtime_profile_name": self.runtime_profile_name,
            "account_summary": self.account_summary,
            "condition_summary": self.condition_summary,
            "status": self.status,
            "warning": self.warning,
            "options": self.options,
        }


def parse_json_object(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def json_dumps_object(data: dict[str, Any] | None) -> str:
    return json.dumps(data or {})


def provider_for_delegation_source(source: str | None) -> str:
    return DELEGATION_SOURCE_PROVIDER.get(str(source or "").strip(), "")


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _clean_lower(value: Any) -> str:
    return _clean_text(value).casefold()


def _unique_list(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        cleaned = _clean_text(value)
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return _unique_list([_clean_text(item) for item in value])
    if isinstance(value, str):
        chunks = value.replace("\n", ",").split(",")
        return _unique_list([chunk.strip() for chunk in chunks])
    return []


def _bool_or_none(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        cleaned = value.strip().casefold()
        if cleaned in {"1", "true", "yes", "y", "on"}:
            return True
        if cleaned in {"0", "false", "no", "n", "off"}:
            return False
    return None


def normalize_github_repository(value: Any) -> str:
    cleaned = _clean_text(value)
    if not cleaned:
        return ""
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        parsed = urlparse(cleaned)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2:
            cleaned = f"{parts[0]}/{parts[1]}"
    cleaned = cleaned.removeprefix("github.com/").strip("/")
    parts = [part for part in cleaned.split("/") if part]
    if len(parts) < 2:
        return ""
    return f"{parts[0]}/{parts[1]}"


def normalize_delegation_source_scope(source: str | None, raw: Any) -> dict[str, Any]:
    provider = provider_for_delegation_source(source)
    data = raw if isinstance(raw, dict) else {}
    if provider == "jira":
        jira_instance = _clean_text(
            data.get("jira_instance")
            or data.get("jira_instance_name")
            or data.get("jira_base_url")
            or data.get("base_url")
        )
        return {"jira_instance": jira_instance} if jira_instance else {}
    return {}


def normalize_delegation_source_conditions(source: str | None, raw: Any) -> dict[str, Any]:
    provider = provider_for_delegation_source(source)
    data = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    if provider == "github":
        repository = normalize_github_repository(data.get("repository") or data.get("repo"))
        if repository:
            out["repository"] = repository
        base_branch = _clean_text(data.get("base_branch") or data.get("base"))
        if base_branch:
            out["base_branch"] = base_branch
        labels_include = normalize_string_list(data.get("labels_include") or data.get("label_include") or data.get("labels"))
        labels_exclude = normalize_string_list(data.get("labels_exclude") or data.get("label_exclude"))
        if labels_include:
            out["labels_include"] = labels_include
        if labels_exclude:
            out["labels_exclude"] = labels_exclude
        authors_include = normalize_string_list(data.get("authors_include") or data.get("author_include") or data.get("author"))
        authors_exclude = normalize_string_list(data.get("authors_exclude") or data.get("author_exclude"))
        if authors_include:
            out["authors_include"] = authors_include
        if authors_exclude:
            out["authors_exclude"] = authors_exclude
        include_drafts = _bool_or_none(data.get("include_drafts"))
        if include_drafts is not None:
            out["include_drafts"] = include_drafts
        return out
    if provider == "jira":
        project_key = _clean_text(data.get("project_key") or data.get("project")).upper()
        if project_key:
            out["project_key"] = project_key
        issue_type = _clean_text(data.get("issue_type") or data.get("type"))
        if issue_type:
            out["issue_type"] = issue_type
        status_include = normalize_string_list(data.get("status_include") or data.get("statuses_include") or data.get("status"))
        status_exclude = normalize_string_list(data.get("status_exclude") or data.get("statuses_exclude"))
        if status_include:
            out["status_include"] = status_include
        if status_exclude:
            out["status_exclude"] = status_exclude
        priority = _clean_text(data.get("priority"))
        if priority:
            out["priority"] = priority
        labels_include = normalize_string_list(data.get("labels_include") or data.get("label_include") or data.get("labels"))
        labels_exclude = normalize_string_list(data.get("labels_exclude") or data.get("label_exclude"))
        if labels_include:
            out["labels_include"] = labels_include
        if labels_exclude:
            out["labels_exclude"] = labels_exclude
        return out
    return {}


def _host_label(url: str) -> str:
    cleaned = _clean_text(url).rstrip("/")
    if not cleaned:
        return ""
    parsed = urlparse(cleaned if "://" in cleaned else f"https://{cleaned}")
    return parsed.netloc or parsed.path


def _load_agent_runtime_profile_config(db: Session, agent_id: str):
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent:
        return None, None, {}, "Target agent was deleted"
    runtime_profile_id = getattr(agent, "runtime_profile_id", None)
    if not runtime_profile_id:
        return agent, None, {}, "Selected agent does not have a runtime profile"
    profile = RuntimeProfileRepository(db).get_by_id(runtime_profile_id)
    if not profile:
        return agent, None, {}, "Selected agent runtime profile is missing"
    try:
        config = json.loads(profile.config_json or "{}")
    except json.JSONDecodeError:
        return agent, profile, {}, "Selected agent runtime profile config is invalid"
    return agent, profile, config if isinstance(config, dict) else {}, None


def _jira_instance_has_auth(instance: dict[str, Any]) -> bool:
    token = _clean_text(instance.get("token"))
    username = _clean_text(instance.get("username"))
    password = _clean_text(instance.get("password"))
    return bool((username and token) or token or (username and password))


def jira_instance_value(instance: dict[str, Any]) -> str:
    return _clean_text(instance.get("name")) or _clean_text(instance.get("url"))


def jira_instance_matches(instance: dict[str, Any], selector: str | None) -> bool:
    cleaned = _clean_text(selector).casefold()
    if not cleaned:
        return False
    candidates = {
        _clean_text(instance.get("name")).casefold(),
        _clean_text(instance.get("url")).rstrip("/").casefold(),
        _host_label(_clean_text(instance.get("url"))).casefold(),
    }
    return cleaned.rstrip("/") in candidates


def select_jira_instance(instances: list[dict[str, Any]], selector: str | None = None) -> dict[str, Any] | None:
    usable = [
        item for item in instances
        if isinstance(item, dict)
        and item.get("enabled") is not False
        and _clean_text(item.get("url"))
        and _jira_instance_has_auth(item)
    ]
    if selector:
        matched = next((item for item in usable if jira_instance_matches(item, selector)), None)
        if matched:
            return matched
        return None
    return usable[0] if usable else None


def jira_instance_options_from_config(config: dict[str, Any]) -> list[dict[str, str]]:
    jira = config.get("jira") if isinstance(config, dict) else None
    instances = jira.get("instances") if isinstance(jira, dict) else []
    options: list[dict[str, str]] = []
    for item in instances if isinstance(instances, list) else []:
        if not isinstance(item, dict) or item.get("enabled") is False or not _clean_text(item.get("url")):
            continue
        value = jira_instance_value(item)
        host = _host_label(_clean_text(item.get("url")))
        name = _clean_text(item.get("name"))
        username = _clean_text(item.get("username"))
        label = name or host
        detail_parts = [part for part in (host if name else "", username) if part]
        if detail_parts:
            label = f"{label} ({', '.join(detail_parts)})"
        options.append({"value": value, "label": label})
    return options


def build_delegation_condition_summary(source: str | None, scope: dict[str, Any] | None, conditions: dict[str, Any] | None) -> str:
    provider = provider_for_delegation_source(source)
    scope = scope or {}
    conditions = conditions or {}
    parts: list[str] = []
    if provider == "github":
        if conditions.get("repository"):
            parts.append(f"repo {conditions['repository']}")
        if conditions.get("base_branch"):
            parts.append(f"base {conditions['base_branch']}")
        if conditions.get("labels_include"):
            parts.append("labels +" + ", ".join(conditions["labels_include"]))
        if conditions.get("labels_exclude"):
            parts.append("labels -" + ", ".join(conditions["labels_exclude"]))
        if conditions.get("authors_include"):
            parts.append("authors " + ", ".join(conditions["authors_include"]))
        if conditions.get("authors_exclude"):
            parts.append("exclude authors " + ", ".join(conditions["authors_exclude"]))
        if conditions.get("include_drafts") is False:
            parts.append("no drafts")
    elif provider == "jira":
        if scope.get("jira_instance"):
            parts.append(f"instance {scope['jira_instance']}")
        if conditions.get("project_key"):
            parts.append(f"project {conditions['project_key']}")
        if conditions.get("issue_type"):
            parts.append(f"type {conditions['issue_type']}")
        if conditions.get("status_include"):
            parts.append("status " + ", ".join(conditions["status_include"]))
        if conditions.get("status_exclude"):
            parts.append("exclude status " + ", ".join(conditions["status_exclude"]))
        if conditions.get("priority"):
            parts.append(f"priority {conditions['priority']}")
        if conditions.get("labels_include"):
            parts.append("labels +" + ", ".join(conditions["labels_include"]))
        if conditions.get("labels_exclude"):
            parts.append("labels -" + ", ".join(conditions["labels_exclude"]))
    elif provider == "timer":
        return "Scheduled by Portal timer"
    return " · ".join(parts) if parts else "All runtime profile source items"


def build_delegation_source_preview(
    db: Session,
    *,
    agent_id: str,
    source: str,
    source_scope: dict[str, Any] | None = None,
    source_conditions: dict[str, Any] | None = None,
) -> DelegationSourcePreview:
    provider = provider_for_delegation_source(source)
    scope = normalize_delegation_source_scope(source, source_scope or {})
    conditions = normalize_delegation_source_conditions(source, source_conditions or {})
    condition_summary = build_delegation_condition_summary(source, scope, conditions)
    if not provider:
        return DelegationSourcePreview(
            provider="",
            source=source,
            account_summary="Unsupported source",
            condition_summary=condition_summary,
            status="missing",
            warning="Unsupported delegation source",
        )
    if provider == "timer":
        return DelegationSourcePreview(
            provider=provider,
            source=source,
            account_summary="Portal timer",
            condition_summary="Scheduled by Portal timer",
            status="ok",
            warning=None,
        )

    _agent, profile, config, warning = _load_agent_runtime_profile_config(db, agent_id)
    if warning:
        return DelegationSourcePreview(
            provider=provider,
            source=source,
            runtime_profile_id=getattr(profile, "id", None),
            runtime_profile_name=getattr(profile, "name", None),
            account_summary=f"{provider.title()} from runtime profile",
            condition_summary=condition_summary,
            status="missing",
            warning=warning,
        )

    profile_name = _clean_text(getattr(profile, "name", None)) or "runtime profile"
    if provider == "github":
        github = config.get("github") if isinstance(config, dict) else None
        base_url = _clean_text((github or {}).get("base_url")) or "https://api.github.com"
        host = _host_label(base_url) or base_url
        warning = None
        if not isinstance(github, dict) or not github.get("enabled"):
            warning = "GitHub is not enabled for selected agent"
        elif not _clean_text(github.get("api_token")):
            warning = "GitHub api_token is missing for selected agent"
        return DelegationSourcePreview(
            provider=provider,
            source=source,
            runtime_profile_id=getattr(profile, "id", None),
            runtime_profile_name=getattr(profile, "name", None),
            account_summary=f"GitHub via {profile_name} ({host})",
            condition_summary=condition_summary,
            status="missing" if warning else "ok",
            warning=warning,
        )

    jira = config.get("jira") if isinstance(config, dict) else None
    instances = jira.get("instances") if isinstance(jira, dict) else []
    options = {"jira_instances": jira_instance_options_from_config(config)}
    warning = None
    selected = None
    if not isinstance(jira, dict) or not jira.get("enabled"):
        warning = "Jira is not enabled for selected agent"
    elif not isinstance(instances, list):
        warning = "No usable Jira instance found for selected agent"
    else:
        selected = select_jira_instance(instances, scope.get("jira_instance"))
        if scope.get("jira_instance") and not selected:
            warning = "Selected Jira instance was not found in the agent runtime profile"
        elif not selected:
            warning = "No usable Jira instance found for selected agent"
    if selected:
        instance_name = _clean_text(selected.get("name"))
        host = _host_label(_clean_text(selected.get("url")))
        username = _clean_text(selected.get("username"))
        subject = instance_name or host or "Jira"
        detail = ", ".join([part for part in (host if instance_name else "", username) if part])
        suffix = f" ({detail})" if detail else ""
        account_summary = f"Jira {subject}{suffix} via {profile_name}"
    else:
        account_summary = f"Jira via {profile_name}"
    return DelegationSourcePreview(
        provider=provider,
        source=source,
        runtime_profile_id=getattr(profile, "id", None),
        runtime_profile_name=getattr(profile, "name", None),
        account_summary=account_summary,
        condition_summary=condition_summary,
        status="missing" if warning else "ok",
        warning=warning,
        options=options,
    )


def _payload_for_item(item: dict[str, Any]) -> dict[str, Any]:
    payload = item.get("source_payload")
    return payload if isinstance(payload, dict) else {}


def _github_pull_payload(item: dict[str, Any]) -> dict[str, Any]:
    payload = _payload_for_item(item)
    pull = payload.get("pull_request")
    return pull if isinstance(pull, dict) else {}


def _jira_issue_payload(item: dict[str, Any]) -> dict[str, Any]:
    payload = _payload_for_item(item)
    issue = payload.get("issue")
    return issue if isinstance(issue, dict) else {}


def _casefold_list(values: Any) -> set[str]:
    if not isinstance(values, list):
        return set()
    return {_clean_text(value).casefold() for value in values if _clean_text(value)}


def _contains_all(actual: set[str], expected: list[str] | None) -> bool:
    return all(_clean_text(item).casefold() in actual for item in (expected or []))


def _contains_any(actual: set[str], expected: list[str] | None) -> bool:
    return any(_clean_text(item).casefold() in actual for item in (expected or []))


def delegation_source_item_matches(
    source: str | None,
    item: dict[str, Any],
    source_scope: dict[str, Any] | None,
    source_conditions: dict[str, Any] | None,
) -> tuple[bool, str | None]:
    provider = provider_for_delegation_source(source)
    conditions = normalize_delegation_source_conditions(source, source_conditions or {})
    _ = normalize_delegation_source_scope(source, source_scope or {})
    if provider == "github":
        pull = _github_pull_payload(item)
        repository = normalize_github_repository(conditions.get("repository"))
        if repository:
            item_repository = normalize_github_repository(f"{pull.get('owner')}/{pull.get('repo')}")
            if item_repository.casefold() != repository.casefold():
                return False, f"repository is not {repository}"
        base_branch = _clean_text(conditions.get("base_branch"))
        if base_branch and _clean_lower(pull.get("base_branch")) != base_branch.casefold():
            return False, f"base branch is not {base_branch}"
        labels = _casefold_list(pull.get("labels"))
        if not _contains_all(labels, conditions.get("labels_include")):
            return False, "required label is missing"
        if _contains_any(labels, conditions.get("labels_exclude")):
            return False, "excluded label is present"
        author = _clean_lower(pull.get("author"))
        authors_include = conditions.get("authors_include") or []
        authors_exclude = conditions.get("authors_exclude") or []
        if authors_include and author not in {_clean_lower(item) for item in authors_include}:
            return False, "author is not allowed"
        if authors_exclude and author in {_clean_lower(item) for item in authors_exclude}:
            return False, "author is excluded"
        if conditions.get("include_drafts") is False and bool(pull.get("draft")):
            return False, "draft PRs are excluded"
        return True, None
    if provider == "jira":
        issue = _jira_issue_payload(item)
        project_key = _clean_text(conditions.get("project_key")).casefold()
        if project_key:
            issue_project = issue.get("project") if isinstance(issue.get("project"), dict) else {}
            item_project = _clean_text(issue_project.get("key") or "").casefold()
            if not item_project:
                item_project = _clean_text(issue.get("key")).split("-", 1)[0].casefold()
            if item_project != project_key:
                return False, f"project is not {conditions.get('project_key')}"
        issue_type = _clean_text(conditions.get("issue_type")).casefold()
        if issue_type:
            payload_type = issue.get("issue_type") if isinstance(issue.get("issue_type"), dict) else {}
            if _clean_text(payload_type.get("name")).casefold() != issue_type:
                return False, f"issue type is not {conditions.get('issue_type')}"
        status_payload = issue.get("status") if isinstance(issue.get("status"), dict) else {}
        status_name = _clean_text(status_payload.get("name")).casefold()
        status_include = conditions.get("status_include") or []
        status_exclude = conditions.get("status_exclude") or []
        if status_include and status_name not in {_clean_lower(item) for item in status_include}:
            return False, "status is not allowed"
        if status_exclude and status_name in {_clean_lower(item) for item in status_exclude}:
            return False, "status is excluded"
        priority = _clean_text(conditions.get("priority")).casefold()
        if priority:
            payload_priority = issue.get("priority") if isinstance(issue.get("priority"), dict) else {}
            if _clean_text(payload_priority.get("name")).casefold() != priority:
                return False, f"priority is not {conditions.get('priority')}"
        labels = _casefold_list(issue.get("labels"))
        if not _contains_all(labels, conditions.get("labels_include")):
            return False, "required label is missing"
        if _contains_any(labels, conditions.get("labels_exclude")):
            return False, "excluded label is present"
    return True, None
