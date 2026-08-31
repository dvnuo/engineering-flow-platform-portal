"""Behaviour tests for the multi-instance Jenkins runtime-profile section.

Covers the whole Portal path: schema sanitizer (both the legacy flat and the
new instances[] shape), public redaction, per-profile Secret encryption, the
settings form parser, and the rendered instance-card UI (server-rendered and
JS-added).
"""
import json
from html.parser import HTMLParser

import pytest

from tests.test_web_runtime_profile_settings import _bind_profile, _build_client

from app.schemas.runtime_profile import (
    normalize_jenkins_section_instances,
    redact_runtime_profile_config_for_public_response,
    sanitize_runtime_profile_config_dict,
)
from app.services.profile_secret_encryption import ENC_PREFIX, decrypt_sensitive_fields, encrypt_sensitive_fields
from app.web import _settings_merge_payload

LEGACY_FLAT_JENKINS = {
    "enabled": True,
    "url": "https://legacy-jenkins.example.com/",
    "username": "legacy-user",
    "password": "legacy-password",
}


# --------------------------------------------------------------------------
# A2 - sanitizer accepts both shapes
# --------------------------------------------------------------------------


def test_legacy_flat_jenkins_profile_normalises_to_a_single_named_instance():
    sanitized = sanitize_runtime_profile_config_dict({"jenkins": dict(LEGACY_FLAT_JENKINS)})

    assert sanitized["jenkins"] == {
        "enabled": True,
        "instances": [
            {
                "name": "jenkins",
                "url": "https://legacy-jenkins.example.com",
                "username": "legacy-user",
                "password": "legacy-password",
            }
        ],
    }
    # The flat keys must not survive alongside the normalized list.
    for legacy_key in ("url", "username", "password"):
        assert legacy_key not in sanitized["jenkins"]


def test_legacy_flat_jenkins_profile_survives_a_sanitize_round_trip():
    once = sanitize_runtime_profile_config_dict({"jenkins": dict(LEGACY_FLAT_JENKINS)})
    twice = sanitize_runtime_profile_config_dict(once)
    assert twice == once
    assert twice["jenkins"]["instances"][0]["password"] == "legacy-password"


def test_jenkins_multi_instance_shape_is_sanitized_like_jira():
    sanitized = sanitize_runtime_profile_config_dict(
        {
            "jenkins": {
                "enabled": True,
                "default_instance": " ci ",
                "instances": [
                    {
                        "name": " ci ",
                        "base_url": " https://ci.example.com/ ",
                        "email": " ci-bot ",
                        "password": " ci-pass ",
                        "api_token": " ci-token ",
                        "enabled": True,
                        "junk": "drop-me",
                    },
                    {"name": "release", "url": "https://release.example.com", "token": "rel-token", "enabled": False},
                    {"name": "no-url-is-dropped"},
                ],
            }
        }
    )

    assert sanitized["jenkins"]["default_instance"] == "ci"
    assert sanitized["jenkins"]["instances"] == [
        {
            "name": "ci",
            "url": "https://ci.example.com",
            "username": "ci-bot",
            "password": "ci-pass",
            "token": "ci-token",
            "enabled": True,
        },
        {"name": "release", "url": "https://release.example.com", "token": "rel-token", "enabled": False},
    ]


def test_jenkins_instances_take_precedence_over_stray_flat_keys():
    sanitized = sanitize_runtime_profile_config_dict(
        {
            "jenkins": {
                "enabled": True,
                "url": "https://stale.example.com",
                "password": "stale-password",
                "instances": [{"name": "ci", "url": "https://ci.example.com", "password": "fresh-password"}],
            }
        }
    )
    assert sanitized["jenkins"]["instances"] == [
        {"name": "ci", "url": "https://ci.example.com", "password": "fresh-password"}
    ]
    assert "stale-password" not in json.dumps(sanitized)


def test_normalize_jenkins_section_instances_leaves_a_urlless_legacy_section_empty():
    assert normalize_jenkins_section_instances({"enabled": False}) == []
    assert normalize_jenkins_section_instances(None) == []


# --------------------------------------------------------------------------
# A3 - redaction
# --------------------------------------------------------------------------


