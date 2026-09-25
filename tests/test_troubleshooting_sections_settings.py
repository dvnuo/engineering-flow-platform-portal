"""Behaviour tests for the troubleshooting-CLI runtime-profile sections.

nexus, splunk and pgsql (PostgreSQL) share the jenkins shape -- enabled,
default_instance, instances[] addressed by name -- and each carries what its
CLI needs (a URL, or for PostgreSQL a host/database/username, plus Splunk's
search defaults).
Covers the whole Portal path: schema sanitizer, public redaction, per-profile
Secret encryption, the settings form parser and its validation, the end-to-end
save through both member panels, the admin seed form, the projection rule that
decides whether the CLI instructions are worth adding and what they say, the
guidance and help topics, the test-connection routes, and the rendered cards
(server-rendered and JS-added).
"""
import json
import re
from pathlib import Path

import pytest
from starlette.datastructures import FormData

from tests.test_default_connections_form import _panel_html as _default_connections_html
from tests.test_jenkins_multi_instance_settings import (
    _initialized_instance_groups,
    _instance_groups_in_template,
    _js_object_literal,
    _js_source,
    _parse_cards,
    _render_panel,
    _shape,
)
from tests.test_web_runtime_profile_settings import _bind_profile, _build_client

from app.schemas.runtime_profile import (
    ALLOWED_RUNTIME_PROFILE_SECTIONS,
    PORTAL_MANAGED_FIELD_TREE,
    TROUBLESHOOTING_INSTANCE_SECTIONS,
    redact_runtime_profile_config_for_public_response,
    sanitize_runtime_profile_config_dict,
    sanitize_runtime_profile_nexus,
    sanitize_runtime_profile_pgsql,
    sanitize_runtime_profile_splunk,
)
from app.services.connection_guidance import CONNECTION_GUIDANCE
from app.services.help_center import get_topic
from app.services.profile_secret_encryption import ENC_PREFIX, decrypt_sensitive_fields, encrypt_sensitive_fields
from app.services.runtime_profile_context_projection import (
    PORTAL_RUNTIME_PROFILE_SECTIONS,
    RUNTIME_PROFILE_CLI_TOOL_INSTRUCTIONS,
    _has_enabled_external_cli_config,
    project_canonical_for_runtime,
)
from app.services.runtime_profile_service import RuntimeProfileService
from app.web import (
    _MANAGED_TEST_TARGETS,
    TROUBLESHOOTING_CARD_FIELD_SPECS,
    TROUBLESHOOTING_CARD_PLACEHOLDERS,
    TROUBLESHOOTING_CARD_ROWS,
    _seed_config_from_form,
    _settings_merge_payload,
    _settings_view_payload,
)

SECTIONS = ("nexus", "splunk", "pgsql")
LABELS = {"nexus": "Nexus", "splunk": "Splunk", "pgsql": "PostgreSQL"}

# The canonical shapes the runtimes' CLIs read, one instance each.
CANONICAL = {
    "nexus": {
        "enabled": True,
        "default_instance": "main",
        "instances": [
            {"name": "main", "url": "https://nexus.example.test", "username": "svc-reader", "password": "nexus-pass", "enabled": True}
        ],
    },
    "splunk": {
        "enabled": True,
        "default_instance": "prod",
        "instances": [
            {
                "name": "prod",
                "url": "https://splunk-api.example.test:8089",
                "token": "splunk-token",
                "default_index": "app_prod",
                "default_earliest": "-1h",
                "max_results": 1000,
                "enabled": True,
            }
        ],
    },
    "pgsql": {
        "enabled": True,
        "default_instance": "orders-uat",
        "instances": [
            {
                "name": "orders-uat",
                "host": "orders-uat.example.test",
                "port": 5432,
                "database": "orders",
                "username": "efp_readonly",
                "password": "pg-pass",
                "sslmode": "require",
                "enabled": True,
            }
        ],
    },
}


def _copy(value):
    return json.loads(json.dumps(value))


# --------------------------------------------------------------------------
# Schema sanitizer
# --------------------------------------------------------------------------


def test_the_sections_are_portal_managed_with_the_jenkins_field_tree():
    for section in SECTIONS:
        assert section in ALLOWED_RUNTIME_PROFILE_SECTIONS
        assert section in PORTAL_RUNTIME_PROFILE_SECTIONS
        assert PORTAL_MANAGED_FIELD_TREE[section] == {"enabled": True, "instances": True, "default_instance": True}
    assert TROUBLESHOOTING_INSTANCE_SECTIONS == SECTIONS


@pytest.mark.parametrize("section", SECTIONS)
def test_the_canonical_shape_survives_the_sanitizer_unchanged(section):
    sanitized = sanitize_runtime_profile_config_dict({section: _copy(CANONICAL[section])})
    assert sanitized[section] == CANONICAL[section]
    assert sanitize_runtime_profile_config_dict(sanitized) == sanitized


def test_sanitizer_normalizes_typed_values_and_aliases():
    sanitized = sanitize_runtime_profile_config_dict(
        {
            "splunk": {
                "enabled": "on",
                "default_instance": " prod ",
                "instances": [
                    {
                        "name": " prod ",
                        "base_url": " https://splunk-api.example.test:8089/ ",
                        "api_token": " splunk-token ",
                        "default_index": " app_prod ",
                        "default_earliest": " -1h ",
                        "max_results": "1000",
                        "enabled": "1",
                        "junk": "drop-me",
                    }
                ],
            },
            "pgsql": {
                "enabled": True,
                "instances": [
                    {"name": "db", "hostname": "db.example.test", "port": "5433", "dbname": "orders", "user": "ro", "sslmode": " VERIFY-FULL ", "enabled": "off"}
                ],
            },
        }
    )
    assert sanitized["splunk"] == {
        "enabled": True,
        "default_instance": "prod",
        "instances": [
            {
                "name": "prod",
                "url": "https://splunk-api.example.test:8089",
                "token": "splunk-token",
                "default_index": "app_prod",
                "default_earliest": "-1h",
                "max_results": 1000,
                "enabled": True,
            }
        ],
    }
    assert sanitized["pgsql"]["instances"] == [
        {"name": "db", "host": "db.example.test", "port": 5433, "database": "orders", "username": "ro", "sslmode": "verify-full", "enabled": False}
    ]


