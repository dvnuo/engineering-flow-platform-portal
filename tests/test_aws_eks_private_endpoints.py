"""Behaviour tests for EKS clusters reached through a private endpoint.

An EKS cluster whose API server endpoint is private is reached from another
VPC through a PrivateLink interface endpoint, so the address describe-cluster
reports is useless from the runtime while the certificate is still issued for
it. The aws section therefore lists such clusters, one row each, and the
runtime's aws-auth CLI points kubectl at the private address. Covers the
schema sanitizer, the settings form parser and its validation, the seed form,
the view payload, the end-to-end save, and the rendered cards (server-rendered
and JS-added) on every panel that edits the section.
"""
import json
import re
from pathlib import Path

import pytest

from starlette.datastructures import FormData

from tests.test_aws_account_matrix_settings import (
    FULL_AWS_SECTION,
    _expand_region_slots,
    _js_array_literal,
    _matrix_form,
    _options,
    _section_select_options,
    _selected_options,
)
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
    AWS_EKS_SERVER_CA_MODES,
    AWS_REGIONS,
    PORTAL_MANAGED_FIELD_TREE,
    normalize_eks_private_endpoint,
    sanitize_runtime_profile_aws,
    sanitize_runtime_profile_config_dict,
)
from app.services.help_center import get_topic
from app.web import _seed_config_from_form, _settings_merge_payload, _settings_view_payload

VPCE = "https://vpce-0ab12cd.vpce-svc-0123.eu-west-1.vpce.amazonaws.com"

EKS_ROWS = [
    {"account": "cps-dev", "cluster": "cps-dev-eks", "private_endpoint": VPCE},
    {
        "account": "123456789012",
        "cluster": "cps-prod-eks",
        "region": "eu-west-1",
        "private_endpoint": "https://vpce-prod.example.test",
        "tls_server_name": "prod.internal.example.test",
        "server_ca": "system",
        "enabled": False,
    },
]


def _aws_with_clusters():
    section = json.loads(json.dumps(FULL_AWS_SECTION))
    section["eks_clusters"] = json.loads(json.dumps(EKS_ROWS))
    return section


def _present(row):
    """A row without its blank fields, however the parser spelled them."""
    return {key: value for key, value in row.items() if value not in ("", None)}


# ------------------------------------------------------------------ schema


def test_sanitizer_keeps_the_full_shape_and_normalizes_the_endpoint():
    out = sanitize_runtime_profile_aws(
        {
            "enabled": True,
            "eks_clusters": [
                {"account": " cps-dev ", "cluster": "cps-dev-eks", "private_endpoint": "vpce-0ab.example.test/"},
                {
                    "account": "123456789012",
                    "cluster": "prod",
                    "region": "EU-WEST-1",
                    "private_endpoint": "https://vpce-prod.example.test:8443/",
                    "tls_server_name": " prod.internal ",
                    "enabled": False,
                },
            ],
        }
    )
    assert out["eks_clusters"] == [
        {"account": "cps-dev", "cluster": "cps-dev-eks", "private_endpoint": "https://vpce-0ab.example.test"},
        {
            "account": "123456789012",
            "cluster": "prod",
            "region": "eu-west-1",
            "private_endpoint": "https://vpce-prod.example.test:8443",
            "tls_server_name": "prod.internal",
            "enabled": False,
        },
    ]


def test_sanitizer_drops_rows_the_runtime_could_not_use():
    out = sanitize_runtime_profile_aws(
        {
            "eks_clusters": [
                {"cluster": "no-account", "private_endpoint": VPCE},
                {"account": "cps-dev", "private_endpoint": VPCE},
                {"account": "cps-dev", "cluster": "no-endpoint"},
                {"account": "cps-dev", "cluster": "http", "private_endpoint": "http://plain.example.test"},
                {"account": "cps-dev", "cluster": "creds", "private_endpoint": "https://u:p@host.example.test"},
                "not a row",
                {"account": "cps-dev", "cluster": "ok", "private_endpoint": VPCE},
                # The same cluster of the same account again; the account name
                # compares case-insensitively, as aws-auth compares it.
                {"account": "CPS-DEV", "cluster": "ok", "private_endpoint": "https://dup.example.test"},
            ]
        }
    )
    assert out["eks_clusters"] == [{"account": "cps-dev", "cluster": "ok", "private_endpoint": VPCE}]


