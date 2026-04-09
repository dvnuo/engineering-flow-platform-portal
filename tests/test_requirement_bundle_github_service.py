import base64

import pytest

from app.schemas.requirement_bundle import BundleRef, RequirementBundleCreateForm
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
    assert result.requirements_exists is True
    assert result.test_cases_exists is False
    assert result.last_commit_sha == "cafebabe"


def test_invalid_manifest_raises_error(monkeypatch):
    service = RequirementBundleGithubService()
    invalid_manifest_text = "bundle_id: RB-1\ntitle: Missing Scope\n"
    manifest_payload = {"content": base64.b64encode(invalid_manifest_text.encode("utf-8")).decode("utf-8")}

    monkeypatch.setattr(service, "_get_file", lambda _repo, _path, _branch: manifest_payload)

    with pytest.raises(RequirementBundleGithubServiceError) as exc_info:
        service.inspect_bundle(BundleRef(repo="octo/assets", path="requirement-bundles/a/b", branch="main"))

    assert "missing required field" in str(exc_info.value)


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
