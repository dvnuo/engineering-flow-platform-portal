import unittest
from types import SimpleNamespace


class K8sServiceNoopTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.services.k8s_service import K8sService
        cls.K8sService = K8sService

    def setUp(self):
        self.service = self.K8sService()
        self.service.enabled = False

    def test_repo_metadata_from_git_url(self):
        metadata = self.service._repo_metadata("git@github.com:Acme/Portal.git", "Feature/ABC")
        self.assertEqual(metadata["repo_slug"], "acme-portal")
        self.assertEqual(metadata["raw_repo_url"], "https://github.com/Acme/Portal.git")
        self.assertEqual(metadata["branch"], "feature-abc")
        self.assertTrue(metadata["repo_hash"])

    def test_build_git_clone_env(self):
        self.service.settings.k8s_git_token_key = "GIT_TOKEN"
        env = self.service._build_git_clone_env("https://github.com/acme/repo.git", "main")
        names = [item.name for item in env]
        self.assertIn("GIT_REPO_URL", names)
        self.assertIn("GIT_BRANCH", names)
        self.assertIn("GIT_TOKEN", names)
        self.assertEqual(self.service._build_git_clone_env("   ", "main"), [])

    def test_git_clone_shell_command(self):
        cmd = self.service._git_clone_shell_command("/skills-code")
        self.assertIn("GIT_ASKPASS", cmd)
        self.assertIn("x-access-token", cmd)
        self.assertIn('find "/skills-code" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +', cmd)
        self.assertNotIn("/app/*", cmd)

    def test_build_code_and_skill_init_containers_and_mounts(self):
        self.service.settings.default_agent_runtime_repo_url = "https://github.com/acme/runtime.git"
        self.service.settings.default_agent_runtime_branch = "runtime-main"
        self.service.settings.default_skill_repo_url = "https://github.com/acme/skills-default.git"
        agent = SimpleNamespace(id="a1", owner_user_id=1, mount_path="/root/.efp", skill_repo_url="https://github.com/acme/skills.git", skill_branch="skills-main")
        inits, mounts = self.service._build_code_and_skill_init_containers_and_mounts(agent)
        self.assertEqual({c.name for c in inits}, {"runtime-git-clone", "skills-git-clone"})
        mount_map = {m.mount_path: m.sub_path for m in mounts}
        self.assertTrue(mount_map["/app/.git"].endswith("runtime-code/.git"))
        self.assertTrue(mount_map["/app/src"].endswith("runtime-code/src"))
        self.assertTrue(mount_map["/app/skills"].endswith("skills-code"))
        self.assertNotIn("runtime-code/skills", mount_map["/app/skills"])

    def test_labels_annotations_include_runtime_and_skill_fields(self):
        self.service.settings.default_agent_runtime_repo_url = "https://github.com/acme/runtime.git"
        self.service.settings.default_agent_runtime_branch = "runtime-main"
        agent = SimpleNamespace(id="a1", owner_user_id=1, skill_repo_url="https://github.com/acme/skills.git", skill_branch="skills-main")
        labels = self.service._agent_common_labels(agent)
        for key in ["runtime-git-repo", "runtime-git-repo-hash", "runtime-git-branch", "skill-git-repo", "skill-git-repo-hash", "skill-git-branch"]:
            self.assertIn(key, labels)
        annotations = self.service._agent_metadata_annotations(agent)
        for key in ["efp/runtime-git-repo-url", "efp/runtime-git-branch", "efp/skill-git-repo-url", "efp/skill-git-branch"]:
            self.assertIn(key, annotations)


if __name__ == "__main__":
    unittest.main()
