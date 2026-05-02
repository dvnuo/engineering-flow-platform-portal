from datetime import datetime, timezone
from types import SimpleNamespace

from app.config import get_settings
from app.utils.agent_responses import build_agent_response


def test_build_agent_response_applies_effective_skill_defaults(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "default_skill_repo_url", "https://github.com/acme/default-skills.git")
    monkeypatch.setattr(settings, "default_skill_branch", "skills-main")

    agent = SimpleNamespace(
        id="a1",
        name="agent",
        status="running",
        visibility="private",
        image="example/image:latest",
        runtime_type="native",
        repo_url="https://github.com/acme/runtime.git",
        branch="main",
        skill_repo_url=None,
        skill_branch=None,
        tool_repo_url=None,
        tool_branch=None,
        owner_user_id=1,
        cpu=None,
        memory=None,
        agent_type="workspace",
        capability_profile_id=None,
        policy_profile_id=None,
        runtime_profile_id=None,
        disk_size_gi=20,
        description=None,
        last_error=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    response = build_agent_response(agent)
    assert response.skill_repo_url is None
    assert response.runtime_type == "native"
    assert response.effective_skill_repo_url == "https://github.com/acme/default-skills.git"
    assert response.effective_skill_branch == "skills-main"
