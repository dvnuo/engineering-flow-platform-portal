"""The app host client: every call of the manager API, and how its failures are told apart."""

import asyncio
import io
import json

import httpx
import pytest

from app.services.apps.errors import AppHostError, AppHostNotConfigured, AppHostUnavailable
from app.services.apps.host_client import AppHostClient

BASE = "http://efp-apps.efp-apps.svc:9000"
TOKEN = "host-secret-token"
APP_ID = "0b0e7c50-1111-4222-8333-444455556666"
STATUS = {"id": APP_ID, "slug": "mindmap", "type": "fastapi", "state": "running", "reason": None}


class Host:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.bodies: list[bytes] = []
        self.answer = httpx.Response(200, json=STATUS)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.bodies.append(request.read())
        return self.answer(request) if callable(self.answer) else self.answer

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]


@pytest.fixture
def host():
    return Host()


def _client(host, **kwargs):
    options = {"base_url": BASE, "token": TOKEN, "transport": httpx.MockTransport(host.handler)}
    options.update(kwargs)
    return AppHostClient(**options)


def test_every_call_carries_the_bearer_token_and_goes_to_the_configured_host(host):
    client = _client(host)
    assert client.get_app(APP_ID) == STATUS
    assert host.last.headers["authorization"] == f"Bearer {TOKEN}"
    assert str(host.last.url) == f"{BASE}/v1/apps/{APP_ID}"


def test_calls_never_use_the_environment_proxy(host, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.test:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example.test:3128")
    kwargs = _client(host)._client_kwargs(None)
    assert kwargs["trust_env"] is False
    assert "proxy" not in kwargs


def test_put_app_sends_the_spec(host):
    spec = {"slug": "mindmap", "name": "Mind map", "enabled": True, "env": {"EFP_APP_TOKEN": "efpa_x"}}
    assert _client(host).put_app(APP_ID, spec)["state"] == "running"
    assert host.last.method == "PUT"
    assert json.loads(host.bodies[-1]) == spec


def test_deploy_posts_the_spec_and_the_bundle_as_multipart(host):
    host.answer = httpx.Response(202, json={"job_id": "job-7"})
    spec = {"sha": "a" * 40, "ref": "main", "entry_dir": "apps/mindmap", "requested_by": "alice"}
    job_id = _client(host).deploy(APP_ID, spec, io.BytesIO(b"\x1f\x8bBUNDLE"))
    assert job_id == "job-7"
    assert host.last.method == "POST"
    assert host.last.url.path == f"/v1/apps/{APP_ID}/releases"
    assert host.last.headers["content-type"].startswith("multipart/form-data; boundary=")
    body = host.bodies[-1]
    assert b'name="spec"' in body and json.dumps(spec).encode("utf-8") in body
    assert b'name="bundle"; filename="bundle.tar.gz"' in body and b"\x1f\x8bBUNDLE" in body


def test_deploy_without_a_job_id_is_an_error(host):
    host.answer = httpx.Response(202, json={})
    with pytest.raises(AppHostError):
        _client(host).deploy(APP_ID, {"sha": "a" * 40}, io.BytesIO(b"x"))


@pytest.mark.parametrize(
    "call, method, path, body",
    [
        (lambda c: c.activate(APP_ID, "b" * 40), "POST", f"/v1/apps/{APP_ID}/activate", {"sha": "b" * 40}),
        (lambda c: c.start(APP_ID), "POST", f"/v1/apps/{APP_ID}/start", None),
        (lambda c: c.stop(APP_ID), "POST", f"/v1/apps/{APP_ID}/stop", None),
        (lambda c: c.restart(APP_ID), "POST", f"/v1/apps/{APP_ID}/restart", None),
        (lambda c: c.put_env(APP_ID, {"A": "1"}), "PUT", f"/v1/apps/{APP_ID}/env", {"env": {"A": "1"}}),
        (lambda c: c.delete_data(APP_ID), "DELETE", f"/v1/apps/{APP_ID}/data", None),
        (lambda c: c.revoke_sessions(12), "POST", "/v1/sessions/revoke", {"user_id": 12}),
        (lambda c: c.get_job("job-1"), "GET", "/v1/jobs/job-1", None),
        (lambda c: c.host_info(), "GET", "/v1/host", None),
    ],
    ids=["activate", "start", "stop", "restart", "env", "delete-data", "revoke", "job", "host"],
)
def test_lifecycle_calls(host, call, method, path, body):
    call(_client(host))
    assert host.last.method == method
    assert host.last.url.path == path
    if body is not None:
        assert json.loads(host.bodies[-1]) == body


def test_delete_app_says_whether_to_purge_the_data(host):
    host.answer = httpx.Response(200, json={"ok": True})
    client = _client(host)
    client.delete_app(APP_ID)
    assert host.last.url.params["purge_data"] == "false"
    client.delete_app(APP_ID, purge_data=True)
    assert host.last.url.params["purge_data"] == "true"


def test_list_logs_and_job_log(host):
    host.answer = httpx.Response(200, json={"apps": [STATUS, "junk"]})
    assert _client(host).list_apps() == [STATUS]

    host.answer = httpx.Response(200, json={"lines": ["a", "b"], "truncated": True})
    assert _client(host).logs(APP_ID, tail=50) == {"lines": ["a", "b"], "truncated": True}
    assert host.last.url.params["tail"] == "50"

    host.answer = httpx.Response(200, text="line 3\n", headers={"X-Log-Size": "120"})
    assert _client(host).get_job_log("job-1", offset=113) == ("line 3\n", 120)
    assert host.last.url.params["offset"] == "113"


@pytest.mark.parametrize("base_url, token", [("", TOKEN), (BASE, ""), ("", "")])
def test_a_host_that_is_not_configured_is_not_called(host, base_url, token):
    with pytest.raises(AppHostNotConfigured) as caught:
        _client(host, base_url=base_url, token=token).get_app(APP_ID)
    assert caught.value.code == "host_not_configured"
    assert caught.value.status_code == 503
    assert host.requests == []


@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("refused"), httpx.ConnectTimeout("timed out"), httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("closed")],
    ids=["refused", "connect-timeout", "read-timeout", "protocol"],
)
def test_a_host_that_cannot_be_reached_is_its_own_error(host, failure):
    def boom(request):
        raise failure

    host.answer = boom
    with pytest.raises(AppHostUnavailable) as caught:
        _client(host).put_app(APP_ID, {})
    error = caught.value
    assert error.code == "host_unreachable"
    assert error.status_code == 503
    assert "cannot be reached" in error.message
    # Something a member can be shown: no URL, no token.
    assert BASE not in error.message and TOKEN not in json.dumps(error.to_payload())


