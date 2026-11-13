"""How the Portal itself reaches a member's connected systems.

Two features talk to GitHub, Jira, Confluence and the rest with a member's own
connector settings: the Test button on a connector, and the starter-card
pickers that list a member's recent work (app/services/suggestions). Both must
leave the Portal the way the member's assistant would -- through the proxy the
Proxy connector assigns that connector, or directly -- and both must verify TLS
against the same trust (system store, certifi, PORTAL_CA_BUNDLE). The rules for
that live here once, so a picker can never reach a system along a path the Test
button did not prove.
"""
from __future__ import annotations

import base64
from typing import Any

from app.services.proxy_plan import KIND_NONE, KIND_PROXY, ProxyChoice, ProxyPlan, build_proxy_plan
from app.services.tls_trust import trust_context


def egress(config: dict, connector: str, host: str, instance_setting: str | None = None) -> tuple[ProxyChoice, ProxyPlan]:
    """Where the runtime's connection from ``connector`` to ``host`` leaves:
    the proxy the Proxy connector assigns it (or the default), or a direct
    connection; see app/services/proxy_plan.py."""
    plan = build_proxy_plan(config.get("proxy") if isinstance(config.get("proxy"), dict) else {})
    return plan.egress(connector, host=host, instance_setting=instance_setting), plan


def client_kwargs(choice: ProxyChoice, plan: ProxyPlan) -> dict:
    """httpx client arguments for a request along ``choice``.

    Without a configured Proxy connector the Portal keeps calling the way it
    always did (its own environment); with one, the request goes exactly
    where the runtime's tool goes, the Portal's environment left aside.
    Every request verifies TLS against the shared trust context (system
    store, certifi, PORTAL_CA_BUNDLE), so ``trust_env=False`` changes the
    path and not what is trusted.
    """
    if choice.kind == KIND_PROXY:
        return {"proxy": choice.proxy_url, "trust_env": False, "verify": trust_context()}
    if choice.kind == KIND_NONE and plan.configured:
        return {"trust_env": False, "verify": trust_context()}
    return {"verify": trust_context()}


def basic_auth_header(username: str, secret: str) -> dict:
    encoded = base64.b64encode(f"{username}:{secret}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {encoded}"}


def instance_auth_headers(instance: dict) -> dict:
    """The Authorization header an instance row (Jira, Confluence) signs in with.

    A username with a token is Basic (the Cloud API-token form, and what a
    Data Center username plus token means too); a token alone is a personal
    access token sent as Bearer; a username with a password is Basic.
    """
    username = str(instance.get("username") or "").strip()
    token = str(instance.get("token") or "").strip()
    if username and token:
        return basic_auth_header(username, token)
    if token:
        return {"Authorization": f"Bearer {token}"}
    password = str(instance.get("password") or "").strip()
    if username and password:
        return basic_auth_header(username, password)
    return {}


def first_auth_instance(instances: Any) -> dict | None:
    """The first instance row that has an address and a complete credential."""
    for item in instances if isinstance(instances, list) else []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        token = str(item.get("token") or "").strip()
        username = str(item.get("username") or "").strip()
        password = str(item.get("password") or "").strip()
        has_username_token = bool(username and token)
        has_token_only = bool(token and not username)
        has_username_password = bool(username and password)
        if url and (has_username_token or has_token_only or has_username_password):
            return item
    return None


def default_instance(section_cfg: dict) -> dict | None:
    """The instance a call runs against: the named default, else the first.

    Mirrors the CLI, which uses the section's default_instance when a
    command carries no --instance. A disabled default is skipped, since
    the assistant could not use it either.
    """
    instances = [item for item in (section_cfg.get("instances") or []) if isinstance(item, dict)]
    usable = [item for item in instances if item.get("enabled") is not False]
    wanted = str(section_cfg.get("default_instance") or "").strip().lower()
    if wanted:
        for item in usable:
            if str(item.get("name") or "").strip().lower() == wanted:
                return item
    return usable[0] if usable else (instances[0] if instances else None)
