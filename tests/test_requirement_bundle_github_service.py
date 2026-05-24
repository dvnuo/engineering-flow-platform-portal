import base64

import pytest
import yaml

from app.schemas.requirement_bundle import BundleRef, RequirementBundleCreateForm, RequirementBundleInspectResponse
from app.services.requirement_bundle_github_service import (
    RequirementBundleGithubService,
    RequirementBundleGithubServiceError,
)


def _manifest_payload(manifest_text: str) -> dict:
    return {"content": base64.b64encode(manifest_text.encode("utf-8")).decode("utf-8")}


def test_create_requirement_bundle(monkeypatch):
    service = RequirementBundleGithubService()
    monkeypatch.setattr(service, "default_repo", "octo/engineering-flow-platform-assets")
    monkeypatch.setattr(service, "bundle_root_dir", "requirement-bundles")
    monkeypatch.setattr("app.services.requirement_bundle_github_service.secrets.token_hex", lambda _n: "deadbeef")

    calls = []

    def _fake_request(method: str, path: str, *, json_body=None):
        calls.append((method, path, json_body))
        if path.endswith("/git/ref/heads/main"):
            return {"object": {"sha": "abc123"}}
        return {"ok": True}

    monkeypatch.setattr(service, "_request", _fake_request)

    bundle_ref = service.create_bundle(
        RequirementBundleCreateForm(
            title="Checkout Flow",
            domain="payments",
            slug="",
            base_branch="main",
        )
    )

    assert bundle_ref.path == "requirement-bundles/payments/checkout-flow"
    assert bundle_ref.branch == "bundle/checkout-flow/deadbeef"

    put_calls = [call for call in calls if call[0] == "PUT"]
    assert len(put_calls) == 3
    bundle_yaml = yaml.safe_load(base64.b64decode(put_calls[0][2]["content"]).decode("utf-8"))
    assert bundle_yaml["artifacts"]["requirements"] == "requirements.yaml"
    assert bundle_yaml["artifacts"]["test_cases"] == "test-cases.yaml"


def test_inspect_manifest_returns_bundle_and_artifacts(monkeypatch):
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
artifacts:
  requirements: requirements.yaml
  test_cases: test-cases.yaml
metadata: {}
"""
    monkeypatch.setattr(service, "_get_file", lambda *_: _manifest_payload(manifest_text))
    monkeypatch.setattr(service, "_file_exists", lambda _r, file_path, _b: file_path.endswith("requirements.yaml"))
    monkeypatch.setattr(service, "_latest_commit_sha_for_path", lambda *_: "cafebabe")

    result = service.inspect_bundle(
        BundleRef(repo="octo/engineering-flow-platform-assets", path="requirement-bundles/payments/checkout", branch="main")
    )
    assert result.bundle_label == "Requirement Bundle"
    assert len(result.artifacts) == 2
    assert result.artifacts[0].artifact_key == "requirements"
    assert result.requirements_file == "requirements.yaml"
    assert result.test_cases_file == "test-cases.yaml"
    assert result.requirements_exists is True
    assert result.test_cases_exists is False


def test_list_bundles_includes_bundle_fields(monkeypatch):
    service = RequirementBundleGithubService()
    monkeypatch.setattr(service, "default_repo", "octo/assets")
    monkeypatch.setattr(service, "bundle_root_dir", "requirement-bundles")

    def _fake_request(method: str, path: str, *, json_body=None):
        assert method == "GET"
        if "matching-refs/heads/bundle/" in path:
            return [{"ref": "refs/heads/bundle/checkout/aaa111"}]
        if "trees/bundle/checkout/aaa111?recursive=1" in path:
            return {"tree": [{"path": "requirement-bundles/payments/checkout/bundle.yaml", "type": "blob"}]}
        raise AssertionError(path)

    monkeypatch.setattr(service, "_request", _fake_request)
    monkeypatch.setattr(
        service,
        "inspect_bundle",
        lambda bundle_ref: RequirementBundleInspectResponse(
            manifest_ref=bundle_ref,
            bundle_ref=bundle_ref,
            manifest={"bundle_id": "RB-checkout", "title": "Checkout", "status": "draft", "scope": {"domain": "payments"}},
            bundle_label="Requirement Bundle",
            artifacts=[],
            requirements_file="requirements.yaml",
            test_cases_file="test-cases.yaml",
            requirements_exists=True,
            test_cases_exists=False,
            last_commit_sha="abc123",
        ),
    )

    results = service.list_bundles()
    assert len(results) == 1
    assert results[0].bundle_label == "Requirement Bundle"
    assert results[0].requirements_exists is True


def test_list_bundles_uses_cache_until_force_refresh(monkeypatch):
    service = RequirementBundleGithubService()
    monkeypatch.setattr(service, "default_repo", "octo/assets")
    monkeypatch.setattr(service, "bundle_root_dir", "requirement-bundles")
    monkeypatch.setattr(service, "bundle_list_cache_ttl_seconds", 60)

    request_calls = []
    inspect_calls = []

    def _fake_request(method: str, path: str, *, json_body=None):
        request_calls.append((method, path))
        assert method == "GET"
        if "matching-refs/heads/bundle/" in path:
            return [{"ref": "refs/heads/bundle/checkout/aaa111"}]
        if "trees/bundle/checkout/aaa111?recursive=1" in path:
            return {"tree": [{"path": "requirement-bundles/payments/checkout/bundle.yaml", "type": "blob"}]}
        raise AssertionError(path)

    def _fake_inspect(bundle_ref):
        inspect_calls.append(bundle_ref.path)
        return RequirementBundleInspectResponse(
            manifest_ref=bundle_ref,
            bundle_ref=bundle_ref,
            manifest={"bundle_id": "RB-checkout", "title": "Checkout", "status": "draft", "scope": {"domain": "payments"}},
            bundle_label="Requirement Bundle",
            artifacts=[],
            requirements_file="requirements.yaml",
            test_cases_file="test-cases.yaml",
            requirements_exists=True,
            test_cases_exists=False,
            last_commit_sha="abc123",
        )

    monkeypatch.setattr(service, "_request", _fake_request)
    monkeypatch.setattr(service, "inspect_bundle", _fake_inspect)

    first = service.list_bundles()
    second = service.list_bundles()
    third = service.list_bundles(force_refresh=True)

    assert first is not second
    assert first[0] is not second[0]
    assert len(request_calls) == 4
    assert len(inspect_calls) == 2
    assert len(third) == 1


def test_invalid_manifest_raises_error(monkeypatch):
    service = RequirementBundleGithubService()
    invalid_manifest_text = "bundle_id: RB-1\ntitle: Missing Scope\n"
    monkeypatch.setattr(service, "_get_file", lambda *_: _manifest_payload(invalid_manifest_text))
    with pytest.raises(RequirementBundleGithubServiceError):
        service.inspect_bundle(BundleRef(repo="octo/assets", path="requirement-bundles/a/b", branch="main"))


def test_manifest_requires_artifact_map(monkeypatch):
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
"""
    monkeypatch.setattr(service, "_get_file", lambda *_: _manifest_payload(manifest_text))
    with pytest.raises(RequirementBundleGithubServiceError, match="artifacts"):
        service.inspect_bundle(BundleRef(repo="octo/assets", path="requirement-bundles/payments/checkout", branch="main"))
