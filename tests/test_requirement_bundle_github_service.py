import base64

import pytest

from app.schemas.requirement_bundle import BundleRef, RequirementBundleCreateForm, RequirementBundleInspectResponse
from app.services.requirement_bundle_github_service import (
    RequirementBundleGithubService,
    RequirementBundleGithubServiceError,
)


def test_slug_normalization():
    assert RequirementBundleGithubService.normalize_slug("  Hello, Portal MVP  ") == "hello-portal-mvp"


def test_repo_path_branch_parsing_helpers():
    owner, repo = RequirementBundleGithubService.parse_repo_full_name("octo/assets")
    assert owner == "octo"
    assert repo == "assets"
    assert RequirementBundleGithubService.normalize_bundle_path("/requirement-bundles/payments/rb1/") == "requirement-bundles/payments/rb1"


def test_create_bundle_calls_refs_and_contents(monkeypatch):
    service = RequirementBundleGithubService()

    monkeypatch.setattr(service, "default_repo", "octo/engineering-flow-platform-assets")
    monkeypatch.setattr(service, "bundle_root_dir", "requirement-bundles")

    calls = []

    def _fake_request(method: str, path: str, *, json_body=None):
        calls.append((method, path, json_body))
        if path.endswith("/git/ref/heads/main"):
            return {"object": {"sha": "abc123"}}
        return {"ok": True}

    monkeypatch.setattr(service, "_request", _fake_request)
    monkeypatch.setattr("app.services.requirement_bundle_github_service.secrets.token_hex", lambda _n: "deadbeef")

    bundle_ref = service.create_bundle(
        RequirementBundleCreateForm(
            title="Checkout Flow",
            domain="payments",
            slug="",
            base_branch="main",
        )
    )

    assert bundle_ref.repo == "octo/engineering-flow-platform-assets"
    assert bundle_ref.path == "requirement-bundles/payments/checkout-flow"
    assert bundle_ref.branch == "bundle/checkout-flow/deadbeef"

    assert calls[0][0] == "GET"
    assert "/git/ref/heads/main" in calls[0][1]
    assert calls[1][0] == "POST"
    assert calls[1][1].endswith("/git/refs")
    assert calls[1][2]["ref"] == "refs/heads/bundle/checkout-flow/deadbeef"

    put_calls = [call for call in calls if call[0] == "PUT"]
    assert len(put_calls) == 3
    assert put_calls[0][1].endswith("/contents/requirement-bundles/payments/checkout-flow/bundle.yaml")


def test_inspect_existing_bundle(monkeypatch):
    service = RequirementBundleGithubService()

    manifest_text = """bundle_id: RB-checkout
title: Checkout
status: draft

scope:
  domain: payments
  summary: Checkout

storage:
  repo: octo/engineering-flow-platform-assets
  path: requirement-bundles/payments/checkout
  base_branch: main
  working_branch: bundle/checkout/abcd1234

links:
  requirements_file: requirements.yaml
  test_cases_file: test-cases.yaml
"""

    manifest_payload = {"content": base64.b64encode(manifest_text.encode("utf-8")).decode("utf-8")}

    monkeypatch.setattr(service, "_get_file", lambda _repo, _path, _branch: manifest_payload)
    monkeypatch.setattr(
        service,
        "_file_exists",
        lambda _repo, file_path, _branch: file_path.endswith("requirements.yaml"),
    )
    monkeypatch.setattr(service, "_latest_commit_sha_for_path", lambda *_args, **_kwargs: "cafebabe")

    result = service.inspect_bundle(
        BundleRef(
            repo="octo/engineering-flow-platform-assets",
            path="requirement-bundles/payments/checkout",
            branch="bundle/checkout/abcd1234",
        )
    )

    assert result.bundle_ref.repo == "octo/engineering-flow-platform-assets"
    assert result.manifest["bundle_id"] == "RB-checkout"
    assert result.requirements_file == "requirements.yaml"
    assert result.test_cases_file == "test-cases.yaml"
    assert result.requirements_exists is True
    assert result.test_cases_exists is False
    assert result.last_commit_sha == "cafebabe"


