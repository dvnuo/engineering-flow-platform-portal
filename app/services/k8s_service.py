from dataclasses import dataclass
from typing import Optional

from app.config import get_settings


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
            return RuntimeStatus(status="failed", message=str(exc))

    def update_agent_runtime(self, agent) -> RuntimeStatus:
        """Update agent runtime (deployment) with new config."""
        if not self.enabled:
            return RuntimeStatus(status="running")
        
        try:
            self._patch_deployment(agent)
            return RuntimeStatus(status="running")
        except Exception as exc:
            return RuntimeStatus(status="failed", message=str(exc))

    def _patch_deployment(self, agent) -> None:
        """Patch existing deployment with new config."""
        from kubernetes import client
        
        # Build env vars for git clone
        env = []
        if agent.repo_url:
            env.extend([
                client.V1EnvVar(name="GIT_REPO_URL", value=agent.repo_url),
                client.V1EnvVar(name="GIT_BRANCH", value=agent.branch or "master"),
            ])
        
        # Build the init container spec
        init_containers = []
        code_sub_path = f"efp-agents/{agent.id}/code"
        
        if agent.repo_url:
            init_containers.append(
                client.V1Container(
                    name="git-clone",
                    image=getattr(agent, 'git_image', None) or self.settings.default_agent_git_image or "alpine/git:latest",
                    command=["sh", "-c"],
                    args=[
                        "mkdir -p /app && "
                        "cd /app && rm -rf .[!.]* * && "
                        "git clone --depth 1 --branch ${GIT_BRANCH} ${GIT_REPO_URL} ."
                    ],
                    env=env,
                    volume_mounts=[client.V1VolumeMount(name="agent-data", mount_path="/app", sub_path=code_sub_path)],
                )
            )
        
        # Build volume mounts
        volume_mounts = []
        if agent.repo_url:
            volume_mounts.append(
                client.V1VolumeMount(name="agent-data", mount_path="/app", sub_path=code_sub_path)
            )
        volume_mounts.append(
            client.V1VolumeMount(name="agent-data", mount_path=agent.mount_path, sub_path=f"efp-agents/{agent.id}/data")
        )
        
        # Patch the deployment
        patch = {
            "spec": {
                "template": {
                    "spec": {
                        "initContainers": init_containers,
                        "containers": [{
                            "name": "agent",
                            "image": agent.image,
                            "ports": [{"containerPort": 8000}],
                            "volumeMounts": volume_mounts,
                        }],
                    }
                }
            }
        }
        
        try:
            self.apps_api.patch_namespaced_deployment(
                name=agent.deployment_name,
                namespace=agent.namespace,
                body=patch,
            )
        except Exception as exc:
            if not self._is_already_exists(exc):
                raise

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
            return RuntimeStatus(status="failed", message=str(exc))

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
            return RuntimeStatus(status="failed", message=str(exc))

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
            return RuntimeStatus(status="failed", message=str(exc))

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
            return RuntimeStatus(status="failed", message=str(exc), cpu_usage="N/A", memory_usage="N/A")

    def _is_already_exists(self, exc: Exception) -> bool:
        try:
            from kubernetes.client.exceptions import ApiException

            return isinstance(exc, ApiException) and exc.status == 409
        except Exception:
            return False

    def _ensure_pvc(self, agent) -> None:
        # Using individual PVC per agent
        from kubernetes import client

        body = client.V1PersistentVolumeClaim(
            metadata=client.V1ObjectMeta(
                name=agent.pvc_name,
                namespace=agent.namespace,
                labels={"app": "agent", "agent-id": agent.id, "managed-by": "portal"},
            ),
            spec=client.V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteOnce"],
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

        labels = {"app": "agent", "agent-id": agent.id, "owner-id": str(agent.owner_user_id)}
        
        # Build init container if repo_url is provided
        init_containers = []
        volume_mounts = []
        
        if agent.repo_url:
            branch = agent.branch or "main"
            git_image = getattr(agent, 'git_image', None) or self.settings.default_agent_git_image or "alpine/git:latest"
            code_sub_path = f"efp-agents/{agent.id}/code"
            # Clone git repo to /app (will be mounted)
            # Add environment variables for git clone
            env = [
                client.V1EnvVar(name="GIT_REPO_URL", value=agent.repo_url),
                client.V1EnvVar(name="GIT_BRANCH", value=agent.branch or "master"),
            ]
            
            init_containers.append(
                client.V1Container(
                    name="git-clone",
                    image=git_image,
                    command=["sh", "-c"],
                    args=[
                        "mkdir -p /app && "
                        "cd /app && rm -rf .[!.]* * && "
                        "git clone --depth 1 --branch ${GIT_BRANCH} ${GIT_REPO_URL} ."
                    ],
                    env=env,
                    volume_mounts=[client.V1VolumeMount(name="agent-data", mount_path="/app", sub_path=code_sub_path)],
                )
            )
            # Mount code to /app in main container
            volume_mounts.append(
                client.V1VolumeMount(name="agent-data", mount_path="/app", sub_path=code_sub_path)
            )
        
        # Always add the data mount
        data_sub_path = f"efp-agents/{agent.id}/data"
        volume_mounts.append(
            client.V1VolumeMount(name="agent-data", mount_path=agent.mount_path, sub_path=data_sub_path)
        )
        
        body = client.V1Deployment(
            metadata=client.V1ObjectMeta(name=agent.deployment_name, namespace=agent.namespace, labels=labels),
            spec=client.V1DeploymentSpec(
                replicas=1,
                selector=client.V1LabelSelector(match_labels={"app": "agent", "agent-id": agent.id}),
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(labels=labels),
                    spec=client.V1PodSpec(
                        init_containers=init_containers,
                        containers=[
                            client.V1Container(
                                name="agent",
                                image=agent.image,
                                ports=[client.V1ContainerPort(container_port=8000)],
                                volume_mounts=volume_mounts,
                            )
                        ],
                        volumes=[
                            client.V1Volume(
                                name="agent-data",
                                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(claim_name=agent.pvc_name),
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

    def _ensure_service(self, agent) -> None:
        from kubernetes import client

        body = client.V1Service(
            metadata=client.V1ObjectMeta(name=agent.service_name, namespace=agent.namespace),
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
