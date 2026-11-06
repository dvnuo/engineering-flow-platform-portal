"""The stylesheet parses to the end: one unbalanced brace drops every rule after it.

A merge of two branches that appended rules at the same place interleaved
their blocks and left five rules unclosed; browsers then ignored the rest of
app.css (the admin tables, task metrics and more were suddenly unstyled). The
tests look for selectors in the source, which does not catch that.
"""

from pathlib import Path


STYLESHEETS = ("app/static/css/app.css", "app/static/css/share.css")
STATIC_SOURCES = STYLESHEETS + ("app/static/js/chat_ui.js", "app/static/js/admin_users.js", "app/static/js/share_view.js")
EQUALS_MARKER = chr(10) + "=" * 7 + chr(10)


def _brace_depth(css: str) -> tuple[int, int | None]:
    """Final depth, and the first line where a closing brace has no opening one."""
    depth = 0
    for line_number, line in enumerate(css.splitlines(), 1):
        for char in line:
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
        if depth < 0:
            return depth, line_number
    return depth, None


def test_stylesheets_have_balanced_braces():
    for rel in STYLESHEETS:
        depth, line_number = _brace_depth(Path(rel).read_text(encoding="utf-8"))
        assert line_number is None, f"{rel}: a closing brace without an opening one at line {line_number}"
        assert depth == 0, f"{rel}: {depth} rule(s) never closed"


def test_no_merge_markers_in_static_assets():
    for rel in STATIC_SOURCES:
        text = Path(rel).read_text(encoding="utf-8")
        for marker in ("<" * 7, ">" * 7, EQUALS_MARKER):
            assert marker not in text, f"{rel} holds a merge marker"


def test_a_rule_never_opens_inside_another_rule():
    """Outside an @media / @keyframes / @supports block, depth is 0 whenever a rule opens."""
    css = Path("app/static/css/app.css").read_text(encoding="utf-8")
    depth = 0
    at_block_depth = 0
    for line_number, line in enumerate(css.splitlines(), 1):
        stripped = line.strip()
        opens = line.count("{")
        closes = line.count("}")
        if opens:
            if stripped.startswith("@"):
                at_block_depth = depth + opens
            elif depth > (at_block_depth if at_block_depth else 0):
                raise AssertionError(f"app.css line {line_number} opens a rule inside another rule: {stripped[:80]}")
        depth += opens - closes
        if at_block_depth and depth < at_block_depth:
            at_block_depth = 0
