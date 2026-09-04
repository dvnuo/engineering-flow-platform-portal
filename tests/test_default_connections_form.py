"""Default Connections is a form, not a JSON textarea.

Editing raw JSON was the original shape and it was unmaintainable: an admin had
to know the config schema, and a stray comma failed the whole save. The panel
now mirrors the Connections form -- same section layout, same instance cards,
same credential fields.

Credentials in the seed are optional and the admin's call. Filled in, they are
a shared service account every new member starts with; left blank, the field
arrives empty for the member to complete. The tests below hold that line: a
blank credential must not reach the stored value as "", or "nobody seeded one"
and "somebody seeded an empty one" would look the same.
"""
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.datastructures import FormData

from app.contracts.llm_catalog import DEFAULT_CONTEXT_SIZE, DEFAULT_REASONING_EFFORT
from app.db import Base
from app.services.profile_secret_encryption import SENSITIVE_FIELD_NAMES
from app.services.runtime_profile_seed_service import RuntimeProfileSeedService
from app.services.runtime_profile_service import RuntimeProfileService
from app.web import _default_connections_context, _seed_config_from_form, _seed_parse_instances


def _database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)()


def _panel_html(**overrides) -> str:
    from jinja2 import Environment, FileSystemLoader

    db = _database()
    if overrides.get("seed"):
        RuntimeProfileSeedService(db).save_seed(overrides["seed"])
    context = _default_connections_context(None, db)
    env = Environment(loader=FileSystemLoader("app/templates"))
    return env.get_template("partials/default_connections_panel.html").render(**context)


# ------------------------------------------------------------------- shape


def test_the_json_textarea_is_gone():
    html = _panel_html()

    assert 'textarea name="seed"' not in html
    assert "Seed JSON" not in html


def test_every_seedable_section_has_a_form_section():
    html = _panel_html()

    for heading in ("LLM model", "Jira", "Confluence", "GitHub", "Jenkins", "Network proxy", "AWS"):
        assert f"<h6>{heading}</h6>" in html, heading


def test_the_panel_offers_a_credential_field_per_connection():
    html = _panel_html(
        seed={"jira": {"enabled": True, "instances": [{"name": "P", "url": "https://x"}]}}
    )

    # Instance cards carry the same credential trio as a member's own panel.
    for field in ("username", "password", "token"):
        assert f'data-field="{field}"' in html, field
    # Section-level credentials, one per service that has one.
    for name in (
        "llm_api_key",
        "github_api_token",
        "proxy_password",
        "aws_password",
        "mobile_browserstack_access_key",
    ):
        assert f'name="{name}"' in html, name


def test_credentials_are_typed_as_passwords():
    # They render into the form so the admin can see and change what is stored;
    # a password input keeps them off the screen until asked for.
    html = _panel_html(
        seed={"jira": {"enabled": True, "instances": [{"name": "P", "url": "https://x", "token": "t"}]}}
    )

    assert 'type="password" data-field="token"' in html
    assert 'type="password" name="github_api_token"' in html


def test_instance_containers_no_longer_opt_out_of_credential_fields():
    # addInstanceRow builds the card client-side; a freshly added row has to
    # offer the same credential fields as a saved one.
    html = _panel_html()

    assert "data-instance-credentials" not in html

    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    assert "instanceCredentials" not in js
    assert 'data-field="token"' in js


def test_provider_offers_an_unset_option_without_promising_a_choice():
    # The blank is worth keeping -- it leaves the profile's provider unwritten.
    # The old label called it "No default - members choose", which the code
    # cannot honour: normalize_provider maps blank to github_copilot on every
    # path, and a member's own panel has no blank option to choose from.
    html = _panel_html()

    assert '<option value="">' in html
    assert "members choose" not in html
    assert "assistants use GitHub Copilot" in html


def _select_options(html: str, name: str) -> list[tuple[str, bool]]:
    block = re.search(rf'name="{name}"[^>]*>(.*?)</select>', html, re.S)
    assert block, name
    return [
        (value, bool(is_selected))
        for value, is_selected, _label in re.findall(
            r'<option value="([^"]*)"[^>]*?(selected)?>([^<]*)</option>', block.group(1)
        )
    ]