def test_the_eks_clusters_key_is_kept_only_when_it_was_sent():
    assert "eks_clusters" not in sanitize_runtime_profile_aws({"enabled": True})
    assert sanitize_runtime_profile_aws({"eks_clusters": []})["eks_clusters"] == []
    assert sanitize_runtime_profile_aws({"eks_clusters": "nope"})["eks_clusters"] == []


def test_field_tree_and_config_sanitizer_pass_the_rows_through():
    assert PORTAL_MANAGED_FIELD_TREE["aws"]["eks_clusters"] is True
    cfg = sanitize_runtime_profile_config_dict({"aws": _aws_with_clusters()})
    assert cfg["aws"]["eks_clusters"] == EKS_ROWS


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("vpce-0ab.example.test", "https://vpce-0ab.example.test"),
        ("https://vpce-0ab.example.test/", "https://vpce-0ab.example.test"),
        ("  https://vpce-0ab.example.test:8443/?x=1#f  ", "https://vpce-0ab.example.test:8443"),
        ("http://plain.example.test", ""),
        ("https://u:p@host.example.test", ""),
        ("https://", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_eks_private_endpoint(raw, expected):
    assert normalize_eks_private_endpoint(raw) == expected


# ------------------------------------------------------------- settings form


def _clusters_form(count=2, **overrides):
    form = _matrix_form()
    form.update(
        {
            "aws_eks_clusters_instance_count": str(count),
            "aws_eks_clusters_instances_0_enabled": "1",
            "aws_eks_clusters_instances_0_account": "cps-dev",
            "aws_eks_clusters_instances_0_cluster": "cps-dev-eks",
            "aws_eks_clusters_instances_0_region": "",
            "aws_eks_clusters_instances_0_private_endpoint": VPCE,
            "aws_eks_clusters_instances_0_tls_server_name": "",
            "aws_eks_clusters_instances_1_account": "123456789012",
            "aws_eks_clusters_instances_1_cluster": "cps-prod-eks",
            "aws_eks_clusters_instances_1_region": "eu-west-1",
            "aws_eks_clusters_instances_1_private_endpoint": "vpce-prod.example.test/",
            "aws_eks_clusters_instances_1_server_ca": "System",
            "aws_eks_clusters_instances_1_tls_server_name": "prod.internal.example.test",
        }
    )
    form.update(overrides)
    return form


def test_settings_form_builds_the_rows_and_normalizes_the_endpoint():
    merged, error = _settings_merge_payload({}, _clusters_form())
    assert error is None
    rows = [_present(row) for row in merged["aws"]["eks_clusters"]]
    assert rows == [
        {"enabled": True, "account": "cps-dev", "cluster": "cps-dev-eks", "private_endpoint": VPCE},
        {
            "enabled": False,
            "account": "123456789012",
            "cluster": "cps-prod-eks",
            "region": "eu-west-1",
            "private_endpoint": "https://vpce-prod.example.test",
            "server_ca": "system",
            "tls_server_name": "prod.internal.example.test",
        },
    ]


BAD_ENDPOINT = (
    "EKS cluster 1 needs an https address for its private endpoint, such as "
    "https://vpce-0ab12cd.vpce-svc-0123.eu-west-1.vpce.amazonaws.com."
)


@pytest.mark.parametrize(
    "overrides,message",
    [
        (
            {"aws_eks_clusters_instances_0_account": ""},
            "EKS cluster 1 needs the account it belongs to: the name or 12-digit id of one of the AWS accounts above.",
        ),
        (
            {"aws_eks_clusters_instances_0_account": "cps-uat"},
            "EKS cluster 1: cps-uat is not one of the AWS accounts above, by name or 12-digit id.",
        ),
        ({"aws_eks_clusters_instances_0_cluster": ""}, "EKS cluster 1 needs the EKS cluster name."),
        ({"aws_eks_clusters_instances_0_private_endpoint": "http://plain.example.test"}, BAD_ENDPOINT),
        ({"aws_eks_clusters_instances_0_private_endpoint": ""}, BAD_ENDPOINT),
        (
            {
                "aws_eks_clusters_instances_1_account": "CPS-DEV",
                "aws_eks_clusters_instances_1_cluster": "cps-dev-eks",
                "aws_eks_clusters_instances_1_region": "",
            },
            "EKS cluster 2 repeats cps-dev-eks for the same account and region.",
        ),
    ],
)
def test_settings_form_refuses_a_row_the_runtime_could_not_use(overrides, message):
    """A bad row comes back as a message naming the card, never as a silent drop."""
    merged, error = _settings_merge_payload({}, _clusters_form(**overrides))
    assert error == message
    assert "aws" not in merged


def test_settings_form_accepts_the_account_given_as_its_id():
    merged, error = _settings_merge_payload({}, _clusters_form(aws_eks_clusters_instances_0_account="818354133892"))
    assert error is None
    assert merged["aws"]["eks_clusters"][0]["account"] == "818354133892"


def test_settings_form_drops_an_empty_card_the_member_added_and_left_alone():
    merged, error = _settings_merge_payload(
        {},
        _clusters_form(
            count=3,
            aws_eks_clusters_instances_2_account="",
            aws_eks_clusters_instances_2_cluster="",
            aws_eks_clusters_instances_2_region="",
            aws_eks_clusters_instances_2_private_endpoint="",
            aws_eks_clusters_instances_2_tls_server_name="",
        ),
    )
    assert error is None
    assert [row["cluster"] for row in merged["aws"]["eks_clusters"]] == ["cps-dev-eks", "cps-prod-eks"]


def test_settings_form_can_clear_the_rows():
    merged, error = _settings_merge_payload(
        {"aws": {"enabled": True, "eks_clusters": json.loads(json.dumps(EKS_ROWS))}},
        _clusters_form(count=0),
    )
    assert error is None
    assert merged["aws"]["eks_clusters"] == []


def test_settings_form_that_did_not_render_the_rows_keeps_them():
    # A form that never carried the count (an older page, or a section the
    # member did not open) must not read as "no rows".
    merged, error = _settings_merge_payload(
        {"aws": {"enabled": True, "eks_clusters": json.loads(json.dumps(EKS_ROWS))}},
        _matrix_form(),
    )
    assert error is None
    assert merged["aws"]["eks_clusters"] == EKS_ROWS


def test_seed_form_reads_the_rows_as_typed():
    seed = _seed_config_from_form(
        {
            "aws_enabled": "on",
            "aws_eks_clusters_instance_count": "2",
            "aws_eks_clusters_instances_0_enabled": "1",
            "aws_eks_clusters_instances_0_account": "cps-dev",
            "aws_eks_clusters_instances_0_cluster": "cps-dev-eks",
            "aws_eks_clusters_instances_0_private_endpoint": VPCE,
            # An empty card left behind is dropped here too.
            "aws_eks_clusters_instances_1_account": "",
        }
    )
    assert [_present(row) for row in seed["aws"]["eks_clusters"]] == [
        {"enabled": True, "account": "cps-dev", "cluster": "cps-dev-eks", "private_endpoint": VPCE}
    ]


def test_view_payload_carries_the_rows_for_the_cards():
    payload = _settings_view_payload({"aws": _aws_with_clusters()})
    rows = payload["aws_eks_clusters"]
    names = {account["name"] for account in FULL_AWS_SECTION["accounts"]}
    by_id = {account["account_id"]: account["name"] for account in FULL_AWS_SECTION["accounts"]}

    def shown(account):
        # A row stored by 12-digit id is shown, and re-posted, as that account's name.
        return by_id.get(account, account)

    # The stored row, plus what the account dropdown needs: which option to
    # select and whether that account is still one of the rows above.
    stripped = [{k: v for k, v in row.items() if k not in ("account_option", "account_listed")} for row in rows]
    assert stripped == [dict(row, account=shown(row["account"])) for row in EKS_ROWS]
    assert [(row["account_option"], row["account_listed"]) for row in rows] == [
        (shown(row["account"]), shown(row["account"]) in names) for row in EKS_ROWS
    ]
    assert payload["aws_account_options"] == [account["name"] for account in FULL_AWS_SECTION["accounts"]]
    assert payload["aws_regions"] == list(AWS_REGIONS)
    assert _settings_view_payload({"aws": {"enabled": True}})["aws_eks_clusters"] == []


# ------------------------------------------------------------- end to end


def test_end_to_end_profile_save_persists_the_rows_and_reloads_them_into_the_panel(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {"aws": {"enabled": False}})
        resp = client.post(f"/app/runtime-profiles/{rp.id}/save", data={"name": "rp", **_clusters_form()})
        assert resp.status_code == 200
        db.refresh(rp)
        stored = json.loads(rp.config_json)["aws"]["eks_clusters"]
        assert stored == [
            {"enabled": True, "account": "cps-dev", "cluster": "cps-dev-eks", "private_endpoint": VPCE},
            {
                "enabled": False,
                "account": "123456789012",
                "cluster": "cps-prod-eks",
                "region": "eu-west-1",
                "private_endpoint": "https://vpce-prod.example.test",
                "server_ca": "system",
                "tls_server_name": "prod.internal.example.test",
            },
        ]

        html = resp.text
        cards = [card for card in _parse_cards(html) if card["group"] == "aws_eks_clusters"]
        assert len(cards) == 2
        assert 'name="aws_eks_clusters_instance_count" value="2"' in html
        assert _selected_options(html, "aws_eks_clusters", 0, "account") == ["cps-dev"]
        assert cards[0]["fields"]["private_endpoint"]["value"] == VPCE
        assert cards[1]["fields"]["tls_server_name"]["value"] == "prod.internal.example.test"
        assert cards[1]["disabled_class"] is True and "Disabled" in cards[1]["text"]

        # A later save that does not carry the rows keeps them.
        again = client.post(
            f"/app/runtime-profiles/{rp.id}/save",
            data={"name": "rp", "__touch_aws": "1", "aws_enabled": "on", "aws_default_region": "eu-west-1"},
        )
        assert again.status_code == 200
        db.refresh(rp)
        assert json.loads(rp.config_json)["aws"]["eks_clusters"] == stored
    finally:
        cleanup()


def test_end_to_end_profile_save_reports_a_bad_row_and_keeps_the_stored_config(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {"aws": {"enabled": True, "domain": "HBEU"}})
        resp = client.post(
            f"/app/runtime-profiles/{rp.id}/save",
            data={"name": "rp", **_clusters_form(aws_eks_clusters_instances_0_account="cps-uat")},
        )
        assert resp.status_code == 200
        assert "EKS cluster 1: cps-uat is not one of the AWS accounts above, by name or 12-digit id." in resp.text
        db.refresh(rp)
        assert json.loads(rp.config_json)["aws"] == {"enabled": True, "domain": "HBEU"}
    finally:
        cleanup()


# ---------------------------------------------------------------- the cards


def test_settings_panel_renders_one_card_per_cluster(monkeypatch):
    html = _render_panel(monkeypatch, {"aws": _aws_with_clusters()})
    cards = [card for card in _parse_cards(html) if card["group"] == "aws_eks_clusters"]

    assert len(cards) == 2
    assert 'name="aws_eks_clusters_instance_count" value="2"' in html
    assert 'data-action="add-instance" data-group="aws_eks_clusters"' in html
    assert _selected_options(html, "aws_eks_clusters", 0, "account") == ["cps-dev"]
    assert cards[0]["fields"]["cluster"]["value"] == "cps-dev-eks"
    assert cards[0]["fields"]["private_endpoint"]["value"] == VPCE
    assert _selected_options(html, "aws_eks_clusters", 0, "region") == []  # "Any region"
    assert _selected_options(html, "aws_eks_clusters", 1, "region") == ["eu-west-1"]
    assert "EKS cluster 1" in cards[0]["text"]
    assert cards[0]["fields"]["enabled"]["aria-label"] == "Enable EKS cluster instance 1"
    assert cards[1]["disabled_class"] is True
    assert "checked" not in cards[1]["fields"]["enabled"]
    assert cards[1]["fields"]["tls_server_name"]["value"] == "prod.internal.example.test"
    assert sorted(cards[0]["fields"]) == ["account", "cluster", "enabled", "private_endpoint", "region", "server_ca", "tls_server_name"]
    # Whose certificate answers at the endpoint is a choice of two, defaulting to the cluster CA.
    assert [value for value, _sel, _label in _options(html, "aws_eks_clusters", 0, "server_ca")] == ["cluster", "system"]
    assert _selected_options(html, "aws_eks_clusters", 0, "server_ca") == ["cluster"]
    assert _selected_options(html, "aws_eks_clusters", 1, "server_ca") == ["system"]


def test_the_cluster_cards_sit_inside_the_aws_section():
    # The rows belong to the aws section: touching a card must mark that
    # section, which the server checks before it reads the rows.
    for rel in ("app/templates/partials/runtime_profile_panel.html", "app/templates/partials/settings_panel.html"):
        text = Path(rel).read_text(encoding="utf-8")
        section = text[text.index('data-managed-section="aws"') :]
        section = section[: section.index("</section>")]
        assert 'data-instance-container="aws_eks_clusters"' in section, rel
        assert 'data-instance-count="aws_eks_clusters"' in section, rel


def test_agent_and_runtime_profile_panels_render_the_same_cluster_card(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        _bind_profile(db, agent, {"aws": _aws_with_clusters()})
        agent_html = client.get(f"/app/agents/{agent.id}/settings/panel").text
        profile_html = client.get(f"/app/runtime-profiles/{agent.runtime_profile_id}/panel").text
    finally:
        cleanup()

    agent_cards = [c for c in _parse_cards(agent_html) if c["group"] == "aws_eks_clusters"]
    profile_cards = [c for c in _parse_cards(profile_html) if c["group"] == "aws_eks_clusters"]
    assert len(agent_cards) == len(profile_cards) == 2
    for agent_card, profile_card in zip(agent_cards, profile_cards):
        assert _shape(agent_card) == _shape(profile_card)
        assert agent_card["fields"] == profile_card["fields"]
        assert agent_card["text"] == profile_card["text"]


def _js_cluster_card_html(account_names=(), selected=""):
    """Evaluate awsEksClusterCardHtml's template literal in Python, from the JS tables.

    A freshly added card is handed to refreshEksAccountOptions right away,
    which fills the account dropdown from the account rows; account_names
    and selected reproduce that step, so the comparison is with the card as
    the member actually sees it.
    """
    js = _js_source()
    start = js.index("function awsEksClusterCardHtml(")
    body = js[start : js.index("\nfunction ", start + 1)]
    literal_match = re.search(r"return `(.*?)`;", body, flags=re.S)
    assert literal_match, "awsEksClusterCardHtml no longer builds the card from a template literal"
    rendered = literal_match.group(1)
    placeholders = _js_object_literal(js, "INSTANCE_GROUP_PLACEHOLDERS")["aws_eks_clusters"]
    for key, copy in placeholders.items():
        rendered = rendered.replace("${placeholders." + key + "}", copy)
    rendered = rendered.replace("${label}", _js_object_literal(js, "INSTANCE_GROUP_LABELS")["aws_eks_clusters"])
    rendered = _expand_region_slots(js, rendered)
    assert "${" not in rendered, "unresolved template slot in awsEksClusterCardHtml literal"
    # normalizeInstanceInputs renumbers the freshly appended card.
    rendered = rendered.replace(
        '<span class="portal-settings-instance-title">EKS cluster</span>',
        '<span class="portal-settings-instance-title">EKS cluster 1</span>',
    )
    rendered = rendered.replace('aria-label="Enable EKS cluster instance"', 'aria-label="Enable EKS cluster instance 1"')
    filled = '<option value="">Choose an account</option>' + "".join(
        f'<option value="{name}"{" selected" if name == selected else ""}>{name}</option>' for name in account_names
    )
    rendered = rendered.replace('<option value="">Choose an account</option>', filled, 1)
    return f'<div class="portal-settings-instance-card" data-instance-item="aws_eks_clusters">{rendered}</div>'


def test_js_added_cluster_card_matches_the_server_rendered_card(monkeypatch):
    config = {
        "aws": {
            "enabled": True,
            "accounts": [{"name": "cps-dev", "account_id": "818354133892"}],
            "eks_clusters": [{"account": "cps-dev", "cluster": "cps-dev-eks", "private_endpoint": VPCE, "enabled": True}],
        }
    }
    server_card = next(c for c in _parse_cards(_render_panel(monkeypatch, config)) if c["group"] == "aws_eks_clusters")
    js_card = next(c for c in _parse_cards(_js_cluster_card_html(account_names=("cps-dev",), selected="cps-dev")) if c["group"] == "aws_eks_clusters")

    assert _shape(js_card) == _shape(server_card)
    assert sorted(js_card["fields"]) == sorted(server_card["fields"])
    assert js_card["attrs"] == server_card["attrs"]
    assert js_card["text"] == server_card["text"]
    for field, attributes in js_card["fields"].items():
        assert attributes.get("type") == server_card["fields"][field].get("type"), field
        assert attributes.get("placeholder") == server_card["fields"][field].get("placeholder"), field
        assert attributes.get("class") == server_card["fields"][field].get("class"), field


def test_the_group_is_initialized_labelled_and_explained_in_the_js():
    js = _js_source()
    assert "aws_eks_clusters" in _initialized_instance_groups(js)
    assert _js_object_literal(js, "INSTANCE_GROUP_LABELS")["aws_eks_clusters"] == "EKS cluster"
    assert _js_object_literal(js, "INSTANCE_GROUP_ITEM_TITLES")["aws_eks_clusters"] == "EKS cluster"
    tooltips = Path("app/static/js/tooltips.js").read_text(encoding="utf-8")
    assert '[data-action="add-instance"][data-group="aws_eks_clusters"]' in tooltips
    for field in ("account", "cluster", "region", "private_endpoint", "tls_server_name", "enabled"):
        assert f'[data-instance-item="aws_eks_clusters"] [data-field="{field}"]' in tooltips, field


def test_default_connections_page_renders_the_seed_rows():
    html = _default_connections_html(seed={"aws": {"enabled": True, "eks_clusters": json.loads(json.dumps(EKS_ROWS))}})
    cards = [card for card in _parse_cards(html) if card["group"] == "aws_eks_clusters"]
    assert len(cards) == 2
    assert cards[0]["fields"]["private_endpoint"]["value"] == VPCE
    assert cards[1]["fields"]["tls_server_name"]["value"] == "prod.internal.example.test"
    assert 'data-action="add-instance" data-group="aws_eks_clusters"' in html


def test_help_page_explains_the_rows():
    topic = get_topic("connect-aws")
    assert topic is not None
    assert "Clusters behind PrivateLink" in topic.body
    assert "aws-auth eks endpoint" in topic.body
    assert "tls_server_name" not in topic.body  # the page speaks the form's language, not the CLI's


# ------------------------------------------------- dropdowns, not text boxes


def test_python_and_js_offer_the_same_regions():
    assert list(AWS_REGIONS) == ["ap-east-1", "eu-west-1", "us-east-1"]
    assert _js_array_literal(_js_source(), "AWS_REGIONS") == list(AWS_REGIONS)


def test_sanitizer_drops_regions_no_dropdown_offers():
    out = sanitize_runtime_profile_aws(
        {
            "default_region": "us-west-2",
            "accounts": [{"name": "cps-dev", "account_id": "818354133892", "regions": ["us-west-2", "EU-WEST-1", "ap-east-1"]}],
            "eks_clusters": [
                {"account": "cps-dev", "cluster": "elsewhere", "region": "us-west-2", "private_endpoint": VPCE},
                {"account": "cps-dev", "cluster": "here", "region": "US-EAST-1", "private_endpoint": VPCE},
            ],
        }
    )
    assert "default_region" not in out
    assert out["accounts"][0]["regions"] == ["eu-west-1", "ap-east-1"]
    assert [(row["cluster"], row["region"]) for row in out["eks_clusters"]] == [("here", "us-east-1")]
    assert sanitize_runtime_profile_aws({"default_region": "AP-EAST-1"})["default_region"] == "ap-east-1"


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"aws_default_region": "us-west-2"}, "AWS default region must be one of ap-east-1, eu-west-1, us-east-1."),
        (
            {"aws_accounts_instances_0_regions": "ap-east-1, us-west-2"},
            "AWS account cps-dev names region us-west-2, which is not supported; choose from ap-east-1, eu-west-1, us-east-1.",
        ),
        (
            {"aws_eks_clusters_instances_1_region": "eu-central-1"},
            "EKS cluster 2 names region eu-central-1, which is not supported; choose from ap-east-1, eu-west-1, us-east-1.",
        ),
        (
            {"aws_eks_clusters_instances_1_server_ca": "corporate"},
            "EKS cluster 2: the certificate authority must be cluster or system, not corporate.",
        ),
    ],
)
def test_settings_form_refuses_a_region_no_dropdown_offers(overrides, message):
    merged, error = _settings_merge_payload({}, _clusters_form(**overrides))
    assert error == message
    assert "aws" not in merged


