from collections.abc import Iterable
import subprocess
import httpx
from typing import Optional
import logging

try:
    from kubernetes import client as k8s_client
    from kubernetes import config as k8s_config
except Exception:
    k8s_client = None
    k8s_config = None

from app.config import get_settings
from app.redaction import sanitize_exception_message

logger = logging.getLogger(__name__)


def sanitize_header_value(value: object, max_length: int = 255) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    sanitized = "".join(
        ch for ch in text if (32 <= ord(ch) <= 126) or (160 <= ord(ch) <= 255)
    ).strip()
    if not sanitized:
        return ""
    return sanitized[:max_length]


def _sanitize_header_value(value: object, max_length: int = 255) -> str:
    return sanitize_header_value(value, max_length=max_length)


def build_portal_identity_fields(user) -> dict[str, str]:
    identity = {}

    user_id = sanitize_header_value(getattr(user, "id", None))
    if user_id:
        identity["user_id"] = user_id

    nickname = sanitize_header_value(getattr(user, "nickname", None))
    username = sanitize_header_value(getattr(user, "username", None))
    user_name = nickname or username
    if user_name:
        identity["user_name"] = user_name

    return identity


def build_portal_identity_headers(user) -> dict[str, str]:
    headers = {"X-Portal-Author-Source": "portal"}
    identity = build_portal_identity_fields(user)
    if identity.get("user_id"):
        headers["X-Portal-User-Id"] = identity["user_id"]
    if identity.get("user_name"):
        headers["X-Portal-User-Name"] = identity["user_name"]

    return headers



def build_portal_execution_headers(user) -> dict[str, str]:
    return build_portal_identity_headers(user)


def build_portal_agent_identity_headers(user, agent) -> dict[str, str]:
    headers = build_portal_identity_headers(user)
    sanitized_name = sanitize_header_value(getattr(agent, "name", None))
    if sanitized_name:
        headers["X-Portal-Agent-Name"] = sanitized_name
    return headers


def build_runtime_trace_headers(trace_context: dict[str, str] | None) -> dict[str, str]:
    trace_context = trace_context or {}
    mapping = {
        "trace_id": "X-Trace-Id",
        "span_id": "X-Span-Id",
        "parent_span_id": "X-Parent-Span-Id",
        "portal_task_id": "X-Portal-Task-Id",
        "portal_dispatch_id": "X-Portal-Dispatch-Id",
    }
    headers: dict[str, str] = {}
    for source_key, header_name in mapping.items():
        sanitized_value = sanitize_header_value(trace_context.get(source_key))
        if sanitized_value and sanitized_value != "-":
            headers[header_name] = sanitized_value
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
                if not k8s_client or not k8s_config:
                    logger.debug("Kubernetes dependency unavailable; skipping CoreV1Api initialization")
                    return None
                try:
                    if settings.k8s_incluster:
                        k8s_config.load_incluster_config()
                    else:
                        k8s_config.load_kube_config(config_file=settings.k8s_kubeconfig)
                    self._core_api = k8s_client.CoreV1Api()
                except Exception as exc:
                    logger.debug(
                        "Unable to initialize Kubernetes CoreV1Api; continuing without k8s service lookup: %s",
                        sanitize_exception_message(exc),
                    )
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
                            base_url = f"http://{self.node_ip}:{port.node_port}"
                            logger.debug(
                                "Resolved runtime base URL via NodePort agent_id=%s service_name=%s namespace=%s service_type=%s base_url=%s",
                                getattr(agent, "id", "-"),
                                getattr(agent, "service_name", "-"),
                                getattr(agent, "namespace", "-"),
                                svc.spec.type,
                                base_url,
                            )
                            return base_url
                # For ClusterIP, try internal DNS
                elif svc.spec.type == "ClusterIP":
                    base_url = f"http://{agent.service_name}.{agent.namespace}.svc.cluster.local:8000"
                    logger.debug(
                        "Resolved runtime base URL via ClusterIP agent_id=%s service_name=%s namespace=%s service_type=%s base_url=%s",
                        getattr(agent, "id", "-"),
                        getattr(agent, "service_name", "-"),
                        getattr(agent, "namespace", "-"),
                        svc.spec.type,
                        base_url,
                    )
                    return base_url
            except ValueError:
                # Re-raise ValueError so caller can handle it with actionable message
                raise
            except Exception as exc:
                logger.debug(
                    "Failed reading k8s service for base URL resolution agent_id=%s service_name=%s namespace=%s exception_class=%s message=%s",
                    getattr(agent, "id", "-"),
                    getattr(agent, "service_name", "-"),
                    getattr(agent, "namespace", "-"),
                    exc.__class__.__name__,
                    sanitize_exception_message(exc),
                )
        # Fallback to internal DNS
        fallback_url = f"http://{agent.service_name}.{agent.namespace}.svc.cluster.local:8000"
        logger.debug(
            "Resolved runtime base URL via fallback DNS agent_id=%s service_name=%s namespace=%s service_type=%s base_url=%s",
            getattr(agent, "id", "-"),
            getattr(agent, "service_name", "-"),
            getattr(agent, "namespace", "-"),
            "fallback",
            fallback_url,
        )
        return fallback_url

    async def forward(
        self,
        agent,
        method: str,
        subpath: str,
        query_items: Iterable[tuple[str, str]],
        body: Optional[bytes],
        headers: dict[str, str],
        extra_headers: Optional[dict[str, str]] = None,
        return_response_headers: bool = False,
    ) -> tuple[int, bytes, str] | tuple[int, bytes, str, dict[str, str]]:
        try:
            base = self.build_agent_base_url(agent).rstrip("/")
        except ValueError as e:
            # Return 502 error when EFP URL cannot be determined
            error_msg = str(e).encode("utf-8")
            if return_response_headers:
                return 502, error_msg, "text/plain", {}
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
        if return_response_headers:
            selected_headers = self._select_passthrough_response_headers(resp.headers)
            return resp.status_code, resp.content, content_type, selected_headers
        return resp.status_code, resp.content, content_type

    @staticmethod
    def _select_passthrough_response_headers(headers) -> dict[str, str]:
        content_disposition = headers.get("content-disposition")
        if not content_disposition:
            return {}
        if "\r" in content_disposition or "\n" in content_disposition:
            return {}
        return {"Content-Disposition": content_disposition}

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
            "x-portal-agent-name": "X-Portal-Agent-Name",
            "x-trace-id": "X-Trace-Id",
            "x-span-id": "X-Span-Id",
            "x-parent-span-id": "X-Parent-Span-Id",
            "x-portal-task-id": "X-Portal-Task-Id",
            "x-portal-dispatch-id": "X-Portal-Dispatch-Id",
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