def test_public_response_never_leaks_jenkins_instance_secrets():
    redacted = redact_runtime_profile_config_for_public_response(
        {
            "jenkins": {
                "enabled": True,
                "instances": [
                    {"name": "ci", "url": "https://ci.example.com", "password": "ci-pass", "token": "ci-token"},
                    {"name": "rel", "url": "https://rel.example.com", "api_token": "rel-api-token"},
                ],
            }
        }
    )
    dumped = json.dumps(redacted)
    for secret in ("ci-pass", "ci-token", "rel-api-token"):
        assert secret not in dumped

    first, second = redacted["jenkins"]["instances"]
    assert first["password_present"] is True and first["token_present"] is True
    assert "password" not in first and "token" not in first
    assert second["token_present"] is True and second["password_present"] is False
    assert "api_token" not in second


def test_public_response_redacts_an_unmigrated_flat_jenkins_section():
    redacted = redact_runtime_profile_config_for_public_response({"jenkins": dict(LEGACY_FLAT_JENKINS)})
    assert "legacy-password" not in json.dumps(redacted)
    assert redacted["jenkins"]["password_present"] is True


# --------------------------------------------------------------------------
# A5 - per-profile Secret encryption covers instances[]
# --------------------------------------------------------------------------


def test_jenkins_instance_secrets_are_encrypted_in_the_profile_secret(monkeypatch):
    monkeypatch.setenv("EFP_CONFIG_KEY", "unit-test-config-key")
    config = sanitize_runtime_profile_config_dict(
        {
            "jenkins": {
                "enabled": True,
                "instances": [
                    {"name": "ci", "url": "https://ci.example.com", "password": "ci-pass", "token": "ci-token"}
                ],
            },
            "jira": {
                "enabled": True,
                "instances": [{"name": "j", "url": "https://j.example.com", "password": "j-pass", "token": "j-token"}],
            },
        }
    )

    encrypted = encrypt_sensitive_fields(config)
    dumped = json.dumps(encrypted)
    for secret in ("ci-pass", "ci-token", "j-pass", "j-token"):
        assert secret not in dumped

    jenkins_instance = encrypted["jenkins"]["instances"][0]
    jira_instance = encrypted["jira"]["instances"][0]
    for instance in (jenkins_instance, jira_instance):
        assert instance["password"].startswith(ENC_PREFIX)
        assert instance["token"].startswith(ENC_PREFIX)
    # Non-secret fields stay readable, exactly like the Jira path.
    assert jenkins_instance["url"] == "https://ci.example.com"

    assert decrypt_sensitive_fields(encrypted) == config


def test_legacy_flat_jenkins_password_is_still_encrypted_after_normalisation(monkeypatch):
    monkeypatch.setenv("EFP_CONFIG_KEY", "unit-test-config-key")
    config = sanitize_runtime_profile_config_dict({"jenkins": dict(LEGACY_FLAT_JENKINS)})
    encrypted = encrypt_sensitive_fields(config)
    assert "legacy-password" not in json.dumps(encrypted)
    assert encrypted["jenkins"]["instances"][0]["password"].startswith(ENC_PREFIX)
    assert decrypt_sensitive_fields(encrypted)["jenkins"]["instances"][0]["password"] == "legacy-password"


# --------------------------------------------------------------------------
# A4 - form parsing through the generic multi-instance parser
# --------------------------------------------------------------------------


def test_settings_form_saves_multiple_jenkins_instances():
    merged, error = _settings_merge_payload(
        {},
        {
            "__touch_jenkins": "1",
            "jenkins_enabled": "on",
            "jenkins_instance_count": "2",
            "jenkins_instances_0_name": "ci",
            "jenkins_instances_0_url": "https://ci.example.com/",
            "jenkins_instances_0_username": "ci-bot",
            "jenkins_instances_0_password": "ci-pass",
            "jenkins_instances_0_token": "ci-token",
            "jenkins_instances_0_enabled": "1",
            "jenkins_instances_1_name": "release",
            "jenkins_instances_1_url": "https://release.example.com",
            "jenkins_instances_1_username": "rel-bot",
            "jenkins_instances_1_password": "rel-pass",
            "jenkins_instances_1_enabled": "",
        },
    )

    assert error is None
    assert merged["jenkins"]["enabled"] is True
    assert [inst["name"] for inst in merged["jenkins"]["instances"]] == ["ci", "release"]
    assert merged["jenkins"]["instances"][0]["token"] == "ci-token"
    assert merged["jenkins"]["instances"][1]["enabled"] is False