def test_sanitizer_drops_rows_the_cli_could_not_address_or_reach():
    nexus = sanitize_runtime_profile_nexus(
        {
            "instances": [
                {"url": "https://nameless.example.test"},
                {"name": "no-url"},
                {"name": "keep", "url": "https://keep.example.test"},
                {"name": "KEEP", "url": "https://dup.example.test"},
                "not-a-row",
            ]
        }
    )
    # Nameless and URL-less rows go; the duplicate name (case-insensitive) loses to the first.
    assert nexus["instances"] == [{"name": "keep", "url": "https://keep.example.test"}]

    pgsql = sanitize_runtime_profile_pgsql(
        {
            "instances": [
                {"name": "no-host", "database": "orders", "username": "ro"},
                {"name": "no-db", "host": "h", "username": "ro"},
                {"name": "no-user", "host": "h", "database": "orders"},
                {"host": "h", "database": "orders", "username": "ro"},
                {"name": "keep", "host": "h", "database": "orders", "username": "ro"},
            ]
        }
    )
    assert pgsql["instances"] == [{"name": "keep", "host": "h", "database": "orders", "username": "ro"}]


@pytest.mark.parametrize("value", ["0", "10001", "abc", "", None, True, "12.5", -1])
def test_an_out_of_range_splunk_max_results_is_dropped(value):
    section = sanitize_runtime_profile_splunk({"instances": [{"name": "p", "url": "https://s", "max_results": value}]})
    assert "max_results" not in section["instances"][0]


@pytest.mark.parametrize("value,expected", [(1, 1), ("10000", 10000), (" 250 ", 250)])
def test_a_splunk_max_results_inside_the_bounds_is_kept_as_an_int(value, expected):
    section = sanitize_runtime_profile_splunk({"instances": [{"name": "p", "url": "https://s", "max_results": value}]})
    assert section["instances"][0]["max_results"] == expected


@pytest.mark.parametrize("value", ["0", "65536", "port", "", None, True, "54.32"])
def test_an_invalid_pgsql_port_is_dropped_but_the_row_kept(value):
    section = sanitize_runtime_profile_pgsql({"instances": [{"name": "d", "host": "h", "database": "o", "username": "u", "port": value}]})
    assert section["instances"] == [{"name": "d", "host": "h", "database": "o", "username": "u"}]


@pytest.mark.parametrize("value", ["disable", "allow", "", None, 3])
def test_an_unknown_pgsql_sslmode_is_dropped(value):
    section = sanitize_runtime_profile_pgsql({"instances": [{"name": "d", "host": "h", "database": "o", "username": "u", "sslmode": value}]})
    assert "sslmode" not in section["instances"][0]


@pytest.mark.parametrize("value", ["0", "301", "abc", "", None, True, "12.5", -5])
def test_an_out_of_range_pgsql_statement_timeout_is_dropped(value):
    # Out of range means the CLI default (30s) applies, not an unusable budget.
    section = sanitize_runtime_profile_pgsql(
        {"instances": [{"name": "d", "host": "h", "database": "o", "username": "u", "statement_timeout_seconds": value}]}
    )
    assert "statement_timeout_seconds" not in section["instances"][0]


@pytest.mark.parametrize("value", ["0", "100001", "many", "", None, True, -1])
def test_an_out_of_range_pgsql_max_rows_is_dropped(value):
    section = sanitize_runtime_profile_pgsql(
        {"instances": [{"name": "d", "host": "h", "database": "o", "username": "u", "max_rows": value}]}
    )
    assert "max_rows" not in section["instances"][0]


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("statement_timeout_seconds", 1, 1),
        ("statement_timeout_seconds", "300", 300),
        ("statement_timeout_seconds", " 10 ", 10),
        ("max_rows", 1, 1),
        ("max_rows", "100000", 100000),
        ("max_rows", " 500 ", 500),
    ],
)
def test_pgsql_query_bounds_inside_the_range_are_kept_as_ints(field, value, expected):
    # They ride to the runtime as EFP_PGSQL_INSTANCES_0_STATEMENT_TIMEOUT_SECONDS
    # and _MAX_ROWS, which the pgsql CLI reads as ints.
    section = sanitize_runtime_profile_pgsql(
        {"instances": [{"name": "d", "host": "h", "database": "o", "username": "u", field: value}]}
    )
    assert section["instances"][0][field] == expected


def test_splunk_namespace_is_kept_and_needs_an_app():
    # Saved searches, macros and lookups belong to a Splunk app; without it the
    # CLI addresses the global namespace and reports nothing.
    section = sanitize_runtime_profile_splunk(
        {"instances": [{"name": "p", "url": "https://s", "app": " cmb_search ", "owner": " user001 "}]}
    )
    assert section["instances"][0]["app"] == "cmb_search"
    assert section["instances"][0]["owner"] == "user001"

    # An owner without an app addresses nothing, so it is dropped rather than
    # stored as a setting that cannot take effect.
    orphan = sanitize_runtime_profile_splunk(
        {"instances": [{"name": "p", "url": "https://s", "owner": "user001"}]}
    )
    assert "owner" not in orphan["instances"][0]
    assert "app" not in orphan["instances"][0]


def test_secrets_are_stored_only_under_password_and_token():
    # A secret rides under `password` or `token`; a `client_secret` key is not
    # a field the tree knows and must never survive.
    sanitized = sanitize_runtime_profile_config_dict(
        {"nexus": {"enabled": True, "instances": [{"name": "p", "url": "https://a", "client_secret": "leak", "token": "kept"}]}}
    )
    assert "leak" not in json.dumps(sanitized)
    assert sanitized["nexus"]["instances"][0]["token"] == "kept"


def test_instances_key_is_kept_only_when_it_was_sent():
    assert sanitize_runtime_profile_nexus({"enabled": True, "instances": []}) == {"enabled": True, "instances": []}
    assert sanitize_runtime_profile_pgsql({"enabled": True, "instances": "nope"}) == {"enabled": True, "instances": []}
    assert "instances" not in sanitize_runtime_profile_splunk({"enabled": True, "default_instance": "x"})


