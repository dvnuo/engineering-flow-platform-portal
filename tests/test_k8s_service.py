import os
from pathlib import Path
import subprocess
import tempfile
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
                "default_agent_settings_repo_url",
                "default_agent_settings_branch",
                "default_agent_settings_repo_subdir",
                "default_agent_settings_asset_version",
                "default_skill_repo_url",
                "default_skill_branch",
                "default_skill_repo_subdir",
                "default_skill_asset_version",
                "default_opencode_permission_mode",
                "default_opencode_allow_bash_all",
                "opencode_workspace_repos_dir",
                "opencode_git_checkout_timeout_seconds",
                "opencode_task_completion_timeout_seconds",
                "opencode_chat_submit_timeout_seconds",
            )
        }
        self.service.settings.default_agent_settings_repo_url = ""
        self.service.settings.default_agent_settings_branch = "master"
        self.service.settings.default_agent_settings_repo_subdir = ""
        self.service.settings.default_agent_settings_asset_version = ""
        self.service.settings.default_skill_repo_url = ""
        self.service.settings.default_skill_branch = "master"
        self.service.settings.default_skill_repo_subdir = ""
        self.service.settings.default_skill_asset_version = ""
        self.service.settings.default_opencode_permission_mode = "workspace_full_access"
        self.service.settings.default_opencode_allow_bash_all = True
        self.service.settings.opencode_workspace_repos_dir = "/workspace/repos"
        self.service.settings.opencode_git_checkout_timeout_seconds = 120
        self.service.settings.opencode_task_completion_timeout_seconds = 3600
        self.service.settings.opencode_chat_submit_timeout_seconds = 900

    def tearDown(self):
        for name, value in self._settings_snapshot.items():
            setattr(self.service.settings, name, value)

    def _find_init_container(self, containers, name):
        return next(c for c in containers if c.name == name)

    def _run_skill_clone_command(self, tmp_path, source_tree_builder, *, subdir=""):
        repo = tmp_path / "repo"
        repo.mkdir()
        source_tree_builder(repo)

        target = tmp_path / "target"
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        fake_git = fake_bin / "git"
        fake_git.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "src=\"$GIT_REPO_URL\"\n"
            "cp -a \"$src\"/. .\n",
            encoding="utf-8",
        )
        fake_git.chmod(0o755)

        command = self.service._skill_git_clone_shell_command(str(target))
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{fake_bin}:{env.get('PATH', '')}",
                "GIT_REPO_URL": str(repo),
                "GIT_BRANCH": "main",
                "SKILL_REPO_SUBDIR": subdir,
            }
        )
        result = subprocess.run(["sh", "-c", command], env=env, cwd=str(tmp_path), text=True, capture_output=True)
        return result, target

    def test_start_agent_patches_deployment_before_scale(self):
        self.service.enabled = True
        calls = []
        scale_calls = []
        agent = SimpleNamespace(deployment_name="agent-a1", namespace="efp-agents")

        def _patch_deployment(_agent):
            calls.append("patch_deployment")

        def _patch_service_metadata(_agent):
            calls.append("patch_service_metadata")

        def _scale_agent_deployment(_agent, replicas):
            calls.append("scale")
            scale_calls.append({"body": {"spec": {"replicas": replicas}}})

        self.service._patch_deployment = _patch_deployment
        self.service._patch_service_metadata = _patch_service_metadata
        self.service._scale_agent_deployment = _scale_agent_deployment

        status = self.service.start_agent(agent)

        self.assertEqual(status.status, "running")
        self.assertEqual(calls, ["patch_deployment", "patch_service_metadata", "scale"])
        self.assertEqual(scale_calls[0]["body"], {"spec": {"replicas": 1}})

    def test_start_agent_scales_when_patch_fails(self):
        self.service.enabled = True
        calls = []
        scale_calls = []
        agent = SimpleNamespace(deployment_name="agent-a1", namespace="efp-agents")

        def _patch_deployment(_agent):
            calls.append("patch_deployment")
            raise ValueError("bad skill subdir")

        def _patch_service_metadata(_agent):
            calls.append("patch_service_metadata")

        def _scale_agent_deployment(_agent, replicas):
            calls.append("scale")
            scale_calls.append({"body": {"spec": {"replicas": replicas}}})

        self.service._patch_deployment = _patch_deployment
        self.service._patch_service_metadata = _patch_service_metadata
        self.service._scale_agent_deployment = _scale_agent_deployment

        status = self.service.start_agent(agent)

        self.assertEqual(status.status, "running")
        self.assertIn("Deployment scaled to 1", status.message)
        self.assertIn("bad skill subdir", status.message)
        self.assertIn("scale", calls)
        self.assertEqual(scale_calls[0]["body"], {"spec": {"replicas": 1}})

    def test_patch_deployment_merge_uses_call_api_content_type_without_generated_content_type_kwarg(self):
        self.service.enabled = True
        calls = []
        agent = SimpleNamespace(deployment_name="agent-a1", namespace="efp-agents")
        body = {"spec": {"template": {"metadata": {"annotations": {"x": "y"}}}}}

        def _call_api(*args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace()

        self.service.apps_api = SimpleNamespace(api_client=SimpleNamespace(call_api=_call_api))

        self.service._patch_agent_deployment_merge(agent, body)

        self.assertEqual(len(calls), 1)
        args, kwargs = calls[0]
        self.assertEqual(args[0], "/apis/apps/v1/namespaces/{namespace}/deployments/{name}")
        self.assertEqual(args[1], "PATCH")
        self.assertEqual(kwargs["path_params"], {"namespace": "efp-agents", "name": "agent-a1"})
        self.assertEqual(kwargs["body"], body)
        self.assertEqual(kwargs["header_params"]["Content-Type"], "application/merge-patch+json")
        self.assertEqual(kwargs["header_params"]["Accept"], "application/json")
        self.assertNotIn("_content_type", kwargs)

    def test_patch_deployment_sets_agent_image_pull_policy_always(self):
        self.service.enabled = True
        captured = {}
        agent = SimpleNamespace(
            id="a1",
            owner_user_id=1,
            runtime_type="native",
            deployment_name="agent-a1",
            namespace="efp-agents",
            image="ghcr.io/acme/runtime:v1",
            mount_path="/workspace",
            cpu=None,
            memory=None,
            skill_repo_url=None,
            skill_branch=None,
            agent_settings_repo_url=None,
            agent_settings_branch=None,
        )

        def _patch_agent_deployment_merge(_agent, body):
            captured["body"] = body

        self.service._patch_agent_deployment_merge = _patch_agent_deployment_merge

        self.service._patch_deployment(agent)

        container = captured["body"]["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(container["imagePullPolicy"], "Always")

    def test_ensure_deployment_sets_agent_image_pull_policy_always(self):
        captured = {}
        agent = SimpleNamespace(
            id="a1",
            owner_user_id=1,
            runtime_type="native",
            deployment_name="agent-a1",
            namespace="efp-agents",
            image="ghcr.io/acme/runtime:v1",
            mount_path="/workspace",
            cpu=None,
            memory=None,
            skill_repo_url=None,
            skill_branch=None,
            agent_settings_repo_url=None,
            agent_settings_branch=None,
        )

        def _create_namespaced_deployment(namespace, body):
            captured["namespace"] = namespace
            captured["body"] = body

        self.service.apps_api = SimpleNamespace(create_namespaced_deployment=_create_namespaced_deployment)

        self.service._ensure_deployment(agent)

        container = captured["body"].spec.template.spec.containers[0]
        self.assertEqual(captured["namespace"], "efp-agents")
        self.assertEqual(container.image_pull_policy, "Always")

    def test_patch_service_merge_uses_call_api_content_type(self):
        self.service.enabled = True
        calls = []
        agent = SimpleNamespace(service_name="agent-a1-svc", namespace="efp-agents")
        body = {"metadata": {"annotations": {"x": "y"}}}

        def _call_api(*args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace()

        self.service.core_api = SimpleNamespace(api_client=SimpleNamespace(call_api=_call_api))

        self.service._patch_agent_service_merge(agent, body)

        self.assertEqual(len(calls), 1)
        args, kwargs = calls[0]
        self.assertEqual(args[0], "/api/v1/namespaces/{namespace}/services/{name}")
        self.assertEqual(args[1], "PATCH")
        self.assertEqual(kwargs["path_params"], {"namespace": "efp-agents", "name": "agent-a1-svc"})
        self.assertEqual(kwargs["body"], body)
        self.assertEqual(kwargs["header_params"]["Content-Type"], "application/merge-patch+json")
        self.assertEqual(kwargs["header_params"]["Accept"], "application/json")

    def test_patch_deployment_scale_merge_uses_call_api_content_type(self):
        self.service.enabled = True
        calls = []
        agent = SimpleNamespace(deployment_name="agent-a1", namespace="efp-agents")

        def _call_api(*args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace()

        self.service.apps_api = SimpleNamespace(api_client=SimpleNamespace(call_api=_call_api))

        self.service._patch_agent_deployment_scale_merge(agent, 1)

        self.assertEqual(len(calls), 1)
        args, kwargs = calls[0]
        self.assertEqual(args[0], "/apis/apps/v1/namespaces/{namespace}/deployments/{name}/scale")
        self.assertEqual(args[1], "PATCH")
        self.assertEqual(kwargs["path_params"], {"namespace": "efp-agents", "name": "agent-a1"})
        self.assertEqual(kwargs["body"], {"spec": {"replicas": 1}})
        self.assertEqual(kwargs["header_params"]["Content-Type"], "application/merge-patch+json")
        self.assertEqual(kwargs["response_type"], "V1Scale")

    def test_k8s_service_does_not_pass_generated_content_type_kwarg(self):
        source = Path("app/services/k8s_service.py").read_text(encoding="utf-8")
        self.assertNotIn("_content_type=", source)
        self.assertNotIn("set_default_header", source)

    def test_restart_agent_does_not_scale_to_zero_and_rolls_out(self):
        self.service.enabled = True
        calls = []
        scale_calls = []
        deployment_patch_calls = []
        agent = SimpleNamespace(deployment_name="agent-a1", namespace="efp-agents")

        def _patch_deployment(_agent):
            calls.append("patch_deployment")

        def _patch_service_metadata(_agent):
            calls.append("patch_service_metadata")

        def _scale_agent_deployment(_agent, replicas):
            calls.append("scale")
            scale_calls.append({"body": {"spec": {"replicas": replicas}}})

        def _patch_agent_deployment_merge(_agent, body):
            calls.append("rollout_patch")
            deployment_patch_calls.append({"body": body})

        self.service._patch_deployment = _patch_deployment
        self.service._patch_service_metadata = _patch_service_metadata
        self.service._scale_agent_deployment = _scale_agent_deployment
        self.service._patch_agent_deployment_merge = _patch_agent_deployment_merge

        status = self.service.restart_agent(agent)

        self.assertEqual(status.status, "restarting")
        self.assertIn("Restart requested:", status.message)
        self.assertTrue(scale_calls)
        self.assertTrue(all(call["body"] == {"spec": {"replicas": 1}} for call in scale_calls))
        self.assertFalse(any(call["body"] == {"spec": {"replicas": 0}} for call in scale_calls))
        self.assertTrue(deployment_patch_calls)
        body = deployment_patch_calls[-1]["body"]
        annotations = body["spec"]["template"]["metadata"]["annotations"]
        self.assertIn("efp.dvnuo.io/restarted-at", annotations)
        self.assertIn("kubectl.kubernetes.io/restartedAt", annotations)
        self.assertIn("efp.dvnuo.io/restart-request-id", annotations)
        self.assertIn("efp.dvnuo.io/restart-requested-at", annotations)
        self.assertEqual(calls, ["patch_deployment", "patch_service_metadata", "scale", "rollout_patch"])

    def test_restart_agent_patches_before_scale_to_avoid_starting_old_config(self):
        self.service.enabled = True
        calls = []
        agent = SimpleNamespace(deployment_name="agent-a1", namespace="efp-agents")

        def _patch_deployment(_agent):
            calls.append("patch_deployment")

        def _patch_service_metadata(_agent):
            calls.append("patch_service_metadata")

        def _scale_agent_deployment(_agent, replicas):
            calls.append("scale")
            self.assertEqual(replicas, 1)

        def _patch_agent_deployment_merge(_agent, _body):
            calls.append("rollout_patch")

        self.service._patch_deployment = _patch_deployment
        self.service._patch_service_metadata = _patch_service_metadata
        self.service._scale_agent_deployment = _scale_agent_deployment
        self.service._patch_agent_deployment_merge = _patch_agent_deployment_merge

        status = self.service.restart_agent(agent)

        self.assertEqual(status.status, "restarting")
        self.assertEqual(calls, ["patch_deployment", "patch_service_metadata", "scale", "rollout_patch"])

    def test_restart_agent_patch_failure_does_not_scale(self):
        self.service.enabled = True
        calls = []
        agent = SimpleNamespace(deployment_name="agent-a1", namespace="efp-agents")

        def _patch_deployment(_agent):
            calls.append("patch_deployment")
            raise ValueError("patch failed")

        def _scale_agent_deployment(_agent, _replicas):
            calls.append("scale")

        self.service._patch_deployment = _patch_deployment
        self.service._scale_agent_deployment = _scale_agent_deployment

        status = self.service.restart_agent(agent)

        self.assertEqual(status.status, "failed")
        self.assertIn("patch failed", status.message)
        self.assertEqual(calls, ["patch_deployment"])

    def test_restart_agent_when_k8s_disabled_is_not_reported_success(self):
        self.service.enabled = False
        agent = SimpleNamespace(deployment_name="agent-a1", namespace="efp-agents")

        status = self.service.restart_agent(agent)

        self.assertEqual(status.status, "failed")
        self.assertIn("Kubernetes integration is disabled", status.message)
        self.assertIn("unsupported", status.message)

    def test_runtime_status_uses_spec_replicas_for_desired_state(self):
        self.service.enabled = True
        agent = SimpleNamespace(deployment_name="agent-a1", namespace="efp-agents", status="stopped", last_error=None)

        deploy = SimpleNamespace(
            spec=SimpleNamespace(replicas=1),
            status=SimpleNamespace(replicas=0, available_replicas=0),
        )

        self.service.apps_api = SimpleNamespace(
            read_namespaced_deployment_status=lambda **_kwargs: deploy,
        )

        status = self.service.get_agent_runtime_status(agent)

        self.assertEqual(status.status, "creating")

    def test_runtime_status_reports_restarting_when_observed_generation_lags(self):
        self.service.enabled = True
        agent = SimpleNamespace(deployment_name="agent-a1", namespace="efp-agents", status="restarting", last_error=None)

        deploy = SimpleNamespace(
            metadata=SimpleNamespace(generation=10),
            spec=SimpleNamespace(replicas=1),
            status=SimpleNamespace(
                observed_generation=9,
                available_replicas=1,
                updated_replicas=1,
                replicas=1,
                ready_replicas=1,
                unavailable_replicas=0,
            ),
        )

        self.service.apps_api = SimpleNamespace(
            read_namespaced_deployment_status=lambda **_kwargs: deploy,
        )

        status = self.service.get_agent_runtime_status(agent)

        self.assertEqual(status.status, "restarting")

    def test_runtime_status_reports_restarting_when_updated_replicas_not_complete(self):
        self.service.enabled = True
        agent = SimpleNamespace(deployment_name="agent-a1", namespace="efp-agents", status="restarting", last_error=None)

        deploy = SimpleNamespace(
            metadata=SimpleNamespace(generation=2),
            spec=SimpleNamespace(replicas=1),
            status=SimpleNamespace(
                observed_generation=2,
                available_replicas=1,
                updated_replicas=0,
                replicas=1,
                unavailable_replicas=0,
            ),
        )

        self.service.apps_api = SimpleNamespace(
            read_namespaced_deployment_status=lambda **_kwargs: deploy,
        )

        status = self.service.get_agent_runtime_status(agent)

        self.assertEqual(status.status, "restarting")

    def test_runtime_status_reports_running_only_when_rollout_complete(self):
        self.service.enabled = True
        agent = SimpleNamespace(deployment_name="agent-a1", namespace="efp-agents", status="running", last_error=None)

        deploy = SimpleNamespace(
            metadata=SimpleNamespace(generation=2),
            spec=SimpleNamespace(replicas=1),
            status=SimpleNamespace(
                observed_generation=2,
                replicas=1,
                available_replicas=1,
                updated_replicas=1,
                ready_replicas=1,
                unavailable_replicas=0,
            ),
        )

        self.service.apps_api = SimpleNamespace(
            read_namespaced_deployment_status=lambda **_kwargs: deploy,
        )

        status = self.service.get_agent_runtime_status(agent)

        self.assertEqual(status.status, "running")

    def test_runtime_status_reports_failed_on_progress_deadline_exceeded(self):
        self.service.enabled = True
        agent = SimpleNamespace(deployment_name="agent-a1", namespace="efp-agents", status="restarting", last_error=None)

        deploy = SimpleNamespace(
            metadata=SimpleNamespace(generation=2),
            spec=SimpleNamespace(replicas=1),
            status=SimpleNamespace(
                observed_generation=2,
                replicas=1,
                available_replicas=0,
                updated_replicas=0,
                unavailable_replicas=1,
                conditions=[
                    SimpleNamespace(
                        type="Progressing",
                        status="False",
                        reason="ProgressDeadlineExceeded",
                        message="Deployment exceeded its progress deadline",
                    )
                ],
            ),
        )

        self.service.apps_api = SimpleNamespace(
            read_namespaced_deployment_status=lambda **_kwargs: deploy,
        )

        status = self.service.get_agent_runtime_status(agent)

        self.assertEqual(status.status, "failed")
        self.assertIn("ProgressDeadlineExceeded", status.message)
        self.assertIn("progress deadline", status.message)

    def test_skill_clone_shell_accepts_directory_skill_and_copies_resources(self):
        def _build(repo: Path):
            skill_dir = repo / "foss-auto-fix"
            (skill_dir / "scripts").mkdir(parents=True)
            (skill_dir / "templates").mkdir()
            (skill_dir / "SKILL.md").write_text("# fake skill\n", encoding="utf-8")
            (skill_dir / "scripts" / "foss_auto_fix_cli.py").write_text("print('ok')\n", encoding="utf-8")
            (skill_dir / "templates" / "default.md").write_text("template\n", encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            result, target = self._run_skill_clone_command(Path(tmp), _build)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((target / "foss-auto-fix" / "SKILL.md").exists())
            self.assertTrue((target / "foss-auto-fix" / "scripts" / "foss_auto_fix_cli.py").exists())
            self.assertTrue((target / "foss-auto-fix" / "templates" / "default.md").exists())

    def test_skill_clone_shell_accepts_nested_subdir_and_does_not_nest_skills_dir(self):
        def _build(repo: Path):
            skill_dir = repo / "skills" / "foss-auto-fix"
            (skill_dir / "scripts").mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# fake skill\n", encoding="utf-8")
            (skill_dir / "scripts" / "foss_auto_fix_cli.py").write_text("print('ok')\n", encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            result, target = self._run_skill_clone_command(Path(tmp), _build, subdir="skills")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((target / "foss-auto-fix" / "SKILL.md").exists())
            self.assertTrue((target / "foss-auto-fix" / "scripts" / "foss_auto_fix_cli.py").exists())
            self.assertFalse((target / "skills" / "foss-auto-fix" / "SKILL.md").exists())

    def test_skill_clone_shell_rejects_readme_only_repo(self):
        def _build(repo: Path):
            (repo / "README.md").write_text("# Not a skill\n", encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            result, _target = self._run_skill_clone_command(Path(tmp), _build)
            self.assertEqual(result.returncode, 36)
            self.assertIn("No skill entries found", result.stderr)
            self.assertIn("README.md", result.stderr)

    def test_skill_clone_shell_accepts_flat_markdown_with_frontmatter(self):
        def _build(repo: Path):
            (repo / "foo.md").write_text("---\nname: foo\ndescription: Demo flat skill\n---\nBody\n", encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            result, target = self._run_skill_clone_command(Path(tmp), _build)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((target / "foo.md").exists())

    def test_skill_clone_shell_rejects_flat_markdown_without_name_description_frontmatter(self):
        def _build(repo: Path):
            (repo / "foo.md").write_text("---\ntitle: Not a skill\n---\nBody\n", encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            result, _target = self._run_skill_clone_command(Path(tmp), _build)
            self.assertEqual(result.returncode, 36)

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

    def test_single_runtime_skill_clone_uses_full_package_command_and_mounts_app_skills(self):
        agent = SimpleNamespace(id="a1", owner_user_id=1, runtime_type="native", mount_path="/workspace", skill_repo_url="https://example.com/skills.git", skill_branch="main")
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

    def test_agent_settings_init_container_overwrites_agents_and_instructions(self):
        agent = SimpleNamespace(
            id="a1",
            owner_user_id=1,
            runtime_type="native",
            mount_path="/workspace",
            agent_settings_repo_url="https://example.com/agents.git",
            agent_settings_branch="main",
            agent_settings_subdir="profiles/default",
            skill_repo_url=None,
            skill_branch="main",
        )
        inits, mounts = self.service._build_code_and_skill_init_containers_and_mounts(agent)
        init_names = {c.name for c in inits}
        mount_map = {m.mount_path: m.sub_path for m in mounts}

        self.assertIn("agent-settings-git-clone", init_names)
        self.assertEqual(mount_map["/workspace"], "efp-agents/a1/data")

        settings_init = self._find_init_container(inits, "agent-settings-git-clone")
        env_map = {e.name: getattr(e, "value", None) for e in settings_init.env}
        init_mount_map = {m.mount_path: m.sub_path for m in settings_init.volume_mounts}
        command = settings_init.args[0]

        self.assertEqual(env_map["GIT_REPO_URL"], "https://example.com/agents.git")
        self.assertEqual(env_map["GIT_BRANCH"], "main")
        self.assertEqual(env_map["AGENT_SETTINGS_REPO_SUBDIR"], "profiles/default")
        self.assertEqual(init_mount_map["/workspace"], "efp-agents/a1/data")
        self.assertIn("AGENTS.md", command)
        self.assertIn("instructions", command)
        self.assertIn('rm -rf "/workspace/instructions"', command)
        self.assertIn('cp -a "${SOURCE_DIR}/instructions"/. "/workspace/instructions"/', command)
        self.assertIn('mkdir -p "/workspace/.efp/instructions"', command)
        self.assertIn('find "/workspace/.efp/instructions" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +', command)
        self.assertIn('cp -a "${SOURCE_DIR}/instructions"/. "/workspace/.efp/instructions"/', command)
        self.assertNotIn("https://example.com/agents.git", command)

    def test_opencode_agent_settings_init_container_uses_workspace_mount(self):
        agent = SimpleNamespace(
            id="a1",
            owner_user_id=1,
            runtime_type="opencode",
            mount_path="/workspace",
            agent_settings_repo_url="https://example.com/agents.git",
            agent_settings_branch="main",
            skill_repo_url=None,
            skill_branch="main",
        )
        inits, mounts = self.service._build_code_and_skill_init_containers_and_mounts(agent)
        init_names = {c.name for c in inits}
        mount_map = {m.mount_path: m.sub_path for m in mounts}

        self.assertIn("opencode-persistent-dirs-init", init_names)
        self.assertIn("agent-settings-git-clone", init_names)
        self.assertEqual(mount_map["/workspace"], "efp-agents/a1/data")

    def test_agent_settings_repo_subdir_rejects_parent_path(self):
        self.service.settings.default_agent_settings_repo_subdir = "../profiles"
        agent = SimpleNamespace(id="a1", owner_user_id=1, runtime_type="native")
        with self.assertRaises(ValueError):
            self.service._agent_settings_repo_subdir(agent)

    def test_agent_settings_asset_version_annotation_forces_rollout(self):
        self.service.settings.default_agent_settings_asset_version = "sha-agent-settings"
        agent = SimpleNamespace(
            id="a1",
            owner_user_id=1,
            runtime_type="native",
            agent_settings_repo_url="https://example.com/agents.git",
            agent_settings_branch="main",
        )
        self.assertEqual(self.service._agent_metadata_annotations(agent)["efp/agent-settings-asset-version"], "sha-agent-settings")
        self.assertEqual(self.service._agent_patch_annotations(agent)["efp/agent-settings-asset-version"], "sha-agent-settings")

    def test_default_skill_repo_subdir_passed_to_skill_clone_env(self):
        self.service.settings.default_skill_repo_subdir = "skills"
        agent = SimpleNamespace(id="a1", owner_user_id=1, runtime_type="native", mount_path="/workspace", skill_repo_url="https://example.com/skills.git", skill_branch="main")
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

    def test_transient_agent_skill_asset_version_overrides_default_annotation(self):
        self.service.settings.default_skill_asset_version = "global-default"
        agent = SimpleNamespace(
            id="a1",
            owner_user_id=1,
            runtime_type="native",
            skill_repo_url="https://example.com/skills.git",
            skill_branch="main",
            skill_asset_version="agent-skill-save-test",
        )

        self.assertEqual(
            self.service._agent_patch_annotations(agent)["efp/skill-asset-version"],
            "agent-skill-save-test",
        )
        self.assertEqual(
            self.service._agent_metadata_annotations(agent)["efp/skill-asset-version"],
            "agent-skill-save-test",
        )

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

    def test_runtime_source_overlay_init_container_and_mounts_are_removed(self):
        agent = SimpleNamespace(id="a1", owner_user_id=1, runtime_type="native", mount_path="/root/.efp", skill_repo_url=None, skill_branch="main")
        inits, mounts = self.service._build_code_and_skill_init_containers_and_mounts(agent)
        self.assertNotIn("runtime-git-clone", {c.name for c in inits})
        self.assertNotIn("/app/src", {m.mount_path for m in mounts})
        self.assertNotIn("/app/.git", {m.mount_path for m in mounts})

    def test_single_runtime_env_excludes_tools_contract(self):
        agent = SimpleNamespace(id="a1", runtime_type="native", mount_path="/workspace")
        env = self.service._build_agent_container_env(agent)
        env_map = {e.name: getattr(e, 'value', None) for e in env}
        self.assertNotIn("EFP_TOOLS_DIR", env_map)
        self.assertNotIn("OPENCODE_TOOLS_DIR", env_map)
        self.assertNotIn("EFP_TOOLS_STRICT_MODE", env_map)
        self.assertNotIn("EFP_OPENCODE_TOOL_REGISTRY_TIMEOUT_SECONDS", env_map)
        self.assertNotIn("EFP_OPENCODE_TOOL_REGISTRY_REQUEST_TIMEOUT_SECONDS", env_map)
        self.assertIn("EFP_SKILLS_DIR", env_map)

    def test_native_env_persists_runtime_sessions_under_workspace_mount(self):
        agent = SimpleNamespace(id="a1", runtime_type="native", mount_path="/workspace")
        env = self.service._build_agent_container_env(agent)
        env_map = {e.name: getattr(e, 'value', None) for e in env}

        self.assertEqual(env_map["PORTAL_RUNTIME_TYPE"], "native")
        self.assertEqual(env_map["EFP_RUNTIME_TYPE"], "native")
        self.assertEqual(env_map["EFP_WORKSPACE_DIR"], "/workspace")
        self.assertEqual(env_map["EFP_CONFIG"], "/root/.efp/config.yaml")
        self.assertEqual(env_map["MOBILE_AUTO_STATE_DIR"], "/workspace/.efp/mobile-auto/runs")
        self.assertEqual(env_map["MOBILE_AUTO_ARTIFACTS_DIR"], "/workspace/.efp/mobile-auto/artifacts")
        self.assertEqual(env_map["BROWSERSTACK_LOCAL_BINARY"], "/usr/local/bin/BrowserStackLocal")
        self.assertEqual(env_map["EFP_RUNTIME_SESSION_ROOT"], "/workspace/.efp/runtime")
        # Native derives credential/config paths from $HOME too, so it must be
        # pinned to /root just like opencode (not left to the runtime default).
        self.assertEqual(env_map["HOME"], "/root")

    def test_native_session_root_follows_custom_workspace_mount(self):
        agent = SimpleNamespace(id="a1", runtime_type="native", mount_path="/custom/workspace")
        env = self.service._build_agent_container_env(agent)
        env_map = {e.name: getattr(e, 'value', None) for e in env}

        self.assertEqual(env_map["EFP_RUNTIME_SESSION_ROOT"], "/custom/workspace/.efp/runtime")
        # Config intentionally does NOT follow the workspace mount: it holds
        # plaintext CLI secrets and must stay out of the agent-writable /
        # server-files-browsable workspace.
        self.assertEqual(env_map["EFP_CONFIG"], "/root/.efp/config.yaml")
        self.assertEqual(env_map["MOBILE_AUTO_STATE_DIR"], "/custom/workspace/.efp/mobile-auto/runs")
        self.assertEqual(env_map["MOBILE_AUTO_ARTIFACTS_DIR"], "/custom/workspace/.efp/mobile-auto/artifacts")

    def test_efp_config_stays_outside_workspace_for_both_runtimes(self):
        # Regression: the EFP config (plaintext CLI secrets) must live outside
        # the workspace so it is neither agent-writable nor browsable via the
        # server-files panel; it is re-projected on every pod start.
        for runtime_type in ("native", "opencode"):
            agent = SimpleNamespace(
                id="a1", runtime_type=runtime_type, mount_path="/custom/workspace", name="A"
            )
            env = self.service._build_agent_container_env(agent)
            env_map = {e.name: getattr(e, "value", None) for e in env}
            self.assertEqual(env_map["EFP_CONFIG"], "/root/.efp/config.yaml")
            self.assertNotIn("/workspace", env_map["EFP_CONFIG"])

    def test_agent_container_resources_apply_cpu_and_memory_limits(self):
        agent = SimpleNamespace(id="a1", cpu="250m", memory="512Mi")
        resources = self.service._build_agent_container_resources(agent)
        self.assertIsNotNone(resources)
        self.assertEqual(resources.requests, {"cpu": "250m", "memory": "512Mi"})
        self.assertEqual(resources.limits, {"cpu": "10", "memory": "2Gi"})

    def test_agent_container_resources_set_limits_even_without_requests(self):
        agent = SimpleNamespace(id="a1", cpu=None, memory=None)
        resources = self.service._build_agent_container_resources(agent)
        self.assertIsNotNone(resources)
        self.assertIsNone(resources.requests)
        self.assertEqual(resources.limits, {"cpu": "10", "memory": "2Gi"})

    def test_agent_container_resource_limits_are_configurable_and_optional(self):
        s = self.service.settings
        original = (s.default_agent_cpu_limit, s.default_agent_memory_limit)
        try:
            s.default_agent_cpu_limit = "4"
            s.default_agent_memory_limit = "1Gi"
            resources = self.service._build_agent_container_resources(
                SimpleNamespace(id="a1", cpu=None, memory=None)
            )
            self.assertEqual(resources.limits, {"cpu": "4", "memory": "1Gi"})
            # An empty value disables that specific limit.
            s.default_agent_cpu_limit = ""
            resources = self.service._build_agent_container_resources(
                SimpleNamespace(id="a1", cpu=None, memory=None)
            )
            self.assertEqual(resources.limits, {"memory": "1Gi"})
        finally:
            s.default_agent_cpu_limit, s.default_agent_memory_limit = original

    def test_single_runtime_env_excludes_old_timeout_contract(self):
        agent = SimpleNamespace(id="a1", runtime_type="native", mount_path="/workspace")
        env = self.service._build_agent_container_env(agent)
        env_map = {e.name: getattr(e, 'value', None) for e in env}

        self.assertNotIn("EFP_TASK_COMPLETION_TIMEOUT_SECONDS", env_map)
        self.assertNotIn("EFP_CHAT_SUBMIT_TIMEOUT_SECONDS", env_map)
        self.assertNotIn("EFP_CHAT_COMPLETION_TIMEOUT_SECONDS", env_map)

    def test_opencode_runtime_type_normalizes_agent_value(self):
        agent = SimpleNamespace(id="a1", runtime_type=" opencode ")
        self.assertEqual(self.service._runtime_type(agent), "opencode")

    def test_opencode_env_restores_adapter_contract(self):
        agent = SimpleNamespace(id="a1", runtime_type="opencode", mount_path="/workspace", name="Agent One")
        env = self.service._build_agent_container_env(agent)
        env_map = {e.name: getattr(e, 'value', None) for e in env}

        self.assertEqual(env_map["PORTAL_RUNTIME_TYPE"], "opencode")
        self.assertEqual(env_map["EFP_RUNTIME_TYPE"], "opencode")
        self.assertEqual(env_map["EFP_WORKSPACE_DIR"], "/workspace")
        self.assertEqual(env_map["EFP_CONFIG"], "/root/.efp/config.yaml")
        self.assertEqual(env_map["MOBILE_AUTO_STATE_DIR"], "/workspace/.efp/mobile-auto/runs")
        self.assertEqual(env_map["MOBILE_AUTO_ARTIFACTS_DIR"], "/workspace/.efp/mobile-auto/artifacts")
        self.assertEqual(env_map["BROWSERSTACK_LOCAL_BINARY"], "/usr/local/bin/BrowserStackLocal")
        self.assertNotIn("EFP_REQUIRE_PORTAL_RUNTIME_CONTEXT", env_map)
        self.assertEqual(env_map["HOME"], "/root")
        self.assertEqual(env_map["OPENCODE_DATA_DIR"], "/root/.local/share/opencode")
        self.assertEqual(env_map["EFP_ADAPTER_STATE_DIR"], "/root/.local/share/efp-compat")
        self.assertEqual(env_map["OPENCODE_WORKSPACE"], "/workspace")
        self.assertEqual(env_map["EFP_WORKSPACE_REPOS_DIR"], "/workspace/repos")
        self.assertEqual(env_map["EFP_GIT_CHECKOUT_TIMEOUT_SECONDS"], "120")
        self.assertEqual(env_map["EFP_TASK_COMPLETION_TIMEOUT_SECONDS"], "3600")
        self.assertEqual(env_map["EFP_CHAT_SUBMIT_TIMEOUT_SECONDS"], "900")
        # Chat completion shares the task completion budget so long chatbox
        # runs are not cut to "incomplete" at the shorter submit timeout.
        self.assertEqual(env_map["EFP_CHAT_COMPLETION_TIMEOUT_SECONDS"], "3600")
        self.assertEqual(env_map["OPENCODE_CONFIG"], "/workspace/.opencode/opencode.json")
        self.assertEqual(env_map["EFP_OPENCODE_URL"], "http://127.0.0.1:4096")
        self.assertEqual(env_map["EFP_OPENCODE_PERMISSION_MODE"], "workspace_full_access")
        self.assertEqual(env_map["EFP_OPENCODE_ALLOW_BASH_ALL"], "true")
        self.assertNotIn("EFP_RUNTIME_SESSION_ROOT", env_map)

    def test_opencode_mounts_workspace_state_adapter_and_skills_assets(self):
        agent = SimpleNamespace(id="a1", owner_user_id=1, runtime_type="opencode", mount_path="/workspace", skill_repo_url="https://example.com/skills.git", skill_branch="main")
        inits, mounts = self.service._build_code_and_skill_init_containers_and_mounts(agent)
        init_names = {c.name for c in inits}
        mount_map = {m.mount_path: m.sub_path for m in mounts}

        self.assertIn("opencode-persistent-dirs-init", init_names)
        self.assertIn("skills-git-clone", init_names)
        self.assertEqual(mount_map["/workspace"], "efp-agents/a1/data")
        self.assertEqual(mount_map["/root/.local/share/opencode"], "efp-agents/a1/opencode-state")
        self.assertEqual(mount_map["/root/.local/share/efp-compat"], "efp-agents/a1/adapter-state")
        self.assertEqual(mount_map["/app/skills"], "efp-agents/a1/skills-code")

        state_init = self._find_init_container(inits, "opencode-persistent-dirs-init")
        command = state_init.args[0]
        self.assertIn('mkdir -p "$AGENT_STATE_ROOT/data/.opencode"', command)
        self.assertIn('"$AGENT_STATE_ROOT/opencode-state"', command)
        self.assertIn('"$AGENT_STATE_ROOT/adapter-state"', command)
        self.assertIn("chown -R 0:0", command)

    def _env_by_name(self, env):
        return {e.name: e for e in env}

    def test_profile_env_uses_bound_profile_secret_config_key(self):
        agent = SimpleNamespace(id="a1", runtime_type="native", mount_path="/workspace", runtime_profile_id="rp-123")
        env = self._env_by_name(self.service._build_agent_container_env(agent))

        config_ref = env["EFP_PROFILE_CONFIG"].value_from.secret_key_ref
        self.assertEqual(config_ref.name, "efp-profile-rp-123")
        self.assertEqual(config_ref.key, "config.json")
        self.assertIsNone(config_ref.optional)

        revision_ref = env["EFP_PROFILE_REVISION"].value_from.secret_key_ref
        self.assertEqual(revision_ref.name, "efp-profile-rp-123")
        self.assertEqual(revision_ref.key, "revision")

        self.assertEqual(env["EFP_PROFILE_ID"].value, "rp-123")

        # The decryption key is injected from the shared agents secret as an
        # optional ref: absent key -> plaintext config; ENC: values without the
        # key fail loudly at runtime start.
        config_key_ref = env["EFP_CONFIG_KEY"].value_from.secret_key_ref
        self.assertEqual(config_key_ref.name, "efp-agents-secret")
        self.assertEqual(config_key_ref.key, "EFP_CONFIG_KEY")
        self.assertTrue(config_key_ref.optional)

    def test_profile_env_opencode_runtime_uses_same_config_key(self):
        agent = SimpleNamespace(id="a1", runtime_type="opencode", mount_path="/workspace", name="A", runtime_profile_id="rp-9")
        env = self._env_by_name(self.service._build_agent_container_env(agent))

        config_ref = env["EFP_PROFILE_CONFIG"].value_from.secret_key_ref
        self.assertEqual(config_ref.name, "efp-profile-rp-9")
        # Both runtimes read the same runtime-agnostic canonical config.
        self.assertEqual(config_ref.key, "config.json")

    def test_profile_env_unbound_agent_uses_shared_none_secret(self):
        agent = SimpleNamespace(id="a1", runtime_type="native", mount_path="/workspace", runtime_profile_id=None)
        env = self._env_by_name(self.service._build_agent_container_env(agent))

        config_ref = env["EFP_PROFILE_CONFIG"].value_from.secret_key_ref
        self.assertEqual(config_ref.name, "efp-profile-none")
        self.assertEqual(config_ref.key, "config.json")
        self.assertEqual(env["EFP_PROFILE_ID"].value, "none")

    def test_ensure_deployment_sets_readiness_probe_and_recreate_strategy(self):
        captured = {}
        agent = SimpleNamespace(
            id="a1",
            owner_user_id=1,
            runtime_type="native",
            deployment_name="agent-a1",
            namespace="efp-agents",
            image="ghcr.io/acme/runtime:v1",
            mount_path="/workspace",
            cpu=None,
            memory=None,
            skill_repo_url=None,
            skill_branch=None,
            agent_settings_repo_url=None,
            agent_settings_branch=None,
        )

        def _create_namespaced_deployment(namespace, body):
            captured["body"] = body

        self.service.apps_api = SimpleNamespace(create_namespaced_deployment=_create_namespaced_deployment)

        self.service._ensure_deployment(agent)

        spec = captured["body"].spec
        self.assertEqual(spec.strategy.type, "Recreate")
        probe = spec.template.spec.containers[0].readiness_probe
        self.assertEqual(probe.http_get.path, "/ready")
        self.assertEqual(probe.http_get.port, 8000)
        self.assertEqual(probe.initial_delay_seconds, 5)
        self.assertEqual(probe.period_seconds, 10)
        self.assertEqual(probe.failure_threshold, 60)

    def test_patch_deployment_sets_readiness_probe_and_recreate_strategy(self):
        self.service.enabled = True
        captured = {}
        agent = SimpleNamespace(
            id="a1",
            owner_user_id=1,
            runtime_type="native",
            deployment_name="agent-a1",
            namespace="efp-agents",
            image="ghcr.io/acme/runtime:v1",
            mount_path="/workspace",
            cpu=None,
            memory=None,
            skill_repo_url=None,
            skill_branch=None,
            agent_settings_repo_url=None,
            agent_settings_branch=None,
        )

        def _patch_agent_deployment_merge(_agent, body):
            captured["body"] = body

        self.service._patch_agent_deployment_merge = _patch_agent_deployment_merge

        self.service._patch_deployment(agent)

        body = captured["body"]
        self.assertEqual(body["spec"]["strategy"], {"type": "Recreate", "rollingUpdate": None})
        container = body["spec"]["template"]["spec"]["containers"][0]
        probe = container["readinessProbe"]
        self.assertEqual(probe["httpGet"]["path"], "/ready")
        self.assertEqual(probe["httpGet"]["port"], 8000)
        self.assertEqual(probe["initialDelaySeconds"], 5)
        self.assertEqual(probe["periodSeconds"], 10)
        self.assertEqual(probe["failureThreshold"], 60)
        env_names = {item["name"] for item in container["env"]}
        self.assertIn("EFP_PROFILE_CONFIG", env_names)
        self.assertIn("EFP_PROFILE_REVISION", env_names)
        self.assertIn("EFP_PROFILE_ID", env_names)
        self.assertIn("EFP_CONFIG_KEY", env_names)

    def test_upsert_secret_creates_then_replaces_on_conflict(self):
        from kubernetes.client.exceptions import ApiException

        self.service.enabled = True
        calls = []

        def _create(namespace, body):
            calls.append(("create", namespace, body.metadata.name))
            raise ApiException(status=409)

        def _replace(name, namespace, body):
            calls.append(("replace", namespace, name))

        self.service.core_api = SimpleNamespace(
            create_namespaced_secret=_create,
            replace_namespaced_secret=_replace,
        )
        self.service.settings.agents_namespace = "efp-agents"

        self.service.upsert_secret("efp-profile-rp-1", {"native.json": "{}", "revision": "1"})

        self.assertEqual(
            calls,
            [("create", "efp-agents", "efp-profile-rp-1"), ("replace", "efp-agents", "efp-profile-rp-1")],
        )

    def test_delete_secret_swallows_not_found(self):
        from kubernetes.client.exceptions import ApiException

        self.service.enabled = True

        def _delete(name, namespace):
            raise ApiException(status=404)

        self.service.core_api = SimpleNamespace(delete_namespaced_secret=_delete)
        self.service.delete_secret("efp-profile-gone")

    def test_secret_helpers_noop_when_k8s_disabled(self):
        self.service.enabled = False
        self.service.core_api = None
        self.service.upsert_secret("efp-profile-x", {"revision": "1"})
        self.service.delete_secret("efp-profile-x")

    def test_labels_annotations_exclude_tool_git_metadata(self):
        agent = SimpleNamespace(id="a1", owner_user_id=1, runtime_type="native", skill_repo_url="https://example.com/skills.git", skill_branch="main")
        labels = self.service._agent_common_labels(agent)
        anns = self.service._agent_metadata_annotations(agent)
        for k in labels.keys():
            self.assertNotIn("tool-git", k)
        for k in anns.keys():
            self.assertNotIn("tool-git", k)

    def test_labels_annotations_include_runtime_type_without_runtime_git_metadata(self):
        agent = SimpleNamespace(id="a1", owner_user_id=1, runtime_type="opencode", skill_repo_url="https://example.com/skills.git", skill_branch="main")
        labels = self.service._agent_common_labels(agent)
        anns = self.service._agent_metadata_annotations(agent)

        self.assertEqual(labels["runtime-type"], "opencode")
        self.assertEqual(anns["efp/runtime-type"], "opencode")
        self.assertFalse(any(k.startswith("runtime-git") for k in labels.keys()))
        self.assertFalse(any(k.startswith("efp/runtime-git") for k in anns.keys()))

if __name__ == '__main__':
    unittest.main()