def test_settings_form_preserves_a_blank_jenkins_password_from_the_existing_instance():
    """A blank password field means "unchanged" - it must not wipe the secret."""
    merged, error = _settings_merge_payload(
        {
            "jenkins": {
                "enabled": True,
                "instances": [{"name": "ci", "url": "https://ci.example.com", "password": "kept-pass"}],
            }
        },
        {
            "__touch_jenkins": "1",
            "jenkins_enabled": "on",
            "jenkins_instance_count": "1",
            "jenkins_instances_0_original_name": "ci",
            "jenkins_instances_0_original_url": "https://ci.example.com",
            "jenkins_instances_0_name": "ci",
            "jenkins_instances_0_url": "https://ci.example.com",
            "jenkins_instances_0_enabled": "1",
        },
    )
    assert error is None
    assert merged["jenkins"]["instances"][0]["password"] == "kept-pass"


def test_settings_form_migrates_an_unmigrated_flat_profile_without_losing_credentials():
    """Opening + saving the Jenkins form on a legacy profile keeps the password
    even though the browser posts a blank password field for it."""
    merged, error = _settings_merge_payload(
        {"jenkins": dict(LEGACY_FLAT_JENKINS)},
        {
            "__touch_jenkins": "1",
            "jenkins_enabled": "on",
            "jenkins_instance_count": "1",
            "jenkins_instances_0_original_name": "jenkins",
            "jenkins_instances_0_original_url": "https://legacy-jenkins.example.com/",
            "jenkins_instances_0_name": "jenkins",
            "jenkins_instances_0_url": "https://legacy-jenkins.example.com/",
            "jenkins_instances_0_username": "legacy-user",
            "jenkins_instances_0_enabled": "1",
        },
    )
    assert error is None
    assert merged["jenkins"]["instances"] == [
        {
            "name": "jenkins",
            "url": "https://legacy-jenkins.example.com",
            "username": "legacy-user",
            "password": "legacy-password",
            "enabled": True,
        }
    ]


def test_touching_jenkins_without_any_instance_fields_keeps_legacy_credentials():
    """A post that toggles the section but carries no instance rows (partial or
    stale form) must not wipe an unmigrated profile's credentials."""
    merged, error = _settings_merge_payload(
        {"jenkins": dict(LEGACY_FLAT_JENKINS)},
        {"__touch_jenkins": "1", "jenkins_enabled": "on"},
    )
    assert error is None
    assert merged["jenkins"]["instances"] == [
        {
            "name": "jenkins",
            "url": "https://legacy-jenkins.example.com",
            "username": "legacy-user",
            "password": "legacy-password",
        }
    ]


def test_end_to_end_settings_save_persists_multiple_jenkins_instances(monkeypatch):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        rp = _bind_profile(db, agent, {})
        resp = client.post(
            f"/app/agents/{agent.id}/settings/save",
            data={
                "__touch_jenkins": "1",
                "jenkins_enabled": "on",
                "jenkins_instance_count": "2",
                "jenkins_instances_0_name": "ci",
                "jenkins_instances_0_url": "https://ci.example.com",
                "jenkins_instances_0_username": "ci-bot",
                "jenkins_instances_0_password": "ci-pass",
                "jenkins_instances_0_enabled": "1",
                "jenkins_instances_1_name": "release",
                "jenkins_instances_1_url": "https://release.example.com",
                "jenkins_instances_1_token": "rel-token",
                "jenkins_instances_1_enabled": "1",
            },
        )
        assert resp.status_code == 200
        db.refresh(rp)
        assert json.loads(rp.config_json)["jenkins"] == {
            "enabled": True,
            "instances": [
                {"name": "ci", "url": "https://ci.example.com", "username": "ci-bot", "password": "ci-pass", "enabled": True},
                {"name": "release", "url": "https://release.example.com", "token": "rel-token", "enabled": True},
            ],
        }
    finally:
        cleanup()


