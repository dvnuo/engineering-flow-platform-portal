import base64

import pytest
import yaml

from app.schemas.requirement_bundle import BundleRef, RequirementBundleCreateForm, RequirementBundleInspectResponse

from app.services.bundle_template_registry import resolve_bundle_template_from_manifest
from app.services.requirement_bundle_github_service import (
    RequirementBundleGithubService,
    RequirementBundleGithubServiceError,
)


def _manifest_payload(manifest_text: str) -> dict:
    return {"content": base64.b64encode(manifest_text.encode("utf-8")).decode("utf-8")}


def test_create_requirement_template_bundle(monkeypatch):
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
            template_id="requirement.v1",
            title="Checkout Flow",
            domain="payments",
            slug="",
            base_branch="main",
        )
    )

    assert bundle_ref.path == "requirement-bundles/payments/checkout-flow"
    assert bundle_ref.branch == "bundle/checkout-flow/deadbeef"

    put_calls = [c for c in calls if c[0] == "PUT"]
    assert len(put_calls) == 3
    bundle_yaml = yaml.safe_load(base64.b64decode(put_calls[0][2]["content"]).decode("utf-8"))
    assert bundle_yaml["template_id"] == "requirement.v1"
    assert bundle_yaml["template_version"] == 1
    assert bundle_yaml["artifacts"]["requirements"] == "requirements.yaml"
    assert bundle_yaml["artifacts"]["test_cases"] == "test-cases.yaml"


@pytest.mark.parametrize(
    "template_id,expected_path,expected_branch,prefix,artifact_files",
    [
        ("research.v1", "requirement-bundles/research/payments/checkout-flow", "bundle/research/checkout-flow/deadbeef", "RS", {"research-notes.yaml"}),
        ("development.v1", "requirement-bundles/development/payments/checkout-flow", "bundle/development/checkout-flow/deadbeef", "DEV", {"implementation-plan.yaml"}),
        ("operations.v1", "requirement-bundles/operations/payments/checkout-flow", "bundle/operations/checkout-flow/deadbeef", "OPS", {"runbook.yaml"}),
    ],
)
def test_create_non_requirement_template_bundle(monkeypatch, template_id, expected_path, expected_branch, prefix, artifact_files):
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
            template_id=template_id,
            title="Checkout Flow",
            domain="payments",
            slug="",
            base_branch="main",
        )
    )
    assert bundle_ref.path == expected_path
    assert bundle_ref.branch == expected_branch

    put_calls = [c for c in calls if c[0] == "PUT"]
    assert len(put_calls) == 2
    bundle_yaml = yaml.safe_load(base64.b64decode(put_calls[0][2]["content"]).decode("utf-8"))
    assert bundle_yaml["bundle_id"].startswith(f"{prefix}-")
    created_files = {call[1].split("/contents/", 1)[1].split("/")[-1] for call in put_calls[1:]}
    assert created_files == artifact_files


def test_inspect_new_manifest_returns_template_and_artifacts(monkeypatch):
    service = RequirementBundleGithubService()
    manifest_text = """bundle_id: RS-checkout
template_id: research.v1
template_version: 1
title: Checkout
status: draft
scope:
  domain: payments
  summary: Checkout
storage:
  repo: octo/engineering-flow-platform-assets
  path: requirement-bundles/research/payments/checkout
  base_branch: main
  working_branch: bundle/research/checkout/abcd1234
artifacts:
  research_notes: research-notes.yaml
metadata: {}
"""
    monkeypatch.setattr(service, "_get_file", lambda *_: _manifest_payload(manifest_text))
    monkeypatch.setattr(service, "_file_exists", lambda *_: True)
    monkeypatch.setattr(service, "_latest_commit_sha_for_path", lambda *_: "cafebabe")

    result = service.inspect_bundle(
        BundleRef(repo="octo/engineering-flow-platform-assets", path="requirement-bundles/research/payments/checkout", branch="main")
    )
    assert result.template_id == "research.v1"
    assert result.template_label == "Research Bundle"
    assert result.template_version == 1
    assert len(result.artifacts) == 1
    assert result.artifacts[0].artifact_key == "research_notes"


def test_inspect_legacy_manifest_falls_back_to_requirement_template(monkeypatch):
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
    monkeypatch.setattr(service, "_get_file", lambda *_: _manifest_payload(manifest_text))
    monkeypatch.setattr(service, "_file_exists", lambda _r, file_path, _b: file_path.endswith("requirements.yaml"))
    monkeypatch.setattr(service, "_latest_commit_sha_for_path", lambda *_: "cafebabe")

    result = service.inspect_bundle(
        BundleRef(repo="octo/engineering-flow-platform-assets", path="requirement-bundles/payments/checkout", branch="main")
    )
    assert result.template_id == "requirement.v1"
    assert result.template_label == "Requirement Bundle"
    assert result.requirements_file == "requirements.yaml"
    assert result.test_cases_file == "test-cases.yaml"
    assert result.requirements_exists is True
    assert result.test_cases_exists is False


def test_list_bundles_includes_template_fields(monkeypatch):
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
            template_id="requirement.v1",
            template_label="Requirement Bundle",
            template_version=1,
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
    assert results[0].template_id == "requirement.v1"
    assert results[0].template_label == "Requirement Bundle"


def test_invalid_manifest_raises_error(monkeypatch):
    service = RequirementBundleGithubService()
    invalid_manifest_text = "bundle_id: RB-1\ntitle: Missing Scope\n"
    monkeypatch.setattr(service, "_get_file", lambda *_: _manifest_payload(invalid_manifest_text))
    with pytest.raises(RequirementBundleGithubServiceError):
        service.inspect_bundle(BundleRef(repo="octo/assets", path="requirement-bundles/a/b", branch="main"))


def test_registry_resolve_template_requires_template_id_or_links():
    with pytest.raises(ValueError) as exc_info:
        resolve_bundle_template_from_manifest({"bundle_id": "RB-missing"})
    assert "bundle.yaml requires 'template_id' or legacy 'links'" in str(exc_info.value)


def test_service_resolve_template_wraps_registry_value_error():
    service = RequirementBundleGithubService()
    with pytest.raises(RequirementBundleGithubServiceError) as exc_info:
        service._resolve_template_from_manifest({"bundle_id": "RB-missing"})
    assert "bundle.yaml requires 'template_id' or legacy 'links'" in str(exc_info.value)
