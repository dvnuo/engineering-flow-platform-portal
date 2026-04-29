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
        optional_inputs=("review_event", "head_sha", "review_target", "review_target_type", "writeback_mode", "skill_name", "skill_execution_mode"),
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


def _require_non_empty_ref(ref: dict[str, Any] | None, *, field_name: str) -> None:
    if not isinstance(ref, dict):
        raise ValueError(f"{field_name} is required for this task template")
    repo = str(ref.get("repo") or "").strip()
    path = str(ref.get("path") or "").strip()
    if not repo or not path:
        raise ValueError(f"{field_name} requires non-empty repo/path")


def _sources_has_non_empty_value(sources: dict[str, Any]) -> bool:
    for value in sources.values():
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    return True
                if item is not None and not isinstance(item, str):
                    return True
        if value is not None and not isinstance(value, (str, list, dict)):
            return True
    return False


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
        _require_non_empty_ref(bundle_ref, field_name="bundle_ref")
        _require_non_empty_ref(manifest_ref, field_name="manifest_ref")
        bundle_template_id = str(normalized_input.get("bundle_template_id") or "").strip()
        if not bundle_template_id:
            raise ValueError("bundle_template_id is required for this task template")
        if template.compatible_bundle_templates and bundle_template_id not in template.compatible_bundle_templates:
            raise ValueError(
                f"bundle_template_id '{bundle_template_id}' is not compatible with template '{template.template_id}'"
            )

    if template.requires_sources:
        sources = normalized_input.get("sources")
        if not isinstance(sources, dict):
            raise ValueError("sources is required for this task template")
        if not _sources_has_non_empty_value(sources):
            raise ValueError("sources requires at least one non-empty source")

    normalized_input["task_template_id"] = template.template_id
    normalized_input["task_type"] = template.task_type
    if template.provider:
        normalized_input["provider"] = template.provider
    if template.default_trigger:
        normalized_input["trigger"] = template.default_trigger

    if template.default_skill_name and not normalized_input.get("skill_name"):
        normalized_input["skill_name"] = template.default_skill_name
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
