import json
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

    base_url = str(github.get("base_url") or "").strip()
    api_token = str(github.get("api_token") or "").strip()
    if not base_url or not api_token:
        raise ProviderConfigResolverError("GitHub base_url/api_token is missing for selected agent")

    return GithubProviderConfig(
        base_url=base_url,
        api_token=api_token,
        runtime_profile_id=profile.id,
    )
