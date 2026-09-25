"""Behaviour tests for the AWS account matrix on the runtime-profile aws section.

The aws section used to hold one directory account (domain/username/password).
The aws-auth CLI in the runtime now signs in to several AWS accounts, so the
section also carries the provider it signs in with, session and kubeconfig
settings, and one row per account (name, 12-digit id, role, regions). Covers
the whole Portal path: schema sanitizer, public redaction, the settings form
parser and its validation, the end-to-end save, the projection rule that
decides whether the CLI instructions are worth adding, and the rendered cards
(server-rendered and JS-added) on every panel that edits the section.
"""
import json
import re
from pathlib import Path

import pytest
from starlette.datastructures import FormData

from tests.test_default_connections_form import _panel_html as _default_connections_html
from tests.test_jenkins_multi_instance_settings import (
    _initialized_instance_groups,
    _js_object_literal,
    _js_source,
    _parse_cards,
    _render_panel,
    _shape,
)
from tests.test_web_runtime_profile_settings import _bind_profile, _build_client

from app.schemas.runtime_profile import (
    PORTAL_MANAGED_FIELD_TREE,
    redact_runtime_profile_config_for_public_response,
    sanitize_runtime_profile_aws,
    sanitize_runtime_profile_config_dict,
)
from app.services.connection_guidance import CONNECTION_GUIDANCE
from app.services.help_center import get_topic
from app.services.runtime_profile_context_projection import (
    RUNTIME_PROFILE_CLI_TOOL_INSTRUCTIONS,
    _has_enabled_aws_config,
    project_canonical_for_runtime,
)
from app.web import _seed_config_from_form, _settings_merge_payload, _settings_view_payload

IDP_URL = "https://adfs.example.test/adfs/ls/IdpInitiatedSignOn.aspx?loginToRp=urn:amazon:webservices"

FULL_AWS_SECTION = {
    "enabled": True,
    "provider": "adfs-assume",
    "domain": "HBEU",
    "username": "GB-SVC-XXX",
    "password": "adfs-password",
    "idp_url": IDP_URL,
    "source_profile": "base",
    "default_account": "cps-dev",
    "default_region": "ap-east-1",
    "session_duration_seconds": 3600,
    "kubeconfig_path": "~/.efp/kube/config",
    "accounts": [
        {
            "name": "cps-dev",
            "account_id": "818354133892",
            "role": "ADFS-ReadOnly",
            "regions": ["ap-east-1", "eu-west-1"],
            "enabled": True,
        },
        {
            "name": "cps-prod",
            "account_id": "123456789012",
            "role": "ADFS-ReadOnly",
            "role_arn": "arn:aws:iam::123456789012:role/ADFS-ReadOnly",
            "profile": "prod-ro",
            "regions": ["eu-west-1"],
            "enabled": False,
        },
    ],
}


# --------------------------------------------------------------------------
# Schema sanitizer
# --------------------------------------------------------------------------


def test_sanitizer_accepts_the_full_shape_and_normalizes_regions_typed_as_a_string():
    raw = json.loads(json.dumps(FULL_AWS_SECTION))
    raw["provider"] = " ADFS-Assume "
    raw["session_duration_seconds"] = "3600"
    # The form posts one comma-separated string; whitespace separators, blanks
    # and repeats are tolerated, order is kept.
    raw["accounts"][0]["regions"] = " ap-east-1, eu-west-1 ap-east-1 ,, "
    raw["accounts"][1]["enabled"] = "off"
    raw["accounts"][1]["junk"] = "drop-me"

    sanitized = sanitize_runtime_profile_config_dict({"aws": raw})

    assert sanitized["aws"] == FULL_AWS_SECTION


def test_sanitizer_survives_a_round_trip():
    once = sanitize_runtime_profile_config_dict({"aws": FULL_AWS_SECTION})
    assert sanitize_runtime_profile_config_dict(once) == once


def test_account_ids_are_stored_as_twelve_digit_strings():
    sanitized = sanitize_runtime_profile_aws(
        {"accounts": [{"name": "num", "account_id": 818354133892}, {"name": "padded", "account_id": " 000000000001 "}]}
    )
    assert [row["account_id"] for row in sanitized["accounts"]] == ["818354133892", "000000000001"]


def test_sanitizer_drops_rows_the_runtime_would_refuse():
    sanitized = sanitize_runtime_profile_aws(
        {
            "accounts": [
                {"name": "eleven", "account_id": "81835413389"},
                {"name": "letters", "account_id": "81835413389a"},
                {"name": "blank-id", "account_id": ""},
                {"name": "", "account_id": "818354133892"},
                {"name": "keep", "account_id": "818354133892", "role": " ADFS-ReadOnly "},
                {"name": "KEEP", "account_id": "999999999999"},
                "not-a-row",
            ]
        }
    )
    # Only the well-formed row survives; the duplicate name (case-insensitive)
    # loses to the first occurrence.
    assert sanitized["accounts"] == [{"name": "keep", "account_id": "818354133892", "role": "ADFS-ReadOnly"}]


