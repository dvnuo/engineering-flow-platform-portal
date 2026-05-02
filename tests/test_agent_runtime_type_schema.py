import pytest
from pydantic import ValidationError

from app.schemas.agent import AgentCreateRequest, AgentUpdateRequest


def test_create_request_default_runtime_type_native():
    payload = AgentCreateRequest(name="a")
    assert payload.runtime_type == "native"


def test_create_request_runtime_type_normalize():
    payload = AgentCreateRequest(name="a", runtime_type=" OpenCode ")
    assert payload.runtime_type == "opencode"


def test_update_request_runtime_type_normalize():
    payload = AgentUpdateRequest(runtime_type=" NATIVE ")
    assert payload.runtime_type == "native"


def test_invalid_runtime_type_raises():
    with pytest.raises(ValidationError):
        AgentCreateRequest(name="a", runtime_type="bad")


def test_tool_repo_url_normalize():
    payload = AgentCreateRequest(name="a", tool_repo_url="git@github.com:Acme/Tools.git")
    assert payload.tool_repo_url == "https://github.com/Acme/Tools.git"