def test_thinking_level_and_context_size_no_longer_offer_a_blank():
    # A member's own panel rejects a blank one outright ("must be a supported
    # value") and has no blank option, so anything seeded blank was overwritten
    # the first time they saved. Offering a choice the next screen refuses is
    # worse than not offering it.
    html = _panel_html()

    for name in ("llm_reasoning_effort", "llm_max_context_tokens"):
        assert "" not in [value for value, _ in _select_options(html, name)], name


def test_an_unseeded_form_preselects_the_managed_default_not_the_first_option():
    # Without an explicit selection the browser takes whichever option comes
    # first -- Low and 64K -- so saving an untouched panel would quietly pin
    # every new member to the smallest settings.
    html = _panel_html()

    assert [value for value, chosen in _select_options(html, "llm_reasoning_effort") if chosen] == [
        DEFAULT_REASONING_EFFORT
    ]
    assert [value for value, chosen in _select_options(html, "llm_max_context_tokens") if chosen] == [
        str(DEFAULT_CONTEXT_SIZE)
    ]


def test_a_seeded_thinking_level_and_context_size_come_back_selected():
    html = _panel_html(seed={"llm": {"reasoning_effort": "low", "max_context_tokens": 64000}})

    assert [value for value, chosen in _select_options(html, "llm_reasoning_effort") if chosen] == ["low"]
    assert [value for value, chosen in _select_options(html, "llm_max_context_tokens") if chosen] == ["64000"]


def test_saved_values_come_back_into_the_form():
    html = _panel_html(
        seed={
            "jira": {
                "enabled": True,
                "instances": [{"name": "Prod", "url": "https://x.atlassian.net", "project": "ABC", "api_version": "3"}],
            }
        }
    )

    assert 'value="ABC"' in html
    assert '<option value="3" selected>REST API v3</option>' in html


def test_saved_credentials_come_back_into_the_form():
    # An admin has to be able to see what is seeded to decide whether to change
    # it; a masked round trip would mean re-typing the token to edit the URL.
    html = _panel_html(
        seed={"jira": {"enabled": True, "instances": [{"name": "P", "url": "https://x", "token": "seeded-token"}]}}
    )

    assert 'value="seeded-token"' in html


def test_the_stored_value_dump_masks_credentials():
    # The form reveals a secret only when the admin clicks its eye; this dump
    # has no such gesture, so it must not print one in passing.
    html = _panel_html(
        seed={"jira": {"enabled": True, "instances": [{"name": "P", "url": "https://x", "token": "seeded-token"}]}}
    )

    dump = html.split("<pre")[1]
    assert "seeded-token" not in dump
    assert "[REDACTED]" in dump


# ------------------------------------------------------------------ parsing


def _form(pairs: dict) -> FormData:
    return FormData(list(pairs.items()))


def test_instances_are_read_from_the_indexed_field_names():
    parsed = _seed_parse_instances(
        _form(
            {
                "jira_instance_count": "1",
                "jira_instances_0_enabled": "1",
                "jira_instances_0_name": "Prod",
                "jira_instances_0_url": "https://x",
                "jira_instances_0_project": "ABC",
            }
        ),
        "jira",
        ["enabled", "name", "url", "project"],
    )

    assert parsed == [{"enabled": True, "name": "Prod", "url": "https://x", "project": "ABC"}]


def test_an_empty_card_the_admin_added_and_left_is_dropped():
    parsed = _seed_parse_instances(
        _form({"jira_instance_count": "2", "jira_instances_0_name": "Prod", "jira_instances_1_name": ""}),
        "jira",
        ["name", "url"],
    )

    assert [row["name"] for row in parsed] == ["Prod"]


def test_a_nonsense_count_yields_no_instances():
    parsed = _seed_parse_instances(_form({"jira_instance_count": "not-a-number"}), "jira", ["name"])

    assert parsed == []


def test_an_untouched_form_stores_nothing():
    # Otherwise every save would write empty sections and the stored value would
    # imply an intent the admin never expressed.
    assert _seed_config_from_form(_form({})) == {}


def test_instance_credentials_are_read_from_the_form():
    config = _seed_config_from_form(
        _form(
            {
                "jira_enabled": "on",
                "jira_instance_count": "1",
                "jira_instances_0_name": "Prod",
                "jira_instances_0_url": "https://x",
                "jira_instances_0_username": "bot@example.com",
                "jira_instances_0_token": "shared-token",
            }
        )
    )

    instance = config["jira"]["instances"][0]
    assert instance["username"] == "bot@example.com"
    assert instance["token"] == "shared-token"


