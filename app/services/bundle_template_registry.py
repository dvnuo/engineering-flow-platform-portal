from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BundleTemplateDefinition:
    template_id: str
    display_name: str
    bundle_id_prefix: str
    path_segment: str | None
    branch_segment: str | None
    artifact_files: dict[str, str]
    compatible_task_template_ids: tuple[str, ...]


BUNDLE_TEMPLATES: tuple[BundleTemplateDefinition, ...] = (
    BundleTemplateDefinition(
        template_id="requirement.v1",
        display_name="Requirement Bundle",
        bundle_id_prefix="RB",
        path_segment=None,
        branch_segment=None,
        artifact_files={"requirements": "requirements.yaml", "test_cases": "test-cases.yaml"},
        compatible_task_template_ids=("collect_requirements_to_bundle", "design_test_cases_from_bundle"),
    ),
    BundleTemplateDefinition(
        template_id="research.v1",
        display_name="Research Bundle",
        bundle_id_prefix="RS",
        path_segment="research",
        branch_segment="research",
        artifact_files={"research_notes": "research-notes.yaml"},
        compatible_task_template_ids=("collect_research_notes_to_bundle",),
    ),
    BundleTemplateDefinition(
        template_id="development.v1",
        display_name="Development Bundle",
        bundle_id_prefix="DEV",
        path_segment="development",
        branch_segment="development",
        artifact_files={"implementation_plan": "implementation-plan.yaml"},
        compatible_task_template_ids=("generate_implementation_plan_from_bundle",),
    ),
    BundleTemplateDefinition(
        template_id="operations.v1",
        display_name="Operations Bundle",
        bundle_id_prefix="OPS",
        path_segment="operations",
        branch_segment="operations",
        artifact_files={"runbook": "runbook.yaml"},
        compatible_task_template_ids=("generate_runbook_from_bundle",),
    ),
)


def list_bundle_templates() -> tuple[BundleTemplateDefinition, ...]:
    return BUNDLE_TEMPLATES


def get_bundle_template(template_id: str | None) -> BundleTemplateDefinition | None:
    target = (template_id or "").strip()
    for template in BUNDLE_TEMPLATES:
        if template.template_id == target:
            return template
    return None


def require_bundle_template(template_id: str | None) -> BundleTemplateDefinition:
    template = get_bundle_template(template_id)
    if template is None:
        raise ValueError(f"Unsupported bundle template: {template_id}")
    return template


def resolve_bundle_template_from_manifest(manifest: dict[str, Any]) -> BundleTemplateDefinition:
    manifest_template_id = str(manifest.get("template_id") or "").strip()
    if manifest_template_id:
        return require_bundle_template(manifest_template_id)

    links = manifest.get("links")
    if isinstance(links, dict) and links:
        return require_bundle_template("requirement.v1")

    raise ValueError("bundle.yaml requires 'template_id' or legacy 'links'")


def artifact_file_for_template(template_id: str, artifact_key: str) -> str | None:
    template = get_bundle_template(template_id)
    if template is None:
        return None
    value = template.artifact_files.get(artifact_key)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
