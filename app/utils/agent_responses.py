from app.config import get_settings
from app.schemas.agent import AgentResponse
from app.utils.git_urls import normalize_git_repo_url


def effective_skill_repo_url(agent, settings=None) -> str | None:
    settings = settings or get_settings()
    return normalize_git_repo_url(getattr(agent, "skill_repo_url", None)) or normalize_git_repo_url(settings.default_skill_repo_url)


def effective_skill_branch(agent, settings=None) -> str:
    settings = settings or get_settings()
    return (getattr(agent, "skill_branch", None) or settings.default_skill_branch or "master").strip() or "master"


def effective_agent_settings_repo_url(agent, settings=None) -> str | None:
    settings = settings or get_settings()
    return normalize_git_repo_url(getattr(agent, "agent_settings_repo_url", None)) or normalize_git_repo_url(settings.default_agent_settings_repo_url)


def effective_agent_settings_branch(agent, settings=None) -> str:
    settings = settings or get_settings()
    return (getattr(agent, "agent_settings_branch", None) or settings.default_agent_settings_branch or "master").strip() or "master"


def effective_agent_settings_subdir(agent, settings=None) -> str:
    settings = settings or get_settings()
    return (getattr(agent, "agent_settings_subdir", None) or settings.default_agent_settings_repo_subdir or "").strip().strip("/")


def build_agent_response(agent, settings=None) -> AgentResponse:
    response = AgentResponse.model_validate(agent)
    response.effective_agent_settings_repo_url = effective_agent_settings_repo_url(agent, settings=settings)
    response.effective_agent_settings_branch = effective_agent_settings_branch(agent, settings=settings)
    response.effective_agent_settings_subdir = effective_agent_settings_subdir(agent, settings=settings)
    response.effective_skill_repo_url = effective_skill_repo_url(agent, settings=settings)
    response.effective_skill_branch = effective_skill_branch(agent, settings=settings)
    return response
