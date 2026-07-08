from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import logging
import re
from typing import Optional
from urllib.parse import urlparse
from uuid import uuid4

from app.config import get_settings
from app.contracts.runtime_type import InvalidRuntimeType, normalize_runtime_type
from app.redaction import sanitize_exception_message
from app.utils.git_urls import normalize_git_repo_url


OPENCODE_INTERNAL_HTTP_PORT = 4096
MERGE_PATCH_CONTENT_TYPE = "application/merge-patch+json"
logger = logging.getLogger(__name__)


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

    @staticmethod
    def _positive_int_setting(value, default: int) -> int:
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            normalized = default
        return normalized if normalized > 0 else default

    def create_agent_runtime(self, agent) -> RuntimeStatus:
        if not self.enabled:
            return RuntimeStatus(status="running")

        try:
            self._ensure_pvc(agent)
            self._ensure_deployment(agent)
            self._ensure_service(agent)
            # Resource creation is accepted, but the Deployment/Pod may still be
            # pulling images, running init containers, or starting the runtime. The
            # status endpoint is the source of truth for readiness.
            return RuntimeStatus(status="creating")
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

    def _skill_repo_url(self, agent) -> str | None:
        return normalize_git_repo_url(getattr(agent, "skill_repo_url", None)) or normalize_git_repo_url(self.settings.default_skill_repo_url)

    def _skill_branch(self, agent) -> str:
        return (getattr(agent, "skill_branch", None) or self.settings.default_skill_branch or "master").strip() or "master"

    def _skill_repo_subdir(self, agent) -> str:
        raw = (
            getattr(agent, "skill_repo_subdir", None)
            or getattr(self.settings, "default_skill_repo_subdir", "")
            or ""
        )
        return self._normalize_skill_repo_subdir(raw)

    def _normalize_skill_repo_subdir(self, raw: str) -> str:
        value = str(raw or "").strip().strip("/")
        if not value:
            return ""
        parts = [part for part in value.split("/") if part]
        if any(part in {".", ".."} for part in parts):
            raise ValueError(f"Invalid DEFAULT_SKILL_REPO_SUBDIR: {raw!r}")
        return "/".join(parts)

    def _skill_asset_version(self, agent) -> str:
        return str(
            getattr(agent, "skill_asset_version", None)
            or getattr(self.settings, "default_skill_asset_version", "")
            or ""
        ).strip()

    def _agent_settings_repo_url(self, agent) -> str | None:
        return normalize_git_repo_url(getattr(agent, "agent_settings_repo_url", None)) or normalize_git_repo_url(
            self.settings.default_agent_settings_repo_url
        )

    def _agent_settings_branch(self, agent) -> str:
        return (getattr(agent, "agent_settings_branch", None) or self.settings.default_agent_settings_branch or "master").strip() or "master"

    def _agent_settings_repo_subdir(self, agent) -> str:
        raw = (
            getattr(agent, "agent_settings_subdir", None)
            or getattr(self.settings, "default_agent_settings_repo_subdir", "")
            or ""
        )
        return self._normalize_agent_settings_repo_subdir(raw)

    def _normalize_agent_settings_repo_subdir(self, raw: str) -> str:
        value = str(raw or "").strip().strip("/")
        if not value:
            return ""
        parts = [part for part in value.split("/") if part]
        if any(part in {".", ".."} for part in parts):
            raise ValueError(f"Invalid DEFAULT_AGENT_SETTINGS_REPO_SUBDIR: {raw!r}")
        return "/".join(parts)

    def _agent_settings_asset_version(self, agent) -> str:
        return str(
            getattr(agent, "agent_settings_asset_version", None)
            or getattr(self.settings, "default_agent_settings_asset_version", "")
            or ""
        ).strip()

    def _runtime_type(self, agent) -> str:
        raw = getattr(agent, "runtime_type", None)
        if raw is None or not str(raw).strip():
            return "native"
        try:
            return normalize_runtime_type(raw)
        except InvalidRuntimeType as exc:
            agent_id = getattr(agent, "id", "-")
            raise ValueError(f"Invalid runtime_type for agent {agent_id}: {exc}") from exc

    def _effective_mount_path(self, agent) -> str:
        mount_path = getattr(agent, "mount_path", None)
        if mount_path:
            return mount_path
        return "/workspace"

    def _skills_assets_dir(self) -> str:
        return "/app/skills"

    def _agent_state_root(self, agent) -> str:
        prefix = self.settings.agents_volume_sub_path_prefix
        return f"/agent-data/{prefix}/{agent.id}"

    def _build_asset_dirs_init_container(self, agent, *, include_opencode_state: bool = False):
        from kubernetes import client

        git_image = getattr(agent, "git_image", None) or self.settings.default_agent_git_image or "alpine/git:latest"
        commands = [
            "set -eu",
            'mkdir -p "$AGENT_STATE_ROOT/data"',
        ]
        name = "agent-asset-dirs-init"
        if include_opencode_state:
            name = "opencode-persistent-dirs-init"
            commands.append('mkdir -p "$AGENT_STATE_ROOT/data/.opencode" "$AGENT_STATE_ROOT/opencode-state" "$AGENT_STATE_ROOT/adapter-state"')
            commands.append('chown -R 0:0 "$AGENT_STATE_ROOT/data" "$AGENT_STATE_ROOT/opencode-state" "$AGENT_STATE_ROOT/adapter-state" || true')

        return client.V1Container(
            name=name,
            image=git_image,
            command=["sh", "-c"],
            args=["\n".join(commands)],
            env=[client.V1EnvVar(name="AGENT_STATE_ROOT", value=self._agent_state_root(agent))],
            volume_mounts=[client.V1VolumeMount(name="agent-data", mount_path="/agent-data")],
        )

    def _opencode_state_dir(self) -> str:
        return "/root/.local/share/opencode"

    def _opencode_adapter_state_dir(self) -> str:
        return "/root/.local/share/efp-compat"

    def _opencode_config_path(self, agent) -> str:
        workspace = self._effective_mount_path(agent).rstrip("/") or "/workspace"
        return f"{workspace}/.opencode/opencode.json"

    def _native_runtime_session_root(self, agent) -> str:
        workspace = self._effective_mount_path(agent).rstrip("/") or "/workspace"
        return f"{workspace}/.efp/runtime"

    def _efp_config_path(self, agent) -> str:
        # The EFP config carries plaintext CLI secrets (atlassian/jenkins/aws/
        # browserstack tokens). Keep it OUT of the workspace, which is both
        # agent-writable and browsable/downloadable via the server-files panel.
        # Home (/root) is agent-private and the runtime re-projects the profile
        # on every pod start, so an ephemeral location is fine. Sessions stay on
        # the workspace volume (see _native_runtime_session_root).
        return "/root/.efp/config.yaml"

    def _mobile_state_dir(self, agent) -> str:
        workspace = self._effective_mount_path(agent).rstrip("/") or "/workspace"
        return f"{workspace}/.efp/mobile-auto/runs"

    def _mobile_artifacts_dir(self, agent) -> str:
        workspace = self._effective_mount_path(agent).rstrip("/") or "/workspace"
        return f"{workspace}/.efp/mobile-auto/artifacts"

    def _agent_container_working_dir(self, agent) -> str | None:
        return self._effective_mount_path(agent)

    def _build_code_and_skill_init_containers_and_mounts(self, agent):
        if self._runtime_type(agent) == "opencode":
            return self._build_opencode_init_containers_and_mounts(agent)
        return self._build_native_init_containers_and_mounts(agent)

    def _build_native_init_containers_and_mounts(self, agent):
        from kubernetes import client

        init_containers = [self._build_asset_dirs_init_container(agent)]
        volume_mounts = []
        git_image = getattr(agent, "git_image", None) or self.settings.default_agent_git_image or "alpine/git:latest"
        prefix = self.settings.agents_volume_sub_path_prefix
        agent_settings_repo_url = self._agent_settings_repo_url(agent)
        agent_settings_branch = self._agent_settings_branch(agent)
        agent_settings_repo_subdir = self._agent_settings_repo_subdir(agent)
        skill_repo_url = self._skill_repo_url(agent)
        skill_branch = self._skill_branch(agent)
        skill_repo_subdir = self._skill_repo_subdir(agent)

        if agent_settings_repo_url:
            init_containers.append(
                client.V1Container(
                    name="agent-settings-git-clone",
                    image=git_image,
                    command=["sh", "-c"],
                    args=[self._agent_settings_git_clone_shell_command(self._effective_mount_path(agent))],
                    env=self._build_agent_settings_git_clone_env(
                        agent_settings_repo_url,
                        agent_settings_branch,
                        agent_settings_repo_subdir,
                    ),
                    volume_mounts=[
                        client.V1VolumeMount(
                            name="agent-data",
                            mount_path=self._effective_mount_path(agent),
                            sub_path=f"{prefix}/{agent.id}/data",
                        )
                    ],
                )
            )

        if skill_repo_url:
            skills_sub_path = f"{prefix}/{agent.id}/skills-code"
            init_containers.append(
                client.V1Container(
                    name="skills-git-clone",
                    image=git_image,
                    command=["sh", "-c"],
                    args=[self._skill_git_clone_shell_command("/skills-code")],
                    env=self._build_skill_git_clone_env(skill_repo_url, skill_branch, skill_repo_subdir),
                    volume_mounts=[client.V1VolumeMount(name="agent-data", mount_path="/skills-code", sub_path=skills_sub_path)],
                )
            )
            volume_mounts.append(client.V1VolumeMount(name="agent-data", mount_path=self._skills_assets_dir(), sub_path=skills_sub_path))
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
        volume_mounts = [
            client.V1VolumeMount(
                name="agent-data",
                mount_path=self._effective_mount_path(agent),
                sub_path=f"{prefix}/{agent.id}/data",
            ),
            client.V1VolumeMount(
                name="agent-data",
                mount_path=self._opencode_state_dir(),
                sub_path=f"{prefix}/{agent.id}/opencode-state",
            ),
            client.V1VolumeMount(
                name="agent-data",
                mount_path=self._opencode_adapter_state_dir(),
                sub_path=f"{prefix}/{agent.id}/adapter-state",
            ),
        ]
        init_containers = [self._build_asset_dirs_init_container(agent, include_opencode_state=True)]
        agent_settings_repo_url = self._agent_settings_repo_url(agent)
        agent_settings_branch = self._agent_settings_branch(agent)
        agent_settings_repo_subdir = self._agent_settings_repo_subdir(agent)
        if agent_settings_repo_url:
            init_containers.append(
                client.V1Container(
                    name="agent-settings-git-clone",
                    image=git_image,
                    command=["sh", "-c"],
                    args=[self._agent_settings_git_clone_shell_command(self._effective_mount_path(agent))],
                    env=self._build_agent_settings_git_clone_env(
                        agent_settings_repo_url,
                        agent_settings_branch,
                        agent_settings_repo_subdir,
                    ),
                    volume_mounts=[
                        client.V1VolumeMount(
                            name="agent-data",
                            mount_path=self._effective_mount_path(agent),
                            sub_path=f"{prefix}/{agent.id}/data",
                        )
                    ],
                )
            )
        skill_repo_url = self._skill_repo_url(agent)
        skill_branch = self._skill_branch(agent)
        skill_repo_subdir = self._skill_repo_subdir(agent)
        if skill_repo_url:
            skills_sub_path = f"{prefix}/{agent.id}/skills-code"
            init_containers.append(
                client.V1Container(
                    name="skills-git-clone",
                    image=git_image,
                    command=["sh", "-c"],
                    args=[self._skill_git_clone_shell_command("/skills-code")],
                    env=self._build_skill_git_clone_env(skill_repo_url, skill_branch, skill_repo_subdir),
                    volume_mounts=[client.V1VolumeMount(name="agent-data", mount_path="/skills-code", sub_path=skills_sub_path)],
                )
            )
            volume_mounts.append(client.V1VolumeMount(name="agent-data", mount_path=self._skills_assets_dir(), sub_path=skills_sub_path))
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
            "imagePullPolicy": "Always",
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
        self._patch_agent_deployment_merge(agent, patch)

    def _apps_v1_merge_patch(self, *, path: str, path_params: dict, body: dict, response_type: str):
        return self.apps_api.api_client.call_api(
            path,
            "PATCH",
            path_params=path_params,
            query_params=[],
            header_params={
                "Accept": "application/json",
                "Content-Type": MERGE_PATCH_CONTENT_TYPE,
            },
            body=body,
            post_params=[],
            files={},
            response_type=response_type,
            auth_settings=["BearerToken"],
            async_req=False,
            _return_http_data_only=True,
            _preload_content=True,
            collection_formats={},
        )

    def _core_v1_merge_patch(self, *, path: str, path_params: dict, body: dict, response_type: str):
        return self.core_api.api_client.call_api(
            path,
            "PATCH",
            path_params=path_params,
            query_params=[],
            header_params={
                "Accept": "application/json",
                "Content-Type": MERGE_PATCH_CONTENT_TYPE,
            },
            body=body,
            post_params=[],
            files={},
            response_type=response_type,
            auth_settings=["BearerToken"],
            async_req=False,
            _return_http_data_only=True,
            _preload_content=True,
            collection_formats={},
        )

    def _patch_agent_deployment_merge(self, agent, body: dict, *, response_type: str = "V1Deployment"):
        return self._apps_v1_merge_patch(
            path="/apis/apps/v1/namespaces/{namespace}/deployments/{name}",
            path_params={"namespace": agent.namespace, "name": agent.deployment_name},
            body=body,
            response_type=response_type,
        )

    def _patch_agent_deployment_scale_merge(self, agent, replicas: int):
        return self._apps_v1_merge_patch(
            path="/apis/apps/v1/namespaces/{namespace}/deployments/{name}/scale",
            path_params={"namespace": agent.namespace, "name": agent.deployment_name},
            body={"spec": {"replicas": replicas}},
            response_type="V1Scale",
        )

    def _patch_agent_service_merge(self, agent, body: dict):
        return self._core_v1_merge_patch(
            path="/api/v1/namespaces/{namespace}/services/{name}",
            path_params={"namespace": agent.namespace, "name": agent.service_name},
            body=body,
            response_type="V1Service",
        )

    def _patch_service_metadata(self, agent) -> None:
        patch = {
            "metadata": {
                "labels": self._agent_common_labels(agent),
                "annotations": self._agent_patch_annotations(agent),
            }
        }
        self._patch_agent_service_merge(agent, patch)

    def start_agent(self, agent) -> RuntimeStatus:
        if not self.enabled:
            return RuntimeStatus(status="running")

        patch_error = None
        try:
            self._patch_deployment(agent)
            self._patch_service_metadata(agent)
        except Exception as exc:
            patch_error = exc
            logger.exception("Failed to patch agent runtime before start; scaling existing deployment anyway")

        try:
            self._scale_agent_deployment(agent, 1)
        except Exception as exc:
            logger.exception("Failed to scale agent deployment to 1")
            return RuntimeStatus(status="failed", message=sanitize_exception_message(exc))

        if patch_error is not None:
            return RuntimeStatus(
                status="running",
                message=f"Deployment scaled to 1, but config patch failed: {sanitize_exception_message(patch_error)}",
            )

        return RuntimeStatus(status="running")

    def _scale_agent_deployment(self, agent, replicas: int) -> None:
        self._patch_agent_deployment_scale_merge(agent, replicas)

    def stop_agent(self, agent) -> RuntimeStatus:
        if not self.enabled:
            return RuntimeStatus(status="stopped")
        try:
            self._scale_agent_deployment(agent, 0)
            return RuntimeStatus(status="stopped")
        except Exception as exc:
            logger.exception("Failed to stop agent")
            return RuntimeStatus(status="failed", message=sanitize_exception_message(exc))

    def restart_agent(self, agent) -> RuntimeStatus:
        if not self.enabled:
            return RuntimeStatus(
                status="failed",
                message="Kubernetes integration is disabled; restart is unsupported in noop mode.",
            )

        try:
            self._patch_deployment(agent)
            self._patch_service_metadata(agent)
            self._scale_agent_deployment(agent, 1)
            restarted_at = datetime.now(timezone.utc).isoformat()
            restart_request_id = str(uuid4())
            self._patch_agent_deployment_merge(
                agent,
                {
                    "spec": {
                        "template": {
                            "metadata": {
                                "annotations": {
                                    "efp.dvnuo.io/restarted-at": restarted_at,
                                    "kubectl.kubernetes.io/restartedAt": restarted_at,
                                    "efp.dvnuo.io/restart-request-id": restart_request_id,
                                    "efp.dvnuo.io/restart-requested-at": restarted_at,
                                }
                            }
                        }
                    }
                },
            )
        except Exception as exc:
            logger.exception("Failed to roll restart agent deployment")
            return RuntimeStatus(status="failed", message=sanitize_exception_message(exc))

        return RuntimeStatus(
            status="restarting",
            message=f"Restart requested: {restart_request_id}",
        )

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
            metadata = getattr(deploy, "metadata", None)
            deploy_status = getattr(deploy, "status", None)
            desired = self._get_k8s_attr(getattr(deploy, "spec", None), "replicas") or 0
            generation = self._get_k8s_attr(metadata, "generation")
            observed_generation = self._get_k8s_attr(deploy_status, "observed_generation", "observedGeneration")
            replicas = self._get_k8s_attr(deploy_status, "replicas") or 0
            updated = self._get_k8s_attr(deploy_status, "updated_replicas", "updatedReplicas") or 0
            available = self._get_k8s_attr(deploy_status, "available_replicas", "availableReplicas") or 0
            unavailable = self._get_k8s_attr(deploy_status, "unavailable_replicas", "unavailableReplicas")
            conditions = self._get_k8s_attr(deploy_status, "conditions") or []

            if desired <= 0:
                return RuntimeStatus(status="stopped", cpu_usage="0", memory_usage="0")

            for condition in conditions:
                condition_type = self._get_k8s_attr(condition, "type")
                condition_status = self._get_k8s_attr(condition, "status")
                condition_reason = str(self._get_k8s_attr(condition, "reason") or "")
                if (
                    condition_type == "Progressing"
                    and condition_status == "False"
                    and (
                        "ProgressDeadlineExceeded" in condition_reason
                        or "ReplicaSetCreateError" in condition_reason
                    )
                ):
                    condition_message = self._get_k8s_attr(condition, "message")
                    message_parts = [part for part in (condition_reason, condition_message) if part]
                    return RuntimeStatus(
                        status="failed",
                        message=": ".join(message_parts) if message_parts else None,
                        cpu_usage="N/A",
                        memory_usage="N/A",
                    )

            status_while_pending = self._pending_rollout_status(agent)

            if observed_generation is not None and generation is not None and observed_generation < generation:
                return RuntimeStatus(status=status_while_pending, cpu_usage="N/A", memory_usage="N/A")

            rollout_complete = (
                updated >= desired
                and available >= desired
                and replicas == updated
                and unavailable in (0, None)
            )
            if not rollout_complete:
                return RuntimeStatus(status=status_while_pending, cpu_usage="N/A", memory_usage="N/A")

            return RuntimeStatus(status="running", cpu_usage="N/A", memory_usage="N/A")
        except Exception as exc:
            return RuntimeStatus(status="failed", message=sanitize_exception_message(exc), cpu_usage="N/A", memory_usage="N/A")

    def _pending_rollout_status(self, agent) -> str:
        return "restarting" if str(getattr(agent, "status", "") or "").lower() == "restarting" else "creating"

    def _get_k8s_attr(self, obj, *names):
        if obj is None:
            return None
        for name in names:
            if hasattr(obj, name):
                return getattr(obj, name)
        return None

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
        agent_settings_meta = self._repo_metadata(self._agent_settings_repo_url(agent), self._agent_settings_branch(agent))
        skill_meta = self._repo_metadata(self._skill_repo_url(agent), self._skill_branch(agent))
        return {
            "app": "agent", "agent-id": agent.id, "owner-id": str(agent.owner_user_id), "managed-by": "portal",
            "runtime-type": self._sanitize_label_value(runtime_type),
            "agent-settings-git-repo": agent_settings_meta["repo_slug"],
            "agent-settings-git-repo-hash": agent_settings_meta["repo_hash"],
            "agent-settings-git-branch": agent_settings_meta["branch"],
            "skill-git-repo": skill_meta["repo_slug"], "skill-git-repo-hash": skill_meta["repo_hash"], "skill-git-branch": skill_meta["branch"],
        }

    def _agent_metadata_annotations(self, agent) -> dict[str, str]:
        runtime_type = self._runtime_type(agent)
        agent_settings_meta = self._repo_metadata(self._agent_settings_repo_url(agent), self._agent_settings_branch(agent))
        agent_settings_subdir = self._agent_settings_repo_subdir(agent)
        agent_settings_asset_version = self._agent_settings_asset_version(agent)
        skill_meta = self._repo_metadata(self._skill_repo_url(agent), self._skill_branch(agent))
        skill_subdir = self._skill_repo_subdir(agent)
        skill_asset_version = self._skill_asset_version(agent)
        annotations = {}
        annotations["efp/runtime-type"] = runtime_type
        if agent_settings_meta["raw_repo_url"]:
            annotations["efp/agent-settings-git-repo-url"] = agent_settings_meta["raw_repo_url"]
        if agent_settings_meta["raw_branch"]:
            annotations["efp/agent-settings-git-branch"] = agent_settings_meta["raw_branch"]
        if agent_settings_subdir:
            annotations["efp/agent-settings-git-subdir"] = agent_settings_subdir
        if agent_settings_asset_version:
            annotations["efp/agent-settings-asset-version"] = agent_settings_asset_version
        if skill_meta["raw_repo_url"]:
            annotations["efp/skill-git-repo-url"] = skill_meta["raw_repo_url"]
            annotations["efp/git-repo-url"] = skill_meta["raw_repo_url"]
        if skill_meta["raw_branch"]:
            annotations["efp/skill-git-branch"] = skill_meta["raw_branch"]
            annotations["efp/git-branch"] = skill_meta["raw_branch"]
        if skill_subdir:
            annotations["efp/skill-git-subdir"] = skill_subdir
        if skill_asset_version:
            annotations["efp/skill-asset-version"] = skill_asset_version
        return annotations

    def _agent_patch_annotations(self, agent) -> dict[str, Optional[str]]:
        runtime_type = self._runtime_type(agent)
        agent_settings_meta = self._repo_metadata(self._agent_settings_repo_url(agent), self._agent_settings_branch(agent))
        agent_settings_subdir = self._agent_settings_repo_subdir(agent)
        agent_settings_asset_version = self._agent_settings_asset_version(agent)
        skill_meta = self._repo_metadata(self._skill_repo_url(agent), self._skill_branch(agent))
        skill_subdir = self._skill_repo_subdir(agent)
        skill_asset_version = self._skill_asset_version(agent)
        return {
            "efp/runtime-type": runtime_type,
            "efp/agent-settings-git-repo-url": agent_settings_meta["raw_repo_url"] or None,
            "efp/agent-settings-git-branch": agent_settings_meta["raw_branch"] or None,
            "efp/agent-settings-git-subdir": agent_settings_subdir or None,
            "efp/agent-settings-asset-version": agent_settings_asset_version or None,
            "efp/skill-git-repo-url": skill_meta["raw_repo_url"] or None,
            "efp/skill-git-branch": skill_meta["raw_branch"] or None,
            "efp/skill-git-subdir": skill_subdir or None,
            "efp/skill-asset-version": skill_asset_version or None,
            "efp/git-repo-url": skill_meta["raw_repo_url"] or None,
            "efp/git-branch": skill_meta["raw_branch"] or None,
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
                        image_pull_policy="Always",
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

    def _build_skill_git_clone_env(self, repo_url: Optional[str], branch: str, subdir: str):
        from kubernetes import client

        env = self._build_git_clone_env(repo_url, branch)
        env.append(client.V1EnvVar(name="SKILL_REPO_SUBDIR", value=subdir))
        return env

    def _build_agent_settings_git_clone_env(self, repo_url: Optional[str], branch: str, subdir: str):
        from kubernetes import client

        env = self._build_git_clone_env(repo_url, branch)
        env.append(client.V1EnvVar(name="AGENT_SETTINGS_REPO_SUBDIR", value=subdir))
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
            if getattr(agent, "name", None):
                env.append(client.V1EnvVar(name="PORTAL_AGENT_NAME", value=str(agent.name)))
            runtime_type = self._runtime_type(agent)
            workspace_dir = self._effective_mount_path(agent)
            env.append(client.V1EnvVar(name="PORTAL_RUNTIME_TYPE", value=runtime_type))
            env.append(client.V1EnvVar(name="EFP_RUNTIME_TYPE", value=runtime_type))
            env.append(client.V1EnvVar(name="EFP_WORKSPACE_DIR", value=workspace_dir))
            env.append(client.V1EnvVar(name="EFP_SKILLS_DIR", value=self._skills_assets_dir()))
            env.append(client.V1EnvVar(name="EFP_CONFIG", value=self._efp_config_path(agent)))
            env.append(client.V1EnvVar(name="MOBILE_AUTO_STATE_DIR", value=self._mobile_state_dir(agent)))
            env.append(client.V1EnvVar(name="MOBILE_AUTO_ARTIFACTS_DIR", value=self._mobile_artifacts_dir(agent)))
            env.append(client.V1EnvVar(name="BROWSERSTACK_LOCAL_BINARY", value="/usr/local/bin/BrowserStackLocal"))
            if runtime_type == "native":
                env.append(client.V1EnvVar(name="EFP_RUNTIME_SESSION_ROOT", value=self._native_runtime_session_root(agent)))
            elif runtime_type == "opencode":
                configured_repos_dir = str(getattr(self.settings, "opencode_workspace_repos_dir", "") or "").strip()
                workspace_repos_dir = configured_repos_dir or f"{workspace_dir.rstrip('/')}/repos"
                checkout_timeout = self._positive_int_setting(
                    getattr(self.settings, "opencode_git_checkout_timeout_seconds", 120),
                    120,
                )
                task_completion_timeout = self._positive_int_setting(
                    getattr(self.settings, "opencode_task_completion_timeout_seconds", 3600),
                    3600,
                )
                chat_submit_timeout = self._positive_int_setting(
                    getattr(self.settings, "opencode_chat_submit_timeout_seconds", 900),
                    900,
                )
                env.append(client.V1EnvVar(name="EFP_REQUIRE_PORTAL_RUNTIME_CONTEXT", value="true"))
                env.append(client.V1EnvVar(name="HOME", value="/root"))
                env.append(client.V1EnvVar(name="OPENCODE_DATA_DIR", value=self._opencode_state_dir()))
                env.append(client.V1EnvVar(name="EFP_ADAPTER_STATE_DIR", value=self._opencode_adapter_state_dir()))
                env.append(client.V1EnvVar(name="OPENCODE_WORKSPACE", value=workspace_dir))
                env.append(client.V1EnvVar(name="EFP_WORKSPACE_REPOS_DIR", value=workspace_repos_dir))
                env.append(client.V1EnvVar(name="EFP_GIT_CHECKOUT_TIMEOUT_SECONDS", value=str(checkout_timeout)))
                env.append(client.V1EnvVar(name="EFP_TASK_COMPLETION_TIMEOUT_SECONDS", value=str(task_completion_timeout)))
                env.append(client.V1EnvVar(name="EFP_CHAT_SUBMIT_TIMEOUT_SECONDS", value=str(chat_submit_timeout)))
                # Chatbox-driven work can run as long as dispatched tasks; use
                # the task completion budget so long chats do not flip to
                # "incomplete" at the (shorter) submit timeout while OpenCode
                # keeps executing in the background.
                env.append(client.V1EnvVar(name="EFP_CHAT_COMPLETION_TIMEOUT_SECONDS", value=str(task_completion_timeout)))
                env.append(client.V1EnvVar(name="OPENCODE_CONFIG", value=self._opencode_config_path(agent)))
                env.append(client.V1EnvVar(name="EFP_OPENCODE_URL", value=f"http://127.0.0.1:{OPENCODE_INTERNAL_HTTP_PORT}"))
                env.append(
                    client.V1EnvVar(
                        name="EFP_OPENCODE_PERMISSION_MODE",
                        value=str(getattr(self.settings, "default_opencode_permission_mode", "workspace_full_access") or "workspace_full_access"),
                    )
                )
                env.append(
                    client.V1EnvVar(
                        name="EFP_OPENCODE_ALLOW_BASH_ALL",
                        value="true" if bool(getattr(self.settings, "default_opencode_allow_bash_all", True)) else "false",
                    )
                )
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

    def _skill_git_clone_shell_command(self, target_dir: str) -> str:
        return "\n".join(
            [
                "set -eu",
                "trap 'rm -f /tmp/git-askpass.sh' EXIT",
                f'mkdir -p "{target_dir}"',
                "rm -rf /tmp/git-clone-work",
                "mkdir -p /tmp/git-clone-work",
                "cd /tmp/git-clone-work",
                'SKILL_REPO_SUBDIR="${SKILL_REPO_SUBDIR:-}"',
                'if [ -n "${GIT_TOKEN:-}" ]; then',
                "  ASKPASS_SCRIPT=/tmp/git-askpass.sh",
                "  printf '%s\\n' '#!/bin/sh' 'case \"$1\" in' '  *Username*|*username*) echo \"x-access-token\" ;;' '  *) echo \"${GIT_TOKEN}\" ;;' 'esac' > \"${ASKPASS_SCRIPT}\"",
                '  chmod 700 "${ASKPASS_SCRIPT}"',
                '  export GIT_ASKPASS="${ASKPASS_SCRIPT}"',
                "  export GIT_TERMINAL_PROMPT=0",
                "fi",
                'git clone --depth 1 --branch "${GIT_BRANCH}" "${GIT_REPO_URL}" .',
                'SOURCE_DIR="/tmp/git-clone-work"',
                'if [ -n "${SKILL_REPO_SUBDIR}" ]; then',
                '  SOURCE_DIR="/tmp/git-clone-work/${SKILL_REPO_SUBDIR}"',
                "fi",
                'if [ ! -d "${SOURCE_DIR}" ]; then',
                '  echo "Skill repo source directory not found: ${SOURCE_DIR}" >&2',
                '  echo "Set DEFAULT_SKILL_REPO_SUBDIR to the directory containing skill packages, or leave it empty for repo root." >&2',
                "  find /tmp/git-clone-work -maxdepth 3 -type d -print >&2 || true",
                "  exit 35",
                "fi",
                f'find "{target_dir}" -mindepth 1 -maxdepth 1 -exec rm -rf -- {{}} +',
                f'cp -a "${{SOURCE_DIR}}"/. "{target_dir}"/',
                "has_flat_skill_md() {",
                f'  for f in "{target_dir}"/*.md; do',
                '    [ -f "$f" ] || continue',
                "    awk '",
                "      BEGIN { in_fm=0; started=0; found_close=0; seen_name=0; seen_description=0 }",
                "      {",
                "        if (!started && $0 ~ /^[[:space:]]*$/) { next }",
                "        if (!started && $0 == \"---\") { started=1; in_fm=1; next }",
                "        if (!started) { exit 1 }",
                "        if (in_fm && $0 == \"---\") { found_close=1; exit (seen_name && seen_description ? 0 : 1) }",
                "        if (in_fm && $0 ~ /^name:[[:space:]]*[^[:space:]]/) { seen_name=1 }",
                "        if (in_fm && $0 ~ /^description:[[:space:]]*[^[:space:]]/) { seen_description=1 }",
                "      }",
                "      END { if (!found_close) exit 1 }",
                "    ' \"$f\" && return 0",
                "  done",
                "  return 1",
                "}",
                f'if ! find "{target_dir}" -mindepth 2 -maxdepth 2 \\( -name SKILL.md -o -name skill.md \\) -type f | grep -q . \\',
                "   && ! has_flat_skill_md; then",
                f'  echo "No skill entries found after cloning skills repo into {target_dir}" >&2',
                '  echo "Expected either <skill-name>/SKILL.md, <skill-name>/skill.md, or top-level *.md with name/description frontmatter." >&2',
                '  echo "Actual files:" >&2',
                f'  find "{target_dir}" -maxdepth 4 -type f -print | sort >&2 || true',
                "  exit 36",
                "fi",
                "rm -f /tmp/git-askpass.sh",
            ]
        )

    def _agent_settings_git_clone_shell_command(self, workspace_dir: str) -> str:
        return "\n".join(
            [
                "set -eu",
                "trap 'rm -f /tmp/git-askpass.sh' EXIT",
                f'mkdir -p "{workspace_dir}"',
                "rm -rf /tmp/agent-settings-git-clone-work",
                "mkdir -p /tmp/agent-settings-git-clone-work",
                "cd /tmp/agent-settings-git-clone-work",
                'AGENT_SETTINGS_REPO_SUBDIR="${AGENT_SETTINGS_REPO_SUBDIR:-}"',
                'if [ -n "${GIT_TOKEN:-}" ]; then',
                "  ASKPASS_SCRIPT=/tmp/git-askpass.sh",
                "  printf '%s\\n' '#!/bin/sh' 'case \"$1\" in' '  *Username*|*username*) echo \"x-access-token\" ;;' '  *) echo \"${GIT_TOKEN}\" ;;' 'esac' > \"${ASKPASS_SCRIPT}\"",
                '  chmod 700 "${ASKPASS_SCRIPT}"',
                '  export GIT_ASKPASS="${ASKPASS_SCRIPT}"',
                "  export GIT_TERMINAL_PROMPT=0",
                "fi",
                'git clone --depth 1 --branch "${GIT_BRANCH}" "${GIT_REPO_URL}" .',
                'SOURCE_DIR="/tmp/agent-settings-git-clone-work"',
                'if [ -n "${AGENT_SETTINGS_REPO_SUBDIR}" ]; then',
                '  SOURCE_DIR="/tmp/agent-settings-git-clone-work/${AGENT_SETTINGS_REPO_SUBDIR}"',
                "fi",
                'if [ ! -d "${SOURCE_DIR}" ]; then',
                '  echo "Agent settings source directory not found: ${SOURCE_DIR}" >&2',
                '  echo "Set DEFAULT_AGENT_SETTINGS_REPO_SUBDIR to the directory containing AGENTS.md and instructions/, or leave it empty for repo root." >&2',
                "  find /tmp/agent-settings-git-clone-work -maxdepth 3 -type d -print >&2 || true",
                "  exit 45",
                "fi",
                'if [ ! -f "${SOURCE_DIR}/AGENTS.md" ]; then',
                '  echo "Agent settings repo must contain AGENTS.md" >&2',
                "  find \"${SOURCE_DIR}\" -maxdepth 2 -type f -print | sort >&2 || true",
                "  exit 46",
                "fi",
                'if [ ! -d "${SOURCE_DIR}/instructions" ]; then',
                '  echo "Agent settings repo must contain instructions/ directory" >&2',
                "  find \"${SOURCE_DIR}\" -maxdepth 2 -type f -print | sort >&2 || true",
                "  exit 47",
                "fi",
                f'cp -a "${{SOURCE_DIR}}/AGENTS.md" "{workspace_dir}/AGENTS.md"',
                f'rm -rf "{workspace_dir}/instructions"',
                f'mkdir -p "{workspace_dir}/instructions"',
                f'cp -a "${{SOURCE_DIR}}/instructions"/. "{workspace_dir}/instructions"/',
                f'mkdir -p "{workspace_dir}/.efp/instructions"',
                f'find "{workspace_dir}/.efp/instructions" -mindepth 1 -maxdepth 1 -exec rm -rf -- {{}} +',
                f'cp -a "${{SOURCE_DIR}}/instructions"/. "{workspace_dir}/.efp/instructions"/',
                "rm -f /tmp/git-askpass.sh",
            ]
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
