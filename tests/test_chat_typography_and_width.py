"""Chat typography and the width of the chat column.

Two things the stylesheet asked for and the page did not do:

- `.message-markdown` asked for a 1.65 line-height, but the markup also carried
  Tailwind's `text-sm`. The Tailwind runtime appends its stylesheet after
  app.css, so `text-sm`'s 1.25rem line-height (20px) won; an inline code chip
  is taller than that, so chips on consecutive lines touched. Lists had the
  same problem the other way round: preflight strips their markers and nothing
  put them back.
- The chat column was a fixed 980px. On a 3440px-wide screen that is 28% of
  the width, with a thousand blank pixels on either side.

The column is now sized from the conversation pane through a container query
unit, and the member can widen it from the account menu. Geometry cannot be
asserted without a layout engine, so these pin the wiring.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from _js_extract_helpers import _extract_js_function


CSS = Path("app/static/css/app.css").read_text(encoding="utf-8")
CHAT_JS = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
APP_HTML = Path("app/templates/app.html").read_text(encoding="utf-8")
BASE_HTML = Path("app/templates/base.html").read_text(encoding="utf-8")
CHAT_PARTIAL = Path("app/templates/partials/chat_response.html").read_text(encoding="utf-8")

WIDTH_VAR = "--portal-chat-width"
WIDTH_KEY = "portal-chat-width"
WIDTH_CHOICES = ["default", "wide", "full"]


def _rules(selector: str) -> list[str]:
    """Every block whose selector is exactly this one, at any indentation.

    `html[data-chat-width="wide"] .portal-conversation-main` ends with the
    same text as `.portal-conversation-main`, so a plain substring split would
    return the override blocks as well.
    """
    pattern = r"(?m)^[ \t]*" + re.escape(selector) + r" \{"
    blocks = [CSS[match.end():].split("}", 1)[0] for match in re.finditer(pattern, CSS)]
    assert blocks, f"{selector} is not defined"
    return blocks


def _rule(selector: str) -> str:
    rules = _rules(selector)
    assert len(rules) == 1, f"{selector} has {len(rules)} blocks; assert on all of them"
    return rules[0]


def _run_node(script: str):
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping behaviour test")
    result = subprocess.run([node_bin, "-e", script], check=False, text=True, capture_output=True)
    if result.returncode != 0:
        raise AssertionError(f"node failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return json.loads(result.stdout.strip())


# ------------------------------------------------------------ line spacing


def test_message_markup_carries_no_tailwind_text_utility():
    # The runtime's stylesheet lands after app.css, so any utility left on a
    # message element silently beats the rules below. text-sm was the one that
    # set the 20px line-height; the other two did nothing app.css does not.
    for utility in ("text-sm", "max-w-none", "whitespace-pre-wrap"):
        assert utility not in CHAT_JS, f"{utility} is back on a message element"
        assert utility not in CHAT_PARTIAL, f"{utility} is back on a message element"


def test_message_text_owns_its_line_height():
    assert "line-height: 1.7" in _rule(".message-markdown")
    # Without text-sm the member's bubble would fall back to `normal` (about
    # 17px), which is tighter than the 20px it had.
    assert "line-height: 1.7" in _rule(".message-body")


def test_an_inline_code_chip_fits_inside_the_line_box():
    # 14px * 1.7 is a 23.8px line. At .12rem of vertical padding plus a 1px
    # border the chip was 20.8px tall and left two pixels between lines; at
    # .05em it is 18.3px and leaves five.
    rule = _rule(".message-markdown code")
    assert "padding: .05em .4em" in rule
    assert "border: 1px solid" in rule, "the chip keeps its outline; the padding is what gave"


def test_list_markers_are_back():
    # Tailwind preflight sets `ul, ol { list-style: none }` and nothing in
    # .message-markdown overrode it, so a reply's bullets rendered as plain
    # indented lines.
    assert ".message-markdown ul { list-style: disc; }" in CSS
    assert ".message-markdown ol { list-style: decimal; }" in CSS


# --------------------------------------------------------- the chat column


def test_the_column_is_sized_from_the_conversation_pane():
    # A viewport unit would ignore the sidebar and a pinned tool panel; the
    # pane is what the column actually has.
    rule = _rule(".portal-conversation-main")
    assert "container-type: inline-size" in rule
    assert f"{WIDTH_VAR}: clamp(980px, 62cqw, 1400px)" in rule


def test_everything_in_the_column_follows_the_one_width():
    for selector in (".portal-message-list", ".portal-composer", ".message-surface-assistant", ".message-surface-user"):
        assert any(f"var({WIDTH_VAR}, 980px)" in rule for rule in _rules(selector)), selector
    for leftover in ("max-width: 980px", "max-width: 1000px", "min(100%, 920px)", "min(100%, 760px)", "max-width: 920px"):
        assert leftover not in CSS, f"{leftover} is a fixed cap the column has outgrown"


def test_the_message_list_still_reserves_room_for_the_composer():
    # test_chat_surface_layout pins the reserve; this only says the width
    # change did not lose it.
    for rule in _rules(".portal-message-list"):
        assert "var(--portal-composer-height, 200px) + 24px" in rule


def test_every_width_choice_has_the_old_column_as_its_floor():
    # min(100%, width) is how a narrow pane gets the full width; a choice that
    # could compute below 980px would make a phone's column narrower than the
    # default's.
    for choice in ("wide", "full"):
        rule = _rule(f'html[data-chat-width="{choice}"] .portal-conversation-main')
        assert "980px" in rule, choice
        assert "cqw" in rule, choice


def test_the_choice_is_on_the_document_before_first_paint():
    # The stylesheet reads data-chat-width; the pre-paint script in base.html
    # sets it from the same key and value list chat_ui.js uses, so the column
    # does not jump when the script catches up.
    assert f'localStorage.getItem("{WIDTH_KEY}")' in BASE_HTML
    assert 'setAttribute("data-chat-width", preference)' in BASE_HTML
    assert json.dumps(WIDTH_CHOICES) in BASE_HTML
    assert f'const CHAT_WIDTH_PREFERENCE_KEY = "{WIDTH_KEY}";' in CHAT_JS
    assert f"const CHAT_WIDTH_ORDER = {json.dumps(WIDTH_CHOICES)};" in CHAT_JS


def test_the_account_menu_offers_the_three_widths_beside_the_theme():
    start = APP_HTML.index('id="account-menu"')
    menu = APP_HTML[start:APP_HTML.index('id="logout-btn"', start)]
    assert 'aria-labelledby="account-chat-width-label"' in menu
    assert ">Chat width<" in menu
    for choice in WIDTH_CHOICES:
        assert f'role="menuitemradio" aria-checked="false" data-chat-width-option="{choice}"' in menu, choice
    # The theme already had a segmented control; the width uses the same one
    # rather than a copy named after the theme.
    assert menu.count('class="portal-account-options"') == 2
    assert "portal-theme-option" not in APP_HTML
    assert "portal-theme-option" not in CSS
    assert menu.index('data-theme-option="dark"') < menu.index('data-chat-width-option="default"')


def test_chat_ui_wires_the_width_options():
    assert 'chatWidthOptions: Array.from(document.querySelectorAll("[data-chat-width-option]"))' in CHAT_JS
    assert 'option.addEventListener("click", () => applyChatWidth(option.dataset.chatWidthOption))' in CHAT_JS
    assert "applyChatWidth(resolveInitialChatWidth());" in CHAT_JS


def _width_bundle() -> str:
    return "\n".join(
        _extract_js_function(CHAT_JS, name)
        for name in ("normalizeChatWidthPreference", "resolveInitialChatWidth", "applyChatWidth")
    )


def test_a_choice_lands_on_the_document_the_store_and_the_menu():
    result = _run_node(f"""
