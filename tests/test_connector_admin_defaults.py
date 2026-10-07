"""Get administrator defaults: the diff/apply service and its two web routes.

An existing member's settings row never sees later edits to the admin's
Default connectors seed on its own; the button on every settings connector
panel except the model provider pulls them in, and the member decides each
clash (docs/CONNECTORS_CONTRACT.md section 7.1).
"""
from __future__ import annotations

import json

import pytest

from app.models import AuditLog
from app.models.runtime_profile import RuntimeProfile
from app.schemas.runtime_profile import sanitize_runtime_profile_config_dict
from app.services.connector_defaults_service import (
    BOTH_SUFFIX,
    DefaultsChanged,
    admin_defaults_sections,
    apply_connector_defaults,
    preview_connector_defaults,
)
from app.services.connector_registry import CONNECTOR_REGISTRY, SETTINGS_CONNECTORS, get_connector_spec
from app.services.runtime_profile_seed_service import RuntimeProfileSeedService
from tests.test_web_connector_settings import _bind_profile, _build_env, _saved

JIRA = get_connector_spec("jira")
GITHUB = get_connector_spec("github")
NEXUS = get_connector_spec("nexus")
PGSQL = get_connector_spec("pgsql")
AWS = get_connector_spec("aws")
JENKINS = get_connector_spec("jenkins")
BROWSERSTACK = get_connector_spec("browserstack")
PROXY = get_connector_spec("proxy")


def _apply(spec, mine, seed, decisions=None):
    preview = preview_connector_defaults(spec, mine, seed)
    config, summary = apply_connector_defaults(spec, mine, seed, decisions or {}, preview["fingerprint"])
    return preview, config, summary


def _only_conflict(preview):
    (conflict,) = preview["conflicts"]
    return conflict


# ------------------------------------------------------------------- registry


def test_every_settings_connector_but_the_model_provider_offers_admin_defaults():
    offered = {spec.type for spec in SETTINGS_CONNECTORS if spec.admin_defaults}
    assert offered == {spec.type for spec in SETTINGS_CONNECTORS} - {"llm"}
    assert CONNECTOR_REGISTRY["local_bridge"].admin_defaults is False


def test_the_commit_identity_is_never_pulled_in():
    assert admin_defaults_sections(GITHUB) == ("github",)
    seed = {"github": {"base_url": "https://ghe"}, "git": {"user": {"name": "CI Bot", "email": "ci@corp"}}}
    preview = preview_connector_defaults(GITHUB, {"github": {"base_url": "https://ghe"}, "git": {"user": {"name": "Me"}}}, seed)
    assert preview["up_to_date"] is True
    _, config, _ = _apply(GITHUB, {"git": {"user": {"name": "Me"}}}, seed)
    assert config["git"] == {"user": {"name": "Me"}}


# ---------------------------------------------------------------- the preview


def test_no_seed_for_the_connector_is_not_available():
    preview = preview_connector_defaults(JIRA, {"jira": {"enabled": True}}, {"github": {"base_url": "https://x"}})
    assert preview["available"] is False
    assert preview["up_to_date"] is False
    assert preview["additions"] == [] and preview["conflicts"] == []


def test_a_row_that_already_matches_the_seed_is_up_to_date():
    seed = {"jira": {"enabled": True, "instances": [{"name": "Prod", "url": "https://x.atlassian.net", "project": "ABC"}]}}
    mine = {"jira": {"enabled": False, "instances": [{"name": "prod", "url": "https://X.atlassian.net/", "project": "ABC", "token": "own"}]}}
    preview = preview_connector_defaults(JIRA, mine, seed)
    assert preview["available"] is True
    assert preview["up_to_date"] is True, preview


def test_new_instances_and_blank_fields_are_additions_not_conflicts():
    seed = {
        "jira": {
            "enabled": True,
            "instances": [
                {"name": "Prod", "url": "https://x.atlassian.net", "project": "ABC", "token": "shared"},
                {"name": "Sandbox", "url": "https://sb.atlassian.net"},
            ],
        }
    }
    mine = {"jira": {"enabled": True, "instances": [{"name": "Prod", "url": "https://x.atlassian.net"}]}}
    preview = preview_connector_defaults(JIRA, mine, seed)
    assert preview["conflicts"] == []
    labels = {item["label"]: item["detail"] for item in preview["additions"]}
    assert labels["Jira instance Sandbox"].startswith("New: ")
    assert "https://sb.atlassian.net" in labels["Jira instance Sandbox"]
    assert labels["Jira instance Prod"] == "Fills in: API token Set · Project ABC"

    _, config, summary = _apply(JIRA, mine, seed)
    rows = config["jira"]["instances"]
    assert [row["name"] for row in rows] == ["Prod", "Sandbox"]
    assert rows[0]["project"] == "ABC" and rows[0]["token"] == "shared"
    assert summary == {
        "connector": "jira",
        "sections": ["jira"],
        "additions": 2,
        "conflicts": 0,
        "choices": {"admin": 0, "mine": 0, "both": 0},
        "changes": 2,
    }


