"""Unit tests for field-level profile-secret encryption.

The portal encrypts sensitive VALUES (api keys, tokens, passwords) as
``ENC:<fernet-token>`` before writing them into the ``efp-profile-*`` Secret;
each runtime holds a byte-identical copy of this module and decrypts at boot.
"""
import pytest

from app.services.profile_secret_encryption import (
    ENC_PREFIX,
    decrypt_sensitive_fields,
    encrypt_sensitive_fields,
)

KEY = "unit-test-master-key"


def _sample():
    return {
        "llm": {"provider": "github_copilot", "model": "gpt-5.6-terra", "api_key": "sk-secret"},
        "jira": {"instances": [{"url": "https://j", "token": "tok-1", "password": "pw-1"}]},
        "github": {"api_token": "gh-tok"},
        "proxy": {"http_proxy": "http://p:8080"},
    }


def test_roundtrip_recovers_original(monkeypatch):
    monkeypatch.setenv("EFP_CONFIG_KEY", KEY)
    original = _sample()
    encrypted = encrypt_sensitive_fields(original)
    assert decrypt_sensitive_fields(encrypted) == original


def test_only_sensitive_values_encrypted(monkeypatch):
    monkeypatch.setenv("EFP_CONFIG_KEY", KEY)
    encrypted = encrypt_sensitive_fields(_sample())
    # Non-secret fields stay readable.
    assert encrypted["llm"]["provider"] == "github_copilot"
    assert encrypted["llm"]["model"] == "gpt-5.6-terra"
    assert encrypted["jira"]["instances"][0]["url"] == "https://j"
    assert encrypted["proxy"]["http_proxy"] == "http://p:8080"
    # Secret values become ciphertext (nested dicts and lists both covered).
    assert encrypted["llm"]["api_key"].startswith(ENC_PREFIX)
    assert encrypted["jira"]["instances"][0]["token"].startswith(ENC_PREFIX)
    assert encrypted["jira"]["instances"][0]["password"].startswith(ENC_PREFIX)
    assert encrypted["github"]["api_token"].startswith(ENC_PREFIX)


def test_no_key_is_passthrough(monkeypatch):
    monkeypatch.delenv("EFP_CONFIG_KEY", raising=False)
    original = _sample()
    encrypted = encrypt_sensitive_fields(original)
    assert encrypted == original  # no-op copy, plaintext preserved
    # Plaintext config with no ENC: values decrypts to itself even without a key.
    assert decrypt_sensitive_fields(encrypted) == original


def test_does_not_mutate_input(monkeypatch):
    monkeypatch.setenv("EFP_CONFIG_KEY", KEY)
    original = _sample()
    encrypt_sensitive_fields(original)
    assert original["llm"]["api_key"] == "sk-secret"  # deep-copied, untouched


def test_encrypt_is_idempotent(monkeypatch):
    monkeypatch.setenv("EFP_CONFIG_KEY", KEY)
    once = encrypt_sensitive_fields(_sample())
    twice = encrypt_sensitive_fields(once)
    # Already-encrypted values are not double-wrapped.
    assert twice["llm"]["api_key"] == once["llm"]["api_key"]
    assert not twice["llm"]["api_key"][len(ENC_PREFIX):].startswith(ENC_PREFIX)


def test_placeholder_values_not_encrypted(monkeypatch):
    monkeypatch.setenv("EFP_CONFIG_KEY", KEY)
    encrypted = encrypt_sensitive_fields({"llm": {"api_key": "${LLM_API_KEY}"}})
    assert encrypted["llm"]["api_key"] == "${LLM_API_KEY}"


def test_enc_value_without_key_raises(monkeypatch):
    monkeypatch.setenv("EFP_CONFIG_KEY", KEY)
    encrypted = encrypt_sensitive_fields(_sample())
    monkeypatch.delenv("EFP_CONFIG_KEY", raising=False)
    with pytest.raises(RuntimeError):
        decrypt_sensitive_fields(encrypted)


def test_wrong_key_raises(monkeypatch):
    monkeypatch.setenv("EFP_CONFIG_KEY", KEY)
    encrypted = encrypt_sensitive_fields(_sample())
    monkeypatch.setenv("EFP_CONFIG_KEY", "a-different-key")
    with pytest.raises(RuntimeError):
        decrypt_sensitive_fields(encrypted)