# --------------------------------------------------------------------------
# Downstream projection still recognises an enabled Jenkins setup
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "jenkins_section,expected",
    [
        ({"enabled": True, "instances": [{"name": "ci", "url": "https://ci.example.com"}]}, True),
        ({"enabled": True, "instances": [{"name": "ci", "url": "https://ci.example.com", "enabled": False}]}, False),
        ({"enabled": True, "instances": []}, False),
        ({"enabled": False, "instances": [{"name": "ci", "url": "https://ci.example.com"}]}, False),
        (dict(LEGACY_FLAT_JENKINS), True),
    ],
)
def test_native_cli_instructions_follow_the_jenkins_instances(jenkins_section, expected):
    from app.services.runtime_profile_context_projection import (
        RUNTIME_PROFILE_CLI_TOOL_INSTRUCTIONS,
        project_canonical_for_runtime,
    )

    projected = project_canonical_for_runtime({"jenkins": jenkins_section}, "native")
    texts = projected.get("instruction_texts") or []
    assert (RUNTIME_PROFILE_CLI_TOOL_INSTRUCTIONS in texts) is expected


# --------------------------------------------------------------------------
# UI - rendered instance cards
# --------------------------------------------------------------------------


_VOID_TAGS = {"input", "br", "img", "hr", "meta", "link", "source", "area", "col"}


