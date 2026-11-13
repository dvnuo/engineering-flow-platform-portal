"""Who "me" is in a connected system.

"My pull requests" is only right when the credentials are the member's own.
An administrator may put a shared service account into the Default connectors
(app/services/runtime_profile_seed_service.py), and a member who follows those
defaults then calls GitHub or Jira as that account: ``author:@me`` and
``currentUser()`` would list the service account's work.

So every source asks here first, and gets one of three answers:

- personal: the credentials are the member's (they customized the connector,
  or the account behind the token is them). ``@me`` / ``currentUser()`` mean
  the member, and "recently viewed" is theirs.
- shared: the credentials are someone else's, but the member exists in that
  system under their Portal username. Queries name them explicitly; "recently
  viewed" is not available, since that history belongs to the token.
- unknown: the member cannot be found there. Only search is offered.

No setting asks the member who they are; the answer is worked out and kept
for an hour.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

from app.services.connector_defaults_service import MODE_CUSTOM, connector_mode
from app.services.connector_registry import settings_connector_for_section
from app.services.suggestions.base import (
    IDENTITY_PERSONAL,
    IDENTITY_SHARED,
    IDENTITY_UNKNOWN,
    SourceContext,
    SourceUnavailable,
    json_of,
)
from app.services.suggestions.cache import TTLCache

IDENTITY_TTL_SECONDS = 3600.0

# A name that can sit inside a quoted JQL / CQL string or a URL path as is.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@\-]{0,127}$")

_identities = TTLCache()


@dataclass(frozen=True)
class Identity:
    mode: str
    # The member in that system: a GitHub login, a Jira or Confluence username.
    login: str = ""
    # The account the credentials belong to.
    account: str = ""

    @property
    def personal(self) -> bool:
        return self.mode == IDENTITY_PERSONAL


def clear_identities() -> None:
    _identities.clear()


def credentials_are_members_own(ctx: SourceContext, section: str) -> bool:
    """Whether the member typed this connector's credentials themselves.

    A connector in system mode is read whole from the Default connectors, so
    whatever token it holds is the administrator's.
    """
    spec = settings_connector_for_section(section)
    if spec is None:
        return True
    return connector_mode(spec, ctx.modes) == MODE_CUSTOM


def safe_name(value: str) -> str:
    cleaned = str(value or "").strip()
    return cleaned if _SAFE_NAME.match(cleaned) else ""


def _same(left: str, right: str) -> bool:
    return bool(left) and left.strip().lower() == str(right or "").strip().lower()


def _github_candidate_login(ctx: SourceContext) -> str:
    """The member's GitHub login as far as the Portal can tell: their username,
    with the enterprise suffix the Portal strips at sign-in put back."""
    username = str(getattr(ctx.user, "username", "") or "").strip()
    suffix = str(getattr(ctx.settings, "github_username_suffix", "") or "").strip()
    if suffix and username and not username.lower().endswith(suffix.lower()):
        return username + suffix
    return username


async def github_identity(ctx: SourceContext, base_url: str, headers: dict) -> Identity:
    key = (getattr(ctx.user, "id", None), ctx.revision, "github", base_url)

    async def load() -> Identity:
        response = await ctx.http.get("github", f"{base_url}/user", headers=headers)
        data = json_of(response) if response.status_code == 200 else None
        account = str((data or {}).get("login") or "").strip() if isinstance(data, dict) else ""
        if not account:
            raise SourceUnavailable("unauthorized")
        candidate = _github_candidate_login(ctx)
        if credentials_are_members_own(ctx, "github") or _same(account, candidate) or _same(
            account, getattr(ctx.user, "username", "")
        ):
            return Identity(IDENTITY_PERSONAL, account, account)
        candidate = safe_name(candidate)
        if candidate:
            try:
                lookup = await ctx.http.get("github", f"{base_url}/users/{quote(candidate, safe='')}", headers=headers)
            except SourceUnavailable:
                # The account is known; only the member could not be looked up.
                return Identity(IDENTITY_UNKNOWN, "", account)
            found = json_of(lookup) if lookup.status_code == 200 else None
            login = str((found or {}).get("login") or "").strip() if isinstance(found, dict) else ""
            if login:
                return Identity(IDENTITY_SHARED, login, account)
        return Identity(IDENTITY_UNKNOWN, "", account)

    return await _identities.get_or_load(key, load, lambda _value: IDENTITY_TTL_SECONDS)


async def atlassian_identity(
    ctx: SourceContext,
    *,
    connector: str,
    whoami_url: str,
    lookup_url: str,
    headers: dict,
    instance_setting: str | None = None,
) -> Identity:
    """Jira and Confluence answer the same two questions the same way: who do
    these credentials belong to, and does a user with this username exist.

    ``lookup_url`` takes ``username`` as a query parameter (Server and Data
    Center). Cloud has no usernames, so a shared Cloud account ends as unknown.
    """
    key = (getattr(ctx.user, "id", None), ctx.revision, connector, whoami_url)

    async def load() -> Identity:
        response = await ctx.http.get(connector, whoami_url, headers=headers, instance_setting=instance_setting)
        data = json_of(response) if response.status_code == 200 else None
        if not isinstance(data, dict):
            raise SourceUnavailable("unauthorized")
        account = str(data.get("name") or data.get("username") or data.get("accountId") or "").strip()
        if not account:
            raise SourceUnavailable("unauthorized")
        username = str(getattr(ctx.user, "username", "") or "").strip()
        if credentials_are_members_own(ctx, connector) or _same(account, username) or _same(
            str(data.get("key") or data.get("userKey") or ""), username
        ):
            return Identity(IDENTITY_PERSONAL, account, account)
        candidate = safe_name(username)
        if candidate:
            try:
                lookup = await ctx.http.get(
                    connector,
                    lookup_url,
                    headers=headers,
                    params={"username": candidate},
                    instance_setting=instance_setting,
                )
            except SourceUnavailable:
                # A service account that may not browse users: the member
                # stays unknown rather than the whole source failing.
                return Identity(IDENTITY_UNKNOWN, "", account)
            found = json_of(lookup) if lookup.status_code == 200 else None
            login = str((found or {}).get("name") or (found or {}).get("username") or "").strip() if isinstance(found, dict) else ""
            if safe_name(login):
                return Identity(IDENTITY_SHARED, login, account)
        return Identity(IDENTITY_UNKNOWN, "", account)

    return await _identities.get_or_load(key, load, lambda _value: IDENTITY_TTL_SECONDS)
