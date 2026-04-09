import base64
import re
import secrets
import httpx

from app.config import get_settings
from app.schemas.requirement_bundle import BundleRef, RequirementBundleCreateForm, RequirementBundleInspectResponse


class RequirementBundleGithubServiceError(Exception):
    pass


class RequirementBundleGithubService:
    def __init__(self) -> None:
        settings = get_settings()
        self.api_base_url = settings.assets_github_api_base_url.rstrip("/")
        self.token = settings.assets_github_token
        self.default_repo = settings.assets_repo_full_name
        self.default_base_branch = settings.assets_default_base_branch
        self.bundle_root_dir = settings.assets_bundle_root_dir.strip("/")

    @staticmethod
    def normalize_slug(slug: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "-", slug.strip().lower()).strip("-")
        if not normalized:
            raise RequirementBundleGithubServiceError("Unable to derive a valid slug from title/slug input")
        return normalized

    @staticmethod
    def parse_repo_full_name(repo_full_name: str) -> tuple[str, str]:
        value = (repo_full_name or "").strip()
        if value.count("/") != 1:
            raise RequirementBundleGithubServiceError("Invalid repo format, expected owner/repo")
        owner, repo = value.split("/", 1)
        if not owner or not repo:
            raise RequirementBundleGithubServiceError("Invalid repo format, expected owner/repo")
        return owner, repo

    @staticmethod
    def normalize_bundle_path(path: str) -> str:
        normalized = (path or "").strip().strip("/")
        if not normalized:
            raise RequirementBundleGithubServiceError("Bundle path cannot be empty")
        return normalized

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise RequirementBundleGithubServiceError("assets_github_token is not configured")
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _request(self, method: str, path: str, *, json_body: dict | None = None) -> dict:
        url = f"{self.api_base_url}{path}"
        response = httpx.request(method=method, url=url, headers=self._headers(), json=json_body, timeout=30.0)
        if response.status_code >= 400:
            detail = response.text
            raise RequirementBundleGithubServiceError(f"GitHub API error {response.status_code}: {detail}")
        if not response.text:
            return {}
        return response.json()

    def _get_branch_head_sha(self, repo_full_name: str, branch: str) -> str:
        owner, repo = self.parse_repo_full_name(repo_full_name)
        payload = self._request("GET", f"/repos/{owner}/{repo}/git/ref/heads/{branch}")
        sha = ((payload.get("object") or {}).get("sha") or "").strip()
        if not sha:
            raise RequirementBundleGithubServiceError("Unable to resolve branch head sha")
        return sha

    def _create_branch(self, repo_full_name: str, branch: str, sha: str) -> None:
        owner, repo = self.parse_repo_full_name(repo_full_name)
        self._request(
            "POST",
            f"/repos/{owner}/{repo}/git/refs",
            json_body={"ref": f"refs/heads/{branch}", "sha": sha},
        )

    def _put_file(self, repo_full_name: str, file_path: str, branch: str, content: str, message: str) -> None:
        owner, repo = self.parse_repo_full_name(repo_full_name)
        encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        self._request(
            "PUT",
            f"/repos/{owner}/{repo}/contents/{file_path}",
            json_body={
                "message": message,
                "content": encoded,
                "branch": branch,
            },
        )

    def _get_file(self, repo_full_name: str, file_path: str, branch: str) -> dict:
        owner, repo = self.parse_repo_full_name(repo_full_name)
        return self._request("GET", f"/repos/{owner}/{repo}/contents/{file_path}?ref={branch}")

    def _file_exists(self, repo_full_name: str, file_path: str, branch: str) -> bool:
        owner, repo = self.parse_repo_full_name(repo_full_name)
        url = f"{self.api_base_url}/repos/{owner}/{repo}/contents/{file_path}"
        response = httpx.get(url, headers=self._headers(), params={"ref": branch}, timeout=30.0)
        return response.status_code < 400

    def _latest_commit_sha_for_path(self, repo_full_name: str, file_path: str, branch: str) -> str | None:
        owner, repo = self.parse_repo_full_name(repo_full_name)
        payload = self._request("GET", f"/repos/{owner}/{repo}/commits?sha={branch}&path={file_path}&per_page=1")
        if isinstance(payload, list) and payload:
            sha = (payload[0].get("sha") or "").strip()
            return sha or None
        return None

    @staticmethod
    def _render_bundle_yaml(
        *,
        bundle_id: str,
        title: str,
        domain: str,
        repo: str,
        path: str,
        base_branch: str,
        working_branch: str,
    ) -> str:
        return (
            f"bundle_id: {bundle_id}\n"
            f"title: {title}\n"
            "status: draft\n\n"
            "scope:\n"
            f"  domain: {domain}\n"
            f"  summary: {title}\n\n"
            "storage:\n"
            f"  repo: {repo}\n"
            f"  path: {path}\n"
            f"  base_branch: {base_branch}\n"
            f"  working_branch: {working_branch}\n\n"
            "links:\n"
            "  requirements_file: requirements.yaml\n"
            "  test_cases_file: test-cases.yaml\n"
        )

    @staticmethod
    def _render_requirements_yaml(bundle_id: str) -> str:
        return (
            f"bundle_id: {bundle_id}\n"
            "sources:\n"
            "  jira: []\n"
            "  confluence: []\n"
            "  github_docs: []\n"
            "  figma: []\n"
            "summary: {}\n"
            "functional_requirements: []\n"
            "business_rules: []\n"
            "acceptance_criteria: []\n"
            "edge_cases: []\n"
            "quality_flags:\n"
            "  ambiguities: []\n"
            "  conflicts: []\n"
            "  missing_information: []\n"
        )

    @staticmethod
    def _render_test_cases_yaml(bundle_id: str) -> str:
        return (
            f"bundle_id: {bundle_id}\n"
            'generated_from_requirements_commit: ""\n'
            "test_cases: []\n"
        )

    @staticmethod
    def _decode_content(payload: dict) -> str:
        encoded = (payload.get("content") or "").replace("\n", "")
        if not encoded:
            raise RequirementBundleGithubServiceError("GitHub file payload missing content")
        return base64.b64decode(encoded).decode("utf-8")

    @staticmethod
    def _parse_simple_yaml(yaml_text: str) -> dict:
        root: dict = {}
        stack: list[tuple[int, dict]] = [(-1, root)]

        for raw_line in yaml_text.splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(line.lstrip(" "))
            if ":" not in stripped:
                raise RequirementBundleGithubServiceError(f"Invalid yaml line: {raw_line}")
            key, value = stripped.split(":", 1)
            key = key.strip()
            value = value.strip()

            while stack and indent <= stack[-1][0]:
                stack.pop()
            parent = stack[-1][1]

            if value == "":
                container: dict = {}
                parent[key] = container
                stack.append((indent, container))
            else:
                if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]
                parent[key] = value

        return root

    @staticmethod
    def validate_bundle_manifest(manifest: dict) -> None:
        required_keys = ["bundle_id", "title", "status", "scope", "storage", "links"]
        for key in required_keys:
            if key not in manifest:
                raise RequirementBundleGithubServiceError(f"bundle.yaml missing required field: {key}")

        scope = manifest.get("scope")
        storage = manifest.get("storage")
        links = manifest.get("links")
        if not isinstance(scope, dict):
            raise RequirementBundleGithubServiceError("bundle.yaml field 'scope' must be an object")
        if not isinstance(storage, dict):
            raise RequirementBundleGithubServiceError("bundle.yaml field 'storage' must be an object")
        if not isinstance(links, dict):
            raise RequirementBundleGithubServiceError("bundle.yaml field 'links' must be an object")

        for required in ["domain", "summary"]:
            if required not in scope:
                raise RequirementBundleGithubServiceError(f"bundle.yaml scope missing required field: {required}")
        for required in ["repo", "path", "base_branch", "working_branch"]:
            if required not in storage:
                raise RequirementBundleGithubServiceError(f"bundle.yaml storage missing required field: {required}")
        for required in ["requirements_file", "test_cases_file"]:
            if required not in links:
                raise RequirementBundleGithubServiceError(f"bundle.yaml links missing required field: {required}")

    def create_bundle(self, form: RequirementBundleCreateForm) -> BundleRef:
        slug = self.normalize_slug(form.slug if form.slug else form.title)
        domain = self.normalize_slug(form.domain)
        bundle_path = f"{self.bundle_root_dir}/{domain}/{slug}"
        bundle_id = f"RB-{slug}"
        suffix = secrets.token_hex(4)
        working_branch = f"bundle/{slug}/{suffix}"

        base_sha = self._get_branch_head_sha(self.default_repo, form.base_branch)
        self._create_branch(self.default_repo, working_branch, base_sha)

        bundle_yaml = self._render_bundle_yaml(
            bundle_id=bundle_id,
            title=form.title,
            domain=domain,
            repo=self.default_repo,
            path=bundle_path,
            base_branch=form.base_branch,
            working_branch=working_branch,
        )
        requirements_yaml = self._render_requirements_yaml(bundle_id)
        test_cases_yaml = self._render_test_cases_yaml(bundle_id)

        self._put_file(
            self.default_repo,
            f"{bundle_path}/bundle.yaml",
            working_branch,
            bundle_yaml,
            f"Initialize requirement bundle {bundle_id}",
        )
        self._put_file(
            self.default_repo,
            f"{bundle_path}/requirements.yaml",
            working_branch,
            requirements_yaml,
            f"Initialize requirement skeleton for {bundle_id}",
        )
        self._put_file(
            self.default_repo,
            f"{bundle_path}/test-cases.yaml",
            working_branch,
            test_cases_yaml,
            f"Initialize test case skeleton for {bundle_id}",
        )

        return BundleRef(repo=self.default_repo, path=bundle_path, branch=working_branch)

    def inspect_bundle(self, bundle_ref: BundleRef) -> RequirementBundleInspectResponse:
        path = self.normalize_bundle_path(bundle_ref.path)
        manifest_payload = self._get_file(bundle_ref.repo, f"{path}/bundle.yaml", bundle_ref.branch)
        manifest_yaml = self._decode_content(manifest_payload)
        manifest = self._parse_simple_yaml(manifest_yaml)
        self.validate_bundle_manifest(manifest)

        requirements_exists = self._file_exists(bundle_ref.repo, f"{path}/requirements.yaml", bundle_ref.branch)
        test_cases_exists = self._file_exists(bundle_ref.repo, f"{path}/test-cases.yaml", bundle_ref.branch)
        last_commit_sha = self._latest_commit_sha_for_path(bundle_ref.repo, path, bundle_ref.branch)

        return RequirementBundleInspectResponse(
            bundle_ref=BundleRef(repo=bundle_ref.repo, path=path, branch=bundle_ref.branch),
            manifest=manifest,
            requirements_exists=requirements_exists,
            test_cases_exists=test_cases_exists,
            last_commit_sha=last_commit_sha,
        )
