"""The connector Test probes trust what the runtimes trust.

httpx verifies against certifi's bundle alone, and drops SSL_CERT_FILE as soon as
a probe stops reading its environment (a configured Proxy connector). The
runtimes trust the pod's system store, so an internal CA worked from the
assistant and failed the Portal's Test with CERTIFICATE_VERIFY_FAILED. The
probes now share one context: system store + certifi + PORTAL_CA_BUNDLE.
"""

import asyncio
import datetime as dt
import ssl

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

import app.services.runtime_profile_test_service as test_service_module
from app.services import tls_trust
from app.services.proxy_plan import build_proxy_plan
from app.services.runtime_profile_test_service import RuntimeProfileTestService


def _self_signed_ca_pem(common_name: str) -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


def _subjects(context: ssl.SSLContext) -> set[str]:
    out = set()
    for cert in context.get_ca_certs():
        for rdn in cert.get("subject", ()):
            for key, value in rdn:
                if key == "commonName":
                    out.add(value)
    return out


@pytest.fixture(autouse=True)
def _fresh_context():
    tls_trust.reset_trust_context()
    yield
    tls_trust.reset_trust_context()


def test_trust_context_adds_the_configured_bundle_to_certifi_and_the_system_store(tmp_path):
    bundle = tmp_path / "corporate-ca.pem"
    bundle.write_bytes(_self_signed_ca_pem("EFP Test Corporate Root"))

    plain = tls_trust.build_trust_context("")
    assert plain.verify_mode == ssl.CERT_REQUIRED and plain.check_hostname
    assert len(plain.get_ca_certs()) > 0, "certifi's roots are loaded"
    assert "EFP Test Corporate Root" not in _subjects(plain)

    with_bundle = tls_trust.build_trust_context(str(bundle))
    assert "EFP Test Corporate Root" in _subjects(with_bundle)
    assert len(with_bundle.get_ca_certs()) >= len(plain.get_ca_certs())

    # A directory of PEM files works too (OpenSSL capath).
    as_dir = tls_trust.build_trust_context(str(tmp_path))
    assert as_dir.verify_mode == ssl.CERT_REQUIRED


def test_missing_bundle_path_is_ignored_rather_than_breaking_every_probe(tmp_path):
    context = tls_trust.build_trust_context(str(tmp_path / "nowhere.pem"))
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert len(context.get_ca_certs()) > 0


def test_shared_context_is_cached_per_bundle_setting(tmp_path):
    bundle = tmp_path / "ca.pem"
    bundle.write_bytes(_self_signed_ca_pem("EFP Cached Root"))
    first = tls_trust.trust_context("")
    assert tls_trust.trust_context("") is first
    other = tls_trust.trust_context(str(bundle))
    assert other is not first and "EFP Cached Root" in _subjects(other)
    assert tls_trust.trust_context(str(bundle)) is other


def test_probe_client_arguments_always_carry_the_trust_context():
    plan = build_proxy_plan({})
    direct = RuntimeProfileTestService._client_kwargs(plan.egress("jira", host="jira.example.test"), plan)
    assert isinstance(direct["verify"], ssl.SSLContext)
    assert "trust_env" not in direct

    configured = build_proxy_plan({"enabled": True, "default": "corp", "proxies": [{"name": "corp", "url": "http://proxy.example.test:3128"}]})
    through = RuntimeProfileTestService._client_kwargs(configured.egress("jira", host="jira.example.test"), configured)
    assert through["trust_env"] is False and through["proxy"] == "http://proxy.example.test:3128"
    assert through["verify"] is direct["verify"], "one shared context for every probe"


def _verification_error():
    inner = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate in certificate chain (_ssl.c:1000)")
    inner.verify_message = "self-signed certificate in certificate chain"
    inner.verify_code = 19
    try:
        try:
            raise inner
        except ssl.SSLCertVerificationError as exc:
            raise httpx.ConnectError(str(exc)) from exc
    except httpx.ConnectError as wrapped:
        return wrapped


def test_verification_failure_is_explained_with_host_reason_and_remedy():
    message = tls_trust.describe_verification_failure(_verification_error(), host="jira.corp.example")
    assert message.startswith("TLS certificate verification failed for jira.corp.example: self-signed certificate in certificate chain.")
    assert "PORTAL_CA_BUNDLE" in message and "assistant's runtime" in message
    assert "_ssl.c" not in message
    # The message form without a chained cause (an older wrapper flattened it).
    flat = httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:1000)")
    assert "unable to get local issuer certificate." in tls_trust.describe_verification_failure(flat, host="nexus.corp")
    assert tls_trust.describe_verification_failure(httpx.ConnectError("Connection refused"), host="x") is None
    assert tls_trust.describe_verification_failure(OSError("timed out"), host="x") is None


def test_http_probe_reports_an_untrusted_certificate_in_plain_words(monkeypatch):
    real_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        assert isinstance(kwargs.get("verify"), ssl.SSLContext)

        def _handler(request):
            raise _verification_error()

        kwargs["transport"] = httpx.MockTransport(_handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(test_service_module.httpx, "AsyncClient", _factory)
    service = RuntimeProfileTestService()
    jira = {"enabled": True, "instances": [{"name": "main", "url": "https://jira.corp.example", "username": "u", "token": "t"}]}
    ok, message = asyncio.run(service._test_jira({"jira": jira}))
    assert not ok
    assert message.startswith("TLS certificate verification failed for jira.corp.example: self-signed certificate in certificate chain.")
    assert "Request failed" not in message

    def _plain_factory(*args, **kwargs):
        def _handler(request):
            raise httpx.ConnectError("Connection refused")

        kwargs["transport"] = httpx.MockTransport(_handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(test_service_module.httpx, "AsyncClient", _plain_factory)
    ok, message = asyncio.run(service._test_jira({"jira": jira}))
    assert not ok and message.startswith("Request failed: Connection refused")


def test_pgsql_tunnel_through_an_untrusted_https_proxy_is_explained(monkeypatch):
    def _fake_tunnel(proxy_url, host, port):
        raise _verification_error().__cause__

    monkeypatch.setattr(RuntimeProfileTestService, "_tcp_connect_via_proxy", staticmethod(_fake_tunnel))
    service = RuntimeProfileTestService()
    config = {
        "pgsql": {"enabled": True, "instances": [{"name": "db", "host": "db.internal", "port": 5432, "database": "d", "username": "u"}]},
        "proxy": {"enabled": True, "default": "corp", "proxies": [{"name": "corp", "url": "https://proxy.corp.example:3129"}]},
    }
    ok, message = asyncio.run(service._test_pgsql(config))
    assert not ok
    assert "PostgreSQL connection failed for db at db.internal:5432" in message
    assert "TLS certificate verification failed for proxy.corp.example: self-signed certificate in certificate chain." in message


def test_settings_expose_the_bundle_path():
    from app.config import Settings

    assert Settings(PORTAL_CA_BUNDLE=" /etc/efp/ca.pem ").portal_ca_bundle.strip() == "/etc/efp/ca.pem"
    assert Settings().portal_ca_bundle == ""