@pytest.mark.parametrize("provider", ["okta", "", None, 3])
def test_an_unknown_provider_is_dropped_rather_than_stored(provider):
    assert "provider" not in sanitize_runtime_profile_aws({"provider": provider, "domain": "HBEU"})


@pytest.mark.parametrize("duration", [899, 43201, "abc", "", None, True, "12.5", -3600])
def test_an_out_of_range_session_duration_is_dropped(duration):
    assert "session_duration_seconds" not in sanitize_runtime_profile_aws({"session_duration_seconds": duration})


@pytest.mark.parametrize("duration,expected", [(900, 900), ("43200", 43200), (" 3600 ", 3600)])
def test_a_session_duration_inside_the_sts_bounds_is_kept_as_an_int(duration, expected):
    assert sanitize_runtime_profile_aws({"session_duration_seconds": duration})["session_duration_seconds"] == expected


def test_the_accounts_key_is_kept_only_when_it_was_sent():
    # An empty list is how the member clears the matrix; a section that never
    # mentioned accounts must not grow the key.
    assert sanitize_runtime_profile_aws({"enabled": True, "accounts": []}) == {"enabled": True, "accounts": []}
    assert sanitize_runtime_profile_aws({"enabled": True, "accounts": "nope"}) == {"enabled": True, "accounts": []}
    assert "accounts" not in sanitize_runtime_profile_aws({"enabled": True, "domain": "HBEU"})


def test_section_level_account_keys_are_still_not_portal_fields():
    # The pre-matrix shape never had a single account/role on the section; the
    # tree must not start accepting them now that accounts[] exists.
    sanitized = sanitize_runtime_profile_config_dict(
        {"aws": {"enabled": True, "account": "123456", "role": "ADFS-ReadOnly", "domain": "HBEU"}}
    )
    assert sanitized["aws"] == {"enabled": True, "domain": "HBEU"}


def test_field_tree_lists_the_matrix_fields():
    for key in (
        "provider",
        "idp_url",
        "source_profile",
        "default_account",
        "default_region",
        "session_duration_seconds",
        "kubeconfig_path",
        "accounts",
    ):
        assert PORTAL_MANAGED_FIELD_TREE["aws"][key] is True, key


# --------------------------------------------------------------------------
# Public redaction
# --------------------------------------------------------------------------


def test_public_response_keeps_the_accounts_and_hides_only_the_password():
    redacted = redact_runtime_profile_config_for_public_response({"aws": json.loads(json.dumps(FULL_AWS_SECTION))})

    assert "adfs-password" not in json.dumps(redacted)
    assert redacted["aws"]["password_present"] is True
    assert "password" not in redacted["aws"]
    assert redacted["aws"]["accounts"] == FULL_AWS_SECTION["accounts"]
    assert redacted["aws"]["default_account"] == "cps-dev"


# --------------------------------------------------------------------------
# Settings form parser
# --------------------------------------------------------------------------


def _matrix_form(**overrides):
    form = {
        "__touch_aws": "1",
        "aws_enabled": "on",
        "aws_provider": "adfs-assume",
        "aws_domain": "HBEU",
        "aws_username": "GB-SVC-XXX",
        "aws_password": "adfs-password",
        "aws_idp_url": "",
        "aws_source_profile": "",
        "aws_default_account": "cps-dev",
        "aws_default_region": "ap-east-1",
        "aws_session_duration_seconds": "3600",
        "aws_kubeconfig_path": "~/.efp/kube/config",
        "aws_accounts_instance_count": "2",
        "aws_accounts_instances_0_enabled": "1",
        "aws_accounts_instances_0_original_name": "",
        "aws_accounts_instances_0_name": "cps-dev",
        "aws_accounts_instances_0_account_id": "818354133892",
        "aws_accounts_instances_0_role": "ADFS-ReadOnly",
        "aws_accounts_instances_0_regions": "ap-east-1, eu-west-1",
        "aws_accounts_instances_1_original_name": "",
        "aws_accounts_instances_1_name": "cps-prod",
        "aws_accounts_instances_1_account_id": "123456789012",
        "aws_accounts_instances_1_role": "ADFS-ReadOnly",
        "aws_accounts_instances_1_regions": "eu-west-1",
    }
    form.update(overrides)
    return form


