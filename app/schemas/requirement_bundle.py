from pydantic import BaseModel, field_validator


class BundleRef(BaseModel):
    repo: str
    path: str
    branch: str


class RequirementBundleCreateForm(BaseModel):
    title: str
    domain: str
    slug: str | None = None
    base_branch: str
    collect_agent_id: str | None = None
    design_agent_id: str | None = None

    @field_validator("title", "domain", "base_branch")
    @classmethod
    def _validate_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Field cannot be blank")
        return cleaned


class RequirementBundleInspectResponse(BaseModel):
    bundle_ref: BundleRef
    manifest: dict
    requirements_exists: bool
    test_cases_exists: bool
    last_commit_sha: str | None = None


class RequirementBundleTaskRequest(BaseModel):
    bundle_ref: BundleRef
    assignee_agent_id: str
