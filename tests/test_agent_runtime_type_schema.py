import pytest
from pydantic import ValidationError

from app.schemas.agent import AgentCreateRequest, AgentUpdateRequest


def test_create_request_default_runtime_type_native():
    payload = AgentCreateRequest(name="a")
    assert payload.runtime_type == "native"


def test_create_request_runtime_type_normalize():
    payload = AgentCreateRequest(name="a", runtime_type=" NATIVE ")
    assert payload.runtime_type == "native"


def test_create_request_opencode_runtime_type_normalize():
    payload = AgentCreateRequest(name="a", runtime_type=" opencode ")
    assert payload.runtime_type == "opencode"


def test_update_request_runtime_type_normalize():
    payload = AgentUpdateRequest(runtime_type=" NATIVE ")
    assert payload.runtime_type == "native"


def test_invalid_runtime_type_raises():
    with pytest.raises(ValidationError):
        AgentCreateRequest(name="a", runtime_type="bad")


def test_internal_runtime_type_normalizer_rejects_invalid_values():
    import app.api.agents as agents_api
    with pytest.raises(ValueError):
        agents_api._normalize_runtime_type("bad")


def test_agent_defaults_have_configurable_runtime_type_helper():
    import app.api.agents as agents_api
    assert hasattr(agents_api.settings, "default_runtime_type")
    assert hasattr(agents_api, "_default_runtime_type_from_settings")


def test_runtime_image_parts_rejects_invalid_runtime_type():
    import app.api.agents as agents_api
    with pytest.raises(ValueError):
        agents_api._runtime_image_parts("bad")


def test_default_mount_path_rejects_invalid_runtime_type():
    import app.api.agents as agents_api
    with pytest.raises(ValueError):
        agents_api._default_mount_path_for_runtime("bad")


def test_mount_path_switches_default_native_mount_to_workspace_on_runtime_change():
    import app.api.agents as agents_api
    from types import SimpleNamespace

    agent = SimpleNamespace(runtime_type=None, mount_path="/root/.efp")
    changes = {"runtime_type": "opencode"}
    agents_api._maybe_add_mount_path_switch_for_runtime_change(agent, changes)
    assert changes == {"runtime_type": "opencode", "mount_path": "/workspace"}


def test_update_runtime_type_change_detection_defaults_missing_old_runtime_type_to_native():
    import app.api.agents as agents_api
    from types import SimpleNamespace

    agent = SimpleNamespace(runtime_type=None)
    changes = {"runtime_type": "native"}
    changed = agents_api._normalize_runtime_type_update_change(agent, changes)

    assert changed is False
    assert "runtime_type" not in changes
    assert "image" not in changes


def test_update_runtime_type_change_detection_accepts_opencode_choice():
    import app.api.agents as agents_api
    from types import SimpleNamespace

    agent = SimpleNamespace(runtime_type=None)
    changes = {"runtime_type": "opencode"}
    changed = agents_api._normalize_runtime_type_update_change(agent, changes)

    assert changed is True
    assert changes == {"runtime_type": "opencode"}


def test_update_runtime_type_change_detection_rejects_invalid_new_runtime_type():
    import app.api.agents as agents_api
    from types import SimpleNamespace

    agent = SimpleNamespace(runtime_type="native")
    with pytest.raises(ValueError):
        agents_api._normalize_runtime_type_update_change(agent, {"runtime_type": "bad"})
