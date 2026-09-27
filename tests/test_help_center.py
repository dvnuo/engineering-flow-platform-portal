"""Help is a browsable section whose prose lives in markdown files.

Three rounds of reports:

- Clicking a connection-checklist chip jumped to Assistants. They were anchors
  with href="#profile-section-jira", and the app owns location.hash for its own
  routing -- the router parsed that as a route, rejected it, and fell back.
- Help was one panel in the right-hand drawer with room for a glossary and
  nothing else. It needs a topic list, and Connections needs to link into it.
- Opening Help (from the rail, or from "Full guide" under a Connections field)
  left the side pane titled "Administration", the topic rows had no layout for
  their icon, text and chevron, and changing a guide meant editing Python
  tuples. Topics are app/help/*.md now, rendered in the browser with the
  markdown-it build chat already uses.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests._js_extract_helpers import _extract_js_function

from app.services.connection_guidance import CONNECTION_GUIDANCE, CONNECTOR_GUIDANCE
from app.services.help_center import (
    GROUP_ORDER,
    HELP_DIR,
    all_topics,
    default_topic,
    get_topic,
    split_front_matter,
    topic_id_for_connection,
    topic_id_for_connector,
    topics_by_group,
)


def _chat_ui() -> str:
    return Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")


def _css() -> str:
    return Path("app/static/css/app.css").read_text(encoding="utf-8")


CONNECTOR_TEMPLATES = Path("app/templates/partials/connectors")


def _connector_template(name: str) -> str:
    return (CONNECTOR_TEMPLATES / name).read_text(encoding="utf-8")


def _connector_templates() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(CONNECTOR_TEMPLATES.glob("*.html")))


# ------------------------------------------------- the checklist hash bug


def test_connector_panels_never_link_to_sections_by_in_page_hash():
    html = _connector_templates()

    assert 'href="#profile-section-' not in html, (
        "an in-page href replaces the app's own hash route, which the router "
        "then rejects and falls back to Assistants"
    )


def test_every_connector_section_keeps_its_anchor_id():
    # Each connector form wraps its section(s) in a stable id.
    from app.services.connector_registry import SETTINGS_CONNECTORS

    for spec in SETTINGS_CONNECTORS:
        html = _connector_template(f"{spec.type}.html")
        for section in spec.form_sections:
            assert f'id="profile-section-{section}"' in html, (spec.type, section)


# --------------------------------------------------------- markdown files


def test_every_topic_is_a_markdown_file():
    for topic in all_topics():
        assert (HELP_DIR / f"{topic.id}.md").is_file(), topic.id


def test_the_readme_is_not_a_topic():
    # Authors need the front matter documented next to the files, and that
    # document must not show up in the sub-menu.
    assert (HELP_DIR / "README.md").is_file()
    assert get_topic("README") is None


def test_front_matter_is_split_from_the_body():
    meta, body = split_front_matter(
        "---\ntitle: A guide\nsummary: 'Quoted: yes'\norder: 5\n---\n\n## Heading\n\nText.\n"
    )

    assert meta == {"title": "A guide", "summary": "Quoted: yes", "order": "5"}
    assert body == "## Heading\n\nText."


def test_a_file_without_front_matter_is_all_body():
    assert split_front_matter("## Just prose\n") == ({}, "## Just prose")
    # An unterminated block is prose too, not a silent empty page.
    assert split_front_matter("---\ntitle: x\n## Body") == ({}, "---\ntitle: x\n## Body")


def test_concept_topics_take_their_listing_from_the_front_matter():
    topic = get_topic("getting-started")

    assert topic is not None
    assert topic.title == "Creating your first assistant"
    assert topic.group == "Getting started"
    assert topic.icon == "rocket"
    assert "## How to start" in topic.body


def test_a_file_missing_its_title_is_skipped_rather_than_breaking_the_shell(tmp_path, monkeypatch):
    import app.services.help_center as help_center

    (tmp_path / "good.md").write_text("---\ntitle: Good\ngroup: Working\n---\nBody.\n", encoding="utf-8")
    (tmp_path / "bad.md").write_text("---\ngroup: Working\n---\nNo title.\n", encoding="utf-8")
    monkeypatch.setattr(help_center, "HELP_DIR", tmp_path)

    ids = {topic.id for topic in help_center.all_topics()}

    assert "good" in ids
    assert "bad" not in ids


# --------------------------------------------------------------- topics


def test_connection_topics_are_derived_from_the_shared_guidance():
    # Restating the steps here would let the panel and the guide drift.
    for section, guidance in CONNECTION_GUIDANCE.items():
        topic = get_topic(topic_id_for_connection(section))
        assert topic is not None, section
        assert topic.title == guidance["title"]
        assert list(topic.steps) == list(guidance["steps"])


def test_connector_topics_keep_the_troubleshooting_shared_with_the_panel():
    for key, guidance in CONNECTOR_GUIDANCE.items():
        topic = get_topic(topic_id_for_connector(key))
        assert topic is not None, key
        assert topic.group == "Connectors"
        assert list(topic.steps) == list(guidance["steps"])
        assert list(topic.troubleshooting) == list(guidance["troubleshooting"])
        assert topic.body.strip(), f"{topic.id} adds nothing beyond the panel"


def test_every_connection_has_a_topic():
    covered = {t.connection_section for t in all_topics() if t.connection_section}

    assert covered == set(CONNECTION_GUIDANCE)


def test_topic_ids_are_unique():
    ids = [topic.id for topic in all_topics()]

    assert len(set(ids)) == len(ids)


def test_groups_are_ordered_and_non_empty():
    groups = topics_by_group()

    assert [name for name, _ in groups] == list(GROUP_ORDER)
    assert all(topics for _, topics in groups)


def test_topics_within_a_group_follow_their_order_key():
    groups = dict(topics_by_group())

    assert [t.id for t in groups["Getting started"]][:2] == ["getting-started", "concepts"]
    assert [t.id for t in groups["Working"]][-1] == "shortcuts"


def test_an_unknown_topic_resolves_to_nothing_so_the_route_can_fall_back():
    assert get_topic("nope") is None
    assert get_topic("") is None
    assert default_topic().id == "getting-started"


@pytest.mark.parametrize(
    "topic", [t for t in all_topics() if t.connection_section], ids=lambda t: t.id
)
def test_connection_topics_say_more_than_the_inline_steps(topic):
    # The point of the full guide is the detail that does not fit beside a form
    # field. A topic with only the steps adds nothing.
    assert topic.body.strip(), f"{topic.id} adds nothing beyond the inline steps"


def test_the_shortcut_topic_leaves_the_modifier_to_the_client():
    topic = get_topic("shortcuts")

    assert topic is not None
    assert "{mod}" in topic.body, "the server cannot know the reader's platform"
    render = _extract_js_function(_chat_ui(), "renderHelpMarkdown")
    assert 'replaceAll("{mod}", modifier)' in render


# ------------------------------------------------------------- rendering


def test_the_panel_hands_the_markdown_to_the_browser(monkeypatch):
    from app.main import app
    import app.web as web_module
    from app.models.user import User

    member = User(username="member", password_hash="hash", role="user", is_active=True)
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: member)
    # Not the `with` form: it fires startup, whose schema guard needs a
    # migrated database this test does not have (and CI never has).
    client = TestClient(app)

    response = client.get("/app/help/panel", params={"topic": "connect-jira"})

    assert response.status_code == 200
    html = response.text
    assert 'data-help-topic="connect-jira"' in html
    assert 'data-help-markdown="' in html
    assert "## Getting the token right" in html
    # The inline steps and the setup link still come from the shared guidance.
    for step in CONNECTION_GUIDANCE["jira"]["steps"]:
        assert step in html, step
    assert CONNECTION_GUIDANCE["jira"]["help_url"] in html
    assert "data-help-open-connections" in html


def test_an_unknown_topic_renders_the_default_one(monkeypatch):
    from app.main import app
    import app.web as web_module
    from app.models.user import User

    member = User(username="member", password_hash="hash", role="user", is_active=True)
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: member)
    client = TestClient(app)

    response = client.get("/app/help/panel", params={"topic": "stale-bookmark"})

    assert response.status_code == 200
    assert 'data-help-topic="getting-started"' in response.text


def test_the_markdown_is_rendered_with_its_own_markdown_it():
    js = _chat_ui()
    setup = _extract_js_function(js, "helpMarkdown")
    render = _extract_js_function(js, "renderHelpMarkdown")

    # Same fence handling as chat (mermaid, hljs), but not the chat instance:
    # its breaks: true would turn every hard-wrapped line of prose into <br>.
    assert "md.options.highlight" in setup
    assert "breaks" not in setup
    assert "helpMarkdown().render(" in render
    assert "enhanceMarkdownBlock(el)" in render
    assert "renderMermaidDiagrams(el)" in render


def test_guides_can_link_to_each_other_and_stay_in_this_tab():
    js = _chat_ui()
    setup = _extract_js_function(js, "helpMarkdown")
    render = _extract_js_function(js, "renderHelpMarkdown")

    assert 'startsWith("#/")' in setup
    assert 'a[href^="#/"]' in render
    assert 'anchor.removeAttribute("target")' in render


def test_open_help_topic_renders_the_markdown_after_the_swap():
    fn = _extract_js_function(_chat_ui(), "openHelpTopic")

    assert "renderHelpMarkdown(dom.workspaceDetailContent)" in fn


def test_the_topic_template_carries_no_prose_of_its_own():
    template = Path("app/templates/partials/help_topic_panel.html").read_text(encoding="utf-8")

    assert 'data-help-markdown="{{ topic.body }}"' in template
    assert "portal-help-body" in template
    assert "topic.troubleshooting" in template


# ------------------------------------------------------------- the pane


def test_the_secondary_pane_is_titled_help():
    js = _chat_ui()
    header = _extract_js_function(js, "renderSecondaryPaneHeader")

    assert 'state.activeNavSection === "help"' in header
    assert 'dom.secondaryPaneTitle.textContent = "Help"' in header
    # And on a cold load of #/help/..., before the router has run.
    assert 'if (section === "help") return "Help";' in _extract_js_function(js, "initialPortalSectionTitle")
    assert 'if (section === "users" || section === "help") return "Portal";' in _extract_js_function(
        js, "portalSectionEyebrow"
    )


def test_help_rows_lay_out_like_the_admin_rows():
    # The rows reused the admin row's markup (icon, copy, chevron) but not its
    # grid, so the three pieces just flowed inline.
    css = _css()
    rule = css[css.index(".portal-admin-nav-row,") :]
    rule = rule[: rule.index("}")]

    assert ".portal-help-nav-row {" in rule
    assert "grid-template-columns: auto minmax(0, 1fr) auto" in rule


def test_the_drawer_era_help_styles_are_gone():
    css = _css()

    assert ".portal-help-panel" not in css
    assert ".portal-help-list" not in css


# ---------------------------------------------------------------- routing


def test_help_is_a_routable_section():
    js = _chat_ui()

    assert '"help",' in js
    assert 'section === "help"' in js
    assert "#/help/" in js


def test_the_route_survives_a_reload_and_a_new_tab():
    # Deep links only work if the section parses back out of the hash.
    js = _chat_ui()

    assert "parsed.helpTopicId = decodedId;" in js
    assert 'route.section === "help"' in js


def test_help_is_no_longer_a_drawer_panel():
    js = _chat_ui()
    utility_keys = js[js.index("ALLOWED_UTILITY_PANEL_KEYS") :]
    utility_keys = utility_keys[: utility_keys.index("]);")]

    assert '"help"' not in utility_keys


def test_the_header_names_the_open_topic():
    body = _extract_js_function(_chat_ui(), "syncMainHeader")

    assert "currentHelpTopicTitle()" in body


def test_connectors_link_out_to_the_full_guide():
    html = _connector_template("_macros.html")

    assert 'href="#/help/connect-{{ section }}"' in html
    # A real anchor, so it can be opened in a new tab.
    assert "Full guide for this connector" in html
    # Every settings connector form renders the guide macro.
    from app.services.connector_registry import SETTINGS_CONNECTORS

    for spec in SETTINGS_CONNECTORS:
        assert "setup_guide(" in _connector_template(f"{spec.type}.html"), spec.type


def test_a_guide_links_back_to_connections():
    template = Path("app/templates/partials/help_topic_panel.html").read_text(encoding="utf-8")
    js = _chat_ui()

    assert "data-help-open-connections" in template
    assert "data-help-open-connections" in js, "the button has to be wired, not just rendered"
