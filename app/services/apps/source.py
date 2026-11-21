"""An app's repository, read through the GitHub REST API with the platform's service account.

The Portal image has no ``git`` binary, and nothing here needs a clone: a
branch or a tag is resolved to a commit, ``efp-app.json`` and the icon are
read at that commit, and the code is downloaded as the commit's tarball.

Every call leaves the Portal the way its other GitHub calls do: through the
proxy the platform's Proxy connector assigns to ``github`` when one is
configured (app/services/proxy_plan.py), else by ``GITHUB_PROXY_URL`` or the
pod's environment (app/services/outbound_http.py), verifying TLS against the
shared trust (app/services/tls_trust.py).

The token belongs to a shared service account. It says nothing about the
member who registers an app, so any member may register a directory the
account can read unless its manifest lists ``owners``
(app/services/apps/manifest.py). When the account cannot read a repository
the error names the account, so the member knows whom to add as a
collaborator.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import re
import threading
import time
from typing import Any, BinaryIO, Optional
from urllib.parse import quote, urlparse

import httpx

from app.i18n import N_
from app.redaction import sanitize_exception_message
from app.services.apps import settings as apps_settings
from app.services.apps.errors import (
    AppValidationError,
    ManifestMissing,
    RefNotFound,
    RepoNotReadable,
    RepoUrlInvalid,
    SourceAuthFailed,
    SourceFileMissing,
    SourceTooLarge,
    SourceUnavailable,
)
from app.services.apps.manifest import MANIFEST_FILENAME, MANIFEST_MAX_BYTES, is_safe_relative_path
from app.services.tls_trust import describe_verification_failure, trust_context

logger = logging.getLogger(__name__)

PUBLIC_API_HOST = "api.github.com"
PUBLIC_WEB_HOST = "github.com"
JSON_ACCEPT = "application/vnd.github+json"
# The raw media type every GitHub (and GitHub Enterprise Server) version accepts.
RAW_ACCEPT = "application/vnd.github.v3.raw"
USER_AGENT = "efp-portal"
DEFAULT_TIMEOUT_SECONDS = 30.0
DOWNLOAD_TIMEOUT_SECONDS = 120.0
ACCOUNT_CACHE_SECONDS = 3600.0
ACCOUNT_FAILURE_CACHE_SECONDS = 60.0
REF_MAX_LENGTH = 255

_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_SCP_RE = re.compile(r"^(?:[A-Za-z0-9._-]+@)?(?P<host>[A-Za-z0-9.-]+):(?P<path>[^:]+)$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

_account_lock = threading.Lock()
_account_cache: dict[tuple[str, str], tuple[str, float]] = {}


@dataclass(frozen=True)
class RepoRef:
    """A repository on the configured GitHub: ``host`` is its web host."""

    host: str
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def web_url(self) -> str:
        return f"https://{self.host}/{self.owner}/{self.name}"


@dataclass(frozen=True)
class ResolvedRef:
    sha: str
    ref: str
    message: str = ""
    committed_at: Optional[str] = None


def web_host_of(api_url: str) -> str:
    """The host repositories live on for an API base: github.com for api.github.com, else the API's own host."""
    host = (urlparse(api_url if "://" in api_url else f"https://{api_url}").hostname or "").lower()
    return PUBLIC_WEB_HOST if host in {PUBLIC_API_HOST, PUBLIC_WEB_HOST, ""} else host


def parse_repo_url(url: Any, *, api_url: str | None = None) -> RepoRef:
    """The repository an address names: ``https://host/owner/repo`` or ``git@host:owner/repo``, ``.git`` optional.

    The host must be the GitHub this Portal reads apps from: the token is
    only ever sent to the configured API, so a repository elsewhere could not
    be read anyway.
    """
    text = str(url or "").strip()
    if not text or len(text) > 512 or any(ord(char) <= 32 or ord(char) == 127 for char in text) or "\\" in text:
        raise RepoUrlInvalid()
    host = path = ""
    if "://" in text:
        parsed = urlparse(text)
        if parsed.scheme.lower() not in {"https", "http", "ssh", "git"} or parsed.query or parsed.fragment:
            raise RepoUrlInvalid()
        try:
            host = (parsed.hostname or "").lower()
        except ValueError:
            raise RepoUrlInvalid() from None
        path = parsed.path
    else:
        match = _SCP_RE.match(text)
        if match is None:
            raise RepoUrlInvalid()
        host = match.group("host").lower()
        path = match.group("path")
    segments = [segment for segment in path.strip("/").split("/")]
    if len(segments) != 2:
        raise RepoUrlInvalid()
    owner, name = segments
    if name.lower().endswith(".git"):
        name = name[:-4]
    if not _OWNER_RE.match(owner) or not _REPO_RE.match(name) or name in {".", ".."}:
        raise RepoUrlInvalid()
    if host.startswith("www."):
        host = host[4:]
    expected = web_host_of(api_url or apps_settings.github_api_url())
    if host != expected:
        raise RepoUrlInvalid(
            N_("Apps are read from {host}. Enter a repository there, for example https://{host}/owner/repository."),
            host=expected,
        )
    return RepoRef(host=host, owner=owner, name=name)


