from app.redaction import (
    REDACTED,
    REDACTED_PRIVATE_KEY,
    _is_sensitive_key,
    redact_text,
    redact_value,
    safe_preview,
    sanitize_exception_message,
)
from app.services.profile_secret_encryption import SENSITIVE_FIELD_NAMES


def test_redact_sensitive_dict_keys():
    payload = {"password": "supersecret", "apiToken": "abc123", "username": "alice"}
    redacted = redact_value(payload)

    assert redacted["password"] == REDACTED
    assert redacted["apiToken"] == REDACTED
    assert redacted["username"] == "alice"


def test_redact_nested_structures():
    payload = {
        "outer": [
            {"token": "top-secret"},
            ("ok", {"secret_key": "hidden"}),
        ]
    }
    redacted = redact_value(payload)

    assert redacted["outer"][0]["token"] == REDACTED
    assert redacted["outer"][1][1]["secret_key"] == REDACTED


def test_redact_text_patterns_for_auth_and_cookie():
    text = "Authorization: Bearer abc123 Cookie: sessionid=foo Authorization: Basic dXNlcjpwYXNz token=abc password=xyz"
    redacted = redact_text(text)

    assert "Bearer abc123" not in redacted
    assert "Basic dXNlcjpwYXNz" not in redacted
    assert "sessionid=foo" not in redacted
    assert "token=abc" not in redacted
    assert "password=xyz" not in redacted


def test_redact_url_credentials():
    text = "clone from https://user:pass@example.com/org/repo.git"
    redacted = redact_text(text)

    assert "user:pass@" not in redacted
    assert "https://[REDACTED]:[REDACTED]@example.com/org/repo.git" in redacted


def test_redact_private_key_block():
    text = """-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"""
    redacted = redact_text(text)

    assert redacted == REDACTED_PRIVATE_KEY


def test_safe_preview_redacts_before_truncating():
    preview = safe_preview({"password": "very-secret", "note": "x" * 300}, limit=50)

    assert "very-secret" not in preview
    assert REDACTED in preview
    assert preview.endswith("...")


def test_sanitize_exception_message_redacts_structured_values():
    message = sanitize_exception_message({"password": "secret", "nested": {"token": "abc"}})

    assert "secret" not in message
    assert "abc" not in message
    assert REDACTED in message


def test_redact_text_patterns_for_access_refresh_token_assignments():
    redacted = redact_text("access_token=abc refresh_token=xyz secret_key=qwe")

    assert "abc" not in redacted
    assert "xyz" not in redacted
    assert "qwe" not in redacted
    assert redacted.count("[REDACTED]") >= 3


def test_redact_camel_case_sensitive_keys():
    payload = {
        "githubApiToken": "gh-secret",
        "openaiApiKey": "oa-secret",
        "accessToken": "acc-secret",
    }

    redacted = redact_value(payload)

    assert redacted["githubApiToken"] == REDACTED
    assert redacted["openaiApiKey"] == REDACTED
    assert redacted["accessToken"] == REDACTED


class StringifiesToSecret:
    def __str__(self):
        return "access_token=abc123 password=secret"


def test_safe_preview_redacts_stringified_custom_object():
    preview = safe_preview(StringifiesToSecret(), limit=200)

    assert "abc123" not in preview
    assert "secret" not in preview
    assert "[REDACTED]" in preview


def test_every_field_the_profile_secret_encrypts_is_redacted_from_logs():
    """The invariant, not just the one name that was missing.

    Encrypting a value into the profile Secret and then printing it verbatim on
    the way through would make the encryption decorative. "access_key" slipped
    through exactly that way, so the relationship between the two lists is
    asserted rather than left to whoever edits either one next.
    """
    unredacted = sorted(name for name in SENSITIVE_FIELD_NAMES if not _is_sensitive_key(name))

    assert unredacted == []


def test_a_browserstack_access_key_does_not_reach_a_log_line():
    # The shape it actually has in a runtime profile config.
    payload = {"mobile-auto": {"browserstack": {"username": "team-bot", "access_key": "bs-live-key"}}}

    redacted = redact_value(payload)

    assert redacted["mobile-auto"]["browserstack"]["access_key"] == REDACTED
    # The username is not a credential and stays readable, or the log stops
    # being able to say which account was in play.
    assert redacted["mobile-auto"]["browserstack"]["username"] == "team-bot"


def test_access_key_is_matched_however_it_is_spelled():
    payload = {"access_key": "a", "accessKey": "b", "ACCESS_KEY": "c", "secret_access_key": "d"}

    redacted = redact_value(payload)

    assert list(redacted.values()) == [REDACTED] * 4


def test_an_access_key_id_stays_readable():
    # An AWS access key id names a credential without being one; redacting it
    # would cost a log line its only clue about which key was used.
    assert redact_value({"access_key_id": "AKIAEXAMPLE"})["access_key_id"] == "AKIAEXAMPLE"


def test_access_keys_are_redacted_out_of_free_text_too():
    redacted = redact_text("access_key=bs-live-key&secret_access_key=aws-sec&region=us-east-1")

    assert "bs-live-key" not in redacted
    assert "aws-sec" not in redacted
    # Everything that is not a credential survives, or the line stops being
    # worth logging.
    assert "region=us-east-1" in redacted
