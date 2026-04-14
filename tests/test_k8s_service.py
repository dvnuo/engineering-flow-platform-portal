import unittest
from types import SimpleNamespace
from pathlib import Path

from app.utils.git_urls import normalize_git_repo_url


class K8sServiceNoopTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from app.services.k8s_service import K8sService
        except ModuleNotFoundError as exc:
            raise unittest.SkipTest(f"Missing dependency in environment: {exc}")
        cls.K8sService = K8sService

    def setUp(self):
        self.service = self.K8sService()
        self.service.enabled = False

    def test_create_agent_runtime_noop_running(self):
        agent = SimpleNamespace()
        status = self.service.create_agent_runtime(agent)
        self.assertEqual(status.status, "running")

    def test_stop_agent_runtime_noop_stopped(self):
        agent = SimpleNamespace()
        status = self.service.stop_agent(agent)
        self.assertEqual(status.status, "stopped")

    def test_sanitize_label_value(self):
        self.assertEqual(self.service._sanitize_label_value("Org/Repo_Name"), "org-repo-name")
        self.assertEqual(self.service._sanitize_label_value(""), "unknown")

    def test_sanitize_label_value_truncation_strips_trailing_dash(self):
        value = ("a-" * 32) + "b"
        sanitized = self.service._sanitize_label_value(value)
        self.assertFalse(sanitized.endswith("-"))
        self.assertEqual(self.service._sanitize_label_value("---"), "unknown")

    def test_repo_metadata_from_git_url(self):
        agent = SimpleNamespace(repo_url="git@github.com:Acme/Portal.git", branch="Feature/ABC")
        metadata = self.service._repo_metadata(agent)
        self.assertEqual(metadata["repo_slug"], "acme-portal")
        self.assertEqual(metadata["repo_hash"], "4ddc9d751723")
        self.assertEqual(metadata["branch"], "feature-abc")
        self.assertEqual(metadata["raw_repo_url"], "https://github.com/Acme/Portal.git")

    def test_repo_metadata_from_enterprise_ssh_url_is_canonicalized(self):
        agent = SimpleNamespace(repo_url="ssh://git@github.company.com:8443/Acme/Portal.git", branch="main")
        metadata = self.service._repo_metadata(agent)
        self.assertEqual(metadata["repo_slug"], "acme-portal")
        self.assertEqual(metadata["raw_repo_url"], "https://github.company.com:8443/Acme/Portal.git")

    def test_agent_metadata_annotations_use_canonical_repo_url(self):
        agent = SimpleNamespace(repo_url="git@github.com:Acme/Portal.git", branch="main")
        annotations = self.service._agent_metadata_annotations(agent)
        self.assertEqual(annotations["efp/git-repo-url"], "https://github.com/Acme/Portal.git")

    def test_agent_common_labels_include_git_fields(self):
        agent = SimpleNamespace(
            id="agent-1",
            owner_user_id=7,
            repo_url="https://github.com/org/repo.git",
            branch="main",
        )
        labels = self.service._agent_common_labels(agent)
        self.assertEqual(labels["git-repo"], "org-repo")
        self.assertEqual(labels["git-repo-hash"], "8fc28d240c61")
        self.assertEqual(labels["git-branch"], "main")

    def test_agent_patch_annotations_deletes_repo_url(self):
        agent = SimpleNamespace(repo_url="", branch="main")
        annotations = self.service._agent_patch_annotations(agent)
        self.assertEqual(
            annotations,
            {"efp/git-repo-url": None, "efp/git-branch": "main"},
        )

    def test_agent_container_env_includes_config_key_and_optional_portal_base_url(self):
        self.service.settings.portal_internal_base_url = "http://portal.internal.svc"

        env = self.service._build_agent_container_env(SimpleNamespace(id="agent-123"))
        names = [item.name for item in env]

        self.assertIn("EFP_CONFIG_KEY", names)
        self.assertIn("PORTAL_INTERNAL_BASE_URL", names)
        self.assertIn("PORTAL_AGENT_ID", names)
        removed_runtime_env = "_".join(["RUNTIME", "INTERNAL", "API", "KEY"])
        self.assertNotIn(removed_runtime_env, names)

        portal_base = next(item for item in env if item.name == "PORTAL_INTERNAL_BASE_URL")
        self.assertEqual(portal_base.value, "http://portal.internal.svc")
        portal_agent_id = next(item for item in env if item.name == "PORTAL_AGENT_ID")
        self.assertEqual(portal_agent_id.value, "agent-123")

    def test_agent_container_env_omits_empty_portal_internal_base_url(self):
        self.service.settings.portal_internal_base_url = "   "

        env = self.service._build_agent_container_env(SimpleNamespace(id="agent-123"))
        names = [item.name for item in env]

        self.assertIn("EFP_CONFIG_KEY", names)
        self.assertNotIn("PORTAL_INTERNAL_BASE_URL", names)

    def test_git_clone_shell_command_uses_askpass_not_credential_url(self):
        command = self.service._git_clone_shell_command()
        self.assertNotIn("https://${GIT_USERNAME}:${GIT_TOKEN}@", command)
        self.assertNotIn("${GIT_USERNAME}", command)
        self.assertNotIn(":443", command)
        self.assertNotIn("http.sslVerify=false", command)
        self.assertIn("GIT_ASKPASS", command)
        self.assertIn("x-access-token", command)
        self.assertIn("*Username*|*username*", command)

    def test_build_git_clone_env_includes_token_without_username(self):
        self.service.settings.k8s_git_token_key = "GIT_TOKEN"
        agent = SimpleNamespace(repo_url="https://github.com/acme/repo.git", branch="main")

        env = self.service._build_git_clone_env(agent)
        names = [item.name for item in env]

        self.assertIn("GIT_REPO_URL", names)
        self.assertIn("GIT_BRANCH", names)
        self.assertIn("GIT_TOKEN", names)
        self.assertNotIn("GIT_USERNAME", names)

    def test_build_git_clone_env_returns_empty_for_blank_repo_url(self):
        self.service.settings.k8s_git_token_key = "GIT_TOKEN"
        agent = SimpleNamespace(repo_url="   ", branch="main")

        env = self.service._build_git_clone_env(agent)

        self.assertEqual(env, [])

    def test_checked_in_k8s_manifests_include_lowercase_username_prompt_match(self):
        manifest_paths = [
            "k8s/efp-portal-deployment.yaml",
            "k8s/portal-git-clone/efp-portal-deployment.yaml",
        ]
        for path in manifest_paths:
            content = Path(path).read_text(encoding="utf-8")
            self.assertIn("*Username*|*username*)", content)

    def test_normalize_git_repo_url_ssh_to_https(self):
        self.assertEqual(
            normalize_git_repo_url("git@github.com:Acme/Portal.git"),
            "https://github.com/Acme/Portal.git",
        )
        self.assertEqual(
            normalize_git_repo_url("ssh://git@github.com/Acme/Portal.git"),
            "https://github.com/Acme/Portal.git",
        )

    def test_normalize_git_repo_url_strips_https_credentials(self):
        self.assertEqual(
            normalize_git_repo_url("https://user:token@github.com/Acme/Portal.git"),
            "https://github.com/Acme/Portal.git",
        )
        self.assertEqual(
            normalize_git_repo_url("https://github.com/Acme/Portal.git"),
            "https://github.com/Acme/Portal.git",
        )

    def test_normalize_git_repo_url_preserves_explicit_port(self):
        self.assertEqual(
            normalize_git_repo_url("ssh://git@github.company.com:8443/Acme/Portal.git"),
            "https://github.company.com:8443/Acme/Portal.git",
        )
        self.assertEqual(
            normalize_git_repo_url("https://github.company.com:8443/Acme/Portal.git"),
            "https://github.company.com:8443/Acme/Portal.git",
        )

    def test_normalize_git_repo_url_strips_credentials_and_preserves_port(self):
        self.assertEqual(
            normalize_git_repo_url("https://user:token@github.company.com:8443/Acme/Portal.git"),
            "https://github.company.com:8443/Acme/Portal.git",
        )


if __name__ == "__main__":
    unittest.main()
