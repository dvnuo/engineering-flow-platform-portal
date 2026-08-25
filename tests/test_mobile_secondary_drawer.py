"""Narrow viewports must keep the assistants/tasks/delegations list reachable.

The 768px breakpoint used to apply `display: none !important` to
.portal-secondary-pane with no drawer, hamburger, or restore control, so below
768px there was no way to select an assistant, task, delegation, or runtime
profile at all. These assertions keep the drawer wired up.
"""

from pathlib import Path

import pytest

CSS = Path("app/static/css/app.css").read_text(encoding="utf-8")
JS = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
APP_HTML = Path("app/templates/app.html").read_text(encoding="utf-8")


def _mobile_media_block() -> str:
    marker = "@media (max-width: 768px) {"
    start = CSS.rindex(marker)
    depth = 0
    for index in range(start + len(marker) - 1, len(CSS)):
        if CSS[index] == "{":
            depth += 1
        elif CSS[index] == "}":
            depth -= 1
            if depth == 0:
                return CSS[start : index + 1]
    raise AssertionError("unterminated 768px media block")


def test_secondary_pane_is_not_hidden_outright_on_narrow_viewports():
    block = _mobile_media_block()
    assert ".portal-secondary-pane" in block
    # The regression: the pane itself being removed with no way back.
    assert "display: none !important" not in block.split(".portal-secondary-restore")[0]


def test_narrow_viewport_turns_the_pane_into_an_overlay_drawer():
    block = _mobile_media_block()
    assert "position: absolute" in block
    assert "is-secondary-drawer-open" in block
    assert "visibility: visible" in block


def test_drawer_width_is_relative_to_the_shell_not_the_viewport():
    # .portal-shell is inset from the viewport, so 100vw overflows the right edge.
    assert "min(320px, calc(100% - 68px))" in _mobile_media_block()


def test_backdrop_element_exists_and_is_desktop_hidden_by_default():
    assert 'id="secondary-drawer-backdrop"' in APP_HTML
    assert ".portal-secondary-drawer-backdrop {" in CSS
    base_rule = CSS.split(".portal-secondary-drawer-backdrop {", 1)[1].split("}", 1)[0]
    assert "display: none" in base_rule


def test_backdrop_base_rule_precedes_the_media_query_override():
    # Same specificity, so source order decides whether the drawer backdrop
    # can ever show. Base rule must come first.
    assert CSS.index(".portal-secondary-drawer-backdrop {") < CSS.rindex("@media (max-width: 768px) {")


@pytest.mark.parametrize(
    "snippet",
    [
        "function isSecondaryDrawerViewport()",
        "function applySecondaryDrawerState()",
        "function closeSecondaryDrawer()",
        'classList.toggle("is-secondary-drawer-open", open)',
        "applySecondaryDrawerState();",
    ],
)
def test_drawer_state_helpers_are_wired(snippet):
    assert snippet in JS


def test_drawer_can_be_dismissed_the_ways_an_overlay_should_be():
    assert 'dom.secondaryDrawerBackdrop?.addEventListener("click", closeSecondaryDrawer)' in JS
    assert 'is-secondary-drawer-open' in JS
    assert 'window.addEventListener("resize", applySecondaryDrawerState)' in JS


def test_route_restore_does_not_pop_the_drawer_open_on_mobile():
    assert "const routeDrivenOnMobile = isApplyingPortalRoute && isSecondaryDrawerViewport();" in JS
    assert "if (!preserveCollapsed && !routeDrivenOnMobile) {" in JS
