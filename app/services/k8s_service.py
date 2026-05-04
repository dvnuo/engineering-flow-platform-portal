from dataclasses import dataclass
import hashlib
import logging
import re
logger = logging.getLogger(__name__)
from typing import Optional
from urllib.parse import urlparse

from app.config import get_settings
from app.redaction import sanitize_exception_message
from app.utils.git_urls import normalize_git_repo_url


@dataclass
class RuntimeStatus:
    status: str
    message: Optional[str] = None
    cpu_usage: Optional[str] = None
    memory_usage: Optional[str] = None


class K8sService:
    """Minimal Kubernetes integration service for v1 with local no-op fallback."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.enabled = self.settings.k8s_enabled
        self.apps_api = None
        self.core_api = None

        if self.enabled:
            try:
                from kubernetes import client, config

                if self.settings.k8s_incluster:
                    config.load_incluster_config()
                else:
                    config.load_kube_config(config_file=self.settings.k8s_kubeconfig)
                self.apps_api = client.AppsV1Api()
                self.core_api = client.CoreV1Api()
            except Exception:
                self.enabled = False

    def create_agent_runtime(self, agent) -> RuntimeStatus:
        if not self.enabled:
            return RuntimeStatus(status="running")

        try:
            self._ensure_pvc(agent)
            self._ensure_deployment(agent)
            self._ensure_service(agent)
            return RuntimeStatus(status="running")
        except Exception as exc:
            logger.exception("Failed to start agent")
            return RuntimeStatus(status="failed", message=sanitize_exception_message(exc))

    def update_agent_runtime(self, agent) -> RuntimeStatus:
        """Update agent runtime (deployment) with new config."""
        if not self.enabled:
            return RuntimeStatus(status="running")
        
        try:
            self._patch_deployment(agent)
            self._patch_service_metadata(agent)
            return RuntimeStatus(status="running")
        except Exception as exc:
            logger.exception("Failed to update agent runtime")
            return RuntimeStatus(status="failed", message=sanitize_exception_message(exc))


    def _runtime_repo_url(self) -> str | None:
        return normalize_git_repo_url(self.settings.default_agent_runtime_repo_url or self.settings.default_agent_repo_url)

    def _runtime_branch(self) -> str:
        return (self.settings.default_agent_runtime_branch or self.settings.default_agent_branch or "master").strip() or "master"

    def _skill_repo_url(self, agent) -> str | None:
        return normalize_git_repo_url(getattr(agent, "skill_repo_url", None)) or normalize_git_repo_url(self.settings.default_skill_repo_url)

    def _skill_branch(self, agent) -> str:
        return (getattr(agent, "skill_branch", None) or self.settings.default_skill_branch or "master").strip() or "master"

    def _runtime_type(self, agent) -> str:
        runtime_type = (getattr(agent, "runtime_type", None) or "").strip().lower()
        if runtime_type in {"native", "opencode"}:
            return runtime_type
        return "native"

    def _tool_repo_url(self, agent) -> str | None:
        return normalize_git_repo_url(getattr(agent, "tool_repo_url", None)) or normalize_git_repo_url(self.settings.default_tool_repo_url)

    def _tool_branch(self, agent) -> str:
        return (getattr(agent, "tool_branch", None) or self.settings.default_tool_branch or "main").strip() or "main"

    def _effective_mount_path(self, agent) -> str:
        mount_path = getattr(agent, "mount_path", None)
        if mount_path:
            return mount_path
        if self._runtime_type(agent) == "opencode":
            return "/workspace"
        return self.settings.default_agent_mount_path or "/root/.efp"

    def _skills_assets_dir(self) -> str:
        return "/app/skills"

    def _tools_assets_dir(self) -> str:
        return "/app/tools"

    def _agent_container_working_dir(self, agent) -> str | None:
        if self._runtime_type(agent) == "opencode":
            return self._effective_mount_path(agent)
        return None

    def _build_code_and_skill_init_containers_and_mounts(self, agent):
        runtime_type = self._runtime_type(agent)
        if runtime_type == "opencode":
            return self._build_opencode_init_containers_and_mounts(agent)
        return self._build_native_init_containers_and_mounts(agent)

    def _build_native_init_containers_and_mounts(self, agent):
        from kubernetes import client

        init_containers = []
        volume_mounts = []
        git_image = getattr(agent, "git_image", None) or self.settings.default_agent_git_image or "alpine/git:latest"
        prefix = self.settings.agents_volume_sub_path_prefix
        runtime_repo_url = self._runtime_repo_url()
        runtime_branch = self._runtime_branch()
        skill_repo_url = self._skill_repo_url(agent)
        skill_branch = self._skill_branch(agent)
        tool_repo_url = self._tool_repo_url(agent)
        tool_branch = self._tool_branch(agent)

        if runtime_repo_url:
            runtime_sub_path = f"{prefix}/{agent.id}/runtime-code"
            init_containers.append(
                client.V1Container(
                    name="runtime-git-clone",
                    image=git_image,
                    command=["sh", "-c"],
                    args=[self._git_clone_shell_command("/runtime-code")],
                    env=self._build_git_clone_env(runtime_repo_url, runtime_branch),
                    volume_mounts=[client.V1VolumeMount(name="agent-data", mount_path="/runtime-code", sub_path=runtime_sub_path)],
                )
            )
            volume_mounts.append(client.V1VolumeMount(name="agent-data", mount_path="/app/.git", sub_path=f"{runtime_sub_path}/.git"))
            volume_mounts.append(client.V1VolumeMount(name="agent-data", mount_path="/app/src", sub_path=f"{runtime_sub_path}/src"))
        if skill_repo_url:
            skills_sub_path = f"{prefix}/{agent.id}/skills-code"
            init_containers.append(
                client.V1Container(
                    name="skills-git-clone",
                    image=git_image,
                    command=["sh", "-c"],
                    args=[self._git_clone_shell_command("/skills-code")],
                    env=self._build_git_clone_env(skill_repo_url, skill_branch),
                    volume_mounts=[client.V1VolumeMount(name="agent-data", mount_path="/skills-code", sub_path=skills_sub_path)],
                )
            )
            volume_mounts.append(client.V1VolumeMount(name="agent-data", mount_path=self._skills_assets_dir(), sub_path=skills_sub_path))
        if tool_repo_url:
            tools_sub_path = f"{prefix}/{agent.id}/tools-code"
            init_containers.append(
                client.V1Container(
                    name="tools-git-clone",
                    image=git_image,
                    command=["sh", "-c"],
                    args=[self._git_clone_shell_command("/tools-code")],
                    env=self._build_git_clone_env(tool_repo_url, tool_branch),
                    volume_mounts=[client.V1VolumeMount(name="agent-data", mount_path="/tools-code", sub_path=tools_sub_path)],
                )
            )
            volume_mounts.append(client.V1VolumeMount(name="agent-data", mount_path=self._tools_assets_dir(), sub_path=tools_sub_path))
        volume_mounts.append(
            client.V1VolumeMount(
                name="agent-data",
                mount_path=self._effective_mount_path(agent),
                sub_path=f"{prefix}/{agent.id}/data",
            )
        )
        return init_containers, volume_mounts

    def _build_opencode_init_containers_and_mounts(self, agent):
        from kubernetes import client

        git_image = getattr(agent, "git_image", None) or self.settings.default_agent_git_image or "alpine/git:latest"
        prefix = self.settings.agents_volume_sub_path_prefix
        data_sub_path = f"{prefix}/{agent.id}/data"
        volume_mounts = [
            client.V1VolumeMount(
                name="agent-data",
                mount_path=self._effective_mount_path(agent),
                sub_path=data_sub_path,
            )
        ]
        init_containers = []
        skill_repo_url = self._skill_repo_url(agent)
        skill_branch = self._skill_branch(agent)
        if skill_repo_url:
            skills_sub_path = f"{prefix}/{agent.id}/skills-code"
            init_containers.append(
                client.V1Container(
                    name="skills-git-clone",
                    image=git_image,
                    command=["sh", "-c"],
                    args=[self._git_clone_shell_command("/skills-code")],
                    env=self._build_git_clone_env(skill_repo_url, skill_branch),
                    volume_mounts=[client.V1VolumeMount(name="agent-data", mount_path="/skills-code", sub_path=skills_sub_path)],
                )
            )
            volume_mounts.append(client.V1VolumeMount(name="agent-data", mount_path=self._skills_assets_dir(), sub_path=skills_sub_path))
        tool_repo_url = self._tool_repo_url(agent)
        if tool_repo_url:
            tools_sub_path = f"{prefix}/{agent.id}/tools-code"
            init_containers.append(
                client.V1Container(
                    name="tools-git-clone",
                    image=git_image,
                    command=["sh", "-c"],
                    args=[self._git_clone_shell_command("/tools-code")],
                    env=self._build_git_clone_env(tool_repo_url, self._tool_branch(agent)),
                    volume_mounts=[client.V1VolumeMount(name="agent-data", mount_path="/tools-code", sub_path=tools_sub_path)],
                )
            )
            volume_mounts.append(client.V1VolumeMount(name="agent-data", mount_path=self._tools_assets_dir(), sub_path=tools_sub_path))
        return init_containers, volume_mounts

    def _patch_deployment(self, agent) -> None:
        """Patch existing deployment with new config."""
        from kubernetes import client

        labels = self._agent_common_labels(agent)
        annotations = self._agent_patch_annotations(agent)
        init_containers, volume_mounts = self._build_code_and_skill_init_containers_and_mounts(agent)
        resources = self._build_agent_container_resources(agent)
        working_dir = self._agent_container_working_dir(agent)
        api_client = client.ApiClient()

        container = {
            "name": "agent",
            "image": agent.image,
            "ports": [{"containerPort": 8000}],
            "env": api_client.sanitize_for_serialization(self._build_agent_container_env(agent)),
            "volumeMounts": api_client.sanitize_for_serialization(volume_mounts),
        }
        serialized_resources = api_client.sanitize_for_serialization(resources) if resources else None
        if serialized_resources:
            container["resources"] = serialized_resources
        if working_dir:
            container["workingDir"] = working_dir

        patch = {
            "metadata": {
                "labels": labels,
                "annotations": annotations,
            },
            "spec": {
                "template": {
                    "metadata": {
                        "labels": labels,
                        "annotations": annotations,
                    },
                    "spec": {
                        "initContainers": api_client.sanitize_for_serialization(init_containers),
                        "volumes": [
                            {
                                "name": "agent-data",
                                "persistentVolumeClaim": {"claimName": "efp-agents-efs-pvc"},
                            }
                        ],
                        "containers": [container],
                    }
                }
            }
        }
        self.apps_api.patch_namespaced_deployment(
            name=agent.deployment_name,
            namespace=agent.namespace,
            body=patch,
            _content_type="application/merge-patch+json",
        )

    def _patch_service_metadata(self, agent) -> None:
        patch = {
            "metadata": {
                "labels": self._agent_common_labels(agent),
                "annotations": self._agent_patch_annotations(agent),
            }
        }
        self.core_api.patch_namespaced_service(
            name=agent.service_name,
            namespace=agent.namespace,
            body=patch,
        )

    def start_agent(self, agent) -> RuntimeStatus:
        if not self.enabled:
            return RuntimeStatus(status="running")
        try:
            self.apps_api.patch_namespaced_deployment_scale(
                name=agent.deployment_name,
                namespace=agent.namespace,
                body={"spec": {"replicas": 1}},
            )
            return RuntimeStatus(status="running")
        except Exception as exc:
            return RuntimeStatus(status="failed", message=sanitize_exception_message(exc))

    def stop_agent(self, agent) -> RuntimeStatus:
        if not self.enabled:
            return RuntimeStatus(status="stopped")
        try:
            self.apps_api.patch_namespaced_deployment_scale(
                name=agent.deployment_name,
                namespace=agent.namespace,
                body={"spec": {"replicas": 0}},
            )
            return RuntimeStatus(status="stopped")
        except Exception as exc:
            logger.exception("Failed to stop agent")
            return RuntimeStatus(status="failed", message=sanitize_exception_message(exc))

    def delete_agent_runtime(self, agent, destroy_data: bool = False) -> RuntimeStatus:
        if not self.enabled:
            return RuntimeStatus(status="deleted")

        try:
            self.apps_api.delete_namespaced_deployment(name=agent.deployment_name, namespace=agent.namespace)
            self.core_api.delete_namespaced_service(name=agent.service_name, namespace=agent.namespace)
            # Note: With shared PVC, we cannot delete the PVC as it contains all agents' data.
            # Agent data is stored in subPath efp-agents/{agent.id}.
            # For NFS/EFS, implement file-based cleanup if needed.
            # For local-path storage, data persists until PVC is manually deleted.
            if destroy_data:
                pass  # TODO: Implement subPath cleanup for shared PVC
            return RuntimeStatus(status="deleted")
        except Exception as exc:
            return RuntimeStatus(status="failed", message=sanitize_exception_message(exc))

    def get_agent_runtime_status(self, agent) -> RuntimeStatus:
        if not self.enabled:
            return RuntimeStatus(
                status=agent.status,
                message=agent.last_error,
                cpu_usage="N/A (metrics disabled)",
                memory_usage="N/A (metrics disabled)",
            )

        try:
            deploy = self.apps_api.read_namespaced_deployment_status(name=agent.deployment_name, namespace=agent.namespace)
            replicas = deploy.status.replicas or 0
            available = deploy.status.available_replicas or 0
            if replicas == 0:
                return RuntimeStatus(status="stopped", cpu_usage="0", memory_usage="0")
            if available > 0:
                return RuntimeStatus(status="running", cpu_usage="N/A", memory_usage="N/A")
            return RuntimeStatus(status="creating", cpu_usage="N/A", memory_usage="N/A")
        except Exception as exc:
            return RuntimeStatus(status="failed", message=sanitize_exception_message(exc), cpu_usage="N/A", memory_usage="N/A")

    def _is_already_exists(self, exc: Exception) -> bool:
        try:
            from kubernetes.client.exceptions import ApiException

            return isinstance(exc, ApiException) and exc.status == 409
        except Exception:
            return False

    def _sanitize_label_value(self, value: Optional[str]) -> str:
        normalized = re.sub(r"[^a-z0-9-]", "-", (value or "").lower())
        normalized = re.sub(r"-+", "-", normalized).strip("-")
        if not normalized:
            return "unknown"
        normalized = normalized[:63].strip("-")
        if not normalized:
            return "unknown"
        return normalized

    def _repo_metadata(self, repo_url, branch) -> dict[str, str]:
        repo_url = normalize_git_repo_url(repo_url) or ""
        branch = branch or "master"

        if not repo_url:
            return {
                "repo_slug": "none",
                "repo_hash": "none",
                "branch": self._sanitize_label_value(branch),
                "raw_branch": branch,
                "raw_repo_url": "",
            }

        parsed = urlparse(repo_url)
        repo_path = parsed.path.lstrip("/")
        repo_path = repo_path.removesuffix(".git")
        if repo_path:
            parts = [part for part in repo_path.split("/") if part]
            if len(parts) >= 2:
                repo_slug = f"{parts[-2]}-{parts[-1]}"
            else:
                repo_slug = parts[-1]
        else:
            repo_slug = "repo"

        return {
            "repo_slug": self._sanitize_label_value(repo_slug),
            "repo_hash": hashlib.sha1(repo_url.encode("utf-8")).hexdigest()[:12],
            "branch": self._sanitize_label_value(branch),
            "raw_branch": branch,
            "raw_repo_url": repo_url,
        }

    def _agent_common_labels(self, agent) -> dict[str, str]:
        runtime_type = self._runtime_type(agent)
        runtime_meta = self._repo_metadata(self._runtime_repo_url(), self._runtime_branch())
        skill_meta = self._repo_metadata(self._skill_repo_url(agent), self._skill_branch(agent))
        tool_meta = self._repo_metadata(self._tool_repo_url(agent), self._tool_branch(agent))
        return {
            "app": "agent", "agent-id": agent.id, "owner-id": str(agent.owner_user_id), "managed-by": "portal",
            "runtime-type": self._sanitize_label_value(runtime_type),
            "runtime-git-repo": runtime_meta["repo_slug"], "runtime-git-repo-hash": runtime_meta["repo_hash"], "runtime-git-branch": runtime_meta["branch"],
            "skill-git-repo": skill_meta["repo_slug"], "skill-git-repo-hash": skill_meta["repo_hash"], "skill-git-branch": skill_meta["branch"],
            "tool-git-repo": tool_meta["repo_slug"], "tool-git-repo-hash": tool_meta["repo_hash"], "tool-git-branch": tool_meta["branch"],
        }

    def _agent_metadata_annotations(self, agent) -> dict[str, str]:
        runtime_type = self._runtime_type(agent)
        runtime_meta = self._repo_metadata(self._runtime_repo_url(), self._runtime_branch())
        skill_meta = self._repo_metadata(self._skill_repo_url(agent), self._skill_branch(agent))
        tool_meta = self._repo_metadata(self._tool_repo_url(agent), self._tool_branch(agent))
        annotations = {}
        annotations["efp/runtime-type"] = runtime_type
        if runtime_meta["raw_repo_url"]:
            annotations["efp/runtime-git-repo-url"] = runtime_meta["raw_repo_url"]
        if runtime_meta["raw_branch"]:
            annotations["efp/runtime-git-branch"] = runtime_meta["raw_branch"]
        if skill_meta["raw_repo_url"]:
            annotations["efp/skill-git-repo-url"] = skill_meta["raw_repo_url"]
            annotations["efp/git-repo-url"] = skill_meta["raw_repo_url"]
        if skill_meta["raw_branch"]:
            annotations["efp/skill-git-branch"] = skill_meta["raw_branch"]
            annotations["efp/git-branch"] = skill_meta["raw_branch"]
        if tool_meta["raw_repo_url"]:
            annotations["efp/tool-git-repo-url"] = tool_meta["raw_repo_url"]
        if tool_meta["raw_branch"]:
            annotations["efp/tool-git-branch"] = tool_meta["raw_branch"]
        return annotations

    def _agent_patch_annotations(self, agent) -> dict[str, Optional[str]]:
        runtime_type = self._runtime_type(agent)
        runtime_meta = self._repo_metadata(self._runtime_repo_url(), self._runtime_branch())
        skill_meta = self._repo_metadata(self._skill_repo_url(agent), self._skill_branch(agent))
        tool_meta = self._repo_metadata(self._tool_repo_url(agent), self._tool_branch(agent))
        return {
            "efp/runtime-type": runtime_type,
            "efp/runtime-git-repo-url": runtime_meta["raw_repo_url"] or None,
            "efp/runtime-git-branch": runtime_meta["raw_branch"] or None,
            "efp/skill-git-repo-url": skill_meta["raw_repo_url"] or None,
            "efp/skill-git-branch": skill_meta["raw_branch"] or None,
            "efp/git-repo-url": skill_meta["raw_repo_url"] or None,
            "efp/git-branch": skill_meta["raw_branch"] or None,
            "efp/tool-git-repo-url": tool_meta["raw_repo_url"] or None,
            "efp/tool-git-branch": tool_meta["raw_branch"] or None,
        }

    def _ensure_pvc(self, agent) -> None:
        # Using shared PVC for all agents; each agent uses its own subPath.
        from kubernetes import client

        body = client.V1PersistentVolumeClaim(
            metadata=client.V1ObjectMeta(
                name="efp-agents-efs-pvc",
                namespace=agent.namespace,
                labels={"app": "agent", "managed-by": "portal", "storage-scope": "shared"},
            ),
            spec=client.V1PersistentVolumeClaimSpec(
                access_modes=self.settings.k8s_pvc_access_modes,
                storage_class_name=self.settings.k8s_storage_class,
                resources=client.V1VolumeResourceRequirements(requests={"storage": f"{agent.disk_size_gi}Gi"}),
            ),
        )
        try:
            self.core_api.create_namespaced_persistent_volume_claim(namespace=agent.namespace, body=body)
        except Exception as exc:
            if not self._is_already_exists(exc):
                raise

    def _ensure_deployment(self, agent) -> None:
        from kubernetes import client

        labels = self._agent_common_labels(agent)
        annotations = self._agent_metadata_annotations(agent)
        
        init_containers, volume_mounts = self._build_code_and_skill_init_containers_and_mounts(agent)
        
        body = client.V1Deployment(
            metadata=client.V1ObjectMeta(
                name=agent.deployment_name,
                namespace=agent.namespace,
                labels=labels,
                annotations=annotations or None,
            ),
            spec=client.V1DeploymentSpec(
                replicas=1,
                selector=client.V1LabelSelector(match_labels={"app": "agent", "agent-id": agent.id}),
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(labels=labels, annotations=annotations or None),
                    spec=client.V1PodSpec(
                        init_containers=init_containers,
                        containers=[
                    client.V1Container(
                        name="agent",
                        image=agent.image,
                        ports=[client.V1ContainerPort(container_port=8000)],
                        env=self._build_agent_container_env(agent),
                        resources=self._build_agent_container_resources(agent),
                        volume_mounts=volume_mounts,
                        working_dir=self._agent_container_working_dir(agent),
                    )
                ],
                        volumes=[
                            client.V1Volume(
                                name="agent-data",
                                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(claim_name="efp-agents-efs-pvc"),
                            )
                        ],
                    ),
                ),
            ),
        )
        try:
            self.apps_api.create_namespaced_deployment(namespace=agent.namespace, body=body)
        except Exception as exc:
            if not self._is_already_exists(exc):
                raise

    def _build_git_clone_env(self, repo_url: Optional[str], branch: str):
        from kubernetes import client

        normalized_repo_url = normalize_git_repo_url(repo_url)
        if not normalized_repo_url:
            return []
        env = [
            client.V1EnvVar(name="GIT_REPO_URL", value=normalized_repo_url),
            client.V1EnvVar(name="GIT_BRANCH", value=branch or "master"),
        ]
        if not self.settings.k8s_git_token_key:
            return env

        env.append(
            client.V1EnvVar(
                name="GIT_TOKEN",
                value_from=client.V1EnvVarSource(
                    secret_key_ref=client.V1SecretKeySelector(
                        name="efp-agents-secret",
                        key=self.settings.k8s_git_token_key,
                        optional=True,
                    )
                ),
            )
        )
        return env

    def _build_agent_container_env(self, agent=None):
        from kubernetes import client

        env = [
            client.V1EnvVar(
                name="EFP_CONFIG_KEY",
                value_from=client.V1EnvVarSource(
                    secret_key_ref=client.V1SecretKeySelector(
                        name="efp-agents-secret",
                        key="EFP_CONFIG_KEY",
                        optional=True,
                    )
                ),
            ),
        ]
        base_url = (self.settings.portal_internal_base_url or "").strip()
        if base_url:
            env.append(client.V1EnvVar(name="PORTAL_INTERNAL_BASE_URL", value=base_url))
        if agent is not None and getattr(agent, "id", None):
            env.append(client.V1EnvVar(name="PORTAL_AGENT_ID", value=str(agent.id)))
            runtime_type = self._runtime_type(agent)
            workspace_dir = self._effective_mount_path(agent)
            env.append(client.V1EnvVar(name="PORTAL_RUNTIME_TYPE", value=runtime_type))
            env.append(client.V1EnvVar(name="EFP_RUNTIME_TYPE", value=runtime_type))
            env.append(client.V1EnvVar(name="EFP_WORKSPACE_DIR", value=workspace_dir))
            env.append(client.V1EnvVar(name="EFP_SKILLS_DIR", value=self._skills_assets_dir()))
            env.append(client.V1EnvVar(name="EFP_TOOLS_DIR", value=self._tools_assets_dir()))
            if runtime_type == "opencode":
                env.append(client.V1EnvVar(name="OPENCODE_WORKSPACE", value=workspace_dir))
                env.append(client.V1EnvVar(name="OPENCODE_TOOLS_DIR", value=self._tools_assets_dir()))
        return env

    def _build_agent_container_resources(self, agent):
        from kubernetes import client

        requests = {}
        if getattr(agent, "cpu", None):
            requests["cpu"] = agent.cpu
        if getattr(agent, "memory", None):
            requests["memory"] = agent.memory
        if not requests:
            return None
        return client.V1ResourceRequirements(requests=requests)

    def _git_clone_shell_command(self, target_dir: str) -> str:
        return (
            f"mkdir -p \"{target_dir}\" && "
            "rm -rf /tmp/git-clone-work && mkdir -p /tmp/git-clone-work && cd /tmp/git-clone-work && "
            "REPO_URL=\"${GIT_REPO_URL}\" && "
            "if [ -n \"${GIT_TOKEN}\" ]; then "
            "ASKPASS_SCRIPT=/tmp/git-askpass.sh && "
            "printf '%s\n' '#!/bin/sh' 'case \"$1\" in' '  *Username*|*username*) echo \"x-access-token\" ;;' '  *) echo \"${GIT_TOKEN}\" ;;' 'esac' > \"${ASKPASS_SCRIPT}\" && "
            "chmod 700 \"${ASKPASS_SCRIPT}\" && export GIT_ASKPASS=\"${ASKPASS_SCRIPT}\" && export GIT_TERMINAL_PROMPT=0; "
            "fi && "
            "git clone --depth 1 --branch \"${GIT_BRANCH}\" \"${REPO_URL}\" . && "
            f"find \"{target_dir}\" -mindepth 1 -maxdepth 1 -exec rm -rf -- {{}} + && cp -a /tmp/git-clone-work/. \"{target_dir}/\" && "
            "rm -f /tmp/git-askpass.sh"
        )

    def _ensure_service(self, agent) -> None:
        from kubernetes import client
        labels = self._agent_common_labels(agent)
        annotations = self._agent_metadata_annotations(agent)

        body = client.V1Service(
            metadata=client.V1ObjectMeta(
                name=agent.service_name,
                namespace=agent.namespace,
                labels=labels,
                annotations=annotations or None,
            ),
            spec=client.V1ServiceSpec(
                selector={"app": "agent", "agent-id": agent.id},
                ports=[client.V1ServicePort(port=8000, target_port=8000)],
                type=self.settings.k8s_agent_service_type,
            ),
        )
        try:
            self.core_api.create_namespaced_service(namespace=agent.namespace, body=body)
        except Exception as exc:
            if not self._is_already_exists(exc):
                raise
