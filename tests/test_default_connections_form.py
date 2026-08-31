"""Default Connections is a form, not a JSON textarea.

Editing raw JSON was the original shape and it was unmaintainable: an admin had
to know the config schema, and a stray comma failed the whole save. The panel
now mirrors the Connections form -- same section layout, same instance cards --
minus every credential field, since the seed is shared with all members.
"""
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.datastructures import FormData

from app.db import Base
from app.services.profile_secret_encryption import SENSITIVE_FIELD_NAMES
from app.services.runtime_profile_seed_service import RuntimeProfileSeedService
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


def test_the_panel_offers_no_credential_field():
    # The whole point of the seed is that it carries no secrets, so the form
    # must not invite one -- a field the server then refuses is a trap.
    html = _panel_html(
        seed={"jira": {"enabled": True, "instances": [{"name": "P", "url": "https://x"}]}}
    )

    assert 'type="password"' not in html
    for field in SENSITIVE_FIELD_NAMES:
        assert f'data-field="{field}"' not in html, field
        assert f'name="llm_{field}"' not in html, field


def test_instance_containers_opt_out_of_credential_fields():
    # addInstanceRow builds the card client-side; without this marker a freshly
    # added row would carry the profile panel's username/password/token block.
    html = _panel_html()

    assert html.count('data-instance-credentials="none"') >= 1

    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    assert 'container.dataset.instanceCredentials !== "none"' in js


def test_provider_offers_an_unset_option():
    # Without it the browser preselects whichever provider sorts first, so
    # saving an untouched form silently pins every new member to it.
    html = _panel_html()

    assert '<option value="">No default' in html


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


def test_only_non_secret_fields_are_read_from_the_form():
    # Even if a credential field reached the request, this builder never looks
    # for one. The service re-checks on save, so the guarantee is doubled.
    config = _seed_config_from_form(
        _form(
            {
                "jira_enabled": "on",
                "jira_instance_count": "1",
                "jira_instances_0_name": "Prod",
                "jira_instances_0_url": "https://x",
                "jira_instances_0_token": "should-be-ignored",
                "llm_api_key": "should-be-ignored",
            }
        )
    )

    assert config["jira"]["instances"][0] == {"enabled": False, "name": "Prod", "url": "https://x", "project": "", "api_version": ""}
    assert "api_key" not in config.get("llm", {})


def test_context_size_is_stored_as_a_number():
    config = _seed_config_from_form(_form({"llm_max_context_tokens": "256000"}))

    assert config["llm"]["max_context_tokens"] == 256000


def test_a_non_numeric_context_size_is_ignored():
    config = _seed_config_from_form(_form({"llm_max_context_tokens": "lots"}))

    assert "max_context_tokens" not in config.get("llm", {})


@pytest.mark.parametrize(
    "form_pairs,section,expected",
    [
        ({"github_enabled": "on", "github_base_url": "https://ghe/api/v3"}, "github", {"enabled": True, "base_url": "https://ghe/api/v3"}),
        ({"proxy_enabled": "on", "proxy_url": "http://p:8080"}, "proxy", {"enabled": True, "url": "http://p:8080"}),
        ({"aws_enabled": "on", "aws_domain": "corp"}, "aws", {"enabled": True, "domain": "corp"}),
        ({"mobile_enabled": "on"}, "mobile-auto", {"enabled": True}),
    ],
)
def test_simple_sections_round_trip(form_pairs, section, expected):
    assert _seed_config_from_form(_form(form_pairs))[section] == expected


def test_a_disabled_section_with_instances_is_still_stored():
    # Turning Jira off should not throw away the instances an admin configured;
    # they may be turning it off temporarily.
    config = _seed_config_from_form(
        _form({"jira_instance_count": "1", "jira_instances_0_name": "Prod", "jira_instances_0_url": "https://x"})
    )

    assert config["jira"]["enabled"] is False
    assert len(config["jira"]["instances"]) == 1


def test_the_form_output_survives_the_seed_service():
    # The builder and the validator must agree: anything the form can produce
    # has to be storable.
    db = _database()
    config = _seed_config_from_form(
        _form(
            {
                "llm_provider": "github_copilot",
                "llm_model": "gpt-5.4",
                "jira_enabled": "on",
                "jira_instance_count": "1",
                "jira_instances_0_enabled": "1",
                "jira_instances_0_name": "Prod",
                "jira_instances_0_url": "https://x.atlassian.net",
                "jira_instances_0_project": "ABC",
                "github_enabled": "on",
            }
        )
    )

    RuntimeProfileSeedService(db).save_seed(config)
    stored = RuntimeProfileSeedService(db).get_seed()

    assert stored["jira"]["instances"][0]["project"] == "ABC"
    assert stored["llm"]["provider"] == "github_copilot"
