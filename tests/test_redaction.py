from app.redaction import REDACTED, REDACTED_PRIVATE_KEY, redact_text, redact_value, safe_preview, sanitize_exception_message


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
