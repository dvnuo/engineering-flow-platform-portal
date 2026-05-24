import pytest

from app.services.bundle_action_registry import get_bundle_action, list_bundle_actions, require_bundle_action


def test_registry_actions_and_require_unknown():
    actions = list_bundle_actions()
    assert "collect_requirements_to_bundle" in {action.action_id for action in actions}
    with pytest.raises(ValueError):
        require_bundle_action("unknown")


def test_collect_requirements_action_metadata():
    action = require_bundle_action("collect_requirements_to_bundle")
    assert action.task_type == "bundle_action_task"
    assert action.task_family == "bundle"
    assert action.skill_name == "collect_requirements_to_bundle"
    assert action.requires_sources is True
    assert action.output_artifacts == ("requirements",)
    assert action.compatible_bundle_template_ids == ("requirement.v1",)


def test_design_test_cases_action_metadata():
    action = get_bundle_action("design_test_cases_from_bundle")
    assert action is not None
    assert action.skill_name == "design_test_cases_from_bundle"
    assert action.output_artifacts == ("test_cases",)