# --------------------------------------------------------------------------
# Public redaction and the profile Secret
# --------------------------------------------------------------------------


def test_public_response_never_leaks_instance_secrets():
    redacted = redact_runtime_profile_config_for_public_response({s: _copy(CANONICAL[s]) for s in SECTIONS})
    dumped = json.dumps(redacted)
    for secret in ("nexus-pass", "splunk-token", "pg-pass"):
        assert secret not in dumped

    nexus = redacted["nexus"]["instances"][0]
    assert nexus["password_present"] is True and nexus["token_present"] is False
    assert "password" not in nexus
    splunk = redacted["splunk"]["instances"][0]
    assert splunk["token_present"] is True and splunk["default_index"] == "app_prod"
    pgsql = redacted["pgsql"]["instances"][0]
    assert pgsql["password_present"] is True
    # A PostgreSQL row has no token; saying token_present on it would mislead.
    assert "token_present" not in pgsql
    assert pgsql["host"] == "orders-uat.example.test" and pgsql["port"] == 5432
    for section in SECTIONS:
        assert redacted[section]["default_instance"] == CANONICAL[section]["default_instance"]


def test_instance_secrets_are_encrypted_in_the_profile_secret(monkeypatch):
    monkeypatch.setenv("EFP_CONFIG_KEY", "unit-test-config-key")
    config = sanitize_runtime_profile_config_dict({s: _copy(CANONICAL[s]) for s in SECTIONS})

    encrypted = encrypt_sensitive_fields(config)
    dumped = json.dumps(encrypted)
    for secret in ("nexus-pass", "splunk-token", "pg-pass"):
        assert secret not in dumped
    assert encrypted["nexus"]["instances"][0]["password"].startswith(ENC_PREFIX)
    assert encrypted["splunk"]["instances"][0]["token"].startswith(ENC_PREFIX)
    assert encrypted["pgsql"]["instances"][0]["password"].startswith(ENC_PREFIX)
    # Non-secret fields stay readable.
    assert encrypted["pgsql"]["instances"][0]["host"] == "orders-uat.example.test"
    assert decrypt_sensitive_fields(encrypted) == config


# --------------------------------------------------------------------------
# Settings form parser
# --------------------------------------------------------------------------


def _nexus_form(**overrides):
    form = {
        "__touch_nexus": "1",
        "nexus_enabled": "on",
        "nexus_default_instance": "main",
        "nexus_instance_count": "2",
        "nexus_instances_0_enabled": "1",
        "nexus_instances_0_original_name": "",
        "nexus_instances_0_original_url": "",
        "nexus_instances_0_name": "main",
        "nexus_instances_0_url": "https://nexus.example.test/",
        "nexus_instances_0_username": "svc-reader",
        "nexus_instances_0_password": "nexus-pass",
        "nexus_instances_1_name": "mirror",
        "nexus_instances_1_url": "https://mirror.example.test",
        "nexus_instances_1_token": "mirror-token",
    }
    form.update(overrides)
    return form


def _splunk_form(**overrides):
    form = {
        "__touch_splunk": "1",
        "splunk_enabled": "on",
        "splunk_default_instance": "prod",
        "splunk_instance_count": "1",
        "splunk_instances_0_enabled": "1",
        "splunk_instances_0_name": "prod",
        "splunk_instances_0_url": "https://splunk-api.example.test:8089",
        "splunk_instances_0_token": "splunk-token",
        "splunk_instances_0_default_index": "app_prod",
        "splunk_instances_0_default_earliest": "-1h",
        "splunk_instances_0_max_results": "1000",
    }
    form.update(overrides)
    return form


def _pgsql_form(**overrides):
    form = {
        "__touch_pgsql": "1",
        "pgsql_enabled": "on",
        "pgsql_default_instance": "orders-uat",
        "pgsql_instance_count": "1",
        "pgsql_instances_0_enabled": "1",
        "pgsql_instances_0_original_name": "",
        "pgsql_instances_0_name": "orders-uat",
        "pgsql_instances_0_host": "orders-uat.example.test",
        "pgsql_instances_0_port": "5432",
        "pgsql_instances_0_database": "orders",
        "pgsql_instances_0_username": "efp_readonly",
        "pgsql_instances_0_password": "pg-pass",
        "pgsql_instances_0_sslmode": "require",
    }
    form.update(overrides)
    return form


FORMS = {"nexus": _nexus_form, "splunk": _splunk_form, "pgsql": _pgsql_form}


def test_settings_form_builds_the_nexus_section_from_the_indexed_fields():
    merged, error = _settings_merge_payload({}, _nexus_form())

    assert error is None
    assert merged["nexus"] == {
        "enabled": True,
        "default_instance": "main",
        "instances": [
            {"name": "main", "url": "https://nexus.example.test", "username": "svc-reader", "password": "nexus-pass", "enabled": True},
            # No checkbox posted for the second row: it was switched off.
            {"name": "mirror", "url": "https://mirror.example.test", "token": "mirror-token", "enabled": False},
        ],
    }


@pytest.mark.parametrize("section", ("splunk", "pgsql"))
def test_settings_form_builds_the_canonical_section(section):
    merged, error = _settings_merge_payload({}, FORMS[section]())

    assert error is None
    assert merged[section] == CANONICAL[section]


@pytest.mark.parametrize("section", SECTIONS)
def test_settings_form_preserves_a_blank_secret_from_the_existing_instance(section):
    """A blank password/token field means "unchanged" - it must not wipe the secret."""
    instance = CANONICAL[section]["instances"][0]
    secret_field = "password" if "password" in instance else "token"
    form = FORMS[section](**{f"{section}_instance_count": "1"})
    form.pop(f"{section}_instances_0_{secret_field}", None)
    form[f"{section}_instances_0_original_name"] = instance["name"]
    if "url" in instance:
        form[f"{section}_instances_0_original_url"] = instance["url"]

    merged, error = _settings_merge_payload({section: _copy(CANONICAL[section])}, form)

    assert error is None
    assert merged[section]["instances"][0][secret_field] == instance[secret_field]