def test_settings_form_reads_a_multi_select_regions_post():
    # A <select multiple> posts one value per chosen option under one name;
    # the last value alone must not win.
    pairs = [(key, value) for key, value in _matrix_form().items() if key != "aws_accounts_instances_0_regions"]
    pairs += [("aws_accounts_instances_0_regions", "us-east-1"), ("aws_accounts_instances_0_regions", "ap-east-1")]
    merged, error = _settings_merge_payload({}, FormData(pairs))
    assert error is None
    assert merged["aws"]["accounts"][0]["regions"] == ["us-east-1", "ap-east-1"]
    assert merged["aws"]["accounts"][1]["regions"] == ["eu-west-1"]


def test_seed_form_reads_a_multi_select_regions_post():
    seed = _seed_config_from_form(
        FormData(
            [
                ("aws_enabled", "on"),
                ("aws_accounts_instance_count", "1"),
                ("aws_accounts_instances_0_enabled", "1"),
                ("aws_accounts_instances_0_name", "cps-dev"),
                ("aws_accounts_instances_0_account_id", "818354133892"),
                ("aws_accounts_instances_0_regions", "eu-west-1"),
                ("aws_accounts_instances_0_regions", "us-east-1"),
            ]
        )
    )
    assert seed["aws"]["accounts"][0]["regions"] == "eu-west-1, us-east-1"


