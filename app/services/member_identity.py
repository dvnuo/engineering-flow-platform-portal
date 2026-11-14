"""A Portal member's name in the systems their connectors reach.

The credential in a connector is very often a shared service account, so
"who is asking" is never read from it. It is the member signed in to the
Portal, and what the Portal knows about their name elsewhere is kept here,
in one place, for the two sides that need it: the picker sources
(app/services/suggestions/identity.py) and the identity the Portal hands an
assistant with every chat (``portal_user`` in app/services/proxy_service.py).
"""
from __future__ import annotations

import re

# A GitHub login, as far as it can be told from its shape.
_GITHUB_LOGIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,99}$")


def github_login_for(username: object, suffix: object) -> str:
    """The member's GitHub login: their Portal username with the enterprise
    suffix put back.

    Enterprise-managed users sign in to GitHub as ``<employee id><suffix>``
    and the Portal keys the account by the employee id alone
    (``portal_username_from_github_login``, GITHUB_USERNAME_SUFFIX). This is
    the other direction. Without a suffix the username is returned as it
    is, and a name that cannot be a login gives "".
    """
    name = str(username or "").strip()
    tail = str(suffix or "").strip()
    if not name:
        return ""
    if tail and not name.lower().endswith(tail.lower()):
        name += tail
    return name if _GITHUB_LOGIN.match(name) else ""


__all__ = ["github_login_for"]
