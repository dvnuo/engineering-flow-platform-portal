"""Help is a browsable section, and the connection checklist stops hijacking the hash.

Two reports:

- Clicking a connection-checklist chip jumped to Assistants. They were anchors
  with href="#profile-section-jira", and the app owns location.hash for its own
  routing -- the router parsed that as a route, rejected it, and fell back.
- Help was one panel in the right-hand drawer with room for a glossary and
  nothing else. It needs a topic list, and Connections needs to link into it.
"""
from pathlib import Path

import pytest

from tests._js_extract_helpers import _extract_js_function

from app.services.connection_guidance import CONNECTION_GUIDANCE
from app.services.help_center import (
    GROUP_ORDER,
    all_topics,
    default_topic,
    get_topic,
    topic_id_for_connection,
    topics_by_group,
)


def _chat_ui() -> str:
    return Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")


def _profile_panel() -> str:
    return Path("app/templates/partials/runtime_profile_panel.html").read_text(encoding="utf-8")


# ------------------------------------------------- the checklist hash bug


def test_checklist_entries_are_buttons_not_anchors():
    html = _profile_panel()

    assert 'href="#profile-section-' not in html, (
        "an in-page href replaces the app's own hash route, which the router "
        "then rejects and falls back to Assistants"
    )
    assert "data-scroll-to-section=" in html


def test_the_scroll_handler_prevents_default_navigation():
    js = _chat_ui()

    assert '[data-scroll-to-section]' in js
    assert "scrollIntoView" in js


def test_the_scroll_target_ids_exist_in_the_panel():
    # A chip that scrolls nowhere is worse than one that navigates wrongly.
    html = _profile_panel()

    for section in ("llm", "jira", "confluence", "github"):
        assert f'id="profile-section-{section}"' in html, section


# --------------------------------------------------------------- topics


def test_connection_topics_are_derived_from_the_shared_guidance():
    # Restating the steps here would let the panel and the guide drift.
    for section, guidance in CONNECTION_GUIDANCE.items():
        topic = get_topic(topic_id_for_connection(section))
        assert topic is not None, section
        assert topic.title == guidance["title"]
        assert list(topic.steps) == list(guidance["steps"])


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


def test_an_unknown_topic_resolves_to_nothing_so_the_route_can_fall_back():
    assert get_topic("nope") is None
    assert get_topic("") is None
    assert default_topic() is not None


@pytest.mark.parametrize("topic", [t for t in all_topics() if t.connection_section])
def test_connection_topics_say_more_than_the_inline_steps(topic):
    # The point of the full guide is the detail that does not fit beside a form
    # field. A topic with only the steps adds nothing.
    assert topic.body, f"{topic.id} adds nothing beyond the inline steps"


def test_the_shortcut_topic_leaves_the_modifier_to_the_client():
    topic = get_topic("shortcuts")

    assert topic.shortcuts
    assert any("{mod}" in keys for keys, _ in topic.shortcuts), (
        "the server cannot know the reader's platform"
    )


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


def test_connections_links_out_to_the_full_guide():
    html = _profile_panel()

    assert 'href="#/help/connect-' in html
    # A real anchor, so it can be opened in a new tab.
    assert "Full guide for this connection" in html


def test_a_guide_links_back_to_connections():
    template = Path("app/templates/partials/help_topic_panel.html").read_text(encoding="utf-8")
    js = _chat_ui()

    assert "data-help-open-connections" in template
    assert "data-help-open-connections" in js, "the button has to be wired, not just rendered"