def test_region_dropdowns_offer_only_the_supported_regions(monkeypatch):
    section = _aws_with_clusters()
    section["default_region"] = "us-east-1"
    html = _render_panel(monkeypatch, {"aws": section})

    assert _section_select_options(html, "aws_default_region") == [("", False), ("ap-east-1", False), ("eu-west-1", False), ("us-east-1", True)]
    assert [value for value, _sel, _label in _options(html, "aws_eks_clusters", 0, "region")] == ["", "ap-east-1", "eu-west-1", "us-east-1"]
    assert _selected_options(html, "aws_eks_clusters", 1, "region") == ["eu-west-1"]
    # No free-text region input is left anywhere on the page.
    assert 'placeholder="e.g. ap-east-1"' not in html
    assert 'data-field="regions" value=' not in html and 'data-field="region" value=' not in html


def test_account_dropdown_lists_the_account_rows_and_flags_a_missing_one(monkeypatch):
    section = json.loads(json.dumps(FULL_AWS_SECTION))
    section["eks_clusters"] = [
        {"account": "cps-dev", "cluster": "a", "private_endpoint": VPCE},
        # Stored by 12-digit id: selects that account's name.
        {"account": section["accounts"][1]["account_id"], "cluster": "b", "private_endpoint": VPCE},
        # Its account row was removed since: shown as such, not re-pointed.
        {"account": "gone", "cluster": "c", "private_endpoint": VPCE},
    ]
    html = _render_panel(monkeypatch, {"aws": section})
    names = [account["name"] for account in section["accounts"]]

    assert [value for value, _sel, _label in _options(html, "aws_eks_clusters", 0, "account")] == [""] + names
    assert _selected_options(html, "aws_eks_clusters", 0, "account") == ["cps-dev"]
    assert _selected_options(html, "aws_eks_clusters", 1, "account") == [names[1]]
    assert _selected_options(html, "aws_eks_clusters", 2, "account") == ["gone"]
    assert ("gone", True, "gone (not listed above)") in _options(html, "aws_eks_clusters", 2, "account")
    # Saving that page reports the gap instead of dropping the row.
    merged, error = _settings_merge_payload({}, _clusters_form(aws_eks_clusters_instances_0_account="gone"))
    assert error == "EKS cluster 1: gone is not one of the AWS accounts above, by name or 12-digit id."
    assert "aws" not in merged


