"""Named proxies: the Proxy connector as a list with a default and one choice per connector."""

from __future__ import annotations

import asyncio

import pytest
from starlette.datastructures import FormData

from app.schemas.runtime_profile import (
    PROXY_ASSIGNABLE_CONNECTORS,
    PROXY_DEFAULT_ONLY_CONNECTORS,
    redact_runtime_profile_config_for_public_response,
    sanitize_runtime_profile_config_dict,
    sanitize_runtime_profile_pgsql_proxy,
    sanitize_runtime_profile_proxy,
    sanitize_runtime_profile_proxy_url,
)
from app.services import runtime_profile_test_service as test_service_module
from app.services.connector_registry import (
    PROXY_CHOICE_DEFAULT_ONLY,
    PROXY_CHOICE_SELECTABLE,
    get_connector_spec,
    proxy_assignable_specs,
)
from app.services.proxy_plan import KIND_NONE, KIND_PROXY, build_proxy_plan, mirror_default_proxy
from app.services.runtime_profile_context_projection import build_canonical_profile_config
from app.services.runtime_profile_test_service import RuntimeProfileTestService
from tests.test_default_connections_form import _panel_html
from tests.test_web_connector_settings import _bind_profile, _build_env, _saved


def _form(pairs: dict) -> FormData:
    return FormData(list(pairs.items()))


def list_section(**overrides) -> dict:
    section = {
        "enabled": True,
        "default": "corp-a",
        "proxies": [
            {"name": "corp-a", "url": "http://proxy-a.example.test:3128", "username": "ua", "password": "pa", "no_proxy": "localhost,db.internal"},
            {"name": "corp-b", "url": "https://proxy-b.example.test", "username": "ub", "password": "pb"},
        ],
        "assignments": {"llm": "corp-b", "pgsql": "none", "jira": ""},
    }
    section.update(overrides)
    return section


# --- schema ---------------------------------------------------------------


def test_sanitizer_keeps_the_list_shape_and_drops_what_it_cannot_use():
    out = sanitize_runtime_profile_proxy(
        list_section(
            proxies=[
                {"name": "corp-a", "url": "http://proxy-a.example.test:3128", "username": "ua", "password": "pa", "noProxy": "localhost"},
                {"name": "corp-b", "url": "proxy-b.example.test:3128"},
                {"name": "corp-a", "url": "http://dup.example.test:1"},
                {"name": "bad name", "url": "http://x.example.test:1"},
                {"name": "nourl"},
                {"name": "badurl", "url": "socks5://x.example.test:1080"},
                "not a mapping",
            ],
            assignments={"llm": "corp-b", "pgsql": "none", "jira": "", "nexus": "ghost", "aws": "corp-a", "unknown": "corp-a", "splunk": "DIRECT"},
        )
    )
    assert out["enabled"] is True
    assert out["proxies"] == [
        {"name": "corp-a", "url": "http://proxy-a.example.test:3128", "username": "ua", "password": "pa", "no_proxy": "localhost"},
        {"name": "corp-b", "url": "http://proxy-b.example.test:3128"},
    ]
    assert out["default"] == "corp-a"
    # jira "" follows the default (absent), ghost is dropped, aws never carries one.
    assert out["assignments"] == {"llm": "corp-b", "pgsql": "none", "splunk": "none"}

    missing_default = sanitize_runtime_profile_proxy(list_section(default="gone"))
    assert missing_default["default"] == "corp-a"


def test_sanitizer_upgrades_the_flat_shape_to_one_proxy_named_default():
    out = sanitize_runtime_profile_proxy({"enabled": True, "url": "http://p.example.test:8080", "username": "u", "password": "p", "no_proxy": ".svc"})
    assert out == {
        "enabled": True,
        "proxies": [{"name": "default", "url": "http://p.example.test:8080", "username": "u", "password": "p", "no_proxy": ".svc"}],
        "default": "default",
    }
    assert sanitize_runtime_profile_proxy({"enabled": False}) == {"enabled": False}
    # A list wins over the flat keys that may still ride along.
    both = sanitize_runtime_profile_proxy({"enabled": True, "url": "http://old.example.test:1", "proxies": [{"name": "a", "url": "http://a.example.test:1"}]})
    assert [item["name"] for item in both["proxies"]] == ["a"]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("http://proxy.example.test:3128", "http://proxy.example.test:3128"),
        ("proxy.example.test:3128", "http://proxy.example.test:3128"),
        ("https://u:p@proxy.example.test", "https://u:p@proxy.example.test"),
        ("http://proxy.example.test:3128/path?x=1", "http://proxy.example.test:3128"),
        ("http://proxy.example.test:0", None),
        ("http://proxy.example.test:70000", None),
        ("ftp://proxy.example.test", None),
        ("bad url with spaces", None),
        ("", None),
    ],
)
def test_proxy_url_rules(value, expected):
    assert sanitize_runtime_profile_proxy_url(value) == expected


