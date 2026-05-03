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

    def test_native_runtime_keeps_runtime_and_skill_mounts(self):
        self.service.settings.default_agent_runtime_repo_url = "https://github.com/acme/runtime.git"
        self.service.settings.default_agent_runtime_branch = "runtime-main"
        self.service.settings.default_skill_repo_url = "https://github.com/acme/skills-default.git"
        agent = SimpleNamespace(
            id="a1",
            owner_user_id=1,
            runtime_type="native",
            mount_path="/root/.efp",
            skill_repo_url="https://github.com/acme/skills.git",
            skill_branch="skills-main",
        )
        inits, mounts = self.service._build_code_and_skill_init_containers_and_mounts(agent)
        self.assertEqual({c.name for c in inits}, {"runtime-git-clone", "skills-git-clone"})
        mount_paths = {m.mount_path for m in mounts}
        self.assertIn("/app/.git", mount_paths)
        self.assertIn("/app/src", mount_paths)
        self.assertIn("/app/skills", mount_paths)
        self.assertIn("/root/.efp", mount_paths)
        self.assertNotIn("/workspace/tools", mount_paths)
        self.assertNotIn("tools-git-clone", {c.name for c in inits})

    def test_opencode_runtime_clones_tools_into_workspace_without_native_mounts(self):
        self.service.settings.default_agent_runtime_repo_url = "https://github.com/acme/runtime.git"
        self.service.settings.default_skill_repo_url = "https://github.com/acme/skills-default.git"
        agent = SimpleNamespace(
            id="a2",
            owner_user_id=1,
            runtime_type="opencode",
            mount_path="/workspace",
            tool_repo_url="git@github.com:Acme/Tools.git",
            tool_branch="tools-main",
        )
        inits, mounts = self.service._build_code_and_skill_init_containers_and_mounts(agent)
        self.assertEqual([c.name for c in inits], ["tools-git-clone"])
        clone_env = {e.name: e.value for e in inits[0].env if getattr(e, "value", None)}
        self.assertEqual(clone_env["GIT_REPO_URL"], "https://github.com/Acme/Tools.git")
        self.assertIn("/workspace-data/tools", inits[0].args[0])
        mount_paths = {m.mount_path for m in mounts}
        self.assertIn("/workspace", mount_paths)
        self.assertNotIn("/app/src", mount_paths)
        self.assertNotIn("/app/.git", mount_paths)
        self.assertNotIn("/app/skills", mount_paths)

    def test_labels_annotations_include_runtime_type_and_tool_fields(self):
        agent = SimpleNamespace(
            id="a1",
            owner_user_id=1,
            runtime_type="opencode",
            tool_repo_url="https://github.com/acme/tools.git",
            tool_branch="tools-main",
            skill_repo_url="https://github.com/acme/skills.git",
            skill_branch="skills-main",
        )
        labels = self.service._agent_common_labels(agent)
        for key in ["runtime-type", "tool-git-repo", "tool-git-repo-hash", "tool-git-branch"]:
            self.assertIn(key, labels)
        annotations = self.service._agent_metadata_annotations(agent)
        for key in ["efp/runtime-type", "efp/tool-git-repo-url", "efp/tool-git-branch"]:
            self.assertIn(key, annotations)

    def test_build_agent_container_env_includes_opencode_workspace_env(self):
        agent = SimpleNamespace(id="a1", runtime_type="opencode", mount_path="/workspace")
        env = self.service._build_agent_container_env(agent)
        names = {item.name for item in env}
        for key in [
            "PORTAL_RUNTIME_TYPE",
            "EFP_RUNTIME_TYPE",
            "EFP_WORKSPACE_DIR",
            "OPENCODE_WORKSPACE",
            "EFP_TOOLS_DIR",
            "OPENCODE_TOOLS_DIR",
        ]:
            self.assertIn(key, names)
        self.assertNotIn("GIT_TOKEN", names)

    def test_build_agent_container_resources_uses_requests(self):
        agent = SimpleNamespace(cpu="500m", memory="1Gi")
        resources = self.service._build_agent_container_resources(agent)
        self.assertEqual(resources.requests["cpu"], "500m")
        self.assertEqual(resources.requests["memory"], "1Gi")
        self.assertIsNone(resources.limits)


if __name__ == "__main__":
    unittest.main()