def test_js_keeps_the_account_dropdown_in_step_with_the_account_rows():
    js = _js_source()
    assert "function refreshEksAccountOptions(root)" in js
    init = js[js.index("function initializeManagedSettingsRoot(") :]
    init = init[: init.index("\n}")]
    assert "refreshEksAccountOptions(root);" in init, "the dropdowns must be filled on load"
    handlers = js[js.index('root.addEventListener("input"') : js.index("const scrollBtn")]
    assert 'dataset?.field === "name"' in handlers and "refreshEksAccountOptions(root)" in handlers, "typing an account name must refresh them"
    assert 'if (group === "aws_accounts") refreshEksAccountOptions(root);' in handlers, "removing an account row must refresh them"
    add = js[js.index('if (group === "aws_eks_clusters") {') :]
    add = add[: add.index("return;")]
    assert "refreshEksAccountOptions(root);" in add, "a new EKS card must be filled"


def test_default_connections_page_uses_the_same_dropdowns():
    seed = {
        "aws": {
            "enabled": True,
            "default_region": "eu-west-1",
            "accounts": [{"name": "cps-dev", "account_id": "818354133892", "regions": ["ap-east-1", "us-east-1"]}],
            "eks_clusters": [
                {"account": "cps-dev", "cluster": "a", "region": "us-east-1", "private_endpoint": VPCE},
                {"account": "gone", "cluster": "c", "private_endpoint": VPCE},
            ],
        }
    }
    html = _default_connections_html(seed=seed)
    assert _section_select_options(html, "aws_default_region") == [("", False), ("ap-east-1", False), ("eu-west-1", True), ("us-east-1", False)]
    assert _selected_options(html, "aws_accounts", 0, "regions") == ["ap-east-1", "us-east-1"]
    assert [value for value, _sel, _label in _options(html, "aws_eks_clusters", 0, "account")] == ["", "cps-dev", "gone"]
    assert _selected_options(html, "aws_eks_clusters", 0, "account") == ["cps-dev"]
    assert _selected_options(html, "aws_eks_clusters", 1, "account") == ["gone"]
    assert _selected_options(html, "aws_eks_clusters", 0, "region") == ["us-east-1"]
    assert 'placeholder="e.g. ap-east-1"' not in html


