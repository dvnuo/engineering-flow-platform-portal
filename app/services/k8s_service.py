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

    def create_robot_runtime(self, robot) -> RuntimeStatus:
        if not self.enabled:
            return RuntimeStatus(status="running")

        try:
            self._ensure_pvc(robot)
            self._ensure_deployment(robot)
            self._ensure_service(robot)
            return RuntimeStatus(status="running")
        except Exception as exc:
            return RuntimeStatus(status="failed", message=str(exc))

    def start_robot(self, robot) -> RuntimeStatus:
        if not self.enabled:
            return RuntimeStatus(status="running")
        try:
            self.apps_api.patch_namespaced_deployment_scale(
                name=robot.deployment_name,
                namespace=robot.namespace,
                body={"spec": {"replicas": 1}},
            )
            return RuntimeStatus(status="running")
        except Exception as exc:
            return RuntimeStatus(status="failed", message=str(exc))

    def stop_robot(self, robot) -> RuntimeStatus:
        if not self.enabled:
            return RuntimeStatus(status="stopped")
        try:
            self.apps_api.patch_namespaced_deployment_scale(
                name=robot.deployment_name,
                namespace=robot.namespace,
                body={"spec": {"replicas": 0}},
            )
            return RuntimeStatus(status="stopped")
        except Exception as exc:
            return RuntimeStatus(status="failed", message=str(exc))

    def delete_robot_runtime(self, robot, destroy_data: bool = False) -> RuntimeStatus:
        if not self.enabled:
            return RuntimeStatus(status="deleted")

        try:
            self.apps_api.delete_namespaced_deployment(name=robot.deployment_name, namespace=robot.namespace)
            self.core_api.delete_namespaced_service(name=robot.service_name, namespace=robot.namespace)
            if destroy_data:
                self.core_api.delete_namespaced_persistent_volume_claim(name=robot.pvc_name, namespace=robot.namespace)
            return RuntimeStatus(status="deleted")
        except Exception as exc:
            return RuntimeStatus(status="failed", message=str(exc))

    def get_robot_runtime_status(self, robot) -> RuntimeStatus:
        if not self.enabled:
            return RuntimeStatus(
                status=robot.status,
                message=robot.last_error,
                cpu_usage="N/A (metrics disabled)",
                memory_usage="N/A (metrics disabled)",
            )

        try:
            deploy = self.apps_api.read_namespaced_deployment_status(name=robot.deployment_name, namespace=robot.namespace)
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

    def _ensure_pvc(self, robot) -> None:
        from kubernetes import client

        body = client.V1PersistentVolumeClaim(
            metadata=client.V1ObjectMeta(
                name=robot.pvc_name,
                namespace=robot.namespace,
                labels={"app": "robot", "robot-id": robot.id, "owner-id": str(robot.owner_user_id)},
            ),
            spec=client.V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteOnce"],
                storage_class_name=self.settings.k8s_storage_class,
                resources=client.V1VolumeResourceRequirements(requests={"storage": f"{robot.disk_size_gi}Gi"}),
            ),
        )
        try:
            self.core_api.create_namespaced_persistent_volume_claim(namespace=robot.namespace, body=body)
        except Exception as exc:
            if not self._is_already_exists(exc):
                raise

    def _ensure_deployment(self, robot) -> None:
        from kubernetes import client

        labels = {"app": "robot", "robot-id": robot.id, "owner-id": str(robot.owner_user_id)}
        body = client.V1Deployment(
            metadata=client.V1ObjectMeta(name=robot.deployment_name, namespace=robot.namespace, labels=labels),
            spec=client.V1DeploymentSpec(
                replicas=1,
                selector=client.V1LabelSelector(match_labels={"app": "robot", "robot-id": robot.id}),
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(labels=labels),
                    spec=client.V1PodSpec(
                        containers=[
                            client.V1Container(
                                name="robot",
                                image=robot.image,
                                ports=[client.V1ContainerPort(container_port=80)],
                                volume_mounts=[client.V1VolumeMount(name="robot-data", mount_path=robot.mount_path)],
                            )
                        ],
                        volumes=[
                            client.V1Volume(
                                name="robot-data",
                                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(claim_name=robot.pvc_name),
                            )
                        ],
                    ),
                ),
            ),
        )
        try:
            self.apps_api.create_namespaced_deployment(namespace=robot.namespace, body=body)
        except Exception as exc:
            if not self._is_already_exists(exc):
                raise

    def _ensure_service(self, robot) -> None:
        from kubernetes import client

        body = client.V1Service(
            metadata=client.V1ObjectMeta(name=robot.service_name, namespace=robot.namespace),
            spec=client.V1ServiceSpec(
                selector={"app": "robot", "robot-id": robot.id},
                ports=[client.V1ServicePort(port=80, target_port=80)],
                type="NodePort",
            ),
        )
        try:
            self.core_api.create_namespaced_service(namespace=robot.namespace, body=body)
        except Exception as exc:
            if not self._is_already_exists(exc):
                raise