const CHAT_WIDTH_PREFERENCE_KEY = "{WIDTH_KEY}";
const CHAT_WIDTH_ORDER = {json.dumps(WIDTH_CHOICES)};
const attrs = {{}};
const store = {{}};
globalThis.document = {{ documentElement: {{ setAttribute: (k, v) => {{ attrs[k] = v; }} }} }};
globalThis.localStorage = {{ getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => {{ store[k] = v; }} }};
const options = CHAT_WIDTH_ORDER.map((value) => ({{ dataset: {{ chatWidthOption: value }}, checked: null, setAttribute(k, v) {{ this.checked = v; }} }}));
const dom = {{ chatWidthOptions: options }};
{_width_bundle()}
applyChatWidth("wide");
const afterWide = {{ attr: attrs["data-chat-width"], stored: store[CHAT_WIDTH_PREFERENCE_KEY], checked: options.map((o) => o.checked) }};
applyChatWidth("sideways");
const afterJunk = {{ attr: attrs["data-chat-width"], stored: store[CHAT_WIDTH_PREFERENCE_KEY] }};
store[CHAT_WIDTH_PREFERENCE_KEY] = "full";
const restored = resolveInitialChatWidth();
delete store[CHAT_WIDTH_PREFERENCE_KEY];
const fresh = resolveInitialChatWidth();
console.log(JSON.stringify({{ afterWide, afterJunk, restored, fresh }}));
""")

    assert result["afterWide"] == {"attr": "wide", "stored": "wide", "checked": ["false", "true", "false"]}
    # An unknown value (an old key, a hand-edited store) is the default, not an
    # attribute the stylesheet has no rule for.
    assert result["afterJunk"] == {"attr": "default", "stored": "default"}
    assert result["restored"] == "full"
    assert result["fresh"] == "default"
