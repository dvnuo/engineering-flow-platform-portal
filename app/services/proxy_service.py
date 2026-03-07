from collections.abc import Iterable

import httpx
from typing import Optional

# Import K8s client for service lookup
from kubernetes import client
from app.config import get_settings


class ProxyService:
    def __init__(self):
        self._core_api = None
        self._node_ip = None
    
    @property
    def core_api(self):
        if self._core_api is None:
            settings = get_settings()
            if settings.k8s_enabled:
                try:
                    from kubernetes import config
                    if settings.k8s_incluster:
                        config.load_incluster_config()
                    else:
                        config.load_kube_config(config_file=settings.k8s_kubeconfig)
                    self._core_api = client.CoreV1Api()
                except Exception:
                    pass
        return self._core_api
    
    @property
    def node_ip(self):
        if self._node_ip is None:
            # Use the node IP where portal is running (hostNetwork)
            import os
            self._node_ip = os.environ.get('NODE_IP', '192.168.8.237')
        return self._node_ip

    def build_robot_base_url(self, robot) -> str:
        # Try to get NodePort from K8s service
        if self.core_api:
            try:
                svc = self.core_api.read_namespaced_service(
                    name=robot.service_name,
                    namespace=robot.namespace
                )
                if svc.spec.type == NodePort:
                    # Find the NodePort
                    for port in svc.spec.ports:
                        if port.node_port:
                            return fhttp://{self.node_ip}:{port.node_port}
            except Exception:
                pass
        
        # Fallback to internal DNS (for ClusterIP)
        return fhttp://{robot.service_name}.{robot.namespace}.svc.cluster.local

    async def forward(
        self,
        robot,
        method: str,
        subpath: str,
        query_items: Iterable[tuple[str, str]],
        body: Optional[bytes],
        headers: dict[str, str],
    ) -> tuple[int, bytes, str]:
        base = self.build_robot_base_url(robot).rstrip(/)
        path = f/{subpath} if subpath else /
        url = f{base}{path}

        outbound_headers = {}
        if headers.get(content-type):
            outbound_headers[content-type] = headers[content-type]

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                method=method,
                url=url,
                params=list(query_items),
                content=body,
                headers=outbound_headers,
            )
        content_type = resp.headers.get(content-type, application/octet-stream)
        return resp.status_code, resp.content, content_type
