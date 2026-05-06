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


def test_tool_branch_blank_normalizes_to_none_on_create():
    payload = AgentCreateRequest(name="a", tool_branch=" ")
    assert payload.tool_branch is None


def test_tool_branch_blank_normalizes_to_none_on_update():
    payload = AgentUpdateRequest(tool_branch=" ")
    assert payload.tool_branch is None


def test_internal_runtime_type_normalizer_rejects_invalid_values():
    import app.api.agents as agents_api
    with pytest.raises(ValueError):
        agents_api._normalize_runtime_type("bad")


def test_default_runtime_type_from_settings_rejects_invalid_non_empty_setting(monkeypatch):
    import app.api.agents as agents_api
    monkeypatch.setattr(agents_api.settings, "default_runtime_type", "bad")
    with pytest.raises(ValueError):
        agents_api._default_runtime_type_from_settings()


def test_runtime_image_parts_rejects_invalid_runtime_type():
    import app.api.agents as agents_api
    with pytest.raises(ValueError):
        agents_api._runtime_image_parts("bad")


def test_default_mount_path_rejects_invalid_runtime_type():
    import app.api.agents as agents_api
    with pytest.raises(ValueError):
        agents_api._default_mount_path_for_runtime("bad")