@pytest.mark.parametrize("section", SECTIONS)
def test_settings_form_can_clear_a_secret_explicitly(section):
    instance = CANONICAL[section]["instances"][0]
    secret_field = "password" if "password" in instance else "token"
    form = FORMS[section](**{f"{section}_instance_count": "1"})
    form[f"{section}_instances_0_{secret_field}"] = ""
    form[f"{section}_instances_0_{secret_field}_clear"] = "1"
    form[f"{section}_instances_0_original_name"] = instance["name"]

    merged, error = _settings_merge_payload({section: _copy(CANONICAL[section])}, form)

    assert error is None
    assert secret_field not in merged[section]["instances"][0]


@pytest.mark.parametrize("section", SECTIONS)
def test_settings_form_without_the_rows_keeps_the_stored_ones(section):
    """A client that posts the section without its instance rows (an older
    panel, a partial form) must not wipe the instances."""
    merged, error = _settings_merge_payload(
        {section: _copy(CANONICAL[section])},
        {f"__touch_{section}": "1", f"{section}_enabled": ""},
    )

    assert error is None
    assert merged[section]["enabled"] is False
    assert merged[section]["instances"] == CANONICAL[section]["instances"]
    assert merged[section]["default_instance"] == CANONICAL[section]["default_instance"]


@pytest.mark.parametrize("section", SECTIONS)
def test_settings_form_untouched_section_is_left_alone(section):
    merged, error = _settings_merge_payload(
        {section: _copy(CANONICAL[section])},
        {"__touch_debug": "1", "debug_enabled": "on", "debug_log_level": "INFO"},
    )
    assert error is None
    assert merged[section] == CANONICAL[section]


def test_settings_form_blank_default_instance_clears_it():
    merged, error = _settings_merge_payload({"nexus": _copy(CANONICAL["nexus"])}, _nexus_form(nexus_default_instance=""))
    assert error is None
    assert "default_instance" not in merged["nexus"]


def test_settings_form_rejects_a_row_without_a_name_instead_of_dropping_it():
    merged, error = _settings_merge_payload({}, _nexus_form(nexus_instances_1_name=""))
    assert error == "Nexus instance 2 needs a name; the assistant addresses it with --instance."
    assert "nexus" not in merged

    merged, error = _settings_merge_payload({}, _pgsql_form(pgsql_instances_0_name=""))
    assert error == "PostgreSQL instance 1 needs a name; the assistant addresses it with --instance."
    assert "pgsql" not in merged


def test_settings_form_drops_an_empty_card_the_member_added_and_left_alone():
    merged, error = _settings_merge_payload(
        {},
        _nexus_form(nexus_instances_1_name="", nexus_instances_1_url="", nexus_instances_1_token=""),
    )
    assert error is None
    assert [row["name"] for row in merged["nexus"]["instances"]] == ["main"]


def test_settings_form_rejects_duplicate_names_case_insensitively():
    merged, error = _settings_merge_payload({}, _nexus_form(nexus_instances_1_name="MAIN"))
    assert error == "Nexus instance names must be unique; MAIN is listed more than once."
    assert "nexus" not in merged


def test_settings_form_rejects_a_row_the_cli_could_not_reach():
    merged, error = _settings_merge_payload({}, _splunk_form(splunk_instances_0_url=""))
    assert error == "Splunk instance prod needs a URL."
    assert "splunk" not in merged

    for field in ("host", "database", "username"):
        merged, error = _settings_merge_payload({}, _pgsql_form(**{f"pgsql_instances_0_{field}": ""}))
        assert error == f"PostgreSQL instance orders-uat needs a {field}.", field
        assert "pgsql" not in merged


@pytest.mark.parametrize("port", ["0", "65536", "abc", "-1"])
def test_settings_form_rejects_a_bad_pgsql_port(port):
    merged, error = _settings_merge_payload({}, _pgsql_form(pgsql_instances_0_port=port))
    assert error == "PostgreSQL instance orders-uat needs a port between 1 and 65535."
    assert "pgsql" not in merged


def test_settings_form_blank_pgsql_port_leaves_the_runtime_default():
    merged, error = _settings_merge_payload({}, _pgsql_form(pgsql_instances_0_port=""))
    assert error is None
    assert "port" not in merged["pgsql"]["instances"][0]


@pytest.mark.parametrize("count", ["0", "10001", "many"])
def test_settings_form_rejects_a_splunk_max_results_outside_the_bounds(count):
    merged, error = _settings_merge_payload({}, _splunk_form(splunk_instances_0_max_results=count))
    assert error == "Splunk instance prod needs a max results count between 1 and 10000."
    assert "splunk" not in merged


def test_settings_form_rejects_an_unknown_sslmode():
    merged, error = _settings_merge_payload({}, _pgsql_form(pgsql_instances_0_sslmode="disable"))
    assert error == "PostgreSQL instance orders-uat needs an SSL mode of require, verify-ca, verify-full, prefer."
    assert "pgsql" not in merged


def test_settings_form_rejects_a_default_instance_that_names_no_row():
    merged, error = _settings_merge_payload({}, _nexus_form(nexus_default_instance="other"))
    assert error == "Default Nexus instance other must be the name of one of the instances listed below."
    assert "nexus" not in merged

    merged, error = _settings_merge_payload({}, _nexus_form(nexus_default_instance="MIRROR"))
    assert error is None
    assert merged["nexus"]["default_instance"] == "MIRROR"


def test_view_payload_exposes_each_section_and_its_instances():
    payload = _settings_view_payload({s: _copy(CANONICAL[s]) for s in SECTIONS})
    for section in SECTIONS:
        assert payload[section]["default_instance"] == CANONICAL[section]["default_instance"]
        assert payload[f"{section}_instances"] == CANONICAL[section]["instances"]
    # A profile without the sections renders no cards, not an error.
    empty = _settings_view_payload({})
    for section in SECTIONS:
        assert empty[f"{section}_instances"] == []
        assert empty[section] == {"enabled": False, "instances": []}
    assert empty["troubleshooting_cards"]["rows"] == TROUBLESHOOTING_CARD_ROWS