@pytest.mark.parametrize(
    "status, code, expected_code",
    [
        (409, "slug_taken", "host_slug_taken"),
        (404, "app_not_found", "host_app_not_found"),
        (409, "deploy_in_progress", "host_deploy_in_progress"),
        (404, "release_not_found", "host_release_not_found"),
    ],
)
def test_the_hosts_own_errors_keep_their_code(host, status, code, expected_code):
    host.answer = httpx.Response(status, json={"error": {"code": code, "message": f"the host says {code}"}})
    with pytest.raises(AppHostError) as caught:
        _client(host).put_app(APP_ID, {})
    error = caught.value
    assert not isinstance(error, AppHostUnavailable)
    assert error.code == expected_code
    assert error.host_code == code
    assert error.status_code == 502
    assert error.params["host_status"] == status
    assert f"the host says {code}" in error.message


def test_a_refused_token_points_at_the_setting(host):
    host.answer = httpx.Response(401, json={"error": {"code": "unauthorized", "message": "bad token"}})
    with pytest.raises(AppHostError) as caught:
        _client(host).get_app(APP_ID)
    assert caught.value.code == "host_unauthorized"
    assert "APPS_HOST_API_TOKEN" in caught.value.message
    assert TOKEN not in caught.value.message


def test_a_server_error_without_the_envelope(host):
    host.answer = httpx.Response(500, text="<html>boom</html>")
    with pytest.raises(AppHostError) as caught:
        _client(host).get_app(APP_ID)
    assert caught.value.code == "host_error"
    assert "HTTP 500" in caught.value.message
    assert not isinstance(caught.value, AppHostUnavailable)


def test_an_answer_that_is_not_json(host):
    host.answer = httpx.Response(200, text="ok")
    with pytest.raises(AppHostError):
        _client(host).get_app(APP_ID)


# --- the log stream ---------------------------------------------------------------


def _stream(client, app_id=APP_ID):
    async def run():
        received = []
        async with client.stream_logs(app_id) as chunks:
            async for chunk in chunks:
                received.append(chunk)
        return b"".join(received)

    return asyncio.run(run())


def test_stream_logs_passes_the_event_stream_through():
    events = b'data: {"line": "started"}\n\n: keepalive\n\ndata: {"line": "ready"}\n\n'
    seen = {}

    async def handler(request):
        seen["path"] = request.url.path
        seen["authorization"] = request.headers["authorization"]
        return httpx.Response(200, content=events, headers={"content-type": "text/event-stream"})

    client = AppHostClient(base_url=BASE, token=TOKEN, async_transport=httpx.MockTransport(handler))
    assert _stream(client) == events
    assert seen == {"path": f"/v1/apps/{APP_ID}/logs/stream", "authorization": f"Bearer {TOKEN}"}


def test_stream_logs_errors_before_the_stream_opens():
    async def missing(request):
        return httpx.Response(404, json={"error": {"code": "app_not_found", "message": "no such app"}})

    client = AppHostClient(base_url=BASE, token=TOKEN, async_transport=httpx.MockTransport(missing))
    with pytest.raises(AppHostError) as caught:
        _stream(client)
    assert caught.value.host_code == "app_not_found"

    async def refused(request):
        raise httpx.ConnectError("refused")

    client = AppHostClient(base_url=BASE, token=TOKEN, async_transport=httpx.MockTransport(refused))
    with pytest.raises(AppHostUnavailable):
        _stream(client)

    with pytest.raises(AppHostNotConfigured):
        _stream(AppHostClient(base_url="", token=""))
