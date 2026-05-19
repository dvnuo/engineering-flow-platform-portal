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
        self._settings_snapshot = {
            name: getattr(self.service.settings, name)
            for name in (
                "enable_runtime_source_overlay",
                "default_agent_runtime_repo_url",
                "default_agent_runtime_branch",
                "default_skill_repo_url",
                "default_skill_branch",
                "default_skill_repo_subdir",
                "default_skill_asset_version",
            )
        }
        self.service.settings.enable_runtime_source_overlay = False
        self.service.settings.default_agent_runtime_repo_url = ""
        self.service.settings.default_agent_runtime_branch = ""
        self.service.settings.default_skill_repo_url = ""
        self.service.settings.default_skill_branch = "master"
        self.service.settings.default_skill_repo_subdir = ""
        self.service.settings.default_skill_asset_version = ""

    def tearDown(self):
        for name, value in self._settings_snapshot.items():
            setattr(self.service.settings, name, value)

    def _find_init_container(self, containers, name):
        return next(c for c in containers if c.name == name)

    def test_native_skill_clone_uses_full_package_command_and_mounts_app_skills(self):
        agent = SimpleNamespace(id="a1", owner_user_id=1, runtime_type="native", mount_path="/root/.efp", skill_repo_url="https://example.com/skills.git", skill_branch="main")
        inits, mounts = self.service._build_code_and_skill_init_containers_and_mounts(agent)
        init_names = {c.name for c in inits}
        self.assertIn("skills-git-clone", init_names)
        self.assertNotIn("tools-git-clone", init_names)
        self.assertNotIn("/app/tools", {m.mount_path for m in mounts})
        self.assertIn("/app/skills", {m.mount_path for m in mounts})
        skill_init = self._find_init_container(inits, "skills-git-clone")
        command = skill_init.args[0]
        self.assertIn("SKILL_REPO_SUBDIR", command)
        self.assertIn('cp -a "${SOURCE_DIR}"/.', command)
        self.assertIn("No skill entries found", command)
        self.assertIn("SKILL.md", command)

    def test_opencode_skill_clone_uses_full_package_command_and_mounts_app_skills(self):
        agent = SimpleNamespace(id="a1", owner_user_id=1, runtime_type="opencode", mount_path="/workspace", skill_repo_url="https://example.com/skills.git", skill_branch="main")
        inits, mounts = self.service._build_code_and_skill_init_containers_and_mounts(agent)
        init_names = {c.name for c in inits}
        self.assertIn("skills-git-clone", init_names)
        self.assertNotIn("tools-git-clone", init_names)
        self.assertNotIn("/app/tools", {m.mount_path for m in mounts})
        self.assertIn("/app/skills", {m.mount_path for m in mounts})
        skill_init = self._find_init_container(inits, "skills-git-clone")
        command = skill_init.args[0]
        self.assertIn("SKILL_REPO_SUBDIR", command)
        self.assertIn('cp -a "${SOURCE_DIR}"/.', command)
        self.assertIn("No skill entries found", command)
        self.assertIn("SKILL.md", command)

    def test_default_skill_repo_subdir_passed_to_skill_clone_env(self):
        self.service.settings.default_skill_repo_subdir = "skills"
        agent = SimpleNamespace(id="a1", owner_user_id=1, runtime_type="opencode", mount_path="/workspace", skill_repo_url="https://example.com/skills.git", skill_branch="main")
        inits, _ = self.service._build_code_and_skill_init_containers_and_mounts(agent)
        skill_init = self._find_init_container(inits, "skills-git-clone")
        env_map = {e.name: getattr(e, "value", None) for e in skill_init.env}
        self.assertEqual(env_map["SKILL_REPO_SUBDIR"], "skills")

    def test_skill_repo_subdir_rejects_parent_path(self):
        self.service.settings.default_skill_repo_subdir = "../skills"
        agent = SimpleNamespace(id="a1", owner_user_id=1, runtime_type="native")
        with self.assertRaises(ValueError):
            self.service._skill_repo_subdir(agent)

    def test_skill_repo_subdir_strips_slashes(self):
        self.service.settings.default_skill_repo_subdir = "/packages/skills/"
        agent = SimpleNamespace(id="a1", owner_user_id=1, runtime_type="native")
        self.assertEqual(self.service._skill_repo_subdir(agent), "packages/skills")

    def test_skill_asset_version_annotation_forces_rollout(self):
        self.service.settings.default_skill_asset_version = "sha-abc123"
        agent = SimpleNamespace(id="a1", owner_user_id=1, runtime_type="native", skill_repo_url="https://example.com/skills.git", skill_branch="main")
        self.assertEqual(self.service._agent_metadata_annotations(agent)["efp/skill-asset-version"], "sha-abc123")
        self.assertEqual(self.service._agent_patch_annotations(agent)["efp/skill-asset-version"], "sha-abc123")

    def test_skill_subdir_annotation_forces_rollout(self):
        self.service.settings.default_skill_repo_subdir = "skills"
        agent = SimpleNamespace(id="a1", owner_user_id=1, runtime_type="native", skill_repo_url="https://example.com/skills.git", skill_branch="main")
        self.assertEqual(self.service._agent_metadata_annotations(agent)["efp/skill-git-subdir"], "skills")
        self.assertEqual(self.service._agent_patch_annotations(agent)["efp/skill-git-subdir"], "skills")

    def test_annotations_do_not_include_git_token_or_secret(self):
        self.service.settings.default_skill_asset_version = "sha-abc123"
        self.service.settings.default_skill_repo_subdir = "skills"
        agent = SimpleNamespace(id="a1", owner_user_id=1, runtime_type="native", skill_repo_url="https://example.com/skills.git", skill_branch="main")
        annotations = self.service._agent_metadata_annotations(agent)
        for key, value in annotations.items():
            self.assertNotIn("GIT_TOKEN", key)
            self.assertNotIn("GIT_TOKEN", value)
            self.assertNotIn("secret", key.lower())
            self.assertNotIn("secret", value.lower())
            self.assertNotIn("example-token", value)

    def test_skill_git_clone_command_is_static_and_validates_entries(self):
        command = self.service._skill_git_clone_shell_command("/skills-code")
        self.assertIn("set -eu", command)
        self.assertIn("git clone --depth 1 --branch", command)
        self.assertIn("SOURCE_DIR", command)
        self.assertIn("SKILL_REPO_SUBDIR", command)
        self.assertIn("cp -a", command)
        self.assertIn("find", command)
        self.assertIn("No skill entries found", command)
        self.assertNotIn("https://example.com", command)
        self.assertNotIn("main", command)

    def test_runtime_source_overlay_still_uses_generic_git_clone_command(self):
        self.service.settings.enable_runtime_source_overlay = True
        self.service.settings.default_agent_runtime_repo_url = "https://example.com/runtime.git"
        self.service.settings.default_agent_runtime_branch = "main"
        agent = SimpleNamespace(id="a1", owner_user_id=1, runtime_type="native", mount_path="/root/.efp", skill_repo_url=None, skill_branch="main")
        inits, mounts = self.service._build_code_and_skill_init_containers_and_mounts(agent)
        runtime_init = self._find_init_container(inits, "runtime-git-clone")
        command = runtime_init.args[0]
        self.assertIn("cp -a /tmp/git-clone-work/.", command)
        self.assertNotIn("SKILL_REPO_SUBDIR", command)
        self.assertNotIn("No skill entries found", command)
        self.assertIn("/app/src", {m.mount_path for m in mounts})
        self.assertIn("/app/.git", {m.mount_path for m in mounts})

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