def normalize_entry_dir(value: Any) -> str:
    """The app's directory inside the repository: no leading or trailing slash, "" for the root."""
    text = str(value or "").strip().strip("/")
    if text in {"", "."}:
        return ""
    if "\\" in text or not is_safe_relative_path(text):
        raise AppValidationError(
            N_("The entry directory must be a path inside the repository, for example apps/mindmap."),
            code="entry_dir_invalid",
        )
    return "/".join(segment for segment in text.split("/") if segment and segment != ".")


def check_ref(value: Any) -> str:
    text = str(value or "").strip()
    if (
        not text
        or len(text) > REF_MAX_LENGTH
        or text.startswith(("-", "/"))
        or ".." in text
        or any(ord(char) <= 32 or ord(char) == 127 or char in "~^:?*[\\" for char in text)
    ):
        raise AppValidationError(N_("Enter the name of a branch or a tag."), code="ref_invalid")
    return text


def egress_kwargs(host: str, proxy_config: Any) -> dict:
    """httpx client arguments for a call to ``host``: the platform's Proxy connector when it is set up, else the Portal's own GitHub egress."""
    from app.config import get_settings
    from app.services import connector_http
    from app.services.outbound_http import outbound_client_kwargs
    from app.services.proxy_plan import build_proxy_plan

    plan = build_proxy_plan(proxy_config if isinstance(proxy_config, dict) else {})
    if plan.configured:
        return connector_http.client_kwargs(plan.egress("github", host=host), plan)
    kwargs = outbound_client_kwargs(str(getattr(get_settings(), "github_proxy_url", "") or ""), timeout=DEFAULT_TIMEOUT_SECONDS)
    kwargs.pop("timeout", None)
    kwargs["verify"] = trust_context()
    return kwargs


def platform_proxy_config(db) -> dict:
    """The platform's Proxy connector (Default connectors), or ``{}``."""
    try:
        from app.services.group_service import GroupService

        proxy = GroupService(db).platform_seed().get("proxy")
    except Exception:  # noqa: BLE001 - a broken seed must not stop a deployment from trying the environment
        logger.warning("apps: the platform's proxy settings could not be read; using the environment", exc_info=True)
        return {}
    return proxy if isinstance(proxy, dict) else {}


