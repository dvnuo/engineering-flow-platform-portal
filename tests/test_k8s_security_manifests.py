"""The sample manifests give the Portal its SECRET_KEY and a host of its own."""

from pathlib import Path

import pytest
import yaml

from app.config import Settings

REPO = Path(__file__).resolve().parents[1]
DEPLOYMENTS = ("k8s/efp-portal-deployment.yaml", "k8s/portal-git-clone/efp-portal-deployment.yaml")


def _documents(relative: str) -> list[dict]:
    return [doc for doc in yaml.safe_load_all((REPO / relative).read_text(encoding="utf-8")) if doc]


def _portal_env(relative: str) -> dict[str, dict]:
    deployment = next(doc for doc in _documents(relative) if doc["kind"] == "Deployment")
    container = next(c for c in deployment["spec"]["template"]["spec"]["containers"] if c["name"] == "portal-container")
    return {entry["name"]: entry for entry in container["env"]}


@pytest.mark.parametrize("relative", DEPLOYMENTS)
def test_both_deployments_take_secret_key_from_the_portal_secret(relative):
    env = _portal_env(relative)
    reference = env["SECRET_KEY"]["valueFrom"]["secretKeyRef"]
    assert reference["name"] == "efp-portal-secret"
    assert reference["key"] == "SECRET_KEY"
    assert "value" not in env["SECRET_KEY"]
    # The same Secret the bootstrap password already comes from.
    assert env["BOOTSTRAP_ADMIN_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"] == "efp-portal-secret"


@pytest.mark.parametrize("relative", DEPLOYMENTS)
def test_the_samples_do_not_opt_out_of_the_checks(relative):
    env = _portal_env(relative)
    for name in ("ALLOW_INSECURE_SECRET_KEY", "ORIGIN_GUARD_ENABLED", "INTERNAL_API_BLOCK_FORWARDED"):
        assert name not in env


def test_the_sample_secret_has_a_secret_key_entry():
    (secret,) = _documents("k8s/portal-git-clone/efp-portal-secret.yaml")
    assert secret["metadata"]["name"] == "efp-portal-secret"
    # A placeholder: an empty key is refused at startup like the default one.
    assert secret["stringData"]["SECRET_KEY"] == ""


def test_the_sample_ingress_names_a_host():
    (ingress,) = _documents("k8s/efp-portal-ingress.yaml")
    rules = ingress["spec"]["rules"]
    assert len(rules) == 1
    assert rules[0]["host"] == "portal.example.com"
    assert rules[0]["http"]["paths"][0]["backend"]["service"]["name"] == "efp-portal-service"


def test_the_k8s_readme_explains_the_new_settings():
    text = (REPO / "k8s/README.md").read_text(encoding="utf-8")
    for needle in (
        "SECRET_KEY",
        "ALLOW_INSECURE_SECRET_KEY",
        "ORIGIN_GUARD_ENABLED",
        "INTERNAL_API_AUTH_REQUIRED",
        "INTERNAL_API_BLOCK_FORWARDED",
        "EFP_PORTAL_TRUST_TOKEN",
        "X-Portal-Trust-Token",
        "__Host-portal_session",
        "portal.example.com",
    ):
        assert needle in text, needle
    # The entry that used to be a manual step is in the manifests now.
    assert "(add this key)" not in text


def test_new_settings_defaults_and_environment(monkeypatch):
    for name in ("ORIGIN_GUARD_ENABLED", "ALLOW_INSECURE_SECRET_KEY", "INTERNAL_API_AUTH_REQUIRED", "INTERNAL_API_BLOCK_FORWARDED"):
        monkeypatch.delenv(name, raising=False)
    defaults = Settings(_env_file=None)
    assert defaults.origin_guard_enabled is True
    assert defaults.allow_insecure_secret_key is False
    assert defaults.internal_api_auth_required is False
    assert defaults.internal_api_block_forwarded is True

    monkeypatch.setenv("ORIGIN_GUARD_ENABLED", "false")
    assert Settings(_env_file=None).origin_guard_enabled is False
