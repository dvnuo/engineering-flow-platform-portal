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
        self.service.settings.default_agent_runtime_repo_url = ""
        self.service.settings.default_agent_runtime_branch = ""
        self.service.settings.default_skill_repo_url = ""
        self.service.settings.default_skill_branch = "master"

    def test_native_init_contains_skills_not_tools(self):
        agent = SimpleNamespace(id="a1", owner_user_id=1, runtime_type="native", mount_path="/root/.efp", skill_repo_url="https://example.com/skills.git", skill_branch="main")
        inits, mounts = self.service._build_code_and_skill_init_containers_and_mounts(agent)
        init_names = {c.name for c in inits}
        self.assertIn("skills-git-clone", init_names)
        self.assertNotIn("tools-git-clone", init_names)
        self.assertNotIn("/app/tools", {m.mount_path for m in mounts})
        self.assertIn("/app/skills", {m.mount_path for m in mounts})

    def test_opencode_env_excludes_tools_contract(self):
        agent = SimpleNamespace(id="a1", runtime_type="opencode", mount_path="/workspace")
        env = self.service._build_agent_container_env(agent)
        env_map = {e.name: getattr(e, 'value', None) for e in env}
        self.assertNotIn("EFP_TOOLS_DIR", env_map)
        self.assertNotIn("OPENCODE_TOOLS_DIR", env_map)
        self.assertNotIn("EFP_TOOLS_STRICT_MODE", env_map)
        self.assertNotIn("EFP_OPENCODE_TOOL_REGISTRY_TIMEOUT_SECONDS", env_map)
        self.assertNotIn("EFP_OPENCODE_TOOL_REGISTRY_REQUEST_TIMEOUT_SECONDS", env_map)
        self.assertIn("EFP_SKILLS_DIR", env_map)

    def test_labels_annotations_exclude_tool_git_metadata(self):
        agent = SimpleNamespace(id="a1", owner_user_id=1, runtime_type="native", skill_repo_url="https://example.com/skills.git", skill_branch="main")
        labels = self.service._agent_common_labels(agent)
        anns = self.service._agent_metadata_annotations(agent)
        for k in labels.keys():
            self.assertNotIn("tool-git", k)
        for k in anns.keys():
            self.assertNotIn("tool-git", k)

if __name__ == '__main__':
    unittest.main()