# ------------------------------------------------- whose certificate answers


def test_sanitizer_keeps_a_known_server_ca_and_drops_the_rest():
    rows = sanitize_runtime_profile_aws(
        {
            "eks_clusters": [
                {"account": "cps-dev", "cluster": "a", "private_endpoint": VPCE, "server_ca": " System "},
                {"account": "cps-dev", "cluster": "b", "private_endpoint": VPCE, "server_ca": "cluster"},
                {"account": "cps-dev", "cluster": "c", "private_endpoint": VPCE, "server_ca": "corporate"},
                {"account": "cps-dev", "cluster": "d", "private_endpoint": VPCE},
            ]
        }
    )["eks_clusters"]
    assert [row.get("server_ca") for row in rows] == ["system", "cluster", None, None]
    assert AWS_EKS_SERVER_CA_MODES == ("cluster", "system")


def test_default_connections_page_offers_the_certificate_authority_choice():
    seed = {
        "aws": {
            "enabled": True,
            "accounts": [{"name": "cps-dev", "account_id": "818354133892"}],
            "eks_clusters": [
                {"account": "cps-dev", "cluster": "a", "private_endpoint": VPCE, "server_ca": "system"},
                {"account": "cps-dev", "cluster": "b", "private_endpoint": VPCE},
            ],
        }
    }
    html = _default_connections_html(seed=seed)
    assert [value for value, _sel, _label in _options(html, "aws_eks_clusters", 0, "server_ca")] == ["cluster", "system"]
    assert _selected_options(html, "aws_eks_clusters", 0, "server_ca") == ["system"]
    # An unset value selects nothing here, which the browser shows as the first option: cluster.
    assert _selected_options(html, "aws_eks_clusters", 1, "server_ca") == []


def test_help_page_explains_both_certificate_authorities():
    body = get_topic("connect-aws").body
    assert "Certificate authority" in body
    assert "System trust store" in body
    assert "not supported" not in body

