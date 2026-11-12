"""A state line that is written with its text says it is to be shown.

``.portal-inline-state`` is ``display: none`` on its own (app.css). The class
is also what an empty result slot carries until a script fills it and names
its state (``is-error``, ``is-success``, ...), so the hidden default is wanted.
A line written with its text already in it therefore needs ``is-visible`` or
a state, or nobody ever reads it: that is how the loading lines of the
Sessions, Skills and Usage panels, and a handful of hints, stayed blank.
"""

import re
from pathlib import Path


ROOTS = [Path("app/templates"), Path("app/static/js")]
CSS = Path("app/static/css/app.css")

OPEN_TAG = re.compile(
    r"""<(?:div|span|p)\b[^<>]*?class=(["'])([^"']*\bportal-inline-state\b[^"']*)\1[^<>]*>"""
)
# A class that decides whether the line shows. `hidden` is the page's own
# "not now" and wins over all of them.
DECIDED = ("is-visible", "is-error", "is-success", "is-warning", "hidden")


def _bare_lines_with_text() -> list[str]:
    found = []
    for root in ROOTS:
        for path in sorted(root.rglob("*")):
            if path.suffix not in (".html", ".js"):
                continue
            text = path.read_text(encoding="utf-8")
            for match in OPEN_TAG.finditer(text):
                classes = match.group(2)
                if any(name in classes for name in DECIDED):
                    continue
                # The state is worked out when the line is rendered.
                if "{%" in classes or "${" in classes:
                    continue
                # An empty slot, filled later by a script that names its state.
                if text[match.end():match.end() + 200].lstrip().startswith("</"):
                    continue
                line = text.count("\n", 0, match.start()) + 1
                found.append(f"{path.as_posix()}:{line}")
    return found


def test_a_state_line_written_with_text_is_shown():
    assert _bare_lines_with_text() == []


def test_the_scan_tells_a_line_with_text_from_an_empty_slot():
    samples = {
        '<div class="portal-inline-state">Loading</div>': True,
        "<div class='portal-inline-state'>${t(\"Loading\")}</div>": True,
        "'<div class=\"portal-inline-state\">' + text + '</div>'": True,
        '<div class="portal-inline-state is-visible">Loading</div>': False,
        '<div class="portal-inline-state is-error">Failed</div>': False,
        '<div class="portal-inline-state" data-test-result="jira"></div>': False,
        '<span class="portal-inline-state" role="status">\n  </span>': False,
        '<div class="portal-inline-state hidden" data-result>Later</div>': False,
        'el.className = "portal-inline-state";': False,
    }
    for sample, flagged in samples.items():
        match = OPEN_TAG.search(sample)
        bare = bool(
            match
            and not any(name in match.group(2) for name in DECIDED)
            and not sample[match.end():].lstrip().startswith("</")
        )
        assert bare is flagged, sample


def test_the_stylesheet_still_hides_a_bare_state_line():
    css = CSS.read_text(encoding="utf-8")
    base = css[css.index(".portal-inline-state {"):]
    assert "display: none" in base[:base.index("}")]
    shown = css[css.index(".portal-inline-state.is-visible {"):]
    assert "display: block" in shown[:shown.index("}")]