def test_a_blank_instance_credential_is_left_out_rather_than_stored_empty():
    # A stored "token": "" would read as a seeded credential in the masked dump,
    # which prints [REDACTED] for the key whatever the value is.
    config = _seed_config_from_form(
        _form(
            {
                "jira_instance_count": "1",
                "jira_instances_0_name": "Prod",
                "jira_instances_0_url": "https://x",
                "jira_instances_0_username": "",
                "jira_instances_0_password": "",
                "jira_instances_0_token": "",
            }
        )
    )

    instance = config["jira"]["instances"][0]
    for field in ("password", "token"):
        assert field not in instance, field
    # Fields that are not credentials keep the empty string they always kept.
    assert instance["username"] == ""
    assert instance["project"] == ""


@pytest.mark.parametrize(
    "form_pairs,section,expected",
    [
        (
            {"github_enabled": "on", "github_base_url": "https://ghe/api/v3", "github_api_token": "ghp_x"},
            "github",
            {"enabled": True, "base_url": "https://ghe/api/v3", "api_token": "ghp_x"},
        ),
        (
            {"proxy_enabled": "on", "proxy_url": "http://p:8080", "proxy_username": "u", "proxy_password": "p"},
            "proxy",
            {"enabled": True, "url": "http://p:8080", "username": "u", "password": "p"},
        ),
        (
            {"aws_enabled": "on", "aws_domain": "corp", "aws_username": "u", "aws_password": "p"},
            "aws",
            {"enabled": True, "domain": "corp", "username": "u", "password": "p"},
        ),
        (
            {"mobile_enabled": "on", "mobile_browserstack_username": "u", "mobile_browserstack_access_key": "k"},
            "mobile-auto",
            {"enabled": True, "browserstack": {"username": "u", "access_key": "k"}},
        ),
    ],
)
def test_sections_round_trip_with_credentials(form_pairs, section, expected):
    assert _seed_config_from_form(_form(form_pairs))[section] == expected


@pytest.mark.parametrize(
    "form_pairs,section,expected",
    [
        ({"github_enabled": "on", "github_base_url": "https://ghe/api/v3"}, "github", {"enabled": True, "base_url": "https://ghe/api/v3"}),
        ({"proxy_enabled": "on", "proxy_url": "http://p:8080"}, "proxy", {"enabled": True, "url": "http://p:8080"}),
        ({"aws_enabled": "on", "aws_domain": "corp"}, "aws", {"enabled": True, "domain": "corp"}),
        ({"mobile_enabled": "on"}, "mobile-auto", {"enabled": True}),
    ],
)
def test_sections_round_trip_without_credentials(form_pairs, section, expected):
    # Blank credential boxes leave the section exactly as it was before the
    # panel grew them, so an admin who seeds none sees no difference.
    assert _seed_config_from_form(_form(form_pairs))[section] == expected


def test_a_credential_alone_is_enough_to_store_a_section():
    # An admin may seed only a shared token, leaving the toggle off until they
    # are ready; the token still has to survive the save.
    config = _seed_config_from_form(_form({"github_api_token": "ghp_x"}))

    assert config["github"] == {"enabled": False, "api_token": "ghp_x"}


def test_the_llm_api_key_is_read_only_for_the_provider_it_belongs_to():
    # The Copilot key field is hidden while AI Platform is selected, so reading
    # it would seed a credential the admin cannot see to remove.
    copilot = _seed_config_from_form(
        _form({"llm_provider": "github_copilot", "llm_api_key": "key", "llm_ai_platform_password": "pw"})
    )
    assert copilot["llm"]["api_key"] == "key"
    assert "ai_platform" not in copilot["llm"]

    ai_platform = _seed_config_from_form(
        _form(
            {
                "llm_provider": "ai_platform",
                "llm_api_key": "key",
                "llm_ai_platform_username": "u",
                "llm_ai_platform_password": "pw",
                "llm_ai_platform_usercase": "uc",
            }
        )
    )
    assert "api_key" not in ai_platform["llm"]
    assert ai_platform["llm"]["ai_platform"]["auth"] == {"username": "u", "password": "pw", "usercase": "uc"}


