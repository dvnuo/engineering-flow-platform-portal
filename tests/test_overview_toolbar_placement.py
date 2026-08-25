"""Tasks and Delegations put their actions where every other section does.

Assistants and Administration render their actions in `.portal-main-toolbar`.
Tasks and Delegations instead kept theirs inside the panel body, in a
`.portal-overview-hero` that also repeated the section title already shown in
the header. Same product, two different places to look for the same kind of
control.

The hero is gone. Its actions moved to the main toolbar, and the one piece of
information it carried that nothing else showed — the health headline and the
timestamp — moved to the status line, which is what that line is for.
"""

from pathlib import Path

import pytest

APP_HTML = Path("app/templates/app.html").read_text(encoding="utf-8")
CHAT_JS = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
CSS = Path("app/static/css/app.css").read_text(encoding="utf-8")
TASKS_HTML = Path("app/templates/partials/my_tasks_panel.html").read_text(encoding="utf-8")
DELEGATIONS_HTML = Path("app/templates/partials/delegations_panel.html").read_text(encoding="utf-8")

PANELS = pytest.mark.parametrize(
    "panel", [TASKS_HTML, DELEGATIONS_HTML], ids=["tasks", "delegations"]
)


@PANELS
def test_overview_hero_is_gone(panel):
    assert "portal-overview-hero" not in panel
    assert "portal-overview-title-block" not in panel
    assert "portal-overview-actions" not in panel
    assert "portal-overview-eyebrow" not in panel


@PANELS
def test_panels_no_longer_repeat_the_header_title(panel):
    # embed-title already shows "Tasks" / "Delegations".
    assert "portal-panel-title" not in panel.split("portal-overview-overview-grid", 1)[0]


def test_hero_styles_were_removed_with_the_markup():
    for dead in (
        ".portal-overview-hero",
        ".portal-overview-title-block",
        ".portal-overview-eyebrow",
        ".portal-overview-actions",
    ):
        assert dead not in CSS, dead


@pytest.mark.parametrize(
    "button_id",
    [
        "header-task-scope-all",
        "header-task-scope-mine",
        "header-task-refresh",
        "header-create-task",
        "header-delegation-scope-all",
        "header-delegation-scope-mine",
        "header-delegation-refresh",
        "header-create-delegation",
    ],
)
def test_actions_live_in_the_main_toolbar(button_id):
    toolbar = APP_HTML.split('class="portal-main-toolbar"', 1)[1].split("</header>", 1)[0]
    assert f'id="{button_id}"' in toolbar, button_id


def test_toolbar_groups_are_shown_per_section():
    assert 'id="header-task-actions"' in APP_HTML
    assert 'id="header-delegation-actions"' in APP_HTML
    assert "function syncOverviewToolbars()" in CHAT_JS
    assert 'taskGroup?.classList.toggle("hidden", section !== "tasks")' in CHAT_JS
    assert 'delegationGroup?.classList.toggle("hidden", section !== "delegations")' in CHAT_JS
    assert "syncOverviewToolbars();" in CHAT_JS.split("function syncMainHeader()", 1)[1]


def test_handlers_are_document_level_now_that_buttons_left_the_panel():
    # A listener scoped to #workspace-detail-content would never see them.
    # Anchored on the comment because chat_ui.js has several document click
    # listeners and the first one is unrelated.
    marker = "  // Bound to the document rather than the panel:"
    assert marker in CHAT_JS
    listener = CHAT_JS.split(marker, 1)[1]
    assert 'document.addEventListener("click", async (event) => {' in listener.split("\n  });", 1)[0]
    for attr in (
        "data-task-overview-scope",
        "data-delegation-overview-scope",
        "data-refresh-task-overview",
        "data-refresh-delegation-overview",
        "data-open-create-task-main",
        "data-open-create-delegation-main",
    ):
        assert attr in listener.split("\n  });", 1)[0], attr


def test_scope_selection_is_reflected_on_the_toolbar():
    assert 'btn.classList.toggle("is-active", active)' in CHAT_JS
    assert 'btn.setAttribute("aria-pressed", active ? "true" : "false")' in CHAT_JS


@PANELS
def test_health_headline_and_timestamp_are_handed_to_the_status_line(panel):
    assert 'data-overview-headline="{{ overview.health.headline }}"' in panel
    assert "data-overview-generated-at=" in panel


def test_status_line_localises_the_overview_timestamp():
    assert "function applyOverviewStatusLine()" in CHAT_JS
    assert "toLocaleString(" in CHAT_JS.split("function applyOverviewStatusLine()", 1)[1].split("\n}", 1)[0]
    # Falls back until the panel carrying the numbers has swapped in.
    assert 'if (!applyOverviewStatusLine()) setChatStatus("Task health, workload, and recent activity");' in CHAT_JS
    assert 'if (!applyOverviewStatusLine()) setChatStatus("Manage delegations");' in CHAT_JS


def test_long_titles_cannot_push_the_toolbar_out_of_the_header():
    # "Delegations" plus its actions overflowed a 375px header by 21px, because
    # the copy block defaulted to min-width:auto and refused to shrink.
    copy_rule = CSS.split(".portal-main-header-copy {", 1)[1].split("}", 1)[0]
    assert "min-width: 0" in copy_rule
    assert "flex: 0 1 auto" in copy_rule
    toolbar_rule = CSS.split(".portal-main-toolbar {", 1)[1].split("}", 1)[0]
    assert "flex: 0 0 auto" in toolbar_rule
