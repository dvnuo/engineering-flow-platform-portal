"""Hover hints across the Portal.

The app relied on the browser's native `title` bubble: about a second to appear,
unthemeable, invisible to keyboard users, and mostly repeating the label it sat
on ("Sessions", "Tasks"). Whole screens had none at all — the Runtime Profile
panel showed 56 controls with hints on 11 of them, and it is the most
jargon-heavy screen in the product.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.web import static_url

TOOLTIP_JS = Path("app/static/js/tooltips.js").read_text(encoding="utf-8")
CSS = Path("app/static/css/app.css").read_text(encoding="utf-8")
BASE_HTML = Path("app/templates/base.html").read_text(encoding="utf-8")
CHAT_JS = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")


def _registry_entries():
    """(selector, hint) pairs from the HINTS table."""
    block = TOOLTIP_JS.split("var HINTS = [", 1)[1].split("\n  ];", 1)[0]
    return re.findall(r"""\[\s*(['"])(.+?)\1\s*,\s*(['"])(.+?)\3\s*\]""", block)


def test_engine_is_loaded_on_every_page():
    assert "tooltips.js" in BASE_HTML


def test_hints_are_shown_on_keyboard_focus_not_just_hover():
    # The single biggest gap in native title: it never appears for keyboard users.
    assert 'document.addEventListener("focusin"' in TOOLTIP_JS
    # ...but only for keyboard focus, so clicking a button does not pop its hint.
    assert 'if (!el.matches(":focus-visible")) return;' in TOOLTIP_JS


def test_hints_reach_disabled_controls():
    # Disabled elements never dispatch mouse events, and they carry the most
    # useful hints of all ("Select an assistant first").
    assert "document.elementFromPoint(event.clientX, event.clientY)" in TOOLTIP_JS


def test_bubble_is_described_to_assistive_tech():
    assert 'bubble.setAttribute("role", "tooltip")' in TOOLTIP_JS
    assert 'el.setAttribute("aria-describedby", id)' in TOOLTIP_JS
    # Describing must not clobber the element's own accessible name.
    assert 'el.setAttribute("aria-label", title)' in TOOLTIP_JS


def test_legacy_title_is_retired_so_two_bubbles_never_stack():
    assert 'el.removeAttribute("title")' in TOOLTIP_JS


def test_curated_hints_beat_label_echoing_titles():
    # Most legacy titles just repeated the visible label, which is not a hint.
    assert "Registry beats a legacy title" in TOOLTIP_JS


def test_touch_taps_do_not_leave_a_bubble_parked_over_the_target():
    assert 'event.pointerType === "touch"' in TOOLTIP_JS
    assert "if (pointerIsCoarse) return;" in TOOLTIP_JS


@pytest.mark.parametrize("event_name", ["click", "keydown", "scroll", "resize", "blur"])
def test_bubble_never_outlives_its_context(event_name):
    assert f'"{event_name}"' in TOOLTIP_JS


def test_bubble_is_kept_inside_the_viewport():
    assert "VIEWPORT_MARGIN_PX" in TOOLTIP_JS
    assert "clampedLeft" in TOOLTIP_JS and "clampedTop" in TOOLTIP_JS
    # And flips rather than hanging off an edge.
    assert 'placement = "bottom"' in TOOLTIP_JS and 'placement = "top"' in TOOLTIP_JS


def test_styling_uses_theme_tokens_and_respects_reduced_motion():
    rule = CSS.split(".portal-tooltip {", 1)[1].split("}", 1)[0]
    assert "var(--portal-surface)" in rule
    assert "var(--portal-text)" in rule
    assert ".portal-tooltip { transition: none; }" in CSS


def test_disabled_buttons_get_their_reason_as_a_hint():
    # setButtonDisabled is where "why can't I click this" is answered.
    assert "setTooltip(button, nextHint)" in CHAT_JS


def test_registry_covers_the_jargon_heavy_settings_panel():
    selectors = {entry[1] for entry in _registry_entries()}
    for name in (
        "llm_reasoning_effort",
        "llm_max_context_tokens",
        "proxy_url",
        "jira_enabled",
        "confluence_enabled",
        "github_enabled",
        "git_user_name",
        "debug_log_level",
    ):
        assert any(name in selector for selector in selectors), name


def test_registry_explains_the_products_own_vocabulary():
    hints = " ".join(entry[3] for entry in _registry_entries())
    # Rail entries should say what the concept is, not repeat the menu label.
    assert "running workspace you chat with" in hints
    assert "runs on its own" in hints
    assert "start work automatically" in hints
    assert "credentials and integrations" in hints


def test_no_registry_hint_merely_repeats_its_own_selector_id():
    for _q1, selector, _q2, hint in _registry_entries():
        if not selector.startswith("#"):
            continue
        words = selector[1:].replace("-btn", "").replace("-", " ").strip()
        assert hint.lower() != words.lower(), selector


def test_instance_card_hints_are_scoped_per_integration():
    selectors = {entry[1] for entry in _registry_entries()}
    # Jira and Confluence both have a data-field="url"; they need different help.
    assert '[data-instance-item="jira"] [data-field="url"]' in selectors
    assert '[data-instance-item="confluence"] [data-field="url"]' in selectors


def test_static_urls_carry_a_content_version():
    # Without content-hashed filenames a browser keeps a cached asset until its
    # own copy expires; a stale chat_ui.js survived a shipped change in testing.
    assert re.match(r"^/static/js/tooltips\.js\?v=\d+$", static_url("js/tooltips.js"))
    assert "static_url(" in BASE_HTML


def test_static_url_falls_back_rather_than_raising_on_a_typo():
    assert static_url("js/does-not-exist.js") == "/static/js/does-not-exist.js"


def test_versioned_asset_is_served():
    # Plain constructor: the `with` form runs startup, whose schema guard
    # requires a migrated database that CI does not have.
    client = TestClient(app)
    assert client.get("/static/js/tooltips.js", params={"v": "123"}).status_code == 200
