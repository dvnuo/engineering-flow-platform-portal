from collections.abc import Iterable
import logging

import httpx
from typing import Optional

from kubernetes import client
from app.config import get_settings

logger = logging.getLogger(__name__)


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
                except Exception as e:
                    logger.warning(f"Failed to load K8s config: {e}")
        return self._core_api
    
    @property
    def node_ip(self):
        if self._node_ip is None:
            import os
            self._node_ip = os.environ.get('NODE_IP', '192.168.8.237')
        return self._node_ip

    def _get_service_url(self, robot) -> Optional[str]:
        """Get the service URL, trying NodePort first then internal DNS."""
        if not self.core_api:
            return f"http://{robot.service_name}.{robot.namespace}.svc.cluster.local"
        
        try:
            svc = self.core_api.read_namespaced_service(
                name=robot.service_name,
                namespace=robot.namespace
            )
            
            # Try NodePort first
            if svc.spec.type == "NodePort":
                for port in svc.spec.ports:
                    if port.node_port:
                        url = f"http://{self.node_ip}:{port.node_port}"
                        # Quick health check
                        try:
                            import socket
                            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            sock.settimeout(2)
                            result = sock.connect_ex((self.node_ip, port.node_port))
                            sock.close()
                            if result == 0:
                                logger.info(f"NodePort connection successful: {url}")
                                return url
                            else:
                                logger.warning(f"NodePort not reachable: {url}")
                        except Exception as e:
                            logger.warning(f"Health check failed for {url}: {e}")
            
            # Fallback to internal DNS
            logger.info(f"Falling back to internal DNS for {robot.service_name}")
            return f"http://{robot.service_name}.{robot.namespace}.svc.cluster.local"
            
        except Exception as e:
            logger.warning(f"Failed to get service info: {e}")
            return f"http://{robot.service_name}.{robot.namespace}.svc.cluster.local"

    def build_robot_base_url(self, robot) -> str:
        url = self._get_service_url(robot)
        return url if url else f"http://{robot.service_name}.{robot.namespace}.svc.cluster.local"

    async def forward(
        self,
        robot,
        method: str,
        subpath: str,
        query_items: Iterable[tuple[str, str]],
        body: Optional[bytes],
        headers: dict[str, str],
    ) -> tuple[int, bytes, str]:
        base = self.build_robot_base_url(robot).rstrip("/")
        path = f"/{subpath}" if subpath else "/"
        url = f"{base}{path}"

        outbound_headers = {}
        if headers.get("content-type"):
            outbound_headers["content-type"] = headers["content-type"]

        logger.info(f"Proxying {method} request to {url}")
        
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.request(
                    method=method,
                    url=url,
                    params=list(query_items),
                    content=body,
                    headers=outbound_headers,
                )
            content_type = resp.headers.get("content-type", "application/octet-stream")
            logger.info(f"Proxy response: {resp.status_code}")
            return resp.status_code, resp.content, content_type
        except httpx.ConnectError as e:
            logger.error(f"Connection error to {url}: {e}")
            # Try fallback URL
            fallback_url = f"http://{robot.service_name}.{robot.namespace}.svc.cluster.local}{path}"
            logger.info(f"Trying fallback: {fallback_url}")
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.request(
                        method=method,
                        url=fallback_url,
                        params=list(query_items),
                        content=body,
                        headers=outbound_headers,
                    )
                content_type = resp.headers.get("content-type", "application/octet-stream")
                return resp.status_code, resp.content, content_type
            except Exception as fallback_error:
                raise Exception(f"Both NodePort and internal DNS failed. Last error: {fallback_error}") from fallback_error
        except Exception as e:
            raise Exception(f"Proxy error: {e}") from e