def test_pgsql_row_proxy_accepts_a_proxy_name():
    assert sanitize_runtime_profile_pgsql_proxy("corp-b") == "corp-b"
    assert sanitize_runtime_profile_pgsql_proxy("none") == "none"
    assert sanitize_runtime_profile_pgsql_proxy("proxy.example.test:3128") == "http://proxy.example.test:3128"
    assert sanitize_runtime_profile_pgsql_proxy("http://u:p@proxy.example.test:3128") is None


def test_public_redaction_hides_every_proxy_password():
    redacted = redact_runtime_profile_config_for_public_response(
        {"proxy": list_section(proxies=[{"name": "a", "url": "http://u:p@a.example.test:1", "password": "pw"}, {"name": "b", "url": "http://b.example.test:1"}])}
    )
    entries = redacted["proxy"]["proxies"]
    assert entries[0] == {"name": "a", "url": "http://a.example.test:1", "password_present": True}
    assert entries[1] == {"name": "b", "url": "http://b.example.test:1", "password_present": False}
    assert "pw" not in str(redacted)
    # The top-level flag keeps meaning what it meant for the single proxy: the default one's password.
    assert redacted["proxy"]["password_present"] is True
    other_default = redact_runtime_profile_config_for_public_response(
        {"proxy": list_section(default="b", proxies=[{"name": "a", "url": "http://a.example.test:1", "password": "pw"}, {"name": "b", "url": "http://b.example.test:1"}])}
    )
    assert other_default["proxy"]["password_present"] is False


def test_whole_config_sanitizer_runs_the_proxy_upgrade():
    config = sanitize_runtime_profile_config_dict({"proxy": {"enabled": True, "url": "http://p.example.test:1"}})
    assert config["proxy"]["proxies"][0]["name"] == "default"


# --- registry and projection ------------------------------------------------


def test_registry_tells_which_connectors_can_pick_a_proxy():
    specs = proxy_assignable_specs()
    assert [spec.type for spec in specs] and set(spec.type for spec in specs) == set(PROXY_ASSIGNABLE_CONNECTORS)
    assert "proxy" not in {spec.type for spec in specs}
    for spec in specs:
        expected = PROXY_CHOICE_DEFAULT_ONLY if spec.type in PROXY_DEFAULT_ONLY_CONNECTORS else PROXY_CHOICE_SELECTABLE
        assert spec.proxy_choice == expected, spec.type
    assert get_connector_spec("proxy").proxy_choice == ""
    assert get_connector_spec("local_bridge").proxy_choice == ""
    assert get_connector_spec("proxy").state_of({"proxy": list_section()}) == "connected"
    assert get_connector_spec("proxy").state_of({"proxy": {"enabled": True, "proxies": []}}) == "not_set_up"


def test_secret_projection_mirrors_the_default_proxy_for_older_runtimes():
    canonical = build_canonical_profile_config({"proxy": list_section()})
    proxy = canonical["proxy"]
    assert proxy["url"] == "http://proxy-a.example.test:3128"
    assert proxy["username"] == "ua" and proxy["password"] == "pa" and proxy["no_proxy"] == "localhost,db.internal"
    assert [item["name"] for item in proxy["proxies"]] == ["corp-a", "corp-b"]
    assert proxy["assignments"] == {"llm": "corp-b", "pgsql": "none"}
    # The mirror lives in the projection only; the sanitized row keeps the list alone.
    assert "url" not in sanitize_runtime_profile_proxy(list_section())
    assert mirror_default_proxy({"enabled": True}) == {"enabled": True}


def test_plan_resolves_assignments_and_egress():
    plan = build_proxy_plan(list_section())
    llm = plan.choice("llm", host="chat.example.test")
    assert llm.entry.name == "corp-b" and llm.setting == "https://ub:pb@proxy-b.example.test"
    assert plan.choice("pgsql", host="orders.internal").setting == "none"
    assert plan.choice("jira", host="jira.example.test").kind == "environment"
    # The environment resolves to the default proxy for a probe, unless its NO_PROXY exempts the host.
    jira = plan.egress("jira", host="jira.example.test")
    assert jira.kind == KIND_PROXY and jira.entry.name == "corp-a"
    exempt = plan.egress("jira", host="db.internal")
    assert exempt.kind == KIND_NONE
    assert build_proxy_plan({"enabled": True}).egress("jira", host="jira.example.test").kind == KIND_NONE


