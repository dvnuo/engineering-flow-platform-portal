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
        self.service.settings.default_agent_repo_url = ""
        self.service.settings.default_agent_branch = "master"
        self.service.settings.default_skill_repo_url = ""
        self.service.settings.default_skill_branch = "master"
        self.service.settings.default_tool_repo_url = ""
        self.service.settings.default_tool_branch = "main"
        self.service.settings.default_agent_mount_path = "/root/.efp"
        self.service.settings.agents_volume_sub_path_prefix = "efp-agents"

    def test_native_runtime_clones_runtime_skills_and_tools_to_app_asset_dirs(self):
        self.service.settings.enable_runtime_source_overlay = True
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
            tool_repo_url="https://example.com/tools.git",
            tool_branch="main",
        )
        inits, mounts = self.service._build_code_and_skill_init_containers_and_mounts(agent)
        self.assertEqual({c.name for c in inits}, {"runtime-git-clone", "skills-git-clone", "tools-git-clone"})
        mount_paths = {m.mount_path for m in mounts}
        self.assertIn("/app/.git", mount_paths)
        self.assertIn("/app/src", mount_paths)
        self.assertIn("/app/skills", mount_paths)
        self.assertIn("/app/tools", mount_paths)
        self.assertIn("/root/.efp", mount_paths)
        tools_init = next(c for c in inits if c.name == "tools-git-clone")
        self.assertIn("/tools-code", tools_init.args[0])

    def test_opencode_runtime_clones_skills_and_tools_to_app_asset_dirs_without_native_runtime_mounts(self):
        self.service.settings.enable_runtime_source_overlay = True
        self.service.settings.default_agent_runtime_repo_url = "https://github.com/acme/runtime.git"
        self.service.settings.default_skill_repo_url = "https://github.com/acme/skills-default.git"
        agent = SimpleNamespace(
            id="a2",
            owner_user_id=1,
            runtime_type="opencode",
            mount_path="/workspace",
            skill_repo_url="https://example.com/skills.git",
            tool_repo_url="git@github.com:Acme/Tools.git",
            tool_branch="tools-main",
        )
        inits, mounts = self.service._build_code_and_skill_init_containers_and_mounts(agent)
        self.assertEqual({c.name for c in inits}, {"opencode-persistent-dirs-init", "skills-git-clone", "tools-git-clone"})
        state_init = next(c for c in inits if c.name == "opencode-persistent-dirs-init")
        state_env = {e.name: e.value for e in state_init.env}
        self.assertEqual(state_env["AGENT_STATE_ROOT"], "/agent-data/efp-agents/a2")
        self.assertEqual(state_init.volume_mounts[0].mount_path, "/agent-data")
        self.assertIsNone(getattr(state_init.volume_mounts[0], "sub_path", None))
        command = state_init.args[0]
        self.assertIn("$AGENT_STATE_ROOT/data/.opencode", command)
        self.assertIn("$AGENT_STATE_ROOT/opencode-state", command)
        self.assertIn("$AGENT_STATE_ROOT/adapter-state", command)
        self.assertIn("chown -R 10001:10001", command)
        self.assertNotIn("runtime-git-clone", {c.name for c in inits})
        clone_env = {e.name: e.value for e in next(c for c in inits if c.name == "tools-git-clone").env if getattr(e, "value", None)}
        self.assertEqual(clone_env["GIT_REPO_URL"], "https://github.com/Acme/Tools.git")
        self.assertIn("/tools-code", next(c for c in inits if c.name == "tools-git-clone").args[0])
        self.assertIn("/skills-code", next(c for c in inits if c.name == "skills-git-clone").args[0])
        mount_paths = {m.mount_path for m in mounts}
        self.assertIn("/workspace", mount_paths)
        self.assertNotIn("/app/src", mount_paths)
        self.assertNotIn("/app/.git", mount_paths)
        self.assertIn("/app/skills", mount_paths)
        self.assertIn("/app/tools", mount_paths)
        self.assertIn("/home/opencode/.local/share/opencode", mount_paths)
        self.assertIn("/home/opencode/.local/share/efp-compat", mount_paths)
        mounts_by_path = {m.mount_path: m for m in mounts}
        self.assertEqual(mounts_by_path["/home/opencode/.local/share/opencode"].sub_path, "efp-agents/a2/opencode-state")
        self.assertEqual(mounts_by_path["/home/opencode/.local/share/efp-compat"].sub_path, "efp-agents/a2/adapter-state")
        self.assertEqual(mounts_by_path["/workspace"].sub_path, "efp-agents/a2/data")
        self.assertNotIn("/workspace/tools", mount_paths)
        self.assertNotIn("/workspace-data", mount_paths)

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

    def test_opencode_runtime_env_uses_app_asset_dirs(self):
        agent = SimpleNamespace(id="a1", name="agent-one", runtime_type="opencode", mount_path="/workspace")
        env = self.service._build_agent_container_env(agent)
        env_map = {item.name: getattr(item, "value", None) for item in env}
        self.assertEqual(env_map["EFP_RUNTIME_TYPE"], "opencode")
        self.assertEqual(env_map["OPENCODE_WORKSPACE"], "/workspace")
        self.assertEqual(env_map["EFP_SKILLS_DIR"], "/app/skills")
        self.assertEqual(env_map["EFP_TOOLS_DIR"], "/app/tools")
        self.assertEqual(env_map["OPENCODE_TOOLS_DIR"], "/app/tools")
        self.assertEqual(env_map["EFP_ADAPTER_STATE_DIR"], "/home/opencode/.local/share/efp-compat")
        self.assertEqual(env_map["OPENCODE_CONFIG"], "/workspace/.opencode/opencode.json")
        self.assertEqual(env_map["OPENCODE_VERSION"], "1.14.29")
        self.assertEqual(env_map["EFP_OPENCODE_URL"], "http://127.0.0.1:4096")
        self.assertEqual(env_map["PORTAL_AGENT_NAME"], "agent-one")
        self.assertNotEqual(env_map["OPENCODE_TOOLS_DIR"], "/workspace/tools")

    def test_native_runtime_env_exposes_skills_and_tools_without_opencode_workspace(self):
        agent = SimpleNamespace(id="a1", runtime_type="native", mount_path="/root/.efp")
        env = self.service._build_agent_container_env(agent)
        env_map = {item.name: getattr(item, "value", None) for item in env}
        self.assertEqual(env_map["EFP_RUNTIME_TYPE"], "native")
        self.assertEqual(env_map["EFP_SKILLS_DIR"], "/app/skills")
        self.assertEqual(env_map["EFP_TOOLS_DIR"], "/app/tools")
        self.assertNotIn("OPENCODE_WORKSPACE", env_map)
        self.assertNotIn("OPENCODE_TOOLS_DIR", env_map)
        self.assertNotIn("EFP_ADAPTER_STATE_DIR", env_map)
        self.assertNotIn("EFP_OPENCODE_URL", env_map)

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
        self.service.settings.default_skill_repo_url = ""
        self.service.settings.default_tool_repo_url = ""
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
        self.assertEqual([c["name"] for c in body["spec"]["template"]["spec"]["initContainers"]], ["opencode-persistent-dirs-init"])
        containers = body["spec"]["template"]["spec"]["containers"]
        self.assertEqual(len(containers), 1)
        container = containers[0]
        self.assertEqual(container["name"], "agent")
        self.assertEqual(container["workingDir"], "/workspace")
        mount_paths = {m["mountPath"] for m in container["volumeMounts"]}
        self.assertEqual(mount_paths, {"/workspace", "/home/opencode/.local/share/opencode", "/home/opencode/.local/share/efp-compat", "/app/tools"})
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
        self.service.settings.default_skill_repo_url = ""
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
        self.assertEqual(len(init_containers), 2)
        self.assertEqual(init_containers[0]["name"], "opencode-persistent-dirs-init")
        self.assertEqual(init_containers[1]["name"], "tools-git-clone")
        self.assertIn("/tools-code", init_containers[1]["args"][0])
        env_map = {item["name"]: item.get("value") for item in init_containers[1]["env"]}
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
        self.service.settings.default_tool_repo_url = "https://github.com/acme/tools.git"
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
        self.assertIn("/app/tools", mount_paths)
        self.assertIn("/root/.efp", mount_paths)
        init_names = {c["name"] for c in body["spec"]["template"]["spec"]["initContainers"]}
        self.assertIn("runtime-git-clone", init_names)
        self.assertIn("skills-git-clone", init_names)
        self.assertIn("tools-git-clone", init_names)

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

    def test_ensure_service_exposes_runtime_port_8000(self):
        class FakeCoreApi:
            def __init__(self):
                self.calls = []

            def create_namespaced_service(self, **kwargs):
                self.calls.append(kwargs)

        self.service.enabled = True
        self.service.core_api = FakeCoreApi()
        agent = SimpleNamespace(
            id="a-port",
            owner_user_id=1,
            runtime_type="opencode",
            namespace="efp-agents",
            service_name="agent-a-port",
        )

        self.service._ensure_service(agent)
        body = self.service.core_api.calls[0]["body"]
        self.assertEqual(body.spec.ports[0].port, 8000)
        self.assertEqual(body.spec.ports[0].target_port, 8000)
        self.assertEqual(body.spec.type, self.service.settings.k8s_agent_service_type)

    def test_ensure_deployment_sets_container_port_8000_for_native_and_opencode(self):
        class FakeAppsApi:
            def __init__(self):
                self.calls = []

            def create_namespaced_deployment(self, **kwargs):
                self.calls.append(kwargs)

        self.service.enabled = True
        self.service.apps_api = FakeAppsApi()
        for runtime_type, mount_path, image in [
            ("native", "/root/.efp", "native:latest"),
            ("opencode", "/workspace", "opencode:latest"),
        ]:
            agent = SimpleNamespace(
                id=f"a-{runtime_type}",
                owner_user_id=1,
                runtime_type=runtime_type,
                mount_path=mount_path,
                image=image,
                deployment_name=f"agent-{runtime_type}",
                namespace="efp-agents",
                service_name=f"svc-{runtime_type}",
                cpu=None,
                memory=None,
            )
            self.service._ensure_deployment(agent)
            body = self.service.apps_api.calls[-1]["body"]
            container = body.spec.template.spec.containers[0]
            self.assertEqual(container.ports[0].container_port, 8000)
            if runtime_type == "opencode":
                self.assertEqual(container.working_dir, "/workspace")
            else:
                self.assertIsNone(container.working_dir)

    def test_patch_deployment_preserves_runtime_port_8000(self):
        class FakeAppsApi:
            def __init__(self):
                self.calls = []

            def patch_namespaced_deployment(self, **kwargs):
                self.calls.append(kwargs)

        self.service.enabled = True
        self.service.apps_api = FakeAppsApi()
        agent = SimpleNamespace(
            id="a-patch-port",
            owner_user_id=1,
            runtime_type="opencode",
            mount_path="/workspace",
            image="opencode:latest",
            deployment_name="agent-patch-port",
            namespace="efp-agents",
            tool_repo_url=None,
            tool_branch=None,
            cpu=None,
            memory=None,
        )
        self.service._patch_deployment(agent)
        body = self.service.apps_api.calls[0]["body"]
        container = body["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(container["ports"], [{"containerPort": 8000}])

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
