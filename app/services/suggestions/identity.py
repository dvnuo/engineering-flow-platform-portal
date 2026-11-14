"""Who "me" is in a connected system: always the member signed in to the Portal.

A connector's credential says which account the Portal calls as. It does not
say whose work to list. Members routinely share one service account: an
administrator puts it into the Default connectors, or a member types the
team's token into their own settings. ``author:@me`` and ``currentUser()``
then mean that account, and a picker built on them shows every member the
same list of somebody else's work.

So the member is never taken from the credential. They are the person signed
in to the Portal, known in the connected system by their Portal username (on
GitHub with the enterprise suffix the Portal strips at sign-in put back).
Every source asks here first and gets one of three answers:

- personal: the account behind the credential is that same person. ``@me`` /
  ``currentUser()`` mean the member, and "recently viewed" is theirs.
- shared: it is another account, and the member exists in that system.
  Queries name them explicitly; "recently viewed" is not offered, since that
  history belongs to the account.
- unknown: the member cannot be found there. Only search is offered. The
  account's own work is never shown in their place.

Whether the member typed the credential themselves (a connector they
customized) is deliberately not part of the answer. It says who typed the
token, not whose account it opens, and treating the two as the same listed
the service account's branches as the member's.

No setting asks the member who they are; the answer is worked out and kept
for an hour.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

from app.services.member_identity import github_login_for
from app.services.proxy_plan import hostname_of
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
# A member nobody could find is asked about again sooner: the usual cause is
# an account that was just created, or a suffix that was just configured.
UNKNOWN_TTL_SECONDS = 300.0

PUBLIC_GITHUB_API_HOST = "api.github.com"

# A name that can sit inside a quoted JQL / CQL string or a URL path as is.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@\-]{0,127}$")

_identities = TTLCache()


@dataclass(frozen=True)
class Identity:
    mode: str
    # The member in that system: a GitHub login, a Jira or Confluence username.
    login: str = ""
    # The account the credentials belong to. Never a stand-in for the member.
    account: str = ""

    @property
    def personal(self) -> bool:
        return self.mode == IDENTITY_PERSONAL


def clear_identities() -> None:
    _identities.clear()


def safe_name(value: str) -> str:
    cleaned = str(value or "").strip()
    return cleaned if _SAFE_NAME.match(cleaned) else ""


def _same(left: str, right: str) -> bool:
    return bool(left) and left.strip().lower() == str(right or "").strip().lower()


def _ttl(identity: Identity) -> float:
    return UNKNOWN_TTL_SECONDS if identity.mode == IDENTITY_UNKNOWN else IDENTITY_TTL_SECONDS


def _not_allowed_to_look(exc: SourceUnavailable) -> bool:
    """Whether a failed lookup of the member is an answer or an accident.

    An account that may not look other users up will not be allowed to in a
    minute either: the member stays unknown. A timeout or an outage says
    nothing about the member, so it fails this one request and is not kept.
    """
    return exc.reason == "unauthorized"


def github_candidate_logins(ctx: SourceContext, base_url: str) -> list[str]:
    """The member's GitHub login as far as the Portal can tell, likeliest first.

    The Portal strips the enterprise suffix at sign-in (``12345678_corp``
    signs in as ``12345678``), so the login is the username with the suffix
    put back. The suffix belongs to the GitHub the Portal signs members in
    from; a GitHub Enterprise Server of the same company may know them by the
    bare username, so that is the second guess there. Not on github.com: the
    bare name there is whoever registered it.
    """
    username = str(getattr(ctx.user, "username", "") or "").strip()
    bare = github_login_for(username, "")
    login = github_login_for(username, getattr(ctx.settings, "github_username_suffix", ""))
    names = [login]
    if login.lower() != bare.lower() and hostname_of(base_url) != PUBLIC_GITHUB_API_HOST:
        names.append(bare)
    return [name for name in names if name]


async def github_identity(ctx: SourceContext, base_url: str, headers: dict) -> Identity:
    key = (getattr(ctx.user, "id", None), ctx.revision, "github", base_url)

    async def load() -> Identity:
        response = await ctx.http.get("github", f"{base_url}/user", headers=headers)
        data = json_of(response) if response.status_code == 200 else None
        account = str((data or {}).get("login") or "").strip() if isinstance(data, dict) else ""
        if not account:
            raise SourceUnavailable("unauthorized")
        candidates = github_candidate_logins(ctx, base_url)
        if any(_same(account, candidate) for candidate in candidates):
            return Identity(IDENTITY_PERSONAL, account, account)
        for candidate in candidates:
            try:
                lookup = await ctx.http.get("github", f"{base_url}/users/{quote(candidate, safe='')}", headers=headers)
            except SourceUnavailable as exc:
                if not _not_allowed_to_look(exc):
                    raise
                break
            found = json_of(lookup) if lookup.status_code == 200 else None
            if not isinstance(found, dict):
                continue
            login = str(found.get("login") or "").strip()
            # An organization answers at this address too, and is nobody's "me".
            if login and str(found.get("type") or "User") == "User":
                return Identity(IDENTITY_SHARED, login, account)
        return Identity(IDENTITY_UNKNOWN, "", account)

    return await _identities.get_or_load(key, load, _ttl)


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
    Center). Cloud has no usernames, so nobody can be named there and a Cloud
    site ends as unknown: its account is not assumed to be the member.
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
        # The username and nothing else: a Jira user key outlives a rename,
        # so the key "alice" may by now belong to somebody called otherwise.
        if _same(str(data.get("name") or data.get("username") or ""), username):
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
            except SourceUnavailable as exc:
                if not _not_allowed_to_look(exc):
                    raise
                # A service account that may not browse users: the member
                # stays unknown rather than the whole source failing.
                return Identity(IDENTITY_UNKNOWN, "", account)
            found = json_of(lookup) if lookup.status_code == 200 else None
            login = str((found or {}).get("name") or (found or {}).get("username") or "").strip() if isinstance(found, dict) else ""
            if safe_name(login):
                return Identity(IDENTITY_SHARED, login, account)
        return Identity(IDENTITY_UNKNOWN, "", account)

    return await _identities.get_or_load(key, load, _ttl)