def test_a_matched_instance_with_a_differing_field_is_an_item_conflict_with_three_options():
    seed = {"jira": {"instances": [{"name": "Prod", "url": "https://x.atlassian.net", "token": "shared", "api_version": "3"}]}}
    mine = {"jira": {"instances": [{"name": "Jira", "url": "https://x.atlassian.net", "token": "own", "api_version": "2"}]}}
    preview = preview_connector_defaults(JIRA, mine, seed)
    assert preview["additions"] == []
    conflict = _only_conflict(preview)
    assert conflict["kind"] == "item"
    assert conflict["label"] == "Jira instance Jira"
    assert conflict["options"] == ["admin", "mine", "both"]
    fields = {field["label"]: field for field in conflict["fields"]}
    assert fields["Name"] == {"label": "Name", "mine": "Jira", "theirs": "Prod", "secret": False}
    assert fields["API version"]["mine"] == "2" and fields["API version"]["theirs"] == "3"
    # The token differs, which the member must know; its values stay hidden.
    assert fields["API token"] == {"label": "API token", "mine": "Set", "theirs": "Set", "secret": True}


def test_secret_values_never_appear_in_a_preview_and_urls_are_redacted():
    seed = {
        "jira": {"instances": [{"name": "Prod", "url": "https://x", "token": "shared-token-value", "password": "shared-pw"}]},
        "proxy": {"url": "http://bot:s3cret-pw@proxy.corp:8080"},
    }
    mine = {"jira": {"instances": [{"name": "Prod", "url": "https://x", "token": "my-token-value"}]}}
    text = json.dumps(preview_connector_defaults(JIRA, mine, seed))
    for secret in ("shared-token-value", "shared-pw", "my-token-value"):
        assert secret not in text
    proxy = json.dumps(preview_connector_defaults(PROXY, {}, seed))
    assert "s3cret-pw" not in proxy
    assert "proxy.corp" in proxy


def test_choosing_admin_lays_the_seed_row_over_mine_and_keeps_member_only_fields():
    seed = {"jira": {"instances": [{"name": "Prod", "url": "https://x", "token": "shared"}]}}
    mine = {"jira": {"instances": [{"name": "Prod", "url": "https://x", "token": "own", "project": "MINE", "enabled": False}]}}
    conflict = _only_conflict(preview_connector_defaults(JIRA, mine, seed))
    _, config, summary = _apply(JIRA, mine, seed, {conflict["id"]: "admin"})
    (row,) = config["jira"]["instances"]
    assert row["token"] == "shared"
    assert row["project"] == "MINE"
    # Whether the row is on stays the member's choice.
    assert row["enabled"] is False
    assert summary["choices"] == {"admin": 1, "mine": 0, "both": 0}


def test_a_seed_credential_of_another_kind_is_a_conflict_and_replaces_mine_as_a_group():
    seed = {"jenkins": {"instances": [{"name": "ci", "url": "https://ci", "username": "svc", "password": "shared-pw"}]}}
    mine = {"jenkins": {"instances": [{"name": "ci", "url": "https://ci", "username": "me", "token": "my-token"}]}}
    preview = preview_connector_defaults(JENKINS, mine, seed)
    conflict = _only_conflict(preview)
    fields = {field["label"]: field for field in conflict["fields"]}
    assert fields["Password"] == {"label": "Password", "mine": "Not set", "theirs": "Set", "secret": True}
    assert fields["Username"]["mine"] == "me"
    _, config, _ = _apply(JENKINS, mine, seed, {conflict["id"]: "admin"})
    (row,) = config["jenkins"]["instances"]
    assert row == {"name": "ci", "url": "https://ci", "username": "svc", "password": "shared-pw"}


def test_choosing_both_appends_the_seed_row_renamed_when_its_name_is_taken():
    seed = {"nexus": {"instances": [{"name": "Prod", "url": "https://nexus.corp", "token": "shared"}]}}
    mine = {"nexus": {"instances": [{"name": "Prod", "url": "https://nexus.old", "token": "own"}]}}
    conflict = _only_conflict(preview_connector_defaults(NEXUS, mine, seed))
    _, config, _ = _apply(NEXUS, mine, seed, {conflict["id"]: "both"})
    rows = config["nexus"]["instances"]
    assert [row["name"] for row in rows] == ["Prod", f"Prod ({BOTH_SUFFIX})"]
    assert rows[0]["url"] == "https://nexus.old" and rows[0]["token"] == "own"
    assert rows[1]["url"] == "https://nexus.corp" and rows[1]["token"] == "shared"
    # The sanitizers keep both rows: the names differ.
    assert len(sanitize_runtime_profile_config_dict(config)["nexus"]["instances"]) == 2


def test_both_renames_jira_rows_too_and_the_copy_then_matches_its_seed_row():
    seed = {"jira": {"instances": [{"name": "Prod", "url": "https://x", "token": "shared"}]}}
    mine = {"jira": {"instances": [{"name": "Prod", "url": "https://x", "token": "own"}]}}
    conflict = _only_conflict(preview_connector_defaults(JIRA, mine, seed))
    _, config, _ = _apply(JIRA, mine, seed, {conflict["id"]: "both"})
    assert [row["name"] for row in config["jira"]["instances"]] == ["Prod", f"Prod ({BOTH_SUFFIX})"]
    # Next time, the copy is the seed's row: nothing left to pull in.
    assert preview_connector_defaults(JIRA, config, seed)["up_to_date"] is True


def test_keeping_both_twice_numbers_the_second_copy():
    seed = {"nexus": {"instances": [{"name": "Prod", "url": "https://nexus.corp"}]}}
    mine = {
        "nexus": {
            "instances": [
                {"name": "Prod", "url": "https://nexus.old"},
                {"name": f"Prod ({BOTH_SUFFIX})", "url": "https://nexus.older"},
            ]
        }
    }
    preview = preview_connector_defaults(NEXUS, mine, seed)
    # Both of my rows are "Prod" by base name; the seed row is matched to the
    # one whose other fields clash least, here a tie broken by position.
    conflict = _only_conflict(preview)
    _, config, _ = _apply(NEXUS, mine, seed, {conflict["id"]: "both"})
    assert [row["name"] for row in config["nexus"]["instances"]][-1] == f"Prod ({BOTH_SUFFIX} 2)"


