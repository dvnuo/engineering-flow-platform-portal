from collections.abc import Iterable
import subprocess
import httpx
from typing import Optional

# Import K8s client for service lookup
from kubernetes import client
from app.config import get_settings


def _sanitize_header_value(value: object, max_length: int = 255) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    sanitized = "".join(
        ch for ch in text if (32 <= ord(ch) <= 126) or (160 <= ord(ch) <= 255)
    ).strip()
    if not sanitized:
        return ""
    return sanitized[:max_length]


def build_portal_identity_headers(user) -> dict[str, str]:
    headers = {"X-Portal-Author-Source": "portal"}

    user_id = _sanitize_header_value(getattr(user, "id", None))
    if user_id:
        headers["X-Portal-User-Id"] = user_id

    display_name = getattr(user, "nickname", None) or getattr(user, "username", None)
    user_name = _sanitize_header_value(display_name)
    if user_name:
        headers["X-Portal-User-Name"] = user_name

    return headers


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
            # 1. Try environment variable override first
            import os
            env_ip = os.environ.get('K8S_NODE_IP') or os.environ.get('NODE_IP')
            if env_ip:
                self._node_ip = env_ip
            else:
                # 2. Auto-detect via hostname -I (with timeout to prevent hangs)
                try:
                    result = subprocess.run(
                        ['hostname', '-I'], capture_output=True, text=True, timeout=30
                    )
                    if result.returncode == 0:
                        # Filter for IPv4 addresses only (exclude IPv6, link-local, etc.)
                        ips = result.stdout.strip().split()
                        for ip in ips:
                            # IPv4 check: contains '.' and not loopback
                            if '.' in ip and not ip.startswith('127.'):
                                self._node_ip = ip
                                break
                        # If no suitable IPv4 found, require env var instead of silent wrong fallback
                except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
                    # hostname command failed or unavailable - will require env var
                    pass
                
                # 3. Last resort: raise error instead of silent wrong fallback
                if not self._node_ip:
                    raise ValueError(
                        "Cannot determine node IP for K8s proxy. "
                        "Set K8S_NODE_IP environment variable."
                    )
        return self._node_ip

    def build_agent_base_url(self, agent) -> str:
        # Try to get NodePort from K8s service
        if self.core_api:
            try:
                svc = self.core_api.read_namespaced_service(
                    name=agent.service_name,
                    namespace=agent.namespace
                ) # Check if it's NodePort type
                if svc.spec.type == "NodePort":
                    # Find the NodePort
                    for port in svc.spec.ports:
                        if port.node_port:
                            return f"http://{self.node_ip}:{port.node_port}"
                # For ClusterIP, try internal DNS
                elif svc.spec.type == "ClusterIP":
                    return f"http://{agent.service_name}.{agent.namespace}.svc.cluster.local:8000"
            except ValueError:
                # Re-raise ValueError so caller can handle it with actionable message
                raise
            except Exception:
                pass
        # Fallback to internal DNS
        return f"http://{agent.service_name}.{agent.namespace}.svc.cluster.local:8000"

    async def forward(
        self,
        agent,
        method: str,
        subpath: str,
        query_items: Iterable[tuple[str, str]],
        body: Optional[bytes],
        headers: dict[str, str],
        extra_headers: Optional[dict[str, str]] = None,
    ) -> tuple[int, bytes, str]:
        try:
            base = self.build_agent_base_url(agent).rstrip("/")
        except ValueError as e:
            # Return 502 error when EFP URL cannot be determined
            error_msg = str(e).encode('utf-8')
            return 502, error_msg, "text/plain"
        
        path = f"/{subpath}" if subpath else "/"
        url = f"{base}{path}"

        outbound_headers = self._build_outbound_headers(headers, extra_headers)

        async with httpx.AsyncClient(timeout=None) as client:
            resp = await client.request(
                method=method,
                url=url,
                params=list(query_items),
                content=body,
                headers=outbound_headers,
            )
        content_type = resp.headers.get("content-type", "application/octet-stream")
        return resp.status_code, resp.content, content_type

    async def forward_multipart(
        self,
        agent,
        method: str,
        subpath: str,
        query_items: Iterable[tuple[str, str]],
        files,
        data=None,
        headers: Optional[dict[str, str]] = None,
        extra_headers: Optional[dict[str, str]] = None,
    ) -> tuple[int, bytes, str]:
        try:
            base = self.build_agent_base_url(agent).rstrip("/")
        except ValueError as e:
            error_msg = str(e).encode("utf-8")
            return 502, error_msg, "text/plain"

        path = f"/{subpath}" if subpath else "/"
        url = f"{base}{path}"
        outbound_headers = self._build_outbound_headers(headers or {}, extra_headers)

        async with httpx.AsyncClient(timeout=None) as client:
            resp = await client.request(
                method=method,
                url=url,
                params=list(query_items),
                files=files,
                data=data,
                headers=outbound_headers,
            )

        content_type = resp.headers.get("content-type", "application/octet-stream")
        return resp.status_code, resp.content, content_type

    @staticmethod
    def _build_outbound_headers(
        headers: dict[str, str], extra_headers: Optional[dict[str, str]]
    ) -> dict[str, str]:
        allowed_extra_headers = {
            "x-portal-author-source": "X-Portal-Author-Source",
            "x-portal-user-id": "X-Portal-User-Id",
            "x-portal-user-name": "X-Portal-User-Name",
        }
        forbidden_extra_headers = {
            "content-type",
            "host",
            "content-length",
            "transfer-encoding",
            "connection",
        }

        outbound_headers = {}
        if headers.get("content-type"):
            outbound_headers["content-type"] = headers["content-type"]
        if extra_headers:
            for key, value in extra_headers.items():
                if not key:
                    continue
                key_lower = key.lower()
                if key_lower in forbidden_extra_headers:
                    continue
                canonical_name = allowed_extra_headers.get(key_lower)
                if not canonical_name:
                    continue
                sanitized_value = _sanitize_header_value(value)
                if not sanitized_value:
                    continue
                outbound_headers[canonical_name] = sanitized_value
        return outbound_headers