class AppSourceService:
    """GitHub REST calls for one token and API base. Cheap to build; holds no connection."""

    def __init__(
        self,
        *,
        token: str | None = None,
        api_url: str | None = None,
        proxy_config: dict | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.token = apps_settings.git_token() if token is None else str(token or "").strip()
        self.api_url = (api_url or apps_settings.github_api_url()).rstrip("/")
        self.proxy_config = proxy_config or {}
        self.transport = transport
        self.timeout = timeout

    # ------------------------------------------------------------------ plumbing

    def parse(self, url: Any) -> RepoRef:
        return parse_repo_url(url, api_url=self.api_url)

    def _client(self, *, timeout: float | None = None) -> httpx.Client:
        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(timeout or self.timeout, connect=10.0),
            "follow_redirects": True,
        }
        if self.transport is not None:
            kwargs["transport"] = self.transport
        else:
            kwargs.update(egress_kwargs(urlparse(self.api_url).hostname or "", self.proxy_config))
        return httpx.Client(**kwargs)

    def _headers(self, accept: str = JSON_ACCEPT) -> dict[str, str]:
        headers = {"Accept": accept, "User-Agent": USER_AGENT}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _reason(self, exc: BaseException) -> str:
        host = urlparse(self.api_url).hostname or self.api_url
        verification = describe_verification_failure(exc, host=host)
        if verification:
            return verification
        text = sanitize_exception_message(exc) or exc.__class__.__name__
        if self.token:
            text = text.replace(self.token, "[REDACTED]")
        return text[:300]

    def _check_common(self, response: httpx.Response) -> None:
        """Statuses that mean the same whatever was asked."""
        status = response.status_code
        if status == 401:
            raise SourceAuthFailed()
        if status in (403, 429) and response.headers.get("x-ratelimit-remaining") == "0":
            raise SourceUnavailable(reason="the GitHub API rate limit is used up; try again later")
        if status >= 500:
            raise SourceUnavailable(reason=f"HTTP {status}")

    def _get(self, path: str, *, accept: str = JSON_ACCEPT, params: dict | None = None) -> httpx.Response:
        try:
            with self._client() as client:
                response = client.get(f"{self.api_url}{path}", headers=self._headers(accept), params=params)
        except httpx.HTTPError as exc:
            raise SourceUnavailable(reason=self._reason(exc)) from exc
        self._check_common(response)
        return response

    @staticmethod
    def _repo_path(repo: RepoRef) -> str:
        return f"/repos/{quote(repo.owner, safe='')}/{quote(repo.name, safe='')}"

    # ------------------------------------------------------------------- account

    def account_login(self) -> str | None:
        """The login of the account behind the token, asked once and remembered; None when it cannot be told."""
        if not self.token:
            return None
        key = (self.api_url, hashlib.sha256(self.token.encode("utf-8")).hexdigest())
        now = time.monotonic()
        with _account_lock:
            cached = _account_cache.get(key)
            # A name is kept for an hour; "could not be told" only for a minute,
            # so a GitHub that is away costs a page one wait rather than one per request.
            if cached is not None and now - cached[1] < (ACCOUNT_CACHE_SECONDS if cached[0] else ACCOUNT_FAILURE_CACHE_SECONDS):
                return cached[0] or None
        try:
            response = self._get("/user")
            data = response.json() if response.status_code == 200 else {}
        except Exception:  # noqa: BLE001 - the name only improves a message
            data = {}
        login = str((data or {}).get("login") or "").strip() if isinstance(data, dict) else ""
        with _account_lock:
            _account_cache[key] = (login, now)
        return login or None

    def _not_readable(self, repo: RepoRef) -> RepoNotReadable:
        account = self.account_login()
        if account:
            return RepoNotReadable(
                N_("The service account {account} cannot read {repo}. Add {account} to the repository as a read-only collaborator, then try again."),
                account=account,
                repo=repo.full_name,
            )
        return RepoNotReadable(repo=repo.full_name)

    def ensure_readable(self, repo: RepoRef) -> dict:
        """The repository's own record; RepoNotReadable when the service account cannot see it."""
        response = self._get(self._repo_path(repo))
        if response.status_code in (403, 404):
            raise self._not_readable(repo)
        if response.status_code != 200:
            raise SourceUnavailable(reason=f"HTTP {response.status_code}")
        data = response.json()
        return data if isinstance(data, dict) else {}

    # ---------------------------------------------------------------------- refs

    def resolve_ref(self, repo: RepoRef, ref: str) -> ResolvedRef:
        """The commit a branch, a tag or a commit id names."""
        name = check_ref(ref)
        response = self._get(f"{self._repo_path(repo)}/commits/{quote(name, safe='/')}")
        if response.status_code == 200:
            data = response.json() if response.content else {}
            sha = str((data or {}).get("sha") or "").strip().lower()
            if not _SHA_RE.match(sha):
                raise SourceUnavailable(reason="GitHub returned no commit id")
            commit = data.get("commit") if isinstance(data.get("commit"), dict) else {}
            committer = commit.get("committer") if isinstance(commit.get("committer"), dict) else {}
            return ResolvedRef(
                sha=sha,
                ref=name,
                message=str(commit.get("message") or "").strip()[:2000],
                committed_at=str(committer.get("date") or "").strip() or None,
            )
        if response.status_code in (403, 404, 409, 422):
            # The same answer for "no such ref" and "no such repository for you":
            # asking for the repository itself tells them apart.
            self.ensure_readable(repo)
            raise RefNotFound(ref=name, repo=repo.full_name)
        raise SourceUnavailable(reason=f"HTTP {response.status_code}")

    def _list_names(self, repo: RepoRef, kind: str, limit: int) -> list[dict]:
        response = self._get(f"{self._repo_path(repo)}/{kind}", params={"per_page": max(1, min(int(limit), 100))})
        if response.status_code in (403, 404):
            raise self._not_readable(repo)
        if response.status_code != 200:
            raise SourceUnavailable(reason=f"HTTP {response.status_code}")
        data = response.json()
        items = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                continue
            commit = item.get("commit") if isinstance(item.get("commit"), dict) else {}
            items.append({"name": str(item["name"]), "sha": str(commit.get("sha") or "")})
        return items

    def list_tags(self, repo: RepoRef, limit: int = 100) -> list[dict]:
        """``[{"name", "sha"}]``, newest first as GitHub lists them (one page)."""
        return self._list_names(repo, "tags", limit)

    def list_branches(self, repo: RepoRef, limit: int = 100) -> list[dict]:
        return self._list_names(repo, "branches", limit)

    def commits_ahead(self, repo: RepoRef, base_sha: str, head_ref: str) -> int:
        """How many commits ``head_ref`` has that ``base_sha`` does not."""
        head = check_ref(head_ref)
        base = quote(str(base_sha or "").strip(), safe="")
        response = self._get(f"{self._repo_path(repo)}/compare/{base}...{quote(head, safe='/')}")
        if response.status_code == 200:
            data = response.json()
            try:
                return max(0, int((data or {}).get("ahead_by") or 0))
            except (TypeError, ValueError):
                return 0
        if response.status_code in (403, 404, 422):
            self.ensure_readable(repo)
            raise RefNotFound(ref=head, repo=repo.full_name)
        raise SourceUnavailable(reason=f"HTTP {response.status_code}")

    # --------------------------------------------------------------------- files

    def read_file(self, repo: RepoRef, sha: str, path: str, *, max_bytes: int) -> bytes:
        """A file's bytes at a commit; SourceFileMissing when it is not there (or is a directory)."""
        if not is_safe_relative_path(path) or "\\" in path:
            raise SourceFileMissing(path=str(path), repo=repo.full_name, sha=str(sha)[:7])
        url = f"{self.api_url}{self._repo_path(repo)}/contents/{quote(path.strip('/'), safe='/')}"
        chunks: list[bytes] = []
        total = 0
        content_type = ""
        try:
            with self._client() as client:
                with client.stream("GET", url, headers=self._headers(RAW_ACCEPT), params={"ref": sha}) as response:
                    self._check_common(response)
                    if response.status_code in (403, 404):
                        raise SourceFileMissing(path=path, repo=repo.full_name, sha=str(sha)[:7])
                    if response.status_code != 200:
                        raise SourceUnavailable(reason=f"HTTP {response.status_code}")
                    content_type = response.headers.get("content-type", "").lower()
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise SourceTooLarge(
                                N_("{path} is larger than {limit_kb} KB."),
                                code="file_too_large",
                                path=path,
                                limit_kb=max(1, max_bytes // 1024),
                            )
                        chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise SourceUnavailable(reason=self._reason(exc)) from exc
        content = b"".join(chunks)
        # A directory comes back as a JSON listing whatever was asked for.
        if content_type.startswith("application/json") and content.lstrip().startswith(b"["):
            raise SourceFileMissing(path=path, repo=repo.full_name, sha=str(sha)[:7])
        return content

    def read_manifest(self, repo: RepoRef, sha: str, entry_dir: str, *, ref: str | None = None) -> bytes:
        """``efp-app.json`` of the entry directory at a commit; ManifestMissing when there is none."""
        directory = normalize_entry_dir(entry_dir)
        path = f"{directory}/{MANIFEST_FILENAME}" if directory else MANIFEST_FILENAME
        try:
            return self.read_file(repo, sha, path, max_bytes=MANIFEST_MAX_BYTES)
        except SourceFileMissing:
            raise ManifestMissing(path=directory or "/", repo=repo.full_name, ref=ref or str(sha)[:7]) from None

    def download_tarball(self, repo: RepoRef, sha: str, dest: BinaryIO, *, max_bytes: int | None = None) -> int:
        """Write the commit's ``.tar.gz`` into ``dest``; returns its size. Stops at ``max_bytes``."""
        limit = apps_settings.max_bundle_bytes() if max_bytes is None else int(max_bytes)
        url = f"{self.api_url}{self._repo_path(repo)}/tarball/{quote(str(sha), safe='')}"
        total = 0
        try:
            with self._client(timeout=DOWNLOAD_TIMEOUT_SECONDS) as client:
                with client.stream("GET", url, headers=self._headers()) as response:
                    self._check_common(response)
                    if response.status_code in (403, 404):
                        raise self._not_readable(repo)
                    if response.status_code != 200:
                        raise SourceUnavailable(reason=f"HTTP {response.status_code}")
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > limit:
                            raise SourceTooLarge(limit_mb=max(1, limit // (1024 * 1024)))
                        dest.write(chunk)
        except httpx.HTTPError as exc:
            raise SourceUnavailable(reason=self._reason(exc)) from exc
        dest.flush()
        return total


def source_for(db) -> AppSourceService:
    """The source service as the Portal reaches GitHub for the platform's service account."""
    return AppSourceService(proxy_config=platform_proxy_config(db))


def reset_account_cache() -> None:
    """Forget the remembered account names (tests, and a token changed on a running Portal)."""
    with _account_lock:
        _account_cache.clear()