def test_a_seed_name_another_of_my_rows_has_is_not_offered():
    # The seed calls my second server by my first server's name; taking that
    # name would make the sanitizer drop a row, so the name is left out of
    # the clash and the row keeps its own name on "admin".
    seed = {"nexus": {"instances": [{"name": "Prod", "url": "https://nexus.two", "token": "shared"}]}}
    mine = {"nexus": {"instances": [{"name": "Prod", "url": "https://nexus.one"}, {"name": "Second", "url": "https://nexus.two"}]}}
    preview = preview_connector_defaults(NEXUS, mine, seed)
    assert preview["conflicts"] == []
    (addition,) = preview["additions"]
    assert addition["label"] == "Nexus instance Second"
    assert addition["detail"] == "Fills in: API token Set"
    _, config, _ = _apply(NEXUS, mine, seed)
    rows = config["nexus"]["instances"]
    assert [row["name"] for row in rows] == ["Prod", "Second"]
    assert rows[1]["token"] == "shared"


def test_the_best_matching_row_wins_not_the_first():
    # Two of my PostgreSQL rows point at one database; the seed row is the
    # one whose name it carries.
    seed = {"pgsql": {"instances": [{"name": "orders-admin", "host": "h", "database": "d", "username": "svc", "password": "x"}]}}
    mine = {
        "pgsql": {
            "instances": [
                {"name": "orders", "host": "h", "database": "d", "username": "u1"},
                {"name": "orders-admin", "host": "h", "database": "d", "username": "u2"},
            ]
        }
    }
    conflict = _only_conflict(preview_connector_defaults(PGSQL, mine, seed))
    assert conflict["label"] == "PostgreSQL instance orders-admin"
    assert {field["label"] for field in conflict["fields"]} == {"Username"}


def test_keeping_mine_leaves_the_row_alone_including_its_blank_fields():
    seed = {"jira": {"instances": [{"name": "Prod", "url": "https://x", "project": "ABC", "api_version": "3"}]}}
    mine = {"jira": {"instances": [{"name": "Prod", "url": "https://x", "api_version": "2"}]}}
    conflict = _only_conflict(preview_connector_defaults(JIRA, mine, seed))
    _, config, summary = _apply(JIRA, mine, seed, {conflict["id"]: "mine"})
    assert config["jira"]["instances"] == [{"name": "Prod", "url": "https://x", "api_version": "2"}]
    assert summary["choices"]["mine"] == 1
    assert summary["changes"] == 0


def test_a_conflict_without_a_decision_keeps_mine():
    seed = {"github": {"base_url": "https://ghe/api/v3"}}
    mine = {"github": {"base_url": "https://other/api/v3"}}
    _, config, summary = _apply(GITHUB, mine, seed, {})
    assert config["github"]["base_url"] == "https://other/api/v3"
    assert summary["choices"]["mine"] == 1


def test_unknown_choices_unoffered_choices_and_a_stale_fingerprint_are_refused():
    seed = {"github": {"base_url": "https://ghe/api/v3"}}
    mine = {"github": {"base_url": "https://other/api/v3"}}
    preview = preview_connector_defaults(GITHUB, mine, seed)
    conflict = _only_conflict(preview)
    assert conflict["kind"] == "field" and conflict["options"] == ["admin", "mine"]
    with pytest.raises(ValueError):
        apply_connector_defaults(GITHUB, mine, seed, {conflict["id"]: "theirs"}, preview["fingerprint"])
    with pytest.raises(ValueError):
        apply_connector_defaults(GITHUB, mine, seed, {conflict["id"]: "both"}, preview["fingerprint"])
    # An id the preview did not list is ignored.
    config, _ = apply_connector_defaults(GITHUB, mine, seed, {"no.such": "admin"}, preview["fingerprint"])
    assert config["github"]["base_url"] == "https://other/api/v3"
    with pytest.raises(DefaultsChanged):
        apply_connector_defaults(GITHUB, mine, seed, {}, "0000000000000000")
    with pytest.raises(DefaultsChanged):
        apply_connector_defaults(GITHUB, mine, {}, {}, preview["fingerprint"])


def test_the_fingerprint_follows_this_connectors_sections_on_both_sides_but_not_credential_values():
    seed = {"github": {"base_url": "https://ghe/api/v3", "api_token": "t1"}, "jira": {"enabled": True}}
    mine = {"github": {"base_url": "https://mine"}, "proxy": {"url": "http://p"}}
    first = preview_connector_defaults(GITHUB, mine, seed)["fingerprint"]
    seed["jira"]["enabled"] = False
    mine["proxy"]["url"] = "http://other"
    assert preview_connector_defaults(GITHUB, mine, seed)["fingerprint"] == first
    seed["github"]["api_token"] = "rotated-but-still-set"
    assert preview_connector_defaults(GITHUB, mine, seed)["fingerprint"] == first
    seed["github"]["base_url"] = "https://changed"
    second = preview_connector_defaults(GITHUB, mine, seed)["fingerprint"]
    assert second != first
    # The member saving their row in another tab invalidates the dialog too.
    mine["github"]["base_url"] = "https://mine-2"
    assert preview_connector_defaults(GITHUB, mine, seed)["fingerprint"] != second


