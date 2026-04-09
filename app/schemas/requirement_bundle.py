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

    @field_validator("title", "domain", "base_branch")
    @classmethod
    def _validate_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Field cannot be blank")
        return cleaned


class RequirementBundleInspectResponse(BaseModel):
    manifest_ref: BundleRef
    bundle_ref: BundleRef
    manifest: dict
    requirements_file: str
    test_cases_file: str
    requirements_exists: bool
    test_cases_exists: bool
    last_commit_sha: str | None = None


class RequirementBundleListItem(BaseModel):
    bundle_id: str
    title: str
    domain: str
    status: str
    bundle_ref: BundleRef
    manifest_ref: BundleRef
    requirements_exists: bool
    test_cases_exists: bool
    last_commit_sha: str | None = None


class RequirementBundleCreateRequest(BaseModel):
    title: str
    domain: str
    slug: str | None = None
    base_branch: str

    @field_validator("title", "domain", "base_branch")
    @classmethod
    def _validate_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Field cannot be blank")
        return cleaned


class RequirementBundleTaskRequest(BaseModel):
    bundle_ref: BundleRef
    assignee_agent_id: str