def test_leaving_the_provider_unset_keeps_the_shared_copilot_key():
    # Unset resolves to GitHub Copilot, which the option now says out loud, so
    # its key still applies. Gating on a provider being picked meant a stored
    # key was dropped by the next save: the field is hidden by CSS rather than
    # removed, so the browser posts it back and the builder threw it away.
    config = _seed_config_from_form(_form({"llm_api_key": "key"}))

    assert config["llm"]["api_key"] == "key"


def test_the_copilot_key_is_ignored_while_ai_platform_is_selected():
    config = _seed_config_from_form(
        _form({"llm_provider": "ai_platform", "llm_api_key": "key", "llm_ai_platform_username": "u"})
    )

    assert "api_key" not in config["llm"]


def test_an_empty_credential_is_not_displayed_as_though_it_were_set():
    # save_seed does not drop blanks the way the form does, so a seed written
    # through the JSON API can hold "token": "". redact_value masks by key name
    # whatever the value is, which would print [REDACTED] for a credential
    # nobody set -- while the summary, which checks the value, said otherwise.
    html = _panel_html(
        seed={"jira": {"enabled": True, "instances": [{"name": "P", "url": "https://x", "token": ""}]}}
    )

    dump = html.split("<pre")[1]
    assert "[REDACTED]" not in dump
    assert "token" not in dump


def test_context_size_is_stored_as_a_number():
    config = _seed_config_from_form(_form({"llm_max_context_tokens": "256000"}))

    assert config["llm"]["max_context_tokens"] == 256000


def test_a_non_numeric_context_size_is_ignored():
    config = _seed_config_from_form(_form({"llm_max_context_tokens": "lots"}))

    assert "max_context_tokens" not in config.get("llm", {})


def test_a_disabled_section_with_instances_is_still_stored():
    # Turning Jira off should not throw away the instances an admin configured;
    # they may be turning it off temporarily.
    config = _seed_config_from_form(
        _form({"jira_instance_count": "1", "jira_instances_0_name": "Prod", "jira_instances_0_url": "https://x"})
    )

    assert config["jira"]["enabled"] is False
    assert len(config["jira"]["instances"]) == 1


def test_the_form_output_survives_the_seed_service():
    # The builder and the store must agree: anything the form can produce has to
    # be storable, credentials included.
    db = _database()
    config = _seed_config_from_form(
        _form(
            {
                "llm_provider": "github_copilot",
                "llm_model": "gpt-5.4",
                "llm_api_key": "shared-key",
                "jira_enabled": "on",
                "jira_instance_count": "1",
                "jira_instances_0_enabled": "1",
                "jira_instances_0_name": "Prod",
                "jira_instances_0_url": "https://x.atlassian.net",
                "jira_instances_0_token": "shared-token",
                "jira_instances_0_project": "ABC",
                "github_enabled": "on",
            }
        )
    )

    RuntimeProfileSeedService(db).save_seed(config)
    stored = RuntimeProfileSeedService(db).get_seed()

    assert stored["jira"]["instances"][0]["project"] == "ABC"
    assert stored["jira"]["instances"][0]["token"] == "shared-token"
    assert stored["llm"]["provider"] == "github_copilot"
    assert stored["llm"]["api_key"] == "shared-key"


# ------------------------------------------------------------- what members get


def test_a_new_member_inherits_the_seeded_credentials():
    # The whole point of seeding a shared account: the member's first profile
    # already has it, so there is nothing left for them to paste in.
    db = _database()
    RuntimeProfileSeedService(db).save_seed(
        {
            "jira": {
                "enabled": True,
                "instances": [{"name": "Prod", "url": "https://x.atlassian.net", "token": "shared-token"}],
            }
        }
    )

    config_json = RuntimeProfileService(db)._seeded_default_config_json()

    assert "shared-token" in config_json


def test_a_seed_without_credentials_still_leaves_them_to_the_member():
    db = _database()
    RuntimeProfileSeedService(db).save_seed(
        {"jira": {"enabled": True, "instances": [{"name": "Prod", "url": "https://x.atlassian.net"}]}}
    )

    config_json = RuntimeProfileService(db)._seeded_default_config_json()

    for field in SENSITIVE_FIELD_NAMES:
        assert f'"{field}"' not in config_json, field
