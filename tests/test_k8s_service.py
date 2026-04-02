import unittest
from types import SimpleNamespace


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

    def test_repo_metadata_from_git_url(self):
        agent = SimpleNamespace(repo_url="git@github.com:Acme/Portal.git", branch="Feature/ABC")
        metadata = self.service._repo_metadata(agent)
        self.assertEqual(metadata["repo_slug"], "acme-portal")
        self.assertEqual(metadata["repo_hash"], "db405ee23bb4")
        self.assertEqual(metadata["branch"], "feature-abc")

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


if __name__ == "__main__":
    unittest.main()