# --------------------------------------------------------------------------
# End to end through both save routes
# --------------------------------------------------------------------------


@pytest.mark.parametrize("section", SECTIONS)
def test_end_to_end_profile_save_persists_the_section_and_reloads_it_into_the_panel(monkeypatch, section):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {section: {"enabled": False}})
        form = FORMS[section](**{f"{section}_instance_count": "1"})
        resp = client.post(f"/app/runtime-profiles/{rp.id}/save", data={"name": "rp", **form})
        assert resp.status_code == 200
        db.refresh(rp)
        assert json.loads(rp.config_json)[section] == CANONICAL[section]

        # The re-rendered panel carries the rows back so the next save round-trips.
        html = resp.text
        cards = [card for card in _parse_cards(html) if card["group"] == section]
        assert len(cards) == 1
        assert f'name="{section}_instance_count" value="1"' in html
        instance = CANONICAL[section]["instances"][0]
        assert cards[0]["fields"]["name"]["value"] == instance["name"]
        assert cards[0]["attrs"]["data-original-field"] == (["name", "url"] if "url" in instance else ["name"])
        assert f'name="{section}_default_instance" value="{instance["name"]}"' in html

        # A later save that posts neither the secrets nor the rows keeps both.
        again = client.post(
            f"/app/runtime-profiles/{rp.id}/save",
            data={"name": "rp", f"__touch_{section}": "1", f"{section}_enabled": "on"},
        )
        assert again.status_code == 200
        db.refresh(rp)
        assert json.loads(rp.config_json)[section] == CANONICAL[section]
    finally:
        cleanup()


def test_end_to_end_profile_save_reports_a_bad_row_and_keeps_the_stored_config(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {"pgsql": {"enabled": True}})
        resp = client.post(
            f"/app/runtime-profiles/{rp.id}/save",
            data={"name": "rp", **_pgsql_form(pgsql_instances_0_port="99999")},
        )
        assert resp.status_code == 200
        assert "PostgreSQL instance orders-uat needs a port between 1 and 65535." in resp.text
        db.refresh(rp)
        assert json.loads(rp.config_json)["pgsql"] == {"enabled": True}
    finally:
        cleanup()


def test_end_to_end_settings_save_persists_the_sections_through_the_assistant_panel(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {})
        data = {}
        for section in SECTIONS:
            data.update(FORMS[section](**{f"{section}_instance_count": "1"}))
        resp = client.post(f"/app/agents/{agent.id}/settings/save", data=data)
        assert resp.status_code == 200
        db.refresh(rp)
        stored = json.loads(rp.config_json)
        for section in SECTIONS:
            assert stored[section] == CANONICAL[section], section
    finally:
        cleanup()


# --------------------------------------------------------------------------
# Test-connection routes accept the new targets
# --------------------------------------------------------------------------


def test_the_test_routes_know_every_section():
    assert {"jenkins", "nexus", "splunk", "pgsql"} <= _MANAGED_TEST_TARGETS