def test_settings_form_builds_the_account_matrix_from_the_indexed_fields():
    merged, error = _settings_merge_payload({}, _matrix_form())

    assert error is None
    assert merged["aws"] == {
        "enabled": True,
        "provider": "adfs-assume",
        "domain": "HBEU",
        "username": "GB-SVC-XXX",
        "password": "adfs-password",
        "default_account": "cps-dev",
        "default_region": "ap-east-1",
        "session_duration_seconds": 3600,
        "kubeconfig_path": "~/.efp/kube/config",
        "accounts": [
            {
                "name": "cps-dev",
                "account_id": "818354133892",
                "role": "ADFS-ReadOnly",
                "regions": ["ap-east-1", "eu-west-1"],
                "enabled": True,
            },
            # No checkbox posted for the second row: it was switched off.
            {"name": "cps-prod", "account_id": "123456789012", "role": "ADFS-ReadOnly", "regions": ["eu-west-1"], "enabled": False},
        ],
    }


def test_settings_form_without_a_password_field_keeps_the_stored_password():
    """A client that posts the section without the password input (an older
    panel, a partial form) must not log the profile out of AWS."""
    merged, error = _settings_merge_payload(
        {"aws": json.loads(json.dumps(FULL_AWS_SECTION))},
        {"__touch_aws": "1", "aws_enabled": "on", "aws_domain": "HBEU", "aws_username": "GB-SVC-XXX", "aws_default_region": "eu-west-1"},
    )

    assert error is None
    assert merged["aws"]["password"] == "adfs-password"
    assert merged["aws"]["default_region"] == "eu-west-1"
    # Nor does such a post lose the rows it never rendered.
    assert merged["aws"]["accounts"] == FULL_AWS_SECTION["accounts"]
    assert merged["aws"]["provider"] == "adfs-assume"


def test_settings_form_blank_password_still_clears_it():
    # The form renders the stored password into its input, so a blank one is
    # the member emptying it (same as proxy), not leaving it alone.
    merged, error = _settings_merge_payload(
        {"aws": {"enabled": True, "domain": "HBEU", "username": "u", "password": "old"}},
        {"__touch_aws": "1", "aws_enabled": "on", "aws_domain": "HBEU", "aws_username": "u", "aws_password": ""},
    )
    assert error is None
    assert "password" not in merged["aws"]


def test_settings_form_can_clear_the_matrix():
    merged, error = _settings_merge_payload(
        {"aws": json.loads(json.dumps(FULL_AWS_SECTION))},
        _matrix_form(aws_accounts_instance_count="0", aws_default_account=""),
    )
    assert error is None
    assert merged["aws"]["accounts"] == []
    assert "default_account" not in merged["aws"]


def test_settings_form_preserves_api_only_account_fields_by_original_name():
    """role_arn and profile have no input on the card; a save must carry the
    stored ones along, matched by the row's original name, not its index."""
    merged, error = _settings_merge_payload(
        {"aws": json.loads(json.dumps(FULL_AWS_SECTION))},
        _matrix_form(
            aws_accounts_instance_count="1",
            aws_accounts_instances_0_original_name="cps-prod",
            aws_accounts_instances_0_name="cps-prod",
            aws_accounts_instances_0_account_id="123456789012",
            aws_accounts_instances_0_role="ADFS-ReadOnly",
            aws_accounts_instances_0_regions="eu-west-1",
            aws_default_account="cps-prod",
        ),
    )
    assert error is None
    assert merged["aws"]["accounts"] == [
        {
            "name": "cps-prod",
            "account_id": "123456789012",
            "role": "ADFS-ReadOnly",
            "role_arn": "arn:aws:iam::123456789012:role/ADFS-ReadOnly",
            "profile": "prod-ro",
            "regions": ["eu-west-1"],
            "enabled": True,
        }
    ]


def test_settings_form_lowercases_the_provider_and_rejects_an_unknown_one():
    merged, error = _settings_merge_payload({}, _matrix_form(aws_provider="SAML2AWS"))
    assert error is None
    assert merged["aws"]["provider"] == "saml2aws"

    merged, error = _settings_merge_payload({"keep": True}, _matrix_form(aws_provider="okta"))
    assert error == "AWS provider must be adfs-assume, saml2aws, or assume-role."
    assert merged == {"keep": True}


def test_settings_form_rejects_a_malformed_account_id_instead_of_dropping_the_row():
    merged, error = _settings_merge_payload({}, _matrix_form(aws_accounts_instances_1_account_id="12345"))
    assert error == "AWS account cps-prod needs a 12-digit account id."
    assert "aws" not in merged


def test_settings_form_rejects_an_account_row_without_a_name():
    merged, error = _settings_merge_payload({}, _matrix_form(aws_accounts_instances_1_name=""))
    assert error == "AWS account 2 needs a name; it becomes the AWS CLI profile the assistant uses."
    assert "aws" not in merged


def test_settings_form_drops_an_empty_card_the_member_added_and_left_alone():
    merged, error = _settings_merge_payload(
        {},
        _matrix_form(
            aws_accounts_instances_1_name="",
            aws_accounts_instances_1_account_id="",
            aws_accounts_instances_1_role="",
            aws_accounts_instances_1_regions="",
        ),
    )
    assert error is None
    assert [row["name"] for row in merged["aws"]["accounts"]] == ["cps-dev"]


