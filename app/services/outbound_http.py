"""Outbound HTTP settings for the few external services the portal calls itself.

The portal runs inside the cluster, where reaching github.com usually means
going through an egress proxy while the company IdP is reachable directly.
httpx honours HTTP(S)_PROXY / NO_PROXY from the environment by default, which
is fine when the pod environment is right and impossible to diagnose when it
is not. Each destination therefore gets an explicit setting:

* empty        -> use the environment proxy variables (httpx default)
* ``direct``   -> ignore the environment and connect directly
* a proxy URL  -> use exactly that proxy (``http://proxy.corp:3128``)
"""

from __future__ import annotations

import os

from app.config import get_settings

settings = get_settings()

DIRECT_VALUES = frozenset({"direct", "none", "off"})


def outbound_client_kwargs(proxy_setting: str | None, *, timeout: float, **extra) -> dict:
    """Keyword arguments for ``httpx.AsyncClient`` honouring a proxy setting."""
    kwargs = {"timeout": timeout, **extra}
    value = (proxy_setting or "").strip()
    if value.lower() in DIRECT_VALUES:
        kwargs["trust_env"] = False
    elif value:
        kwargs["proxy"] = value
    return kwargs


def describe_egress(proxy_setting: str | None) -> str:
    """Human-readable summary of how a request leaves the pod, for error messages."""
    value = (proxy_setting or "").strip()
    if value.lower() in DIRECT_VALUES:
        return "direct connection (environment proxy settings ignored)"
    if value:
        return f"configured proxy {value}"
    env_proxy = ""
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        env_proxy = os.environ.get(name, "").strip()
        if env_proxy:
            return f"environment proxy {name}={env_proxy}"
    return "direct connection (no proxy configured)"


def github_client_kwargs(**extra) -> dict:
    return outbound_client_kwargs(settings.github_proxy_url, timeout=float(settings.github_http_timeout_seconds), **extra)


def describe_github_egress() -> str:
    return describe_egress(settings.github_proxy_url)


def sso_client_kwargs(**extra) -> dict:
    return outbound_client_kwargs(settings.sso_proxy_url, timeout=15.0, **extra)


def describe_sso_egress() -> str:
    return describe_egress(settings.sso_proxy_url)
