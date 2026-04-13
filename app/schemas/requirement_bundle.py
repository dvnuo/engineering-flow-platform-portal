from pydantic import BaseModel, field_validator


class BundleRef(BaseModel):
    repo: str
    path: str
    branch: str


class RequirementBundleCreateForm(BaseModel):
    template_id: str = "requirement.v1"
    title: str
    domain: str
    slug: str | None = None
    base_branch: str

    @field_validator("title", "domain", "base_branch", "template_id")
    @classmethod
    def _validate_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Field cannot be blank")
        return cleaned


class BundleArtifactStatus(BaseModel):
    artifact_key: str
    file_path: str
    exists: bool


class RequirementBundleInspectResponse(BaseModel):
    manifest_ref: BundleRef
    bundle_ref: BundleRef
    manifest: dict
    template_id: str
    template_label: str
    template_version: int
    artifacts: list[BundleArtifactStatus]
    requirements_file: str | None = None
    test_cases_file: str | None = None
    requirements_exists: bool | None = None
    test_cases_exists: bool | None = None
    last_commit_sha: str | None = None


class RequirementBundleListItem(BaseModel):
    bundle_id: str
    title: str
    domain: str
    status: str
    template_id: str
    template_label: str
    artifacts: list[BundleArtifactStatus] | None = None
    bundle_ref: BundleRef
    manifest_ref: BundleRef
    requirements_exists: bool | None = None
    test_cases_exists: bool | None = None
    last_commit_sha: str | None = None


class RequirementBundleCreateRequest(BaseModel):
    template_id: str = "requirement.v1"
    title: str
    domain: str
    slug: str | None = None
    base_branch: str

    @field_validator("title", "domain", "base_branch", "template_id")
    @classmethod
    def _validate_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Field cannot be blank")
        return cleaned


class RequirementBundleTaskRequest(BaseModel):
    bundle_ref: BundleRef
    assignee_agent_id: str