def test_settings_form_rejects_duplicate_account_names_case_insensitively():
    merged, error = _settings_merge_payload({}, _matrix_form(aws_accounts_instances_1_name="CPS-DEV"))
    assert error == "AWS account names must be unique; CPS-DEV is listed more than once."
    assert "aws" not in merged


def test_settings_form_rejects_a_default_account_that_names_no_row():
    merged, error = _settings_merge_payload({}, _matrix_form(aws_default_account="cps-uat"))
    assert error == "Default AWS account cps-uat must be the name or 12-digit id of one of the accounts listed below."
    assert "aws" not in merged


def test_settings_form_accepts_a_default_account_given_as_the_account_id():
    merged, error = _settings_merge_payload({}, _matrix_form(aws_default_account="123456789012"))
    assert error is None
    assert merged["aws"]["default_account"] == "123456789012"


@pytest.mark.parametrize("duration", ["899", "43201", "1h", "-3600"])
def test_settings_form_rejects_a_session_duration_outside_the_sts_bounds(duration):
    merged, error = _settings_merge_payload({}, _matrix_form(aws_session_duration_seconds=duration))
    assert error == "AWS session duration must be a whole number of seconds between 900 and 43200."
    assert "aws" not in merged


def test_settings_form_blank_session_duration_clears_the_override():
    merged, error = _settings_merge_payload(
        {"aws": {"enabled": True, "session_duration_seconds": 7200}},
        _matrix_form(aws_session_duration_seconds=""),
    )
    assert error is None
    assert "session_duration_seconds" not in merged["aws"]


def test_settings_form_untouched_aws_section_is_left_alone():
    merged, error = _settings_merge_payload(
        {"aws": json.loads(json.dumps(FULL_AWS_SECTION))},
        {"__touch_debug": "1", "debug_enabled": "on", "debug_log_level": "INFO"},
    )
    assert error is None
    assert merged["aws"] == FULL_AWS_SECTION


def test_view_payload_joins_the_regions_for_the_card_input():
    payload = _settings_view_payload({"aws": json.loads(json.dumps(FULL_AWS_SECTION))})

    assert [row["regions"] for row in payload["aws_accounts"]] == ["ap-east-1, eu-west-1", "eu-west-1"]
    assert payload["aws_accounts"][0]["account_id"] == "818354133892"
    assert payload["aws_auth_providers"] == ["adfs-assume", "saml2aws", "assume-role"]
    # A profile without the matrix renders no cards, not an error.
    assert _settings_view_payload({"aws": {"enabled": True}})["aws_accounts"] == []


# --------------------------------------------------------------------------
# End to end through the runtime-profile save route
# --------------------------------------------------------------------------


def test_end_to_end_profile_save_persists_the_matrix_and_reloads_it_into_the_panel(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {"aws": {"enabled": False}})
        resp = client.post(
            f"/app/runtime-profiles/{rp.id}/save",
            data={"name": "rp", **_matrix_form(aws_provider="saml2aws", aws_idp_url=IDP_URL)},
        )
        assert resp.status_code == 200
        db.refresh(rp)
        stored = json.loads(rp.config_json)["aws"]
        assert stored["provider"] == "saml2aws"
        assert stored["idp_url"] == IDP_URL
        assert stored["password"] == "adfs-password"
        assert stored["default_account"] == "cps-dev"
        assert stored["session_duration_seconds"] == 3600
        assert [row["name"] for row in stored["accounts"]] == ["cps-dev", "cps-prod"]
        assert stored["accounts"][0]["regions"] == ["ap-east-1", "eu-west-1"]
        assert stored["accounts"][1]["enabled"] is False

        # The re-rendered panel carries the rows back so the next save round-trips.
        html = resp.text
        cards = [card for card in _parse_cards(html) if card["group"] == "aws_accounts"]
        assert len(cards) == 2
        assert 'name="aws_accounts_instance_count" value="2"' in html
        assert cards[0]["fields"]["name"]["value"] == "cps-dev"
        assert cards[0]["fields"]["account_id"]["value"] == "818354133892"
        assert cards[0]["fields"]["role"]["value"] == "ADFS-ReadOnly"
        assert _selected_options(html, "aws_accounts", 0, "regions") == ["ap-east-1", "eu-west-1"]
        assert cards[0]["attrs"]["data-original-field"] == ["name"]
        assert cards[1]["disabled_class"] is True and "Disabled" in cards[1]["text"]
        assert '<option value="saml2aws" selected>saml2aws</option>' in html
        assert 'name="aws_default_account" value="cps-dev"' in html
        assert f'name="aws_idp_url" value="{IDP_URL}"' in html
        assert 'name="aws_session_duration_seconds" value="3600"' in html

        # A later save that posts neither the password nor the rows keeps both.
        again = client.post(
            f"/app/runtime-profiles/{rp.id}/save",
            data={"name": "rp", "__touch_aws": "1", "aws_enabled": "on", "aws_default_region": "eu-west-1"},
        )
        assert again.status_code == 200
        db.refresh(rp)
        stored = json.loads(rp.config_json)["aws"]
        assert stored["password"] == "adfs-password"
        assert stored["default_region"] == "eu-west-1"
        assert [row["name"] for row in stored["accounts"]] == ["cps-dev", "cps-prod"]
    finally:
        cleanup()


