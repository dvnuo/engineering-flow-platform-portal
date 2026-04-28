from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class TaskTemplateDefinition:
    template_id: str
    label: str
    description: str
    task_type: str
    task_family: str
    provider: str | None = None
    default_trigger: str | None = None
    default_skill_name: str | None = None
    required_inputs: tuple[str, ...] = ()
    optional_inputs: tuple[str, ...] = ()
    output_artifacts: tuple[str, ...] = ()
    compatible_bundle_templates: tuple[str, ...] = ()
    requires_bundle: bool = False
    requires_sources: bool = False
    dispatch_immediately_default: bool = True


TASK_TEMPLATES: tuple[TaskTemplateDefinition, ...] = (
    TaskTemplateDefinition(
        template_id="collect_requirements_to_bundle",
        label="Collect Requirements to Bundle",
        description="Collect requirement artifacts from Jira/Confluence/GitHub Docs sources.",
        task_type="bundle_action_task",
        task_family="bundle",
        default_skill_name="collect_requirements_to_bundle",
        output_artifacts=("requirements",),
        compatible_bundle_templates=("requirement.v1",),
        requires_bundle=True,
        requires_sources=True,
    ),
    TaskTemplateDefinition(
        template_id="design_test_cases_from_bundle",
        label="Design Test Cases from Bundle",
        description="Generate structured test cases from requirements artifact.",
        task_type="bundle_action_task",
        task_family="bundle",
        default_skill_name="design_test_cases_from_bundle",
        output_artifacts=("test_cases",),
        compatible_bundle_templates=("requirement.v1",),
        requires_bundle=True,
    ),
    TaskTemplateDefinition(
        template_id="collect_research_notes_to_bundle",
        label="Collect Research Notes",
        description="Collect and summarize research notes from supported sources.",
        task_type="bundle_action_task",
        task_family="bundle",
        default_skill_name="collect_research_notes_to_bundle",
        output_artifacts=("research_notes",),
        compatible_bundle_templates=("research.v1",),
        requires_bundle=True,
        requires_sources=True,
    ),
    TaskTemplateDefinition(
        template_id="generate_implementation_plan_from_bundle",
        label="Generate Implementation Plan",
        description="Generate implementation plan artifacts from the bundle state.",
        task_type="bundle_action_task",
        task_family="bundle",
        default_skill_name="generate_implementation_plan_from_bundle",
        output_artifacts=("implementation_plan",),
        compatible_bundle_templates=("development.v1",),
        requires_bundle=True,
    ),
    TaskTemplateDefinition(
        template_id="generate_runbook_from_bundle",
        label="Generate Runbook",
        description="Generate operational runbook and rollout/rollback guidance.",
        task_type="bundle_action_task",
        task_family="bundle",
        default_skill_name="generate_runbook_from_bundle",
        output_artifacts=("runbook",),
        compatible_bundle_templates=("operations.v1",),
        requires_bundle=True,
    ),
    TaskTemplateDefinition(
        template_id="github_pr_review",
        label="GitHub PR Review",
        description="Review requested GitHub pull requests.",
        task_type="github_review_task",
        task_family="review",
        provider="github",
        default_trigger="github_pr_review_requested",
        default_skill_name="review-pull-request",
        required_inputs=("owner", "repo", "pull_number"),
        optional_inputs=("review_event", "head_sha", "review_target", "review_target_type", "writeback_mode", "skill_name"),
    ),
)


def list_task_templates() -> tuple[TaskTemplateDefinition, ...]:
    return TASK_TEMPLATES


def list_task_template_dicts() -> list[dict[str, Any]]:
    return [asdict(item) for item in TASK_TEMPLATES]


def get_task_template(template_id: str | None) -> TaskTemplateDefinition | None:
    target = (template_id or "").strip()
    for template in TASK_TEMPLATES:
        if template.template_id == target:
            return template
    return None


def require_task_template(template_id: str | None) -> TaskTemplateDefinition:
    template = get_task_template(template_id)
    if template is None:
        raise ValueError(f"Unsupported task template: {template_id}")
    return template


def _normalize_runtime_input(raw_input: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(raw_input, dict):
        return {}
    return dict(raw_input)


def build_agent_task_create_payload_from_template(
    template_id: str,
    raw_input: dict[str, Any] | None,
    assignee_agent_id: str,
    current_user_id: int | None = None,
) -> dict[str, Any]:
    template = require_task_template(template_id)
    normalized_input = _normalize_runtime_input(raw_input)

    missing_required = [field for field in template.required_inputs if normalized_input.get(field) in (None, "")]
    if missing_required:
        raise ValueError(f"Missing required template input fields: {', '.join(missing_required)}")

    if template.requires_bundle:
        bundle_ref = normalized_input.get("bundle_ref")
        manifest_ref = normalized_input.get("manifest_ref")
        if not isinstance(bundle_ref, dict) or not isinstance(manifest_ref, dict):
            raise ValueError("bundle_ref and manifest_ref are required for this task template")

    if template.requires_sources:
        sources = normalized_input.get("sources")
        if not isinstance(sources, dict):
            raise ValueError("sources is required for this task template")

    if template.default_skill_name and not normalized_input.get("skill_name"):
        normalized_input["skill_name"] = template.default_skill_name
    if template.default_trigger and not normalized_input.get("trigger"):
        normalized_input["trigger"] = template.default_trigger
    if template.template_id == "github_pr_review" and not normalized_input.get("review_event"):
        normalized_input["review_event"] = "COMMENT"

    payload = {
        "template_id": template.template_id,
        "assignee_agent_id": assignee_agent_id,
        "source": "portal",
        "task_type": template.task_type,
        "task_family": template.task_family,
        "provider": template.provider,
        "trigger": template.default_trigger,
        "input_payload_json": normalized_input,
        "status": "queued",
    }
    if current_user_id is not None:
        payload["created_by_user_id"] = current_user_id
    return payload
