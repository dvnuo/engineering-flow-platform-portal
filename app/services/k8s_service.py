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
            if destroy_data and False:  # Never delete shared PVC
                self.core_api.delete_namespaced_persistent_volume_claim(name="efp-agents-pvc", namespace=agent.namespace)
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
        # Using shared PVC for all agents - fixed config, no agent-specific fields
        from kubernetes import client

        body = client.V1PersistentVolumeClaim(
            metadata=client.V1ObjectMeta(
                name="efp-agents-pvc",
                namespace=agent.namespace,
                labels={"app": "efp-agents", "managed-by": "portal"},
            ),
            spec=client.V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteOnce"],
                storage_class_name=self.settings.k8s_storage_class,
                resources=client.V1VolumeResourceRequirements(requests={"storage": self.settings.k8s_shared_pvc_size}),  # Fixed size for shared PVC
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
        body = client.V1Deployment(
            metadata=client.V1ObjectMeta(name=agent.deployment_name, namespace=agent.namespace, labels=labels),
            spec=client.V1DeploymentSpec(
                replicas=1,
                selector=client.V1LabelSelector(match_labels={"app": "agent", "agent-id": agent.id}),
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(labels=labels),
                    spec=client.V1PodSpec(
                        containers=[
                            client.V1Container(
                                name="agent",
                                image=agent.image,
                                ports=[client.V1ContainerPort(container_port=8000)],
                                volume_mounts=[client.V1VolumeMount(name="agent-data", mount_path=agent.mount_path, sub_path="efp-agents/" + agent.id)],
                            )
                        ],
                        volumes=[
                            client.V1Volume(
                                name="agent-data",
                                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(claim_name="efp-agents-pvc"),
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
                type="NodePort",
            ),
        )
        try:
            self.core_api.create_namespaced_service(namespace=agent.namespace, body=body)
        except Exception as exc:
            if not self._is_already_exists(exc):
                raise