def test_end_to_end_profile_save_reports_a_bad_account_id_and_keeps_the_stored_config(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {"aws": {"enabled": True, "domain": "HBEU"}})
        resp = client.post(
            f"/app/runtime-profiles/{rp.id}/save",
            data={"name": "rp", **_matrix_form(aws_accounts_instances_0_account_id="8183")},
        )
        assert resp.status_code == 200
        assert "AWS account cps-dev needs a 12-digit account id." in resp.text
        db.refresh(rp)
        assert json.loads(rp.config_json)["aws"] == {"enabled": True, "domain": "HBEU"}
    finally:
        cleanup()


# --------------------------------------------------------------------------
# Projection: when the CLI instructions are worth adding, and what they say
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "aws_section,expected",
    [
        ({"enabled": True, "provider": "assume-role"}, False),
        ({"enabled": True, "provider": "assume-role", "accounts": []}, False),
        ({"enabled": True, "provider": "assume-role", "accounts": [{"name": "off", "account_id": "818354133892", "enabled": False}]}, False),
        ({"enabled": True, "provider": "assume-role", "accounts": [{"name": "on", "account_id": "818354133892"}]}, True),
        ({"enabled": True, "provider": "assume-role", "accounts": [{"name": "arn", "role_arn": "arn:aws:iam::818354133892:role/ro"}]}, True),
        ({"enabled": False, "provider": "assume-role", "accounts": [{"name": "on", "account_id": "818354133892"}]}, False),
        # The directory-account providers keep the domain+username+password rule.
        ({"enabled": True, "provider": "adfs-assume", "accounts": [{"name": "on", "account_id": "818354133892"}]}, False),
        ({"enabled": True, "provider": "saml2aws", "domain": "HBEU", "username": "u", "password": "p"}, True),
        ({"enabled": True, "domain": "HBEU", "username": "u", "password": "p"}, True),
        ({"enabled": True, "domain": "HBEU", "username": "u"}, False),
    ],
)
def test_assume_role_counts_as_configured_only_with_an_enabled_account(aws_section, expected):
    assert _has_enabled_aws_config({"aws": aws_section}) is expected


def test_native_cli_instructions_follow_the_aws_rule_through_the_projection():
    projected = project_canonical_for_runtime(
        {"aws": {"enabled": True, "provider": "assume-role", "accounts": [{"name": "on", "account_id": "818354133892"}]}},
        "native",
    )
    assert RUNTIME_PROFILE_CLI_TOOL_INSTRUCTIONS in (projected.get("instruction_texts") or [])

    projected = project_canonical_for_runtime({"aws": {"enabled": True, "provider": "assume-role"}}, "native")
    assert "instruction_texts" not in projected


def test_cli_instructions_tell_the_assistant_how_to_sign_in_and_address_each_account():
    text = RUNTIME_PROFILE_CLI_TOOL_INSTRUCTIONS
    assert "`aws-auth account list --json`" in text
    assert "`aws-auth login --account <name> --json`" in text
    assert "`aws-auth login --all --json`" in text
    assert "pass `--profile <name>` to every aws command" in text
    assert "`aws-auth eks kubeconfig --account <name> --cluster <cluster> --json`" in text
    assert "`--context <name>/<cluster>`" in text
    assert "never apply, delete, edit, patch, scale, rollout, exec, port-forward, or read secrets" in text
    assert "expired or missing token" in text
    assert "Avoid changing cloud resources unless the user asks." in text
    assert "prefer `aws --output json`" not in text


# --------------------------------------------------------------------------
# UI: rendered cards on every panel that edits the section
# --------------------------------------------------------------------------


def _card_html(html, group, index):
    """The markup of the index-th card of a group."""
    pieces = html.split(f'data-instance-item="{group}"')
    assert len(pieces) > index + 1, f"no card {index} for {group}"
    return pieces[index + 1]


def _options(html, group, index, field):
    """(value, selected, label) of every option in a card's select field."""
    card = _card_html(html, group, index)
    match = re.search(rf'<select[^>]*data-field="{field}"[^>]*>(.*?)</select>', card, flags=re.S)
    assert match, f"card {index} of {group} has no <select data-field={field!r}>"
    return [
        (value, bool(selected), label.strip())
        for value, selected, label in re.findall(r'<option value="([^"]*)"\s*(selected)?\s*>(.*?)</option>', match.group(1), flags=re.S)
    ]


