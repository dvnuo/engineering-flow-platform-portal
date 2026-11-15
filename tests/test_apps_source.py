"""The repository source: GitHub REST with the service account's token, no git binary."""

import io
import json

import httpx
import pytest

from app.services.apps import source as source_module
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
from app.services.apps.source import AppSourceService, RepoRef, check_ref, normalize_entry_dir, parse_repo_url, web_host_of

TOKEN = "ghp_service_account_token"
SHA = "0123456789abcdef0123456789abcdef01234567"
REPO = RepoRef(host="github.com", owner="acme", name="tools")
MANIFEST = {"name": "Mind map", "type": "fastapi", "entry": "main:app", "owners": ["alice"]}


class GitHub:
    """A scripted GitHub: ``routes[(method, path)]`` is a response or a function of the request."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.routes = {
            ("GET", "/user"): httpx.Response(200, json={"login": "efp-bot"}),
            ("GET", "/repos/acme/tools"): httpx.Response(200, json={"full_name": "acme/tools", "default_branch": "main"}),
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get((request.method, request.url.path))
        if route is None:
            return httpx.Response(404, json={"message": "Not Found"})
        return route(request) if callable(route) else route

    def paths(self):
        return [request.url.path for request in self.requests]


@pytest.fixture
def github():
    source_module.reset_account_cache()
    yield GitHub()
    source_module.reset_account_cache()


def _service(github, token=TOKEN, api_url="https://api.github.com"):
    return AppSourceService(token=token, api_url=api_url, transport=httpx.MockTransport(github.handler))


# --- addresses ------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/acme/tools",
        "https://github.com/acme/tools/",
        "https://github.com/acme/tools.git",
        "http://github.com/acme/tools",
        "https://www.github.com/acme/tools",
        "https://GitHub.com/acme/tools",
        "git@github.com:acme/tools.git",
        "git@github.com:acme/tools",
        "ssh://git@github.com/acme/tools.git",
        "  https://github.com/acme/tools  ",
    ],
)
def test_repository_addresses_in_their_usual_forms(url):
    repo = parse_repo_url(url, api_url="https://api.github.com")
    assert (repo.host, repo.owner, repo.name) == ("github.com", "acme", "tools")
    assert repo.full_name == "acme/tools"
    assert repo.web_url == "https://github.com/acme/tools"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        None,
        "acme/tools",
        "https://github.com/acme",
        "https://github.com/acme/tools/tree/main",
        "https://github.com/acme/tools?x=1",
        "https://github.com/acme/tools#readme",
        "https://github.com/ac me/tools",
        "https://github.com/acme/..",
        "https://github.com/-acme/tools",
        "ftp://github.com/acme/tools",
        "file:///etc/passwd",
        "https://github.com\\acme\\tools",
        "https://github.com/acme/to\nols",
    ],
)
def test_addresses_that_are_not_a_repository(url):
    with pytest.raises(RepoUrlInvalid) as caught:
        parse_repo_url(url, api_url="https://api.github.com")
    assert caught.value.status_code == 422


def test_only_the_configured_github_is_read():
    with pytest.raises(RepoUrlInvalid) as caught:
        parse_repo_url("https://gitlab.com/acme/tools", api_url="https://api.github.com")
    assert caught.value.params == {"host": "github.com"}
    assert "github.com" in caught.value.message

    enterprise = "https://ghe.example.com/api/v3"
    assert web_host_of(enterprise) == "ghe.example.com"
    assert parse_repo_url("git@ghe.example.com:acme/tools.git", api_url=enterprise).host == "ghe.example.com"
    with pytest.raises(RepoUrlInvalid):
        parse_repo_url("https://github.com/acme/tools", api_url=enterprise)


def test_credentials_in_an_address_are_never_kept():
    repo = parse_repo_url("https://someone:ghp_secret@github.com/acme/tools.git", api_url="https://api.github.com")
    assert repo.web_url == "https://github.com/acme/tools"


@pytest.mark.parametrize("value, expected", [("", ""), (".", ""), ("/", ""), ("apps/mindmap", "apps/mindmap"), ("/apps/mindmap/", "apps/mindmap"), ("apps//mindmap", "apps/mindmap")])
def test_entry_directory_is_normalised(value, expected):
    assert normalize_entry_dir(value) == expected


@pytest.mark.parametrize("value", ["../x", "apps/../../x", "apps\\mindmap", "a/\x00b"])
def test_entry_directory_must_stay_inside_the_repository(value):
    with pytest.raises(AppValidationError):
        normalize_entry_dir(value)


@pytest.mark.parametrize("value", ["", " ", "-rf", "a..b", "a b", "a~1", "a^", "a:b", "feature/[x]", "x" * 256])
def test_refs_that_are_not_a_branch_or_tag_name(value):
    with pytest.raises(AppValidationError):
        check_ref(value)


def test_ref_names_with_slashes_are_fine():
    assert check_ref(" release/2026.11 ") == "release/2026.11"


# --- refs -------------------------------------------------------------------------


def test_resolve_a_branch_to_its_commit(github):
    github.routes[("GET", "/repos/acme/tools/commits/main")] = httpx.Response(
        200, json={"sha": SHA.upper(), "commit": {"message": "Add the board\n\nbody", "committer": {"date": "2026-11-15T08:30:00Z"}}}
    )
    resolved = _service(github).resolve_ref(REPO, "main")
    assert resolved.sha == SHA
    assert resolved.ref == "main"
    assert resolved.message.startswith("Add the board")
    assert resolved.committed_at == "2026-11-15T08:30:00Z"
    request = github.requests[-1]
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["accept"] == "application/vnd.github+json"


def test_a_tag_with_a_slash_keeps_its_slash_in_the_path(github):
    github.routes[("GET", "/repos/acme/tools/commits/release/1.0")] = httpx.Response(200, json={"sha": SHA, "commit": {}})
    assert _service(github).resolve_ref(REPO, "release/1.0").sha == SHA


def test_unknown_ref_in_a_readable_repository(github):
    github.routes[("GET", "/repos/acme/tools/commits/nope")] = httpx.Response(422, json={"message": "No commit found for SHA: nope"})
    with pytest.raises(RefNotFound) as caught:
        _service(github).resolve_ref(REPO, "nope")
    assert caught.value.params == {"ref": "nope", "repo": "acme/tools"}
    assert caught.value.status_code == 422


def test_unreadable_repository_names_the_service_account(github):
    # GitHub answers 404 for a private repository the token cannot see.
    github.routes.pop(("GET", "/repos/acme/tools"))
    with pytest.raises(RepoNotReadable) as caught:
        _service(github).resolve_ref(REPO, "main")
    assert caught.value.code == "repo_not_readable"
    assert caught.value.params == {"account": "efp-bot", "repo": "acme/tools"}
    assert "efp-bot" in caught.value.message and "read-only collaborator" in caught.value.message
    assert TOKEN not in caught.value.message


def test_the_account_is_asked_once_and_remembered(github):
    github.routes.pop(("GET", "/repos/acme/tools"))
    service = _service(github)
    for _ in range(3):
        with pytest.raises(RepoNotReadable):
            service.ensure_readable(REPO)
    assert github.paths().count("/user") == 1
    # Another service object with the same token reuses the answer too.
    assert _service(github).account_login() == "efp-bot"
    assert github.paths().count("/user") == 1


def test_unreadable_repository_when_the_account_cannot_be_told(github):
    github.routes.pop(("GET", "/repos/acme/tools"))
    github.routes[("GET", "/user")] = httpx.Response(403, json={"message": "Resource not accessible"})
    with pytest.raises(RepoNotReadable) as caught:
        _service(github).ensure_readable(REPO)
    assert caught.value.params == {"repo": "acme/tools"}
    assert "service account" in caught.value.message


def test_an_account_that_cannot_be_told_is_not_asked_for_on_every_call(github, monkeypatch):
    github.routes[("GET", "/user")] = httpx.Response(502)
    clock = {"now": 1000.0}
    monkeypatch.setattr(source_module.time, "monotonic", lambda: clock["now"])
    service = _service(github)
    assert service.account_login() is None
    assert service.account_login() is None
    assert github.paths().count("/user") == 1
    # A minute later it is asked again, and a name is then kept for an hour.
    clock["now"] += 61
    github.routes[("GET", "/user")] = httpx.Response(200, json={"login": "efp-bot"})
    assert service.account_login() == "efp-bot"
    clock["now"] += 3000
    assert service.account_login() == "efp-bot"
    assert github.paths().count("/user") == 2


def test_without_a_token_nothing_is_sent_as_authorization(github):
    github.routes[("GET", "/repos/acme/tools/commits/main")] = httpx.Response(200, json={"sha": SHA, "commit": {}})
    service = _service(github, token="")
    assert service.resolve_ref(REPO, "main").sha == SHA
    assert "authorization" not in github.requests[-1].headers
    assert service.account_login() is None


def test_a_refused_token_is_its_own_error(github):
    github.routes[("GET", "/repos/acme/tools/commits/main")] = httpx.Response(401, json={"message": "Bad credentials"})
    with pytest.raises(SourceAuthFailed) as caught:
        _service(github).resolve_ref(REPO, "main")
    assert caught.value.code == "github_auth_failed"


def test_rate_limit_and_server_errors_are_unavailable(github):
    github.routes[("GET", "/repos/acme/tools/commits/main")] = httpx.Response(403, headers={"x-ratelimit-remaining": "0"}, json={})
    with pytest.raises(SourceUnavailable) as caught:
        _service(github).resolve_ref(REPO, "main")
    assert "rate limit" in caught.value.message
    github.routes[("GET", "/repos/acme/tools/commits/main")] = httpx.Response(502)
    with pytest.raises(SourceUnavailable) as caught:
        _service(github).resolve_ref(REPO, "main")
    assert "502" in caught.value.message


def test_a_connection_failure_never_leaks_the_token(github):
    def boom(request):
        raise httpx.ConnectError(f"connect failed with Authorization: Bearer {TOKEN} and {TOKEN}")

    github.routes[("GET", "/repos/acme/tools/commits/main")] = boom
    with pytest.raises(SourceUnavailable) as caught:
        _service(github).resolve_ref(REPO, "main")
    assert caught.value.code == "github_unreachable"
    assert TOKEN not in caught.value.message
    assert TOKEN not in json.dumps(caught.value.to_payload())


def test_tags_and_branches(github):
    github.routes[("GET", "/repos/acme/tools/tags")] = httpx.Response(
        200, json=[{"name": "v1.1.0", "commit": {"sha": SHA}}, {"name": "v1.0.0", "commit": {"sha": "f" * 40}}, {"oops": 1}]
    )
    github.routes[("GET", "/repos/acme/tools/branches")] = httpx.Response(200, json=[{"name": "main", "commit": {"sha": SHA}}])
    service = _service(github)
    assert service.list_tags(REPO) == [{"name": "v1.1.0", "sha": SHA}, {"name": "v1.0.0", "sha": "f" * 40}]
    assert github.requests[-1].url.params["per_page"] == "100"
    assert service.list_branches(REPO) == [{"name": "main", "sha": SHA}]


def test_commits_ahead_uses_the_compare_api(github):
    github.routes[("GET", f"/repos/acme/tools/compare/{SHA}...main")] = httpx.Response(200, json={"ahead_by": 4, "behind_by": 0})
    assert _service(github).commits_ahead(REPO, SHA, "main") == 4
    with pytest.raises(RefNotFound):
        _service(github).commits_ahead(REPO, SHA, "gone")


# --- files ------------------------------------------------------------------------


def test_read_the_manifest_at_a_commit(github):
    def contents(request):
        assert request.url.params["ref"] == SHA
        assert request.headers["accept"] == "application/vnd.github.v3.raw"
        return httpx.Response(200, content=json.dumps(MANIFEST).encode("utf-8"), headers={"content-type": "application/vnd.github.v3.raw; charset=utf-8"})

    github.routes[("GET", "/repos/acme/tools/contents/apps/mindmap/efp-app.json")] = contents
    content = _service(github).read_manifest(REPO, SHA, "/apps/mindmap/")
    assert json.loads(content)["name"] == "Mind map"


def test_manifest_at_the_repository_root(github):
    github.routes[("GET", "/repos/acme/tools/contents/efp-app.json")] = httpx.Response(200, content=b"{}")
    assert _service(github).read_manifest(REPO, SHA, "") == b"{}"


def test_missing_manifest_says_where_it_was_looked_for(github):
    with pytest.raises(ManifestMissing) as caught:
        _service(github).read_manifest(REPO, SHA, "apps/mindmap", ref="main")
    assert caught.value.params == {"path": "apps/mindmap", "repo": "acme/tools", "ref": "main"}
    assert caught.value.code == "manifest_missing"


def test_a_directory_is_not_a_file(github):
    github.routes[("GET", "/repos/acme/tools/contents/icon.png")] = httpx.Response(200, json=[{"name": "a.png", "type": "file"}])
    with pytest.raises(SourceFileMissing):
        _service(github).read_file(REPO, SHA, "icon.png", max_bytes=1000)


def test_a_file_over_its_cap_is_refused(github):
    github.routes[("GET", "/repos/acme/tools/contents/icon.png")] = httpx.Response(200, content=b"x" * 2049)
    with pytest.raises(SourceTooLarge) as caught:
        _service(github).read_file(REPO, SHA, "icon.png", max_bytes=2048)
    assert caught.value.code == "file_too_large"
    assert caught.value.params == {"path": "icon.png", "limit_kb": 2}


def test_paths_that_leave_the_repository_are_not_requested(github):
    with pytest.raises(SourceFileMissing):
        _service(github).read_file(REPO, SHA, "../../etc/passwd", max_bytes=1000)
    assert github.requests == []


def test_download_the_tarball_of_a_commit_following_the_redirect(github):
    payload = b"\x1f\x8b" + b"z" * 5000
    github.routes[("GET", f"/repos/acme/tools/tarball/{SHA}")] = httpx.Response(
        302, headers={"location": f"https://codeload.github.com/acme/tools/legacy.tar.gz/{SHA}?token=temporary"}
    )

    def codeload(request):
        # The service account's token stays with the API host.
        assert "authorization" not in request.headers
        return httpx.Response(200, content=payload)

    github.routes[("GET", f"/acme/tools/legacy.tar.gz/{SHA}")] = codeload
    dest = io.BytesIO()
    assert _service(github).download_tarball(REPO, SHA, dest, max_bytes=10000) == len(payload)
    assert dest.getvalue() == payload


def test_download_stops_at_the_size_cap(github):
    github.routes[("GET", f"/repos/acme/tools/tarball/{SHA}")] = httpx.Response(200, content=b"z" * (3 * 1024 * 1024))
    with pytest.raises(SourceTooLarge) as caught:
        _service(github).download_tarball(REPO, SHA, io.BytesIO(), max_bytes=2 * 1024 * 1024)
    assert caught.value.code == "bundle_too_large"
    assert caught.value.params == {"limit_mb": 2}


def test_download_of_an_unreadable_repository(github):
    with pytest.raises(RepoNotReadable):
        _service(github).download_tarball(REPO, SHA, io.BytesIO(), max_bytes=1000)


def test_enterprise_api_base_is_used_as_configured(github):
    github.routes[("GET", "/api/v3/repos/acme/tools/commits/main")] = httpx.Response(200, json={"sha": SHA, "commit": {}})
    service = _service(github, api_url="https://ghe.example.com/api/v3/")
    assert service.resolve_ref(RepoRef(host="ghe.example.com", owner="acme", name="tools"), "main").sha == SHA
    assert github.requests[-1].url.host == "ghe.example.com"


# --- egress -----------------------------------------------------------------------


def test_egress_follows_the_platform_proxy_connector_when_one_is_set_up():
    proxy = {"enabled": True, "default": "corp", "proxies": [{"name": "corp", "url": "http://proxy.example.test:3128", "no_proxy": "internal.example"}]}
    kwargs = source_module.egress_kwargs("api.github.com", proxy)
    assert kwargs["proxy"] == "http://proxy.example.test:3128"
    assert kwargs["trust_env"] is False
    assert kwargs["verify"] is not None
    # A host the proxy's NO_PROXY exempts is reached directly.
    direct = source_module.egress_kwargs("ghe.internal.example", proxy)
    assert "proxy" not in direct and direct["trust_env"] is False


def test_egress_without_a_proxy_connector_uses_the_portals_github_setting(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "github_proxy_url", "http://egress.example.test:8080")
    kwargs = source_module.egress_kwargs("api.github.com", {})
    assert kwargs["proxy"] == "http://egress.example.test:8080"
    assert "timeout" not in kwargs and kwargs["verify"] is not None

    monkeypatch.setattr(get_settings(), "github_proxy_url", "direct")
    assert source_module.egress_kwargs("api.github.com", None)["trust_env"] is False

    monkeypatch.setattr(get_settings(), "github_proxy_url", "")
    plain = source_module.egress_kwargs("api.github.com", None)
    assert "proxy" not in plain and "trust_env" not in plain


def test_the_token_setting_falls_back_to_the_git_repo_token(monkeypatch):
    from app.config import get_settings
    from app.services.apps import settings as apps_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "apps_git_auth_pat", "")
    monkeypatch.setattr(settings, "git_repo_auth_pat", "ghp_shared")
    assert apps_settings.git_token() == "ghp_shared"
    assert AppSourceService().token == "ghp_shared"
    monkeypatch.setattr(settings, "apps_git_auth_pat", "ghp_apps_only")
    assert apps_settings.git_token() == "ghp_apps_only"