# --- forms ------------------------------------------------------------------


def _cards_form(**extra) -> FormData:
    pairs = {
        "__touch_proxy": "1",
        "proxy_enabled": "on",
        "proxy_instance_count": "2",
        "proxy_instances_0_name": "corp-a",
        "proxy_instances_0_url": "http://proxy-a.example.test:3128",
        "proxy_instances_0_username": "ua",
        "proxy_instances_0_password": "pa",
        "proxy_instances_0_no_proxy": "localhost",
        "proxy_instances_1_name": "corp-b",
        "proxy_instances_1_url": "proxy-b.example.test:3128",
        "proxy_default": "corp-b",
        "proxy_assign_llm": "corp-a",
        "proxy_assign_pgsql": "none",
        "proxy_assign_jira": "",
        "proxy_assign_aws": "corp-a",
    }
    pairs.update(extra)
    return _form(pairs)


def test_member_save_stores_cards_default_and_assignments(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {"proxy": {"enabled": True, "url": "http://old.example.test:1"}})
        resp = env.client.post("/app/connectors/proxy/save", data=dict(_cards_form().multi_items()))
        assert resp.status_code == 200, resp.text
        assert "Saved." in resp.text
        saved = _saved(env.db, rp)["proxy"]
        assert saved["proxies"] == [
            {"name": "corp-a", "url": "http://proxy-a.example.test:3128", "username": "ua", "password": "pa", "no_proxy": "localhost"},
            {"name": "corp-b", "url": "http://proxy-b.example.test:3128"},
        ]
        assert saved["default"] == "corp-b"
        # aws is default-only: its posted choice is ignored; jira "" is the default.
        assert saved["assignments"] == {"llm": "corp-a", "pgsql": "none"}
        assert "url" not in saved
    finally:
        env.cleanup()


def test_member_save_refuses_a_card_it_could_not_keep(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {"proxy": list_section()})
        bad_url = env.client.post("/app/connectors/proxy/save", data=dict(_cards_form(proxy_instances_1_url="socks5://x:1").multi_items()))
        assert bad_url.status_code == 200
        assert "Proxy corp-b needs an http:// or https:// URL" in bad_url.text
        no_name = env.client.post("/app/connectors/proxy/save", data=dict(_cards_form(proxy_instances_1_name="").multi_items()))
        assert "Proxy 2 needs a name" in no_name.text
        bad_name = env.client.post("/app/connectors/proxy/save", data=dict(_cards_form(proxy_instances_1_name="corp b").multi_items()))
        assert "may use letters, digits, - and _ only" in bad_name.text
        duplicate = env.client.post("/app/connectors/proxy/save", data=dict(_cards_form(proxy_instances_1_name="corp-a").multi_items()))
        assert "listed more than once" in duplicate.text
        ghost = env.client.post("/app/connectors/proxy/save", data=dict(_cards_form(proxy_assign_llm="ghost").multi_items()))
        assert "Model provider is set to use the proxy ghost" in ghost.text
        ghost_default = env.client.post("/app/connectors/proxy/save", data=dict(_cards_form(proxy_default="ghost").multi_items()))
        assert "The default proxy ghost is not among the proxies above." in ghost_default.text
        # Nothing of that was stored.
        assert _saved(env.db, rp)["proxy"]["default"] == "corp-a"
    finally:
        env.cleanup()


def test_member_save_keeps_a_password_the_card_did_not_post(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {"proxy": list_section()})
        form = _cards_form(proxy_instances_0_original_name="corp-a")
        pairs = [(key, value) for key, value in form.multi_items() if key != "proxy_instances_0_password"]
        resp = env.client.post("/app/connectors/proxy/save", data=pairs)
        assert resp.status_code == 200
        assert _saved(env.db, rp)["proxy"]["proxies"][0]["password"] == "pa"
    finally:
        env.cleanup()


