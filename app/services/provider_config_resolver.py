import json
import base64
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.repositories.agent_repo import AgentRepository
from app.repositories.runtime_profile_repo import RuntimeProfileRepository


@dataclass
class GithubProviderConfig:
    base_url: str
    api_token: str
    runtime_profile_id: str
    source: str = "agent_runtime_profile"


@dataclass
class JiraProviderConfig:
    base_url: str
    headers: dict
    runtime_profile_id: str
    instance_name: str | None = None
    username: str | None = None
    api_version: str = "2"
    source: str = "agent_runtime_profile"


class ProviderConfigResolverError(ValueError):
    pass


def resolve_github_for_agent(db: Session, agent_id: str) -> GithubProviderConfig:
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent or not agent.runtime_profile_id:
        raise ProviderConfigResolverError("Selected agent does not have a runtime profile")

    profile = RuntimeProfileRepository(db).get_by_id(agent.runtime_profile_id)
    if not profile:
        raise ProviderConfigResolverError("Selected agent does not have a runtime profile")

    try:
        config = json.loads(profile.config_json or "{}")
    except json.JSONDecodeError as exc:
        raise ProviderConfigResolverError("Selected agent runtime profile config is invalid") from exc

    github = config.get("github") if isinstance(config, dict) else None
    if not isinstance(github, dict) or not github.get("enabled"):
        raise ProviderConfigResolverError("GitHub is not enabled for selected agent")

    base_url = str(github.get("base_url") or "").strip() or "https://api.github.com"
    api_token = str(github.get("api_token") or "").strip()
    if not api_token:
        raise ProviderConfigResolverError("GitHub api_token is missing for selected agent")

    return GithubProviderConfig(
        base_url=base_url,
        api_token=api_token,
        runtime_profile_id=profile.id,
    )


def _first_enabled_auth_instance(instances: list) -> dict | None:
    for item in instances:
        if not isinstance(item, dict):
            continue
        if item.get("enabled") is False:
            continue
        url = str(item.get("url") or "").strip()
        token = str(item.get("token") or "").strip()
        username = str(item.get("username") or "").strip()
        password = str(item.get("password") or "").strip()
        has_username_token = bool(username and token)
        has_token_only = bool(token and not username)
        has_username_password = bool(username and password)
        if url and (has_username_token or has_token_only or has_username_password):
            return item
    return None


def _auth_headers_for_instance(instance: dict) -> dict:
    username = str(instance.get("username") or "").strip()
    token = str(instance.get("token") or "").strip()
    if username and token:
        encoded = base64.b64encode(f"{username}:{token}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}
    if token:
        return {"Authorization": f"Bearer {token}"}
    password = str(instance.get("password") or "").strip()
    if username and password:
        encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}
    return {}


def resolve_jira_for_agent(db: Session, agent_id: str) -> JiraProviderConfig:
    agent = AgentRepository(db).get_by_id(agent_id)
    if not agent or not agent.runtime_profile_id:
        raise ProviderConfigResolverError("Selected agent does not have a runtime profile")

    profile = RuntimeProfileRepository(db).get_by_id(agent.runtime_profile_id)
    if not profile:
        raise ProviderConfigResolverError("Selected agent does not have a runtime profile")

    try:
        config = json.loads(profile.config_json or "{}")
    except json.JSONDecodeError as exc:
        raise ProviderConfigResolverError("Selected agent runtime profile config is invalid") from exc

    jira = config.get("jira") if isinstance(config, dict) else None
    if not isinstance(jira, dict) or not jira.get("enabled"):
        raise ProviderConfigResolverError("Jira is not enabled for selected agent")

    instance = _first_enabled_auth_instance(jira.get("instances") or [])
    if not instance:
        raise ProviderConfigResolverError("No usable Jira instance found for selected agent")

    base_url = str(instance.get("url") or "").strip().rstrip("/")
    if not base_url:
        raise ProviderConfigResolverError("Jira URL is missing for selected agent")
    api_version = str(instance.get("api_version") or "2").strip()
    if api_version not in {"2", "3"}:
        api_version = "2"
    headers = _auth_headers_for_instance(instance)
    if not headers:
        raise ProviderConfigResolverError("Jira credentials are missing for selected agent")

    return JiraProviderConfig(
        base_url=base_url,
        headers=headers,
        runtime_profile_id=profile.id,
        instance_name=str(instance.get("name") or "").strip() or None,
        username=str(instance.get("username") or "").strip() or None,
        api_version=api_version,
    )