class _CardParser(HTMLParser):
    """Collects a structural signature of every ``portal-settings-instance-card``."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards = []
        self._stack = []
        self._depth = 0

    def handle_startendtag(self, tag, attrs):
        # `<input ... />` must not also close an enclosing element.
        self._record(tag, attrs)

    def handle_starttag(self, tag, attrs):
        self._record(tag, attrs)
        if tag not in _VOID_TAGS:
            self._depth += 1

    def _record(self, tag, attrs):
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if "portal-settings-instance-card" in classes:
            self._stack.append(
                {
                    "start_depth": self._depth,
                    "card": {
                        "group": attributes.get("data-instance-item"),
                        "disabled_class": "is-instance-disabled" in classes,
                        "path": [],
                        "fields": {},
                        "attrs": {},
                        "text": [],
                    },
                }
            )
        if self._stack:
            card = self._stack[-1]["card"]
            card["path"].append((tag, tuple(sorted(classes)), attributes.get("data-field")))
            if attributes.get("data-field"):
                card["fields"][attributes["data-field"]] = attributes
            for key in ("data-instance-state", "data-original-field", "data-action"):
                if key in attributes:
                    card["attrs"].setdefault(key, []).append(attributes.get(key) or True)

    def handle_endtag(self, tag):
        if tag in _VOID_TAGS:
            return
        self._depth -= 1
        while self._stack and self._depth <= self._stack[-1]["start_depth"]:
            self.cards.append(self._stack.pop()["card"])

    def handle_data(self, data):
        if self._stack and data.strip():
            self._stack[-1]["card"]["text"].append(data.strip())


def _parse_cards(html):
    parser = _CardParser()
    parser.feed(html)
    return parser.cards


def _shape(card):
    """Structural signature: tag/class/data-field path, ignoring values."""
    return card["path"]


def _render_panel(monkeypatch, config):
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        _bind_profile(db, agent, config)
        resp = client.get(f"/app/agents/{agent.id}/settings/panel")
        assert resp.status_code == 200
        return resp.text
    finally:
        cleanup()


JENKINS_TWO_INSTANCES = {
    "jenkins": {
        "enabled": True,
        "instances": [
            {"name": "ci", "url": "https://ci.example.com", "username": "ci-bot", "password": "ci-pass", "enabled": True},
            {"name": "release", "url": "https://release.example.com", "enabled": False},
        ],
    }
}


def test_settings_panel_renders_one_card_per_jenkins_instance(monkeypatch):
    html = _render_panel(monkeypatch, JENKINS_TWO_INSTANCES)
    cards = [card for card in _parse_cards(html) if card["group"] == "jenkins"]

    assert len(cards) == 2
    assert 'name="jenkins_instance_count" value="2"' in html
    assert cards[0]["fields"]["name"]["value"] == "ci"
    assert cards[0]["fields"]["url"]["value"] == "https://ci.example.com"
    assert cards[0]["fields"]["username"]["value"] == "ci-bot"
    assert cards[0]["fields"]["password"]["value"] == "ci-pass"
    assert "token" in cards[0]["fields"]
    assert cards[1]["fields"]["name"]["value"] == "release"


def test_view_payload_exposes_jenkins_instances_for_an_unsanitized_flat_section():
    """The panel context must not depend on the caller having sanitized first;
    a flat section handed straight to the view still yields instance cards."""
    from app.web import _settings_view_payload

    payload = _settings_view_payload({"jenkins": dict(LEGACY_FLAT_JENKINS)})

    assert payload["jenkins_instances"] == [
        {
            "name": "jenkins",
            "url": "https://legacy-jenkins.example.com/",
            "username": "legacy-user",
            "password": "legacy-password",
        }
    ]


def test_settings_panel_prefills_an_unmigrated_flat_jenkins_profile(monkeypatch):
    html = _render_panel(monkeypatch, {"jenkins": dict(LEGACY_FLAT_JENKINS)})
    cards = [card for card in _parse_cards(html) if card["group"] == "jenkins"]

    assert len(cards) == 1
    assert cards[0]["fields"]["name"]["value"] == "jenkins"
    assert cards[0]["fields"]["url"]["value"] == "https://legacy-jenkins.example.com"
    assert cards[0]["fields"]["username"]["value"] == "legacy-user"
    assert cards[0]["fields"]["password"]["value"] == "legacy-password"


@pytest.mark.parametrize("group", ["jira", "confluence", "jenkins"])
def test_instance_card_head_groups_the_enabled_toggle_with_the_title(monkeypatch, group):
    config = {
        group: {
            "enabled": True,
            "instances": [{"name": "one", "url": "https://one.example.com", "enabled": True}],
        }
    }
    html = _render_panel(monkeypatch, config)
    card = next(c for c in _parse_cards(html) if c["group"] == group)

    tags = [(tag, classes) for tag, classes, _ in card["path"]]
    head_index = next(i for i, (_, classes) in enumerate(tags) if "portal-settings-instance-head" in classes)
    main_index = next(i for i, (_, classes) in enumerate(tags) if "portal-settings-instance-head-main" in classes)
    title_index = next(i for i, (_, classes) in enumerate(tags) if "portal-settings-instance-title" in classes)
    toggle_index = next(i for i, (_, classes) in enumerate(tags) if "toggle-switch" in classes)
    remove_index = next(i for i, (_, classes) in enumerate(tags) if "portal-instance-remove" in classes)

    # title + toggle live inside the head's left-hand group; remove sits after it.
    assert head_index < main_index < title_index < toggle_index < remove_index
    # The toggle is the shared design-system switch, not a bare checkbox.
    assert any("toggle-slider" in classes for _, classes in tags)
    enabled_input = card["fields"]["enabled"]
    assert enabled_input["type"] == "checkbox"
    assert enabled_input["aria-label"] == f"Enable {group.capitalize()} instance 1"


@pytest.mark.parametrize("group", ["jira", "confluence", "jenkins"])
def test_disabled_instance_card_is_visually_and_textually_marked(monkeypatch, group):
    config = {
        group: {
            "enabled": True,
            "instances": [
                {"name": "on", "url": "https://on.example.com", "enabled": True},
                {"name": "off", "url": "https://off.example.com", "enabled": False},
            ],
        }
    }
    html = _render_panel(monkeypatch, config)
    enabled_card, disabled_card = [c for c in _parse_cards(html) if c["group"] == group]

    assert enabled_card["disabled_class"] is False
    assert disabled_card["disabled_class"] is True
    # Not colour alone: the state word changes too.
    assert "Enabled" in enabled_card["text"] and "Disabled" not in enabled_card["text"]
    assert "Disabled" in disabled_card["text"]
    assert "checked" not in disabled_card["fields"]["enabled"]
    assert "checked" in enabled_card["fields"]["enabled"]


def test_disabled_instance_card_dims_only_its_body():
    css = _read("app/static/css/app.css")
    # The dim must target the body, so the head/toggle/remove stay legible.
    assert ".portal-settings-instance-card.is-instance-disabled .portal-settings-instance-body" in css
    assert ".portal-settings-instance-body" in css
    assert ".portal-instance-remove:focus-visible" in css


def _read(path):
    from pathlib import Path

    return Path(path).read_text(encoding="utf-8")


@pytest.mark.parametrize("group", ["jira", "confluence", "jenkins"])
def test_agent_and_runtime_profile_panels_render_the_same_instance_card(monkeypatch, group):
    """Both panels share the card markup; a fix in one must land in the other."""
    config = {
        group: {"enabled": True, "instances": [{"name": "one", "url": "https://one.example.com", "enabled": True}]}
    }
    client, db, agent, cleanup = _build_client(monkeypatch)
    try:
        _bind_profile(db, agent, config)
        agent_html = client.get(f"/app/agents/{agent.id}/settings/panel").text
        profile_html = client.get(f"/app/runtime-profiles/{agent.runtime_profile_id}/panel").text
    finally:
        cleanup()

    agent_card = next(c for c in _parse_cards(agent_html) if c["group"] == group)
    profile_card = next(c for c in _parse_cards(profile_html) if c["group"] == group)
    assert _shape(agent_card) == _shape(profile_card)
    assert agent_card["fields"] == profile_card["fields"]
    assert agent_card["text"] == profile_card["text"]


@pytest.mark.parametrize("group", ["jira", "confluence", "jenkins"])
def test_js_added_instance_card_matches_the_server_rendered_card(monkeypatch, group):
    """A row added via data-action="add-instance" must be structurally identical
    to a saved row, otherwise the two look different side by side."""
    config = {
        group: {"enabled": True, "instances": [{"name": "one", "url": "https://one.example.com", "enabled": True}]}
    }
    server_card = next(c for c in _parse_cards(_render_panel(monkeypatch, config)) if c["group"] == group)
    js_card = next(c for c in _parse_cards(_js_card_html(group)) if c["group"] == group)

    assert _shape(js_card) == _shape(server_card)
    assert sorted(js_card["fields"]) == sorted(server_card["fields"])
    assert js_card["attrs"] == server_card["attrs"]
    for field, attributes in js_card["fields"].items():
        assert attributes.get("type") == server_card["fields"][field].get("type"), field
        assert attributes.get("placeholder") == server_card["fields"][field].get("placeholder"), field
        assert attributes.get("class") == server_card["fields"][field].get("class"), field


def _js_card_html(group):
    """Evaluate addInstanceRow's template literal for ``group`` in Python.

    The literal only interpolates plain string expressions, so substituting the
    handful of ``${...}`` slots reproduces exactly what the browser builds.
    """
    import re

    js = _read("app/static/js/chat_ui.js")
    start = js.index("function addInstanceRow(")
    body = js[start:js.index("\nfunction ", start + 1)]

    literal_match = re.search(r"div\.innerHTML = `(.*?)`;", body, flags=re.S)
    assert literal_match, "addInstanceRow no longer builds the card from a template literal"
    literal = literal_match.group(1)

    def _branch(name):
        match = re.search(rf"const {name} = (.*?);\n", body, flags=re.S)
        assert match, name
        return match.group(1)

    # The per-group copy comes from the JS lookup tables themselves, so the test
    # never re-states values the production code could drift away from.
    placeholders = _js_object_literal(js, "INSTANCE_GROUP_PLACEHOLDERS")[group]
    labels = _js_object_literal(js, "INSTANCE_GROUP_LABELS")

    scoped_field = {
        "jira": '<input type="text" data-field="project" value="" placeholder="Project" class="portal-form-input" />',
        "confluence": '<input type="text" data-field="space" value="" placeholder="Space Key" class="portal-form-input" />',
        "jenkins": "<div></div>",
    }[group]
    api_version = (
        '<div class="grid grid-cols-2 gap-2"><select data-field="api_version" class="portal-form-select">'
        '<option value="" selected>Auto API Version</option><option value="2">REST API v2</option>'
        '<option value="3">REST API v3</option></select><div></div></div>'
        if group == "jira"
        else ""
    )

    # Default Connections reuses this builder without credential fields, so the
    # block is now a slot. This test covers the credential-carrying branch,
    # which is what the runtime-profile and settings panels render.
    credential_fields = (
        '<div class="grid grid-cols-2 gap-2">'
        f'<input type="text" data-field="username" value="" placeholder="{placeholders["username"]}" class="portal-form-input" />'
        '<input type="password" data-field="password" value="" placeholder="Password" class="portal-form-input" /></div>'
        '<div class="grid grid-cols-2 gap-2">'
        '<input type="password" data-field="token" value="" placeholder="API token" class="portal-form-input" />'
        f'{scoped_field}</div>'
    )

    # Guard the substitutions against the JS drifting away from them.
    assert scoped_field in _branch("scopedFieldHtml")
    if group == "jira":
        assert 'data-field="api_version"' in _branch("apiVersionHtml")
    assert 'data-field="token"' in _branch("credentialFieldsHtml")

    rendered = (
        literal.replace("${credentialFieldsHtml}", credential_fields)
        .replace("${scopedFieldHtml}", scoped_field)
        .replace("${apiVersionHtml}", api_version)
        .replace("${urlPlaceholder}", placeholders["url"])
        .replace("${usernamePlaceholder}", placeholders["username"])
        .replace("${label}", labels[group])
        .replace("${group}", group)
    )
    assert "${" not in rendered, "unresolved template slot in addInstanceRow literal"
    # normalizeInstanceInputs stamps the index onto the freshly appended card.
    rendered = rendered.replace(
        f'aria-label="Enable {labels[group]} instance"',
        f'aria-label="Enable {labels[group]} instance 1"',
    )
    return f'<div class="portal-settings-instance-card" data-instance-item="{group}">{rendered}</div>'


def _js_object_literal(js, const_name):
    """Read a JSON-compatible `const NAME = {...};` table out of chat_ui.js."""
    marker = f"const {const_name} = "
    start = js.index(marker) + len(marker)
    assert js[start] == "{", f"{const_name} is not an object literal"
    depth = 0
    for end in range(start, len(js)):
        if js[end] == "{":
            depth += 1
        elif js[end] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(js[start:end + 1])
    raise AssertionError(f"unterminated {const_name} literal in chat_ui.js")


def _instance_groups_in_template(path):
    """Instance groups the server renders, read from the template source."""
    import re
    from pathlib import Path

    return set(re.findall(r'data-instance-group="([a-z_]+)"', Path(path).read_text(encoding="utf-8")))


def _js_source():
    from pathlib import Path

    return Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")


def _initialized_instance_groups(js):
    """Groups initializeManagedSettingsRoot stamps input names onto."""
    import re

    start = js.index("function initializeManagedSettingsRoot(")
    body = js[start : js.index("\n}", start)]
    return set(re.findall(r'normalizeInstanceInputs\(root,\s*"([a-z_]+)"\)', body))


@pytest.mark.parametrize(
    "template",
    [
        "app/templates/partials/runtime_profile_panel.html",
        "app/templates/partials/settings_panel.html",
    ],
)
def test_every_rendered_instance_group_is_initialized_by_the_js(template):
    """Each instance group the template renders must be normalized on load.

    The templates deliberately emit instance inputs with NO ``name`` attribute;
    ``normalizeInstanceInputs`` stamps the indexed names the form parser reads.
    A group that renders but is never initialized therefore submits nothing,
    the parser sees zero rows, and saving silently wipes every instance in that
    section. Deriving the expected set from the template (rather than hard-coding
    it) also fails when a future group is added without its initializer.
    """
    rendered_groups = _instance_groups_in_template(template)
    assert "jenkins" in rendered_groups, "Jenkins should render as an instance group"

    missing = rendered_groups - _initialized_instance_groups(_js_source())
    assert not missing, (
        f"{template} renders instance group(s) {sorted(missing)} that "
        "initializeManagedSettingsRoot never passes to normalizeInstanceInputs; "
        "their inputs would submit no name and saving would wipe them"
    )


def test_toggling_an_instance_updates_its_card_state_live():
    """The enabled toggle must restyle its card without waiting for a re-render.

    Otherwise the dashed/dimmed 'disabled' treatment and the Enabled/Disabled
    word stay stale until the next server render, so the control appears to do
    nothing.
    """
    js = _js_source()
    start = js.index('root.addEventListener("change"')
    handler = js[start : js.index("});", start)]
    assert 'dataset?.field === "enabled"' in handler, "anchor moved; update this test"
    assert "syncInstanceEnabledState(" in handler, (
        "the enabled-toggle change handler must call syncInstanceEnabledState "
        "so the card's disabled styling and state word update immediately"
    )