def test_member_panel_renders_cards_default_and_the_table(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _bind_profile(env.db, env.agent, {"proxy": list_section()})
        html = env.client.get("/app/connectors/proxy/panel").text
        assert 'data-instance-container="proxy"' in html
        assert html.count('data-instance-item="proxy"') == 2
        assert 'data-test-card="proxy"' in html
        assert 'name="proxy_default"' in html and '<option value="corp-a" selected>corp-a</option>' in html
        # Selectable rows offer Default, None and every proxy; the current choice is selected.
        assert 'data-proxy-assign="llm"' in html and 'data-proxy-assign="pgsql"' in html and 'data-proxy-assign="browserstack"' in html
        assert '<option value="corp-b" selected>corp-b</option>' in html
        assert '<option value="none" selected>None (direct)</option>' in html
        # AWS and GitHub show Default alone, disabled.
        assert 'data-proxy-assign-fixed="aws"' in html and 'data-proxy-assign-fixed="github"' in html
        assert 'data-proxy-assign="aws"' not in html
        assert "Always the default: its tools only read the environment." in html
        # The proxy's own password is rendered for editing; the other proxies' names are in every dropdown.
        assert 'value="pa"' in html
    finally:
        env.cleanup()


def test_admin_form_reads_the_cards_and_the_table():
    from app.web import _seed_config_from_form

    seed = _seed_config_from_form(_cards_form())
    assert seed["proxy"] == {
        "enabled": True,
        "proxies": [
            {"name": "corp-a", "url": "http://proxy-a.example.test:3128", "username": "ua", "password": "pa", "no_proxy": "localhost"},
            {"name": "corp-b", "url": "proxy-b.example.test:3128"},
        ],
        "default": "corp-b",
        "assignments": {"llm": "corp-a", "pgsql": "none"},
    }
    errors: list[str] = []
    _seed_config_from_form(_cards_form(proxy_instances_1_url="socks5://x:1"), errors)
    assert errors == ["Proxy corp-b needs an http:// or https:// URL such as http://proxy.example.com:3128."]
    # The single-URL form still reads: as the one proxy named "default".
    legacy = _seed_config_from_form(_form({"proxy_enabled": "on", "proxy_url": "http://p:8080", "proxy_password": "p"}))
    assert legacy["proxy"] == {"enabled": True, "proxies": [{"name": "default", "url": "http://p:8080", "password": "p"}], "default": "default"}


def test_admin_error_rerender_keeps_the_posted_cards():
    from app.web import _proxy_view, _seed_config_from_form

    errors: list[str] = []
    seed = _seed_config_from_form(_cards_form(proxy_instances_1_url="socks5://x:1", proxy_assign_llm="corp-b"), errors)
    assert errors == ["Proxy corp-b needs an http:// or https:// URL such as http://proxy.example.com:3128."]
    # The section carries the cards, the default and the table as posted, bad card included...
    view = _proxy_view(seed["proxy"], as_posted=True)
    assert [entry["name"] for entry in view["proxy_entries"]] == ["corp-a", "corp-b"]
    assert view["proxy_entries"][1]["url"] == "socks5://x:1"
    assert view["proxy_default"] == "corp-b"
    assert view["proxy_assignments"] == {"llm": "corp-b", "pgsql": "none"}
    # ...and the panel re-rendered from it shows them, so the admin can fix the one field.
    html = _panel_html_as_posted(seed)
    assert html.count('data-instance-item="proxy"') == 2
    assert 'value="socks5://x:1"' in html
    assert '<option value="corp-b" selected>corp-b</option>' in html
    # The sanitizer still drops the bad card when the section is read for real.
    assert [entry["name"] for entry in _proxy_view(seed["proxy"])["proxy_entries"]] == ["corp-a"]

    errors = []
    seed = _seed_config_from_form(_cards_form(proxy_default="ghost"), errors)
    assert errors == ["The default proxy ghost is not among the proxies above."]
    assert 'value="ghost" selected>ghost (not listed above)' in _panel_html_as_posted(seed)


def _panel_html_as_posted(seed: dict) -> str:
    """The Default connectors panel the way a rejected save re-renders it: from the posted seed."""
    from jinja2 import Environment, FileSystemLoader

    from app.web import _default_connections_context
    from tests.test_default_connections_form import _database

    context = _default_connections_context(None, _database(), status_type="error", status_message="x", seed_override=seed)
    env = Environment(loader=FileSystemLoader("app/templates"))
    env.globals["copilot_enterprise_sso_url"] = lambda: ""
    return env.get_template("partials/default_connections_panel.html").render(**context)


def test_single_url_form_rewrites_the_default_proxy_of_the_stored_list(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {"proxy": list_section()})
        # A page from before named proxies posts the one URL: it rewrites the
        # default proxy, keeps its no_proxy, the other cards and the table; a
        # password the form did not carry stays.
        resp = env.client.post(
            "/app/connectors/proxy/save",
            data={"__touch_proxy": "1", "proxy_enabled": "on", "proxy_url": "http://new.example.test:2", "proxy_username": "nu"},
        )
        assert resp.status_code == 200 and "Saved." in resp.text
        saved = _saved(env.db, rp)["proxy"]
        assert saved["proxies"][0] == {"name": "corp-a", "url": "http://new.example.test:2", "username": "nu", "password": "pa", "no_proxy": "localhost,db.internal"}
        assert saved["proxies"][1]["name"] == "corp-b" and saved["proxies"][1]["password"] == "pb"
        assert saved["default"] == "corp-a"
        assert saved["assignments"] == {"llm": "corp-b", "pgsql": "none"}
        # A blank password posted clears it, as the cards do.
        resp = env.client.post(
            "/app/connectors/proxy/save",
            data={"__touch_proxy": "1", "proxy_enabled": "on", "proxy_url": "http://new.example.test:2", "proxy_password": ""},
        )
        assert "Saved." in resp.text
        assert "password" not in _saved(env.db, rp)["proxy"]["proxies"][0]
        # A blank URL removes the default proxy; the next card becomes the default.
        resp = env.client.post("/app/connectors/proxy/save", data={"__touch_proxy": "1", "proxy_enabled": "on", "proxy_url": ""})
        assert "Saved." in resp.text
        saved = _saved(env.db, rp)["proxy"]
        assert [entry["name"] for entry in saved["proxies"]] == ["corp-b"] and saved["default"] == "corp-b"
    finally:
        env.cleanup()


def test_admin_panel_renders_cards_and_the_table():
    html = _panel_html(seed={"proxy": list_section()})
    assert html.count('data-instance-item="proxy"') == 2
    assert 'data-test-card="proxy"' not in html
    assert 'name="proxy_default"' in html
    assert 'data-proxy-assign="llm"' in html and 'data-proxy-assign-fixed="aws"' in html
    assert '<option value="corp-b" selected>corp-b</option>' in html


# --- connection tests ---------------------------------------------------------


class _FakeResponse:
    status_code = 200
    text = "{}"

    def json(self):
        return {"displayName": "Alice"}


class _FakeAsyncClient:
    seen: list[dict] = []

    def __init__(self, **kwargs):
        _FakeAsyncClient.seen.append(kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, **kwargs):
        return _FakeResponse()


def test_jira_test_probes_through_the_proxy_the_runtime_would_use(monkeypatch):
    _FakeAsyncClient.seen = []
    monkeypatch.setattr(test_service_module.httpx, "AsyncClient", _FakeAsyncClient)
    service = RuntimeProfileTestService()
    jira = {"enabled": True, "instances": [{"name": "main", "url": "https://jira.example.test", "username": "u", "token": "t"}]}

    ok, _ = asyncio.run(service._test_jira({"jira": jira, "proxy": list_section(assignments={"jira": "corp-b"})}))
    assert ok and _FakeAsyncClient.seen[-1] == {"timeout": 15.0, "proxy": "https://ub:pb@proxy-b.example.test", "trust_env": False}

    ok, _ = asyncio.run(service._test_jira({"jira": jira, "proxy": list_section(assignments={"jira": ""})}))
    assert ok and _FakeAsyncClient.seen[-1]["proxy"] == "http://ua:pa@proxy-a.example.test:3128"

    ok, _ = asyncio.run(service._test_jira({"jira": jira, "proxy": list_section(assignments={"jira": "none"})}))
    assert ok and _FakeAsyncClient.seen[-1] == {"timeout": 15.0, "trust_env": False}

    # Without a Proxy connector the Portal probes the way it always did.
    ok, _ = asyncio.run(service._test_jira({"jira": jira}))
    assert ok and _FakeAsyncClient.seen[-1] == {"timeout": 15.0}


def test_proxy_test_checks_the_named_card(monkeypatch):
    seen = []

    def fake_connect(host, port):
        seen.append((host, port))

    monkeypatch.setattr(RuntimeProfileTestService, "_tcp_connect", staticmethod(fake_connect))
    service = RuntimeProfileTestService()
    config = {"proxy": list_section()}
    ok, message = asyncio.run(service.run_test("proxy", config, proxy_name="corp-b"))
    assert ok and "corp-b" in message and seen[-1] == ("proxy-b.example.test", 443)
    ok, message = asyncio.run(service.run_test("proxy", config))
    assert ok and "corp-a" in message and seen[-1] == ("proxy-a.example.test", 3128)
    ok, message = asyncio.run(service.run_test("proxy", config, proxy_name="ghost"))
    assert not ok and "No proxy named ghost" in message
    ok, message = asyncio.run(service.run_test("proxy", {"proxy": {"enabled": True, "url": "http://legacy.example.test:1"}}))
    assert ok and "default" in message and seen[-1] == ("legacy.example.test", 1)
