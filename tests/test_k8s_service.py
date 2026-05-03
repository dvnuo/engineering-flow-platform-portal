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

    def test_patch_deployment_uses_json_merge_patch_and_replaces_opencode_lists(self):
        class FakeAppsApi:
            def __init__(self):
                self.calls = []

            def patch_namespaced_deployment(self, **kwargs):
                self.calls.append(kwargs)

        self.service.enabled = True
        self.service.apps_api = FakeAppsApi()
        agent = SimpleNamespace(
            id="a1",
            owner_user_id=1,
            runtime_type="opencode",
            mount_path="/workspace",
            tool_repo_url=None,
            tool_branch=None,
            image="opencode:latest",
            deployment_name="agent-a",
            namespace="efp-agents",
        )
        self.service._patch_deployment(agent)
        call = self.service.apps_api.calls[0]
        self.assertEqual(call["_content_type"], "application/merge-patch+json")
        body = call["body"]
        self.assertEqual(body["spec"]["template"]["spec"]["initContainers"], [])
        containers = body["spec"]["template"]["spec"]["containers"]
        self.assertEqual(len(containers), 1)
        container = containers[0]
        self.assertEqual(container["name"], "agent")
        self.assertEqual(container["workingDir"], "/workspace")
        mount_paths = {m["mountPath"] for m in container["volumeMounts"]}
        self.assertEqual(mount_paths, {"/workspace"})
        self.assertNotIn("/app/src", mount_paths)
        self.assertNotIn("/app/.git", mount_paths)
        self.assertNotIn("/app/skills", mount_paths)
        volumes = body["spec"]["template"]["spec"]["volumes"]
        self.assertEqual(volumes[0]["persistentVolumeClaim"]["claimName"], "efp-agents-efs-pvc")

    def test_patch_deployment_opencode_with_tools_replaces_init_containers(self):
        class FakeAppsApi:
            def __init__(self):
                self.calls = []

            def patch_namespaced_deployment(self, **kwargs):
                self.calls.append(kwargs)

        self.service.enabled = True
        self.service.apps_api = FakeAppsApi()
        agent = SimpleNamespace(
            id="a2",
            owner_user_id=1,
            runtime_type="opencode",
            mount_path="/workspace",
            tool_repo_url="git@github.com:Acme/Tools.git",
            tool_branch="tools-main",
            image="opencode:latest",
            deployment_name="agent-b",
            namespace="efp-agents",
        )
        self.service._patch_deployment(agent)
        init_containers = self.service.apps_api.calls[0]["body"]["spec"]["template"]["spec"]["initContainers"]
        self.assertEqual(len(init_containers), 1)
        self.assertEqual(init_containers[0]["name"], "tools-git-clone")
        self.assertIn("/workspace-data/tools", init_containers[0]["args"][0])
        env_map = {item["name"]: item.get("value") for item in init_containers[0]["env"]}
        self.assertEqual(env_map["GIT_REPO_URL"], "https://github.com/Acme/Tools.git")
        names = {item["name"] for item in init_containers}
        self.assertNotIn("runtime-git-clone", names)
        self.assertNotIn("skills-git-clone", names)

    def test_patch_deployment_native_does_not_set_working_dir(self):
        class FakeAppsApi:
            def __init__(self):
                self.calls = []

            def patch_namespaced_deployment(self, **kwargs):
                self.calls.append(kwargs)

        self.service.settings.default_agent_runtime_repo_url = "https://github.com/acme/runtime.git"
        self.service.settings.default_skill_repo_url = "https://github.com/acme/skills.git"
        self.service.enabled = True
        self.service.apps_api = FakeAppsApi()
        agent = SimpleNamespace(
            id="a3",
            owner_user_id=1,
            runtime_type="native",
            mount_path="/root/.efp",
            image="native:latest",
            deployment_name="agent-c",
            namespace="efp-agents",
        )
        self.service._patch_deployment(agent)
        body = self.service.apps_api.calls[0]["body"]
        container = body["spec"]["template"]["spec"]["containers"][0]
        self.assertNotIn("workingDir", container)
        mount_paths = {m["mountPath"] for m in container["volumeMounts"]}
        self.assertIn("/app/.git", mount_paths)
        self.assertIn("/app/src", mount_paths)
        self.assertIn("/app/skills", mount_paths)
        self.assertIn("/root/.efp", mount_paths)
        init_names = {c["name"] for c in body["spec"]["template"]["spec"]["initContainers"]}
        self.assertIn("runtime-git-clone", init_names)
        self.assertIn("skills-git-clone", init_names)

    def test_ensure_deployment_sets_opencode_working_dir(self):
        class FakeAppsApi:
            def __init__(self):
                self.calls = []

            def create_namespaced_deployment(self, **kwargs):
                self.calls.append(kwargs)

        self.service.enabled = True
        self.service.apps_api = FakeAppsApi()
        agent = SimpleNamespace(
            id="a4",
            owner_user_id=1,
            runtime_type="opencode",
            mount_path="/workspace",
            image="opencode:latest",
            deployment_name="agent-d",
            namespace="efp-agents",
            service_name="svc-d",
        )
        self.service._ensure_deployment(agent)
        body = self.service.apps_api.calls[0]["body"]
        self.assertEqual(body.spec.template.spec.containers[0].working_dir, "/workspace")

    def test_patch_deployment_omits_resources_when_no_requests(self):
        class FakeAppsApi:
            def __init__(self):
                self.calls = []

            def patch_namespaced_deployment(self, **kwargs):
                self.calls.append(kwargs)

        self.service.enabled = True
        self.service.apps_api = FakeAppsApi()
        agent = SimpleNamespace(
            id="a5",
            owner_user_id=1,
            runtime_type="opencode",
            mount_path="/workspace",
            image="opencode:latest",
            deployment_name="agent-e",
            namespace="efp-agents",
            tool_repo_url=None,
            tool_branch=None,
            cpu=None,
            memory=None,
        )
        self.service._patch_deployment(agent)
        container = self.service.apps_api.calls[0]["body"]["spec"]["template"]["spec"]["containers"][0]
        self.assertNotIn("resources", container)


if __name__ == "__main__":
    unittest.main()