def test_the_enabled_switch_is_never_a_conflict_but_a_new_section_takes_the_seeds():
    seed = {"proxy": {"enabled": True, "url": "http://proxy.corp:8080"}}
    off = {"proxy": {"enabled": False, "url": "http://proxy.corp:8080"}}
    assert preview_connector_defaults(PROXY, off, seed)["up_to_date"] is True

    preview, config, _ = _apply(PROXY, {}, seed)
    assert [item["label"] for item in preview["additions"]] == ["Proxy URL"]
    assert config["proxy"] == {"enabled": True, "url": "http://proxy.corp:8080"}

    # A section the member has keeps its own switch when a field is filled in.
    _, config, _ = _apply(PROXY, {"proxy": {"enabled": False}}, seed)
    assert config["proxy"] == {"enabled": False, "url": "http://proxy.corp:8080"}


def test_github_scalar_conflicts_offer_no_both_and_the_token_fills_in():
    seed = {"github": {"enabled": True, "base_url": "https://ghe/api/v3", "api_token": "shared"}}
    mine = {"github": {"enabled": True, "base_url": "https://old/api/v3"}}
    preview = preview_connector_defaults(GITHUB, mine, seed)
    assert [(item["label"], item["detail"]) for item in preview["additions"]] == [("GitHub API token", "Set")]
    conflict = _only_conflict(preview)
    assert conflict["label"] == "GitHub API base URL" and conflict["options"] == ["admin", "mine"]
    _, config, _ = _apply(GITHUB, mine, seed, {conflict["id"]: "admin"})
    assert config["github"] == {"enabled": True, "base_url": "https://ghe/api/v3", "api_token": "shared"}


def test_a_field_the_panel_shows_with_a_default_is_not_blank():
    seed = {"aws": {"provider": "saml2aws", "domain": "corp"}}
    mine = {"aws": {"enabled": True, "username": "me"}}
    preview = preview_connector_defaults(AWS, mine, seed)
    assert [item["label"] for item in preview["additions"]] == ["AWS domain"]
    conflict = _only_conflict(preview)
    assert conflict["label"] == "AWS provider"
    assert conflict["fields"] == [{"label": "Provider", "mine": "adfs-assume", "theirs": "saml2aws", "secret": False}]
    assert preview_connector_defaults(AWS, mine, {"aws": {"provider": "adfs-assume"}})["up_to_date"] is True

    seed = {"pgsql": {"instances": [{"name": "o", "host": "h", "database": "d", "username": "u", "port": 5433, "sslmode": "require"}]}}
    mine = {"pgsql": {"instances": [{"name": "o", "host": "h", "database": "d", "username": "u"}]}}
    conflict = _only_conflict(preview_connector_defaults(PGSQL, mine, seed))
    assert conflict["fields"] == [{"label": "Port", "mine": "5432", "theirs": "5433", "secret": False}]


def test_postgresql_rows_match_by_host_and_database_before_name():
    seed = {"pgsql": {"instances": [{"name": "orders", "host": "db.corp", "database": "orders", "username": "ro", "max_rows": 100}]}}
    mine = {"pgsql": {"instances": [{"name": "Orders UAT", "host": "DB.corp", "database": "orders", "username": "me"}]}}
    preview = preview_connector_defaults(PGSQL, mine, seed)
    conflict = _only_conflict(preview)
    assert conflict["label"] == "PostgreSQL instance Orders UAT"
    assert {field["label"] for field in conflict["fields"]} == {"Name", "Username", "Max rows"}
    assert preview["additions"] == []
    _, config, _ = _apply(PGSQL, mine, seed, {conflict["id"]: "admin"})
    (row,) = config["pgsql"]["instances"]
    assert row["username"] == "ro" and row["max_rows"] == 100 and row["name"] == "orders"


def test_aws_accounts_and_eks_clusters_match_through_the_account_id_and_offer_no_both():
    seed = {
        "aws": {
            "enabled": True,
            "accounts": [{"name": "cps-dev", "account_id": "123456789012", "role": "ReadOnly", "regions": ["eu-west-1"]}],
            "eks_clusters": [{"account": "cps-dev", "cluster": "main", "private_endpoint": "https://vpce.example", "region": "eu-west-1"}],
        }
    }
    mine = {
        "aws": {
            "enabled": True,
            "accounts": [{"name": "dev", "account_id": "123456789012", "role": "Admin", "regions": ["eu-west-1"]}],
            "eks_clusters": [{"account": "dev", "cluster": "main", "private_endpoint": "https://vpce.old"}],
        }
    }
    preview = preview_connector_defaults(AWS, mine, seed)
    assert preview["additions"] == []
    conflicts = {item["label"]: item for item in preview["conflicts"]}
    assert set(conflicts) == {"AWS account dev", "EKS cluster main"}
    assert conflicts["AWS account dev"]["options"] == ["admin", "mine"]
    assert {field["label"] for field in conflicts["AWS account dev"]["fields"]} == {"Name", "IAM role"}
    assert conflicts["EKS cluster main"]["options"] == ["admin", "mine"]
    assert {field["label"] for field in conflicts["EKS cluster main"]["fields"]} == {"Private endpoint"}


