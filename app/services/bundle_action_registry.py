from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BundleActionDefinition:
    action_id: str
    label: str
    description: str
    skill_name: str
    output_artifacts: tuple[str, ...]
    compatible_bundle_template_ids: tuple[str, ...]
    requires_sources: bool = False
    task_type: str = "bundle_action_task"
    task_family: str = "bundle"
    provider: str | None = None
    trigger: str | None = None


BUNDLE_ACTIONS: tuple[BundleActionDefinition, ...] = (
    BundleActionDefinition(
        action_id="collect_requirements_to_bundle",
        label="Collect Requirements to Bundle",
        description="Collect requirement artifacts from Jira/Confluence/GitHub Docs sources.",
        skill_name="collect_requirements_to_bundle",
        output_artifacts=("requirements",),
        compatible_bundle_template_ids=("requirement.v1",),
        requires_sources=True,
    ),
    BundleActionDefinition(
        action_id="design_test_cases_from_bundle",
        label="Design Test Cases from Bundle",
        description="Generate structured test cases from requirements artifact.",
        skill_name="design_test_cases_from_bundle",
        output_artifacts=("test_cases",),
        compatible_bundle_template_ids=("requirement.v1",),
    ),
    BundleActionDefinition(
        action_id="collect_research_notes_to_bundle",
        label="Collect Research Notes",
        description="Collect and summarize research notes from supported sources.",
        skill_name="collect_research_notes_to_bundle",
        output_artifacts=("research_notes",),
        compatible_bundle_template_ids=("research.v1",),
        requires_sources=True,
    ),
    BundleActionDefinition(
        action_id="generate_implementation_plan_from_bundle",
        label="Generate Implementation Plan",
        description="Generate implementation plan artifacts from the bundle state.",
        skill_name="generate_implementation_plan_from_bundle",
        output_artifacts=("implementation_plan",),
        compatible_bundle_template_ids=("development.v1",),
    ),
    BundleActionDefinition(
        action_id="generate_runbook_from_bundle",
        label="Generate Runbook",
        description="Generate operational runbook and rollout/rollback guidance.",
        skill_name="generate_runbook_from_bundle",
        output_artifacts=("runbook",),
        compatible_bundle_template_ids=("operations.v1",),
    ),
)


def list_bundle_actions() -> tuple[BundleActionDefinition, ...]:
    return BUNDLE_ACTIONS


def get_bundle_action(action_id: str | None) -> BundleActionDefinition | None:
    target = (action_id or "").strip()
    for action in BUNDLE_ACTIONS:
        if action.action_id == target:
            return action
    return None


def require_bundle_action(action_id: str | None) -> BundleActionDefinition:
    action = get_bundle_action(action_id)
    if action is None:
        raise ValueError(f"Unsupported bundle action: {action_id}")
    return action