def _selected_options(html, group, index, field):
    return [value for value, selected, _label in _options(html, group, index, field) if selected]


def _section_select_options(html, name):
    """(value, selected) of every option in a section-level select."""
    match = re.search(rf'<select name="{name}"[^>]*>(.*?)</select>', html, flags=re.S)
    assert match, f"no <select name={name!r}>"
    return [(value, bool(selected)) for value, selected in re.findall(r'<option value="([^"]*)"\s*(selected)?\s*>', match.group(1))]


def _js_array_literal(js, const_name):
    """Read a JSON-compatible `const NAME = [...];` list out of chat_ui.js."""
    marker = f"const {const_name} = "
    start = js.index(marker) + len(marker)
    assert js[start] == "[", f"{const_name} is not an array literal"
    return json.loads(js[start : js.index("];", start) + 1])


def _expand_region_slots(js, rendered):
    """Expand the region helpers a card literal calls, the way the JS does."""
    regions = _js_array_literal(js, "AWS_REGIONS")
    options = "".join(f'<option value="{region}">{region}</option>' for region in regions)
    rendered = rendered.replace("${AWS_REGIONS.length}", str(len(regions)))
    rendered = rendered.replace('${regionOptionsHtml("Any region")}', '<option value="">Any region</option>' + options)
    return rendered.replace("${regionOptionsHtml()}", options)


def _aws_matrix_profile():
    return {"aws": json.loads(json.dumps(FULL_AWS_SECTION))}


def test_settings_panel_renders_one_card_per_account(monkeypatch):
    html = _render_panel(monkeypatch, _aws_matrix_profile())
    cards = [card for card in _parse_cards(html) if card["group"] == "aws_accounts"]

    assert len(cards) == 2
    assert 'name="aws_accounts_instance_count" value="2"' in html
    assert 'data-instance-group="aws_accounts"' in html
    assert 'data-action="add-instance" data-group="aws_accounts"' in html
    assert cards[0]["fields"]["name"]["value"] == "cps-dev"
    assert cards[0]["fields"]["account_id"]["value"] == "818354133892"
    # Regions are a multi-select of the supported regions, not a text box.
    assert "multiple" in cards[0]["fields"]["regions"]
    assert [value for value, _sel, _label in _options(html, "aws_accounts", 0, "regions")] == ["ap-east-1", "eu-west-1", "us-east-1"]
    assert _selected_options(html, "aws_accounts", 0, "regions") == ["ap-east-1", "eu-west-1"]
    assert _selected_options(html, "aws_accounts", 1, "regions") == ["eu-west-1"]
    assert "Account 1" in cards[0]["text"]
    assert cards[0]["fields"]["enabled"]["aria-label"] == "Enable AWS account instance 1"
    assert cards[1]["disabled_class"] is True
    assert "checked" not in cards[1]["fields"]["enabled"]
    # No credential inputs on an account card: the directory password lives on the section.
    assert sorted(cards[0]["fields"]) == ["account_id", "enabled", "name", "regions", "role"]
    for name in (
        "aws_provider",
        "aws_idp_url",
        "aws_source_profile",
        "aws_default_account",
        "aws_default_region",
        "aws_session_duration_seconds",
        "aws_kubeconfig_path",
    ):
        assert f'name="{name}"' in html, name


def test_the_password_input_stays_on_the_section_with_its_stored_value():
    # The account rows are not secrets; the one secret keeps the authenticated
    # edit-form treatment the template-secrets test guards.
    for rel in ("app/templates/partials/runtime_profile_panel.html", "app/templates/partials/settings_panel.html"):
        text = Path(rel).read_text(encoding="utf-8")
        assert "value=\"{{ raw_aws.get('password', '') }}\"" in text
        section = text[text.index('data-managed-section="aws"') :]
        section = section[: section.index("</section>")]
        assert 'data-instance-container="aws_accounts"' in section
        assert 'data-field="password"' not in section