def test_an_accepted_account_rename_is_followed_by_the_default_and_the_eks_rows():
    seed = {"aws": {"accounts": [{"name": "cps-dev", "account_id": "123456789012", "role": "ReadOnly"}]}}
    mine = {
        "aws": {
            "default_account": "dev",
            "accounts": [{"name": "dev", "account_id": "123456789012", "role": "ReadOnly"}],
            "eks_clusters": [{"account": "dev", "cluster": "main", "private_endpoint": "https://vpce.old"}],
        }
    }
    conflict = _only_conflict(preview_connector_defaults(AWS, mine, seed))
    _, config, _ = _apply(AWS, mine, seed, {conflict["id"]: "admin"})
    assert config["aws"]["accounts"][0]["name"] == "cps-dev"
    assert config["aws"]["default_account"] == "cps-dev"
    assert config["aws"]["eks_clusters"][0]["account"] == "cps-dev"


def test_a_seed_eks_row_names_the_account_the_members_way_or_is_not_added():
    seed = {
        "aws": {
            "accounts": [{"name": "cps-dev", "account_id": "123456789012"}, {"name": "cps-prod", "account_id": "210987654321"}],
            "eks_clusters": [
                {"account": "cps-dev", "cluster": "main", "private_endpoint": "https://vpce.dev"},
                {"account": "cps-prod", "cluster": "main", "private_endpoint": "https://vpce.prod"},
                {"account": "nowhere", "cluster": "ghost", "private_endpoint": "https://vpce.ghost"},
            ],
        }
    }
    mine = {"aws": {"accounts": [{"name": "dev", "account_id": "123456789012"}]}}
    preview = preview_connector_defaults(AWS, mine, seed)
    # My "dev" is the seed's "cps-dev" (same id); I keep my name for it.
    conflict = _only_conflict(preview)
    assert conflict["label"] == "AWS account dev"
    _, config, _ = _apply(AWS, mine, seed, {conflict["id"]: "mine"})
    accounts = {row["name"]: row["account_id"] for row in config["aws"]["accounts"]}
    assert accounts == {"dev": "123456789012", "cps-prod": "210987654321"}
    eks = {row["cluster"] + "@" + row["account"] for row in config["aws"]["eks_clusters"]}
    assert eks == {"main@dev", "main@cps-prod"}


def test_a_dangling_default_instance_or_account_is_dropped_on_apply():
    seed = {"nexus": {"default_instance": "Shared", "instances": [{"name": "Shared", "url": "https://nexus.corp"}]}}
    mine = {"nexus": {"instances": [{"name": "Shared", "url": "https://nexus.mine"}]}}
    conflict = _only_conflict(preview_connector_defaults(NEXUS, mine, seed))
    _, config, _ = _apply(NEXUS, mine, seed, {conflict["id"]: "mine"})
    # Keeping my row means the seed's default still names a row I have.
    assert config["nexus"]["default_instance"] == "Shared"

    seed = {"aws": {"default_account": "cps-dev", "accounts": [{"name": "cps-dev", "account_id": "123456789012"}]}}
    mine = {"aws": {"accounts": [{"name": "dev", "account_id": "123456789012", "role": "Admin"}]}}
    conflict = _only_conflict(preview_connector_defaults(AWS, mine, seed))
    _, config, _ = _apply(AWS, mine, seed, {conflict["id"]: "mine"})
    assert "default_account" not in config["aws"]
    assert config["aws"]["accounts"][0]["name"] == "dev"


def test_an_accepted_instance_rename_is_followed_by_the_default_instance():
    seed = {"nexus": {"instances": [{"name": "Nexus-Prod", "url": "https://nexus.corp"}]}}
    mine = {"nexus": {"default_instance": "Prod", "instances": [{"name": "Prod", "url": "https://nexus.corp"}]}}
    conflict = _only_conflict(preview_connector_defaults(NEXUS, mine, seed))
    _, config, _ = _apply(NEXUS, mine, seed, {conflict["id"]: "admin"})
    assert config["nexus"]["instances"][0]["name"] == "Nexus-Prod"
    assert config["nexus"]["default_instance"] == "Nexus-Prod"


def test_seed_rows_the_sanitizer_would_drop_are_not_promised():
    seed = {"jira": {"instances": [{"name": "No URL"}, {"name": "Ok", "url": "https://x"}]}}
    preview = preview_connector_defaults(JIRA, {}, seed)
    assert [item["label"] for item in preview["additions"]] == ["Jira instance Ok"]


def test_fields_the_panel_does_not_render_are_not_pulled_in():
    seed = {"mobile-auto": {"enabled": True, "state_dir": "/tmp/x", "browserstack": {"username": "team", "local": {"mode": "managed"}}}}
    preview = preview_connector_defaults(BROWSERSTACK, {}, seed)
    assert [item["label"] for item in preview["additions"]] == ["BrowserStack username"]
    _, config, _ = _apply(BROWSERSTACK, {}, seed)
    assert config["mobile-auto"] == {"enabled": True, "browserstack": {"username": "team"}}


def test_a_legacy_flat_jenkins_row_matches_the_seeds_instance():
    seed = {"jenkins": {"instances": [{"name": "jenkins", "url": "https://ci.corp", "username": "svc"}]}}
    mine = {"jenkins": {"enabled": True, "url": "https://ci.corp/", "password": "pw"}}
    preview = preview_connector_defaults(JENKINS, mine, seed)
    assert preview["conflicts"] == []
    assert [item["label"] for item in preview["additions"]] == ["Jenkins instance jenkins"]
    _, config, _ = _apply(JENKINS, mine, seed)
    (row,) = config["jenkins"]["instances"]
    assert row == {"name": "jenkins", "url": "https://ci.corp", "username": "svc", "password": "pw"}