def test_inspect_existing_bundle_uses_manifest_storage_as_canonical_ref(monkeypatch):
    service = RequirementBundleGithubService()

    manifest_text = """bundle_id: RB-checkout
title: Checkout
status: draft

scope:
  domain: payments
  summary: Checkout

storage:
  repo: octo/engineering-flow-platform-assets
  path: requirement-bundles/payments/checkout
  base_branch: main
  working_branch: bundle/checkout/abcd1234

links:
  requirements_file: requirements.yaml
  test_cases_file: test-cases.yaml
"""

    manifest_payload = {"content": base64.b64encode(manifest_text.encode("utf-8")).decode("utf-8")}
    file_exists_calls: list[tuple[str, str, str]] = []
    latest_commit_calls: list[tuple[str, str, str]] = []

    monkeypatch.setattr(service, "_get_file", lambda _repo, _path, _branch: manifest_payload)

    def _fake_file_exists(repo_full_name: str, file_path: str, branch: str) -> bool:
        file_exists_calls.append((repo_full_name, file_path, branch))
        return file_path.endswith("requirements.yaml")

    def _fake_latest_commit_sha(repo_full_name: str, file_path: str, branch: str) -> str:
        latest_commit_calls.append((repo_full_name, file_path, branch))
        return "cafebabe"

    monkeypatch.setattr(service, "_file_exists", _fake_file_exists)
    monkeypatch.setattr(service, "_latest_commit_sha_for_path", _fake_latest_commit_sha)

    result = service.inspect_bundle(
        BundleRef(
            repo="octo/assets",
            path="requirement-bundles/payments/checkout",
            branch="main",
        )
    )

    assert result.bundle_ref.repo == "octo/engineering-flow-platform-assets"
    assert result.bundle_ref.path == "requirement-bundles/payments/checkout"
    assert result.bundle_ref.branch == "bundle/checkout/abcd1234"
    assert file_exists_calls == [
        (
            "octo/engineering-flow-platform-assets",
            "requirement-bundles/payments/checkout/requirements.yaml",
            "bundle/checkout/abcd1234",
        ),
        (
            "octo/engineering-flow-platform-assets",
            "requirement-bundles/payments/checkout/test-cases.yaml",
            "bundle/checkout/abcd1234",
        ),
    ]
    assert latest_commit_calls == [
        (
            "octo/engineering-flow-platform-assets",
            "requirement-bundles/payments/checkout",
            "bundle/checkout/abcd1234",
        )
    ]