@pytest.mark.parametrize("target", ["jenkins", "nexus", "splunk", "pgsql"])
def test_the_profile_test_route_runs_the_target_against_the_submitted_form(monkeypatch, target):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {})
        # Disabled sections fail fast inside the service, so no network is touched.
        resp = client.post(f"/app/runtime-profiles/{rp.id}/test/{target}", data={f"__touch_{target}": "1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False and body["target"] == target
        assert f"{target}.enabled=true" in body["message"]

        agent_resp = client.post(f"/app/agents/{agent.id}/settings/test/{target}", data={})
        assert agent_resp.status_code == 200
        assert agent_resp.json()["target"] == target
    finally:
        cleanup()


# --------------------------------------------------------------------------
# Projection: when the CLI instructions are worth adding, and what they say
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "section,config,expected",
    [
        ("nexus", {"enabled": True, "instances": [{"name": "m", "url": "https://n"}]}, True),
        ("nexus", {"enabled": True, "instances": [{"name": "m", "url": "https://n", "enabled": False}]}, False),
        ("nexus", {"enabled": False, "instances": [{"name": "m", "url": "https://n"}]}, False),
        ("nexus", {"enabled": True, "instances": []}, False),
        ("splunk", {"enabled": True, "instances": [{"name": "p", "url": "https://s:8089", "token": "t"}]}, True),
        ("splunk", {"enabled": True, "instances": [{"name": "p", "token": "t"}]}, False),
        ("splunk", {"enabled": True}, False),
        ("pgsql", {"enabled": True, "instances": [{"name": "d", "host": "h", "database": "o", "username": "u"}]}, True),
        ("pgsql", {"enabled": True, "instances": [{"name": "d", "host": "h", "enabled": False}]}, False),
        ("pgsql", {"enabled": True, "instances": [{"name": "d", "database": "o", "username": "u"}]}, False),
        ("pgsql", {"enabled": False, "instances": [{"name": "d", "host": "h"}]}, False),
    ],
)
def test_a_section_counts_as_configured_only_with_a_reachable_enabled_instance(section, config, expected):
    assert _has_enabled_external_cli_config({section: config}) is expected
    projected = project_canonical_for_runtime({section: config}, "native")
    assert (RUNTIME_PROFILE_CLI_TOOL_INSTRUCTIONS in (projected.get("instruction_texts") or [])) is expected


def test_the_sections_reach_the_runtime_through_the_canonical_config():
    from app.services.runtime_profile_context_projection import build_runtime_profile_context_config

    projected = build_runtime_profile_context_config({s: _copy(CANONICAL[s]) for s in SECTIONS}, runtime_type="native")
    for section in SECTIONS:
        assert projected[section] == CANONICAL[section]
    opencode = build_runtime_profile_context_config({s: _copy(CANONICAL[s]) for s in SECTIONS}, runtime_type="opencode")
    for section in SECTIONS:
        assert opencode[section] == CANONICAL[section]
    assert "instruction_texts" not in opencode


def test_cli_instructions_teach_each_troubleshooting_cli_verbatim():
    text = RUNTIME_PROFILE_CLI_TOOL_INSTRUCTIONS
    expected = (
        "Use nexus for Nexus Repository artifacts (`nexus repo list --json`, `nexus component search --repository <repo> "
        "--name <artifact> --version <ver> --json`), splunk for log searches (`splunk search run --query \"index=<idx> ...\" "
        "--earliest -1h --count 100 --json`; always give a time range and a count), and pgsql for PostgreSQL "
        "(`pgsql schema tables --json`, `pgsql query --sql \"select ...\" --limit 200 --json`; "
        "`pgsql exec` applies statements that change data, and whether that succeeds is decided by the database role "
        "and endpoint this profile configures, not by the CLI). "
        "For every nexus, splunk, and pgsql command add --json and use --instance when "
        "several instances are configured. "
    )
    assert expected in text
    # Placed after the AWS guidance and before the generic write-safety rule.
    assert text.index("Avoid changing cloud resources unless the user asks. ") < text.index("Use nexus for")
    assert text.index("Use nexus for") < text.index("Run write operations with --dry-run")


# --------------------------------------------------------------------------
# Guidance, help, labels and defaults
# --------------------------------------------------------------------------


@pytest.mark.parametrize("section", SECTIONS)
def test_guidance_and_help_describe_what_the_connection_can_do(section):
    guidance = CONNECTION_GUIDANCE[section]
    assert guidance["title"].startswith("Connect ")
    summary = guidance["summary"].lower()
    if section == "pgsql":
        # pgsql is not read-only by construction: the role and the endpoint an
        # admin enters decide that, so the guidance has to say so rather than
        # promise something the tool does not enforce.
        assert "role" in summary and "decide" in summary
        steps = " ".join(guidance["steps"]).lower()
        assert "select and nothing else" in steps and "read-only" in steps
    else:
        assert "read-only" in summary
    assert len(guidance["steps"]) >= 3
    assert guidance["user_fields"]

    topic = get_topic(f"connect-{section}")
    assert topic is not None
    assert topic.connection_section == section
    assert list(topic.steps) == list(guidance["steps"])
    assert topic.icon != "plug", "the markdown file must name its own icon"
    body = topic.body.lower()
    assert f"`{section}`" in topic.body, "the guide names the CLI the assistant runs"
    assert "--instance" in topic.body
    assert "read" in body
    assert "README" not in topic.body


def test_guidance_says_what_to_enter():
    assert "8089" in " ".join(CONNECTION_GUIDANCE["splunk"]["steps"])
    assert "token" in " ".join(CONNECTION_GUIDANCE["splunk"]["steps"]).lower()
    pgsql_steps = " ".join(CONNECTION_GUIDANCE["pgsql"]["steps"])
    assert "5432" in pgsql_steps and "role is the control" in pgsql_steps
    readme = Path("app/help/README.md").read_text(encoding="utf-8")
    for section in SECTIONS:
        assert section in readme


def test_default_config_covers_the_sections():
    defaults = RuntimeProfileService.default_profile_config()
    for section in SECTIONS:
        assert defaults[section] == {"enabled": False, "instances": []}


# --------------------------------------------------------------------------
# Default Connections (admin seed)
# --------------------------------------------------------------------------


def test_default_connections_form_offers_the_sections_and_reads_them_back():
    html = _default_connections_html(seed={s: _copy(CANONICAL[s]) for s in SECTIONS})
    for section, heading in (("nexus", "Nexus Repository"), ("splunk", "Splunk"), ("pgsql", "PostgreSQL")):
        assert f"<h6>{heading}</h6>" in html, section
        assert f'data-instance-container="{section}"' in html
        assert f'name="{section}_instance_count" value="1"' in html
        assert f'name="{section}_default_instance"' in html
        assert f'name="{section}_enabled" checked' in html
    assert 'value="orders-uat.example.test"' in html
    assert 'value="app_prod"' in html
    assert '<option value="require" selected>require</option>' in html

    config = _seed_config_from_form(
        FormData(
            [
                ("pgsql_enabled", "on"),
                ("pgsql_default_instance", "orders-uat"),
                ("pgsql_instance_count", "1"),
                ("pgsql_instances_0_enabled", "1"),
                ("pgsql_instances_0_name", "orders-uat"),
                ("pgsql_instances_0_host", "orders-uat.example.test"),
                ("pgsql_instances_0_port", "5432"),
                ("pgsql_instances_0_database", "orders"),
                ("pgsql_instances_0_username", "efp_readonly"),
                ("pgsql_instances_0_password", ""),
                ("pgsql_instances_0_sslmode", "require"),
                ("splunk_instance_count", "1"),
                ("splunk_instances_0_name", "prod"),
                ("splunk_instances_0_url", "https://splunk-api.example.test:8089"),
                ("splunk_instances_0_token", "shared-token"),
                ("splunk_instances_0_max_results", "500"),
                ("nexus_instance_count", "0"),
            ]
        )
    )
    # A blank shared password is left out rather than stored as "".
    assert config["pgsql"] == {
        "enabled": True,
        "default_instance": "orders-uat",
        "instances": [
            {
                "enabled": True,
                "name": "orders-uat",
                "host": "orders-uat.example.test",
                "port": "5432",
                "database": "orders",
                "username": "efp_readonly",
                "sslmode": "require",
                # Rendered but left blank: a non-credential field keeps the empty
                # string, the same as port or sslmode would.
                "statement_timeout_seconds": "",
                "max_rows": "",
            }
        ],
    }
    # A seeded credential alone is enough to store the section, toggle off.
    assert config["splunk"]["enabled"] is False
    assert config["splunk"]["instances"][0]["token"] == "shared-token"
    assert "nexus" not in config
    # The seed becomes a member's first profile through the sanitizer, which
    # turns the typed numbers into the runtime's int shape.
    sanitized = sanitize_runtime_profile_config_dict(config)
    assert sanitized["pgsql"]["instances"][0]["port"] == 5432
    assert sanitized["splunk"]["instances"][0]["max_results"] == 500


def test_an_untouched_default_connections_section_stores_nothing():
    assert _seed_config_from_form(FormData([(f"{s}_instance_count", "0") for s in SECTIONS])) == {}


# --------------------------------------------------------------------------
# UI: rendered cards on every panel that edits the sections
# --------------------------------------------------------------------------


def _one_instance_profile(section):
    return {section: {"enabled": True, "instances": [_copy(CANONICAL[section]["instances"][0])]}}


@pytest.mark.parametrize("section", SECTIONS)
def test_settings_panel_renders_one_card_per_instance(monkeypatch, section):
    html = _render_panel(monkeypatch, _one_instance_profile(section))
    cards = [card for card in _parse_cards(html) if card["group"] == section]
    instance = CANONICAL[section]["instances"][0]

    assert len(cards) == 1
    assert f'name="{section}_instance_count" value="1"' in html
    assert f'data-instance-group="{section}"' in html
    assert f'data-action="add-instance" data-group="{section}"' in html
    assert f'data-test-target="{section}"' in html
    assert f'data-touch-flag="{section}"' in html
    assert cards[0]["fields"]["enabled"]["aria-label"] == f"Enable {LABELS[section]} instance 1"
    assert "Instance 1" in cards[0]["text"]
    for field, value in instance.items():
        if field == "enabled":
            continue
        rendered = cards[0]["fields"][field]
        if rendered.get("type") in ("text", "password", "number"):
            assert rendered["value"] == str(value), field
    # The card posts exactly the fields the parser reads, no more.
    expected_fields = sorted(field for row in TROUBLESHOOTING_CARD_ROWS[section] for field in row if field) + ["enabled"]
    assert sorted(cards[0]["fields"]) == sorted(expected_fields)
    secrets = [field for field, attrs in cards[0]["fields"].items() if attrs.get("type") == "password"]
    assert secrets == sorted(f for f in ("password", "token") if f in cards[0]["fields"])


@pytest.mark.parametrize("section", SECTIONS)
def test_agent_and_runtime_profile_panels_render_the_same_card(monkeypatch, section):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        _bind_profile(db, agent, _one_instance_profile(section))
        agent_html = client.get(f"/app/agents/{agent.id}/settings/panel").text
        profile_html = client.get(f"/app/runtime-profiles/{agent.runtime_profile_id}/panel").text
    finally:
        cleanup()

    agent_card = next(c for c in _parse_cards(agent_html) if c["group"] == section)
    profile_card = next(c for c in _parse_cards(profile_html) if c["group"] == section)
    assert _shape(agent_card) == _shape(profile_card)
    assert agent_card["fields"] == profile_card["fields"]
    assert agent_card["text"] == profile_card["text"]
    assert f'id="profile-section-{section}"' in profile_html
    assert f'href="#/help/connect-{section}"' in profile_html


def _js_troubleshooting_card_html(section):
    """Evaluate troubleshootingCardHtml for ``section`` in Python.

    The head comes straight from the function's template literal; the body is
    rebuilt from the JS layout tables with the same per-field fragments
    troubleshootingFieldHtml emits, each guarded against drift below.
    """
    js = _js_source()
    rows = _js_object_literal(js, "INSTANCE_GROUP_CARD_ROWS")[section]
    specs = _js_object_literal(js, "INSTANCE_GROUP_FIELD_SPECS")
    placeholders = _js_object_literal(js, "INSTANCE_GROUP_PLACEHOLDERS")[section]
    label = _js_object_literal(js, "INSTANCE_GROUP_LABELS")[section]

    field_start = js.index("function troubleshootingFieldHtml(")
    field_fn = js[field_start : js.index("\nfunction ", field_start + 1)]
    select_fragment = '<select data-field="${field}" class="portal-form-select">${options}</select>'
    option_fragment = '<option value="${value}"${index === 0 ? " selected" : ""}>${text}</option>'
    number_fragment = (
        '<input type="number" data-field="${field}" value="" placeholder="${placeholder}" '
        'min="${spec.min}" max="${spec.max}" step="1" class="portal-form-input" />'
    )
    text_fragment = '<input type="${spec.type || "text"}" data-field="${field}" value="" placeholder="${placeholder}" class="portal-form-input" />'
    for fragment in (select_fragment, option_fragment, number_fragment, text_fragment, "`<div></div>`"):
        assert fragment in field_fn, fragment

    def field_html(field):
        if not field:
            return "<div></div>"
        spec = specs.get(field, {})
        placeholder = placeholders.get(field, "")
        if spec.get("type") == "select":
            options = "".join(
                f'<option value="{value}"{" selected" if index == 0 else ""}>{text}</option>'
                for index, (value, text) in enumerate(spec["options"])
            )
            return f'<select data-field="{field}" class="portal-form-select">{options}</select>'
        if spec.get("type") == "number":
            return (
                f'<input type="number" data-field="{field}" value="" placeholder="{placeholder}" '
                f'min="{spec["min"]}" max="{spec["max"]}" step="1" class="portal-form-input" />'
            )
        return f'<input type="{spec.get("type", "text")}" data-field="{field}" value="" placeholder="{placeholder}" class="portal-form-input" />'

    card_start = js.index("function troubleshootingCardHtml(")
    card_fn = js[card_start : js.index("\nfunction ", card_start + 1)]
    literal_match = re.search(r"return `(.*?)`;", card_fn, flags=re.S)
    assert literal_match, "troubleshootingCardHtml no longer builds the card from a template literal"
    assert '`<div class="grid grid-cols-2 gap-2">${row.map((field) => troubleshootingFieldHtml(group, field)).join("")}</div>`' in card_fn
    assert '`<input type="hidden" data-original-field="url" value="" />`' in card_fn

    has_url = any("url" in row for row in rows)
    body = "".join(f'<div class="grid grid-cols-2 gap-2">{"".join(field_html(f) for f in row)}</div>' for row in rows)
    rendered = (
        literal_match.group(1)
        .replace("${originalUrlHtml}", '<input type="hidden" data-original-field="url" value="" />' if has_url else "")
        .replace("${bodyHtml}", body)
        .replace("${label}", label)
        .replace("${group}", section)
    )
    assert "${" not in rendered, "unresolved template slot in troubleshootingCardHtml literal"
    # normalizeInstanceInputs renumbers the freshly appended card.
    rendered = rendered.replace(
        '<span class="portal-settings-instance-title">Instance</span>',
        '<span class="portal-settings-instance-title">Instance 1</span>',
    ).replace(f'aria-label="Enable {label} instance"', f'aria-label="Enable {label} instance 1"')
    return f'<div class="portal-settings-instance-card" data-instance-item="{section}">{rendered}</div>'


@pytest.mark.parametrize("section", SECTIONS)
def test_js_added_card_matches_the_server_rendered_card(monkeypatch, section):
    """A row added via data-action="add-instance" must be structurally identical
    to a saved row, otherwise the two look different side by side."""
    minimal = {"name": "one", "enabled": True}
    minimal["host" if section == "pgsql" else "url"] = "one.example.test" if section == "pgsql" else "https://one.example.test"
    if section == "pgsql":
        minimal.update({"database": "db", "username": "ro"})
    server_html = _render_panel(monkeypatch, {section: {"enabled": True, "instances": [minimal]}})
    server_card = next(c for c in _parse_cards(server_html) if c["group"] == section)
    js_card = next(c for c in _parse_cards(_js_troubleshooting_card_html(section)) if c["group"] == section)

    assert _shape(js_card) == _shape(server_card)
    assert sorted(js_card["fields"]) == sorted(server_card["fields"])
    assert js_card["attrs"] == server_card["attrs"]
    assert js_card["text"] == server_card["text"]
    for field, attributes in js_card["fields"].items():
        assert attributes.get("type") == server_card["fields"][field].get("type"), field
        assert attributes.get("placeholder") == server_card["fields"][field].get("placeholder"), field
        assert attributes.get("class") == server_card["fields"][field].get("class"), field


def test_the_js_layout_tables_match_the_server_layout():
    js = _js_source()
    assert _js_object_literal(js, "INSTANCE_GROUP_CARD_ROWS") == TROUBLESHOOTING_CARD_ROWS
    assert _js_object_literal(js, "INSTANCE_GROUP_FIELD_SPECS") == TROUBLESHOOTING_CARD_FIELD_SPECS
    placeholders = _js_object_literal(js, "INSTANCE_GROUP_PLACEHOLDERS")
    labels = _js_object_literal(js, "INSTANCE_GROUP_LABELS")
    for section in SECTIONS:
        assert placeholders[section] == TROUBLESHOOTING_CARD_PLACEHOLDERS[section], section
        assert labels[section] == LABELS[section]
        assert section in _initialized_instance_groups(js)


def test_add_instance_row_builds_the_card_for_every_troubleshooting_group():
    js = _js_source()
    start = js.index("function addInstanceRow(")
    body = js[start : js.index("\nfunction ", start + 1)]
    assert "if (INSTANCE_GROUP_CARD_ROWS[group])" in body
    assert "troubleshootingCardHtml(group, instanceGroupLabel(group))" in body
    # The generic card (name/url/credentials) is still what jira/confluence/jenkins get.
    assert 'data-field="url"' in body


@pytest.mark.parametrize(
    "template",
    [
        "app/templates/partials/runtime_profile_panel.html",
        "app/templates/partials/settings_panel.html",
        "app/templates/partials/default_connections_panel.html",
    ],
)
def test_every_panel_renders_the_three_groups(template):
    groups = _instance_groups_in_template(template)
    assert set(SECTIONS) <= groups
    # The seed form builds its add button inside the instance_group macro, so
    # the rendered page is what carries the literal group name there.
    text = Path(template).read_text(encoding="utf-8")
    rendered = _default_connections_html() if template.endswith("default_connections_panel.html") else text
    for section in SECTIONS:
        assert f'name="{section}_enabled"' in text
        assert f'name="{section}_default_instance"' in text
        assert f'data-action="add-instance" data-group="{section}"' in rendered


def test_both_member_panels_carry_a_test_button_and_a_touch_flag_per_section():
    for template in ("app/templates/partials/runtime_profile_panel.html", "app/templates/partials/settings_panel.html"):
        text = Path(template).read_text(encoding="utf-8")
        for section in ("jenkins", *SECTIONS):
            assert f'data-test-target="{section}"' in text, (template, section)
            assert f'data-test-result="{section}"' in text, (template, section)
        for section in SECTIONS:
            assert f'name="__touch_{section}" value="0" data-touch-flag="{section}"' in text, (template, section)
            assert f'data-managed-section="{section}"' in text, (template, section)


def test_tooltips_cover_the_new_inputs_and_beat_the_generic_instance_hints():
    from tests.test_tooltips import _registry_entries

    entries = _registry_entries()
    selectors = [entry[1] for entry in entries]
    for section in ("jenkins", *SECTIONS):
        assert f'[data-test-target="{section}"]' in selectors, section
    for section in SECTIONS:
        for selector in (
            f'input[name="{section}_enabled"]',
            f'input[name="{section}_default_instance"]',
            f'[data-action="add-instance"][data-group="{section}"]',
            f'[data-instance-item="{section}"] [data-field="username"]',
        ):
            assert selector in selectors, selector
    for selector in (
        '[data-instance-item="splunk"] [data-field="default_index"]',
        '[data-instance-item="splunk"] [data-field="default_earliest"]',
        '[data-instance-item="splunk"] [data-field="max_results"]',
        '[data-instance-item="pgsql"] [data-field="host"]',
        '[data-instance-item="pgsql"] [data-field="port"]',
        '[data-instance-item="pgsql"] [data-field="database"]',
        '[data-instance-item="pgsql"] [data-field="sslmode"]',
        '[data-instance-item="pgsql"] [data-field="password"]',
    ):
        assert selector in selectors, selector
    # First match wins, so the scoped hints must come before the generic ones.
    generic_username = selectors.index('[data-instance-item] [data-field="username"]')
    generic_name = selectors.index('[data-instance-item] [data-field="name"]')
    generic_password = selectors.index('[data-instance-item] [data-field="password"]')
    for section in SECTIONS:
        assert selectors.index(f'[data-instance-item="{section}"] [data-field="username"]') < generic_username, section
    assert selectors.index('[data-instance-item="pgsql"] [data-field="password"]') < generic_password
    scoped_name = next(i for i, selector in enumerate(selectors) if '[data-instance-item="pgsql"] [data-field="name"]' in selector)
    assert scoped_name < generic_name