def test_browserstack_compares_the_nested_credentials():
    seed = {"mobile-auto": {"enabled": True, "browserstack": {"username": "team", "access_key": "shared-key"}}}
    mine = {"mobile-auto": {"enabled": False, "browserstack": {"username": "me"}}}
    preview = preview_connector_defaults(BROWSERSTACK, mine, seed)
    assert [item["label"] for item in preview["additions"]] == ["BrowserStack access key"]
    conflict = _only_conflict(preview)
    assert conflict["label"] == "BrowserStack username"
    assert conflict["fields"][0]["mine"] == "me" and conflict["fields"][0]["theirs"] == "team"
    _, config, _ = _apply(BROWSERSTACK, mine, seed, {conflict["id"]: "admin"})
    assert config["mobile-auto"] == {"enabled": False, "browserstack": {"username": "team", "access_key": "shared-key"}}


def test_apply_touches_only_this_connectors_sections():
    seed = {"jira": {"instances": [{"name": "Prod", "url": "https://x"}]}, "github": {"base_url": "https://ghe"}}
    mine = {"github": {"base_url": "https://mine"}, "proxy": {"enabled": True, "url": "http://p"}}
    _, config, _ = _apply(JIRA, mine, seed)
    assert config["github"] == {"base_url": "https://mine"}
    assert config["proxy"] == {"enabled": True, "url": "http://p"}
    assert config["jira"]["instances"][0]["url"] == "https://x"


def test_two_seed_rows_on_one_site_are_decided_separately():
    # One Jira site, two projects: both rows match by URL, so the ids must
    # tell them apart or one decision would land on both.
    seed = {
        "jira": {
            "instances": [
                {"name": "Prod ABC", "url": "https://jira.corp", "project": "ABC", "token": "t-abc"},
                {"name": "Prod XYZ", "url": "https://jira.corp", "project": "XYZ", "token": "t-xyz"},
            ]
        }
    }
    mine = {
        "jira": {
            "instances": [
                {"name": "Prod ABC", "url": "https://jira.corp", "project": "ABC", "token": "own1"},
                {"name": "Prod XYZ", "url": "https://jira.corp", "project": "XYZ", "token": "own2"},
            ]
        }
    }
    preview = preview_connector_defaults(JIRA, mine, seed)
    conflicts = {item["label"]: item for item in preview["conflicts"]}
    assert set(conflicts) == {"Jira instance Prod ABC", "Jira instance Prod XYZ"}
    assert conflicts["Jira instance Prod ABC"]["id"] != conflicts["Jira instance Prod XYZ"]["id"]
    _, config, summary = _apply(
        JIRA, mine, seed, {conflicts["Jira instance Prod ABC"]["id"]: "admin", conflicts["Jira instance Prod XYZ"]["id"]: "mine"}
    )
    assert [row["token"] for row in config["jira"]["instances"]] == ["t-abc", "own2"]
    assert summary["choices"] == {"admin": 1, "mine": 1, "both": 0}


def test_ids_never_carry_a_credential_typed_into_a_url():
    seed = {"jira": {"instances": [{"name": "Prod", "url": "https://bot:s3cret-pw@jira.corp"}]}}
    for mine in ({}, {"jira": {"instances": [{"name": "Prod", "url": "https://bot:s3cret-pw@jira.corp", "project": "X"}]}}):
        text = json.dumps(preview_connector_defaults(JIRA, mine, seed))
        assert "s3cret-pw" not in text


def test_an_eks_row_naming_an_account_nobody_has_is_neither_promised_nor_applied():
    seed = {
        "aws": {
            "accounts": [{"name": "cps-dev", "account_id": "123456789012"}],
            "eks_clusters": [{"account": "nowhere", "cluster": "ghost", "private_endpoint": "https://vpce.ghost"}],
        }
    }
    mine = {"aws": {"enabled": True, "accounts": [{"name": "dev", "account_id": "123456789012"}]}}
    preview = preview_connector_defaults(AWS, mine, seed)
    assert preview["additions"] == []
    conflict = _only_conflict(preview)
    _, config, summary = _apply(AWS, mine, seed, {conflict["id"]: "mine"})
    assert summary["changes"] == 0
    assert "eks_clusters" not in config["aws"]


def test_eks_rows_that_differ_only_by_region_are_one_row_with_a_region_clash():
    seed = {"aws": {"accounts": [{"name": "dev", "account_id": "123456789012"}], "eks_clusters": [{"account": "dev", "cluster": "main", "region": "eu-west-1", "private_endpoint": "https://vpce.a"}]}}
    mine = {"aws": {"accounts": [{"name": "dev", "account_id": "123456789012"}], "eks_clusters": [{"account": "dev", "cluster": "main", "region": "us-east-1", "private_endpoint": "https://vpce.a"}]}}
    preview = preview_connector_defaults(AWS, mine, seed)
    assert preview["additions"] == []
    conflict = _only_conflict(preview)
    assert conflict["fields"] == [{"label": "Region", "mine": "us-east-1", "theirs": "eu-west-1", "secret": False}]


def test_a_seed_default_instance_naming_a_row_the_member_renamed_is_dropped():
    seed = {"nexus": {"default_instance": "Shared", "instances": [{"name": "Shared", "url": "https://nexus.corp"}]}}
    mine = {"nexus": {"instances": [{"name": "Other", "url": "https://nexus.corp"}]}}
    preview = preview_connector_defaults(NEXUS, mine, seed)
    conflict = _only_conflict(preview)
    assert [field["label"] for field in conflict["fields"]] == ["Name"]
    _, config, _ = _apply(NEXUS, mine, seed, {conflict["id"]: "mine"})
    assert "default_instance" not in config["nexus"]
    assert config["nexus"]["instances"] == [{"name": "Other", "url": "https://nexus.corp"}]
    # Taking the seed's name makes the default resolve again.
    _, config, _ = _apply(NEXUS, mine, seed, {conflict["id"]: "admin"})
    assert config["nexus"]["default_instance"] == "Shared"
    assert config["nexus"]["instances"][0]["name"] == "Shared"