def test_inspect_bundle_returns_manifest_ref_and_canonical_bundle_ref(monkeypatch):
    service = RequirementBundleGithubService()

    manifest_text = """bundle_id: RB-checkout
title: Checkout
status: draft
scope:
  domain: payments
  summary: Checkout
storage:
  repo: octo/engineering-flow-platform-assets
  path: requirement-bundles/payments/checkout
  base_branch: main
  working_branch: bundle/checkout/abcd1234
links:
  requirements_file: requirements.yaml
  test_cases_file: test-cases.yaml
"""
    manifest_payload = {"content": base64.b64encode(manifest_text.encode("utf-8")).decode("utf-8")}

    monkeypatch.setattr(service, "_get_file", lambda _repo, _path, _branch: manifest_payload)
    monkeypatch.setattr(service, "_file_exists", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(service, "_latest_commit_sha_for_path", lambda *_args, **_kwargs: "cafebabe")

    result = service.inspect_bundle(
        BundleRef(
            repo="octo/assets",
            path="/requirement-bundles/payments/checkout/",
            branch="main",
        )
    )

    assert result.manifest_ref.repo == "octo/assets"
    assert result.manifest_ref.path == "requirement-bundles/payments/checkout"
    assert result.manifest_ref.branch == "main"
    assert result.bundle_ref.branch == "bundle/checkout/abcd1234"


def test_inspect_bundle_uses_custom_linked_filenames_for_existence_checks(monkeypatch):
    service = RequirementBundleGithubService()

    manifest_text = """bundle_id: RB-checkout
title: Checkout
status: draft
scope:
  domain: payments
  summary: Checkout
storage:
  repo: octo/engineering-flow-platform-assets
  path: requirement-bundles/payments/checkout
  base_branch: main
  working_branch: bundle/checkout/abcd1234
links:
  requirements_file: docs/reqs.yaml
  test_cases_file: outputs/tc.yaml
"""

    manifest_payload = {"content": base64.b64encode(manifest_text.encode("utf-8")).decode("utf-8")}
    file_exists_calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(service, "_get_file", lambda _repo, _path, _branch: manifest_payload)

    def _fake_file_exists(repo_full_name: str, file_path: str, branch: str) -> bool:
        file_exists_calls.append((repo_full_name, file_path, branch))
        return file_path.endswith("reqs.yaml")

    monkeypatch.setattr(service, "_file_exists", _fake_file_exists)
    monkeypatch.setattr(service, "_latest_commit_sha_for_path", lambda *_args, **_kwargs: "cafebabe")

    result = service.inspect_bundle(
        BundleRef(
            repo="octo/engineering-flow-platform-assets",
            path="requirement-bundles/payments/checkout",
            branch="bundle/checkout/abcd1234",
        )
    )

    assert result.requirements_file == "docs/reqs.yaml"
    assert result.test_cases_file == "outputs/tc.yaml"
    assert file_exists_calls == [
        (
            "octo/engineering-flow-platform-assets",
            "requirement-bundles/payments/checkout/docs/reqs.yaml",
            "bundle/checkout/abcd1234",
        ),
        (
            "octo/engineering-flow-platform-assets",
            "requirement-bundles/payments/checkout/outputs/tc.yaml",
            "bundle/checkout/abcd1234",
        ),
    ]


def test_invalid_manifest_raises_error(monkeypatch):
    service = RequirementBundleGithubService()
    invalid_manifest_text = "bundle_id: RB-1\ntitle: Missing Scope\n"
    manifest_payload = {"content": base64.b64encode(invalid_manifest_text.encode("utf-8")).decode("utf-8")}

    monkeypatch.setattr(service, "_get_file", lambda _repo, _path, _branch: manifest_payload)

    with pytest.raises(RequirementBundleGithubServiceError) as exc_info:
        service.inspect_bundle(BundleRef(repo="octo/assets", path="requirement-bundles/a/b", branch="main"))

    assert "missing required field" in str(exc_info.value)


def test_inspect_bundle_rejects_blank_manifest_fields(monkeypatch):
    service = RequirementBundleGithubService()
    manifest_text = """bundle_id: RB-checkout
title: Checkout
status: draft
scope:
  domain: payments
  summary: Checkout
storage:
  repo: octo/engineering-flow-platform-assets
  path: requirement-bundles/payments/checkout
  base_branch: main
  working_branch: "   "
links:
  requirements_file: requirements.yaml
  test_cases_file: test-cases.yaml
"""
    manifest_payload = {"content": base64.b64encode(manifest_text.encode("utf-8")).decode("utf-8")}
    monkeypatch.setattr(service, "_get_file", lambda _repo, _path, _branch: manifest_payload)

    with pytest.raises(RequirementBundleGithubServiceError) as exc_info:
        service.inspect_bundle(
            BundleRef(
                repo="octo/engineering-flow-platform-assets",
                path="requirement-bundles/payments/checkout",
                branch="main",
            )
        )

    assert "bundle.yaml field 'storage.working_branch' must be a non-empty string" in str(exc_info.value)


def test_inspect_bundle_rejects_invalid_storage_repo_value(monkeypatch):
    service = RequirementBundleGithubService()
    manifest_text = """bundle_id: RB-checkout
title: Checkout
status: draft
scope:
  domain: payments
  summary: Checkout
storage:
  repo: not-a-repo
  path: requirement-bundles/payments/checkout
  base_branch: main
  working_branch: bundle/checkout/abcd1234
links:
  requirements_file: requirements.yaml
  test_cases_file: test-cases.yaml
"""
    manifest_payload = {"content": base64.b64encode(manifest_text.encode("utf-8")).decode("utf-8")}
    monkeypatch.setattr(service, "_get_file", lambda _repo, _path, _branch: manifest_payload)

    with pytest.raises(RequirementBundleGithubServiceError) as exc_info:
        service.inspect_bundle(
            BundleRef(
                repo="octo/engineering-flow-platform-assets",
                path="requirement-bundles/payments/checkout",
                branch="main",
            )
        )

    assert "Invalid repo format, expected owner/repo" in str(exc_info.value)


def test_inspect_bundle_supports_complex_yaml_manifest(monkeypatch):
    service = RequirementBundleGithubService()
    manifest_text = """bundle_id: RB-checkout
title: Checkout Bundle
status: draft
flags:
  from_portal: true
scope:
  domain: payments
  summary: checkout summary
  tags:
    - checkout
    - payment
storage:
  repo: octo/engineering-flow-platform-assets
  path: requirement-bundles/payments/checkout
  base_branch: main
  working_branch: bundle/checkout/abcd1234
links:
  requirements_file: requirements.yaml
  test_cases_file: test-cases.yaml
metadata:
  owners:
    - qa
    - pm
"""
    manifest_payload = {"content": base64.b64encode(manifest_text.encode("utf-8")).decode("utf-8")}

    monkeypatch.setattr(service, "_get_file", lambda _repo, _path, _branch: manifest_payload)
    monkeypatch.setattr(service, "_file_exists", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(service, "_latest_commit_sha_for_path", lambda *_args, **_kwargs: "a1b2c3d4")

    result = service.inspect_bundle(
        BundleRef(
            repo="octo/engineering-flow-platform-assets",
            path="requirement-bundles/payments/checkout",
            branch="bundle/checkout/abcd1234",
        )
    )

    assert result.manifest["flags"]["from_portal"] is True
    assert result.manifest["scope"]["tags"] == ["checkout", "payment"]
    assert result.manifest["metadata"]["owners"] == ["qa", "pm"]


def test_list_bundles_discovers_bundle_branches_and_returns_items(monkeypatch):
    service = RequirementBundleGithubService()
    monkeypatch.setattr(service, "default_repo", "octo/assets")
    monkeypatch.setattr(service, "bundle_root_dir", "requirement-bundles")

    def _fake_request(method: str, path: str, *, json_body=None):
        assert method == "GET"
        if "matching-refs/heads/bundle/" in path:
            return [
                {"ref": "refs/heads/bundle/payments/aaa111"},
                {"ref": "refs/heads/bundle/checkout/bbb222"},
            ]
        if "trees/bundle/payments/aaa111?recursive=1" in path:
            return {
                "tree": [
                    {"path": "requirement-bundles/payments/alpha/bundle.yaml", "type": "blob"},
                    {"path": "docs/readme.md", "type": "blob"},
                ]
            }
        if "trees/bundle/checkout/bbb222?recursive=1" in path:
            return {
                "tree": [
                    {"path": "requirement-bundles/checkout/beta/bundle.yaml", "type": "blob"},
                    {"path": "requirement-bundles/checkout/beta/requirements.yaml", "type": "blob"},
                ]
            }
        raise AssertionError(f"Unexpected request path: {path}")

    monkeypatch.setattr(service, "_request", _fake_request)

    def _fake_inspect(bundle_ref: BundleRef):
        title = "Alpha" if bundle_ref.path.endswith("/alpha") else "Beta"
        domain = "payments" if title == "Alpha" else "checkout"
        return RequirementBundleInspectResponse(
            manifest_ref=bundle_ref,
            bundle_ref=bundle_ref,
            manifest={
                "bundle_id": f"RB-{title.lower()}",
                "title": title,
                "status": "draft",
                "scope": {"domain": domain},
            },
            requirements_file="requirements.yaml",
            test_cases_file="test-cases.yaml",
            requirements_exists=True,
            test_cases_exists=False,
            last_commit_sha="abc123",
        )

    monkeypatch.setattr(service, "inspect_bundle", _fake_inspect)

    results = service.list_bundles()

    assert len(results) == 2
    assert [item.title for item in results] == ["Beta", "Alpha"]
    assert results[0].bundle_ref.path == "requirement-bundles/checkout/beta"
    assert results[1].bundle_ref.path == "requirement-bundles/payments/alpha"
    assert results[0].bundle_id == "RB-beta"


def test_list_bundles_skips_invalid_branch_or_manifest(monkeypatch):
    service = RequirementBundleGithubService()
    monkeypatch.setattr(service, "default_repo", "octo/assets")
    monkeypatch.setattr(service, "bundle_root_dir", "requirement-bundles")

    def _fake_request(method: str, path: str, *, json_body=None):
        assert method == "GET"
        if "matching-refs/heads/bundle/" in path:
            return [
                {"ref": "refs/heads/bundle/good/aaa111"},
                {"ref": "refs/heads/bundle/bad/bbb222"},
            ]
        if "trees/bundle/good/aaa111?recursive=1" in path:
            return {
                "tree": [{"path": "requirement-bundles/payments/good/bundle.yaml", "type": "blob"}]
            }
        if "trees/bundle/bad/bbb222?recursive=1" in path:
            raise RequirementBundleGithubServiceError("tree fetch failed")
        raise AssertionError(f"Unexpected path: {path}")

    monkeypatch.setattr(service, "_request", _fake_request)

    def _fake_inspect(bundle_ref: BundleRef):
        return RequirementBundleInspectResponse(
            manifest_ref=bundle_ref,
            bundle_ref=bundle_ref,
            manifest={
                "bundle_id": "RB-good",
                "title": "Good Bundle",
                "status": "draft",
                "scope": {"domain": "payments"},
            },
            requirements_file="requirements.yaml",
            test_cases_file="test-cases.yaml",
            requirements_exists=True,
            test_cases_exists=True,
            last_commit_sha="abc",
        )

    monkeypatch.setattr(service, "inspect_bundle", _fake_inspect)

    results = service.list_bundles()

    assert len(results) == 1
    assert results[0].bundle_id == "RB-good"
    assert results[0].bundle_ref.branch == "bundle/good/aaa111"