def test_agent_and_runtime_profile_panels_render_the_same_account_card(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        _bind_profile(db, agent, _aws_matrix_profile())
        agent_html = client.get(f"/app/agents/{agent.id}/settings/panel").text
        profile_html = client.get(f"/app/runtime-profiles/{agent.runtime_profile_id}/panel").text
    finally:
        cleanup()

    agent_cards = [c for c in _parse_cards(agent_html) if c["group"] == "aws_accounts"]
    profile_cards = [c for c in _parse_cards(profile_html) if c["group"] == "aws_accounts"]
    assert len(agent_cards) == len(profile_cards) == 2
    for agent_card, profile_card in zip(agent_cards, profile_cards):
        assert _shape(agent_card) == _shape(profile_card)
        assert agent_card["fields"] == profile_card["fields"]
        assert agent_card["text"] == profile_card["text"]


def _js_account_card_html():
    """Evaluate awsAccountCardHtml's template literal in Python.

    Like the jenkins helper, the copy comes from the JS lookup tables so the
    test never re-states values the production code could drift away from.
    """
    js = _js_source()
    start = js.index("function awsAccountCardHtml(")
    body = js[start : js.index("\nfunction ", start + 1)]
    literal_match = re.search(r"return `(.*?)`;", body, flags=re.S)
    assert literal_match, "awsAccountCardHtml no longer builds the card from a template literal"
    rendered = literal_match.group(1)
    placeholders = _js_object_literal(js, "INSTANCE_GROUP_PLACEHOLDERS")["aws_accounts"]
    for key, copy in placeholders.items():
        rendered = rendered.replace("${placeholders." + key + "}", copy)
    rendered = rendered.replace("${label}", _js_object_literal(js, "INSTANCE_GROUP_LABELS")["aws_accounts"])
    rendered = _expand_region_slots(js, rendered)
    assert "${" not in rendered, "unresolved template slot in awsAccountCardHtml literal"
    # normalizeInstanceInputs renumbers the freshly appended card.
    rendered = rendered.replace(
        '<span class="portal-settings-instance-title">Account</span>',
        '<span class="portal-settings-instance-title">Account 1</span>',
    )
    rendered = rendered.replace('aria-label="Enable AWS account instance"', 'aria-label="Enable AWS account instance 1"')
    return f'<div class="portal-settings-instance-card" data-instance-item="aws_accounts">{rendered}</div>'


def test_js_added_account_card_matches_the_server_rendered_card(monkeypatch):
    config = {"aws": {"enabled": True, "accounts": [{"name": "cps-dev", "account_id": "818354133892", "enabled": True}]}}
    server_card = next(c for c in _parse_cards(_render_panel(monkeypatch, config)) if c["group"] == "aws_accounts")
    js_card = next(c for c in _parse_cards(_js_account_card_html()) if c["group"] == "aws_accounts")

    assert _shape(js_card) == _shape(server_card)
    assert sorted(js_card["fields"]) == sorted(server_card["fields"])
    assert js_card["attrs"] == server_card["attrs"]
    assert js_card["text"] == server_card["text"]
    for field, attributes in js_card["fields"].items():
        assert attributes.get("type") == server_card["fields"][field].get("type"), field
        assert attributes.get("placeholder") == server_card["fields"][field].get("placeholder"), field
        assert attributes.get("class") == server_card["fields"][field].get("class"), field


def test_add_instance_row_builds_the_account_card_for_the_aws_group():
    js = _js_source()
    start = js.index("function addInstanceRow(")
    body = js[start : js.index("\nfunction ", start + 1)]
    assert 'if (group === "aws_accounts")' in body
    assert "awsAccountCardHtml(" in body
    # The generic card (name/url/credentials) is still what the other groups get.
    assert 'data-field="url"' in body


def test_the_aws_group_is_registered_and_initialized_by_the_js():
    js = _js_source()
    assert "aws_accounts" in _initialized_instance_groups(js)
    assert _js_object_literal(js, "INSTANCE_GROUP_LABELS")["aws_accounts"] == "AWS account"
    placeholders = _js_object_literal(js, "INSTANCE_GROUP_PLACEHOLDERS")["aws_accounts"]
    assert placeholders == {
        "name": "Account name, e.g. cps-dev",
        "account_id": "12-digit AWS account id",
        "role": "IAM role, e.g. ADFS-ReadOnly",
    }
    # The table names what a card is called per group; the AWS group's entry
    # is what this test pins, other groups may add their own.
    assert _js_object_literal(js, "INSTANCE_GROUP_ITEM_TITLES")["aws_accounts"] == "Account"


def test_adding_or_removing_an_account_row_touches_the_aws_section_not_the_group():
    """The rows belong to the "aws" section: its __touch_aws flag is what the
    server checks before reading them, and there is no aws_accounts flag."""
    js = _js_source()
    start = js.index('root.addEventListener("click"')
    handler = js[start : js.index("const scrollBtn", start)]
    assert "markManagedSectionTouched(root, sectionNameForElement(addBtn) || group)" in handler
    assert "const touchedSection = sectionNameForElement(removeBtn) || group;" in handler
    # Resolved before the card is detached, which would leave the button without a section.
    assert handler.index("const touchedSection") < handler.index("?.remove()")
    assert "markManagedSectionTouched(root, touchedSection)" in handler
    for template in ("app/templates/partials/runtime_profile_panel.html", "app/templates/partials/settings_panel.html"):
        text = Path(template).read_text(encoding="utf-8")
        assert 'data-touch-flag="aws"' in text
        assert 'data-touch-flag="aws_accounts"' not in text


def test_tooltips_cover_the_new_inputs_and_beat_the_generic_instance_hints():
    from tests.test_tooltips import _registry_entries

    entries = _registry_entries()
    selectors = [entry[1] for entry in entries]
    for selector in (
        'select[name="aws_provider"]',
        'input[name="aws_idp_url"]',
        'input[name="aws_source_profile"]',
        'input[name="aws_default_account"]',
        'input[name="aws_default_region"]',
        'input[name="aws_session_duration_seconds"]',
        'input[name="aws_kubeconfig_path"]',
        '[data-action="add-instance"][data-group="aws_accounts"]',
        '[data-instance-item="aws_accounts"] [data-field="name"]',
        '[data-instance-item="aws_accounts"] [data-field="account_id"]',
        '[data-instance-item="aws_accounts"] [data-field="role"]',
        '[data-instance-item="aws_accounts"] [data-field="regions"]',
    ):
        assert selector in selectors, selector
    # First match wins, so the account name hint must come before the generic one.
    assert selectors.index('[data-instance-item="aws_accounts"] [data-field="name"]') < selectors.index(
        '[data-instance-item] [data-field="name"]'
    )


# --------------------------------------------------------------------------
# Default Connections (admin seed)
# --------------------------------------------------------------------------


def test_default_connections_form_offers_the_matrix_and_reads_it_back():
    html = _default_connections_html(
        seed={"aws": {"enabled": True, "provider": "saml2aws", "accounts": [{"name": "cps-dev", "account_id": "818354133892", "regions": ["ap-east-1", "eu-west-1"]}]}}
    )
    assert 'data-instance-container="aws_accounts"' in html
    assert 'name="aws_accounts_instance_count" value="1"' in html
    # Regions are a multi-select of the supported regions with the stored ones chosen.
    assert _selected_options(html, "aws_accounts", 0, "regions") == ["ap-east-1", "eu-west-1"]
    assert [value for value, _sel, _label in _options(html, "aws_accounts", 0, "regions")] == ["ap-east-1", "eu-west-1", "us-east-1"]
    assert 'value="818354133892"' in html
    assert '<option value="saml2aws" selected>saml2aws</option>' in html
    # Leaving the provider unset seeds nothing, like the LLM provider.
    assert '<option value="">Leave unset' in html
    for name in ("aws_idp_url", "aws_source_profile", "aws_default_account", "aws_default_region", "aws_session_duration_seconds", "aws_kubeconfig_path"):
        assert f'name="{name}"' in html, name

    config = _seed_config_from_form(
        FormData(
            [
                ("aws_enabled", "on"),
                ("aws_provider", "adfs-assume"),
                ("aws_domain", "HBEU"),
                ("aws_default_account", "cps-dev"),
                ("aws_session_duration_seconds", "3600"),
                ("aws_accounts_instance_count", "1"),
                ("aws_accounts_instances_0_enabled", "1"),
                ("aws_accounts_instances_0_name", "cps-dev"),
                ("aws_accounts_instances_0_account_id", "818354133892"),
                ("aws_accounts_instances_0_role", "ADFS-ReadOnly"),
                ("aws_accounts_instances_0_regions", "ap-east-1, eu-west-1"),
            ]
        )
    )
    assert config["aws"] == {
        "enabled": True,
        "provider": "adfs-assume",
        "domain": "HBEU",
        "default_account": "cps-dev",
        "session_duration_seconds": 3600,
        "accounts": [
            {"enabled": True, "name": "cps-dev", "account_id": "818354133892", "role": "ADFS-ReadOnly", "regions": "ap-east-1, eu-west-1"}
        ],
    }
    # The seed becomes a member's first profile through the sanitizer, which
    # turns the typed regions into the runtime's list shape.
    assert sanitize_runtime_profile_config_dict(config)["aws"]["accounts"][0]["regions"] == ["ap-east-1", "eu-west-1"]


def test_an_untouched_default_connections_aws_section_stores_nothing():
    # The provider select always posts; its blank option must not seed a section.
    assert _seed_config_from_form(FormData([("aws_provider", ""), ("aws_accounts_instance_count", "0")])) == {}


# --------------------------------------------------------------------------
# Guidance and help
# --------------------------------------------------------------------------


def test_guidance_describes_the_account_matrix():
    guidance = CONNECTION_GUIDANCE["aws"]
    assert guidance["summary"] == (
        "Lets the assistant inspect AWS accounts and EKS clusters through the read-only roles you list here."
    )
    steps = " ".join(guidance["steps"])
    assert "12-digit account id" in steps
    assert "provider" in steps
    assert "read-only role" in steps

    topic = get_topic("connect-aws")
    assert topic is not None
    assert list(topic.steps) == list(guidance["steps"])
    assert "aws-auth" in topic.body
    assert "--profile" in topic.body
    assert "assume-role" in topic.body