# ------------------------------------------------------------------ the routes


def _seed(env, config):
    RuntimeProfileSeedService(env.db).save_seed(config)


def test_preview_is_404_for_the_model_provider_unknown_and_local_connectors(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _bind_profile(env.db, env.agent, {})
        for connector_type in ("llm", "local_bridge", "no-such-connector", "mobile"):
            assert env.client.get(f"/app/connectors/{connector_type}/defaults").status_code == 404, connector_type
            assert env.client.post(f"/app/connectors/{connector_type}/defaults/apply", data={"decisions": "{}"}).status_code == 404, connector_type
    finally:
        env.cleanup()


def test_preview_without_a_seed_says_so_and_creates_the_row_on_first_use(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        resp = env.client.get("/app/connectors/jira/defaults")
        assert resp.status_code == 200
        assert resp.headers.get("cache-control") == "no-store"
        payload = resp.json()
        assert payload["connector"] == {"type": "jira", "label": "Jira"}
        assert payload["available"] is False
        assert env.db.query(RuntimeProfile).filter(RuntimeProfile.owner_user_id == env.owner.id).count() == 1
    finally:
        env.cleanup()


def test_preview_reports_conflicts_without_secret_values(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _bind_profile(env.db, env.agent, {"jira": {"instances": [{"name": "Prod", "url": "https://x", "token": "my-secret-token"}]}})
        _seed(env, {"jira": {"enabled": True, "instances": [{"name": "Prod", "url": "https://x", "token": "shared-secret-token", "project": "ABC"}]}})
        resp = env.client.get("/app/connectors/jira/defaults")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["available"] is True and payload["up_to_date"] is False
        assert payload["additions"] == []
        (conflict,) = payload["conflicts"]
        assert conflict["kind"] == "item" and conflict["options"] == ["admin", "mine", "both"]
        assert "my-secret-token" not in resp.text and "shared-secret-token" not in resp.text
    finally:
        env.cleanup()


def test_apply_saves_bumps_the_revision_audits_and_refreshes_the_list(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {"jira": {"enabled": True, "instances": [{"name": "Prod", "url": "https://x", "token": "own"}]}})
        _seed(env, {"jira": {"instances": [{"name": "Prod", "url": "https://x", "token": "shared"}, {"name": "Sandbox", "url": "https://sb"}]}})
        preview = env.client.get("/app/connectors/jira/defaults").json()
        (conflict,) = preview["conflicts"]
        resp = env.client.post(
            "/app/connectors/jira/defaults/apply",
            data={"fingerprint": preview["fingerprint"], "decisions": json.dumps({conflict["id"]: "both"})},
        )
        assert resp.status_code == 200
        assert resp.headers.get("HX-Trigger") == "connectorsChanged"
        assert 'data-settings-status="success"' in resp.text
        assert "Default connectors applied." in resp.text
        assert 'data-connector-type="jira"' in resp.text

        saved = _saved(env.db, rp)
        names = [row["name"] for row in saved["jira"]["instances"]]
        assert names == ["Prod", "Sandbox", f"Prod ({BOTH_SUFFIX})"]
        assert saved["jira"]["instances"][0]["token"] == "own"
        assert saved["jira"]["instances"][2]["token"] == "shared"
        assert rp.revision == 2
        assert env.calls["apply"] == 1

        actions = [row.action for row in env.db.query(AuditLog).all()]
        assert "update_runtime_profile" in actions and "apply_connector_defaults" in actions
        row = env.db.query(AuditLog).filter(AuditLog.action == "apply_connector_defaults").one()
        details = json.loads(row.details_json)
        assert details == {
            "connector": "jira",
            "sections": ["jira"],
            "additions": 1,
            "conflicts": 1,
            "choices": {"admin": 0, "mine": 0, "both": 1},
            "changes": 2,
        }
        for leaked in ("shared", "own", "https://x", conflict["id"]):
            assert leaked not in (row.details_json or "")
    finally:
        env.cleanup()


def test_apply_with_a_stale_fingerprint_saves_nothing(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {"jira": {"instances": [{"name": "Prod", "url": "https://x"}]}})
        _seed(env, {"jira": {"instances": [{"name": "Sandbox", "url": "https://sb"}]}})
        resp = env.client.post("/app/connectors/jira/defaults/apply", data={"fingerprint": "stale", "decisions": "{}"})
        assert resp.status_code == 200
        assert 'data-settings-status="error"' in resp.text
        assert "changed while you were looking" in resp.text
        assert [row["name"] for row in _saved(env.db, rp)["jira"]["instances"]] == ["Prod"]
        assert rp.revision == 1
        assert env.calls["apply"] == 0
        assert env.db.query(AuditLog).count() == 0
    finally:
        env.cleanup()


def test_apply_keeping_mine_everywhere_changes_nothing_and_restarts_nothing(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        # A row stored in the legacy flat Jenkins shape: re-saving it as-is
        # would normalize it and bump the revision over a change nobody made.
        rp = _bind_profile(env.db, env.agent, {"jenkins": {"enabled": True, "url": "https://ci.mine", "username": "me"}})
        _seed(env, {"jenkins": {"instances": [{"name": "jenkins", "url": "https://ci.corp"}]}})
        preview = env.client.get("/app/connectors/jenkins/defaults").json()
        (conflict,) = preview["conflicts"]
        resp = env.client.post(
            "/app/connectors/jenkins/defaults/apply",
            data={"fingerprint": preview["fingerprint"], "decisions": json.dumps({conflict["id"]: "mine"})},
        )
        assert resp.status_code == 200
        assert "Nothing changed" in resp.text
        assert resp.headers.get("HX-Trigger") is None
        assert _saved(env.db, rp)["jenkins"]["url"] == "https://ci.mine"
        assert rp.revision == 1
        assert env.calls["apply"] == 0
    finally:
        env.cleanup()


@pytest.mark.parametrize(
    "decisions",
    ["not json", "[]", '{"a": 1}', '{"a": "theirs"}', "[" * 5000 + "]" * 5000, "x" * 30000],
)
def test_apply_refuses_choices_the_page_did_not_build(monkeypatch, decisions):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {"github": {"base_url": "https://mine"}})
        _seed(env, {"github": {"base_url": "https://ghe"}})
        preview = env.client.get("/app/connectors/github/defaults").json()
        resp = env.client.post("/app/connectors/github/defaults/apply", data={"fingerprint": preview["fingerprint"], "decisions": decisions})
        assert resp.status_code == 200
        assert 'data-settings-status="error"' in resp.text
        assert "not recognized" in resp.text
        assert _saved(env.db, rp)["github"]["base_url"] == "https://mine"
        assert env.calls["apply"] == 0
    finally:
        env.cleanup()


def test_apply_refuses_a_choice_the_conflict_does_not_offer(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {"github": {"base_url": "https://mine"}})
        _seed(env, {"github": {"base_url": "https://ghe"}})
        preview = env.client.get("/app/connectors/github/defaults").json()
        (conflict,) = preview["conflicts"]
        resp = env.client.post(
            "/app/connectors/github/defaults/apply",
            data={"fingerprint": preview["fingerprint"], "decisions": json.dumps({conflict["id"]: "both"})},
        )
        assert 'data-settings-status="error"' in resp.text
        assert _saved(env.db, rp)["github"]["base_url"] == "https://mine"
    finally:
        env.cleanup()


def test_apply_touches_only_the_signed_in_members_row(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _bind_profile(env.db, env.agent, {"jira": {"instances": [{"name": "Prod", "url": "https://x"}]}})
        _seed(env, {"jira": {"instances": [{"name": "Sandbox", "url": "https://sb"}]}})
        env.set_user(env.other)
        preview = env.client.get("/app/connectors/jira/defaults").json()
        resp = env.client.post("/app/connectors/jira/defaults/apply", data={"fingerprint": preview["fingerprint"], "decisions": "{}"})
        assert resp.status_code == 200
        rows = {row.owner_user_id: json.loads(row.config_json) for row in env.db.query(RuntimeProfile).all()}
        assert [row["name"] for row in rows[env.owner.id]["jira"]["instances"]] == ["Prod"]
        assert [row["name"] for row in rows[env.other.id]["jira"]["instances"]] == ["Sandbox"]
    finally:
        env.cleanup()


def test_panels_offer_the_button_except_the_model_provider(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        _bind_profile(env.db, env.agent, {})
        for connector_type in ("jira", "github", "aws", "browserstack", "proxy", "pgsql"):
            text = env.client.get(f"/app/connectors/{connector_type}/panel").text
            assert "data-admin-defaults-button" in text, connector_type
            assert "Get administrator defaults" in text, connector_type
        llm = env.client.get("/app/connectors/llm/panel").text
        assert "data-admin-defaults-button" not in llm
        assert "Get administrator defaults" not in llm
    finally:
        env.cleanup()


def test_apply_refuses_a_decisions_file_part(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {"github": {"base_url": "https://mine"}})
        _seed(env, {"github": {"base_url": "https://ghe"}})
        preview = env.client.get("/app/connectors/github/defaults").json()
        resp = env.client.post(
            "/app/connectors/github/defaults/apply",
            data={"fingerprint": preview["fingerprint"]},
            files={"decisions": ("d.json", b"{}", "application/json")},
        )
        assert resp.status_code == 200
        assert 'data-settings-status="error"' in resp.text
        assert _saved(env.db, rp)["github"]["base_url"] == "https://mine"
        assert env.calls["apply"] == 0
    finally:
        env.cleanup()


def test_apply_whose_only_change_is_dropped_again_reports_nothing_changed(monkeypatch):
    env = _build_env(monkeypatch)
    try:
        rp = _bind_profile(env.db, env.agent, {"nexus": {"instances": [{"name": "Other", "url": "https://nexus.corp"}]}})
        _seed(env, {"nexus": {"default_instance": "Shared", "instances": [{"name": "Shared", "url": "https://nexus.corp"}]}})
        preview = env.client.get("/app/connectors/nexus/defaults").json()
        (conflict,) = preview["conflicts"]
        resp = env.client.post(
            "/app/connectors/nexus/defaults/apply",
            data={"fingerprint": preview["fingerprint"], "decisions": json.dumps({conflict["id"]: "mine"})},
        )
        assert resp.status_code == 200
        assert "Nothing changed" in resp.text
        assert resp.headers.get("HX-Trigger") is None
        assert _saved(env.db, rp)["nexus"] == {"instances": [{"name": "Other", "url": "https://nexus.corp"}]}
        assert rp.revision == 1
        assert env.calls["apply"] == 0
    finally:
        env.cleanup()
