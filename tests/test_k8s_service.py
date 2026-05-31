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
                "default_skill_repo_url",
                "default_skill_branch",
                "default_skill_repo_subdir",
                "default_skill_asset_version",
            )
        }
        self.service.settings.default_skill_repo_url = ""
        self.service.settings.default_skill_branch = "master"
        self.service.settings.default_skill_repo_subdir = ""
        self.service.settings.default_skill_asset_version = ""

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

    def test_single_runtime_env_excludes_old_timeout_contract(self):
        agent = SimpleNamespace(id="a1", runtime_type="native", mount_path="/workspace")
        env = self.service._build_agent_container_env(agent)
        env_map = {e.name: getattr(e, 'value', None) for e in env}

        self.assertNotIn("EFP_TASK_COMPLETION_TIMEOUT_SECONDS", env_map)
        self.assertNotIn("EFP_CHAT_SUBMIT_TIMEOUT_SECONDS", env_map)
        self.assertNotIn("EFP_CHAT_COMPLETION_TIMEOUT_SECONDS", env_map)

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
