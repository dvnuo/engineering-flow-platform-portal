"""Bold and italics beside CJK punctuation.

markdown-it follows CommonMark's flanking rules: a closing `**` that follows
punctuation must be followed by whitespace or punctuation, and an opening
`**` that is followed by punctuation must be preceded by whitespace or
punctuation. Chinese text puts no space after the run and keeps its own
punctuation inside it, so the model's favourite shape, a bold label ending in
a full-width colon that runs straight into its text, rendered as literal
asterisks, and so did a bold parenthesis or quotation between ideographs.

static/js/markdown_cjk_friendly.js applies the CJK-friendly amendment to the
flanking rules on both markdown-it instances, the chat's and the share
page's. These pin the wiring and run the behaviour under node against the
bundled markdown-it, with and without the plugin: CJK shapes gain their
emphasis, everything without a CJK neighbour renders exactly as before.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


BUNDLE = Path("app/static/lib/markdown-it.min.js")
PLUGIN = Path("app/static/js/markdown_cjk_friendly.js")
CHAT_JS = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
SHARE_JS = Path("app/static/js/share_view.js").read_text(encoding="utf-8")
BASE_HTML = Path("app/templates/base.html").read_text(encoding="utf-8")
SHARE_HTML = Path("app/templates/share.html").read_text(encoding="utf-8")
PLUGIN_JS = PLUGIN.read_text(encoding="utf-8")

MARKDOWN_IT_TAG = "lib/markdown-it.min.js"
PLUGIN_TAG = "js/markdown_cjk_friendly.js"
REGISTER = "md.use(window.markdownItCjkFriendly)"


# ------------------------------------------------------------------ wiring


def _script_line(html: str, src: str) -> str:
    lines = [line for line in html.splitlines() if src in line]
    assert len(lines) == 1, f"{src} should be loaded exactly once, found {lines}"
    return lines[0]


def test_both_pages_load_the_plugin_right_after_markdown_it():
    for name, html in (("base.html", BASE_HTML), ("share.html", SHARE_HTML)):
        assert html.index(MARKDOWN_IT_TAG) < html.index(PLUGIN_TAG), name
        # chat_ui.js registers the plugin at top level, so like markdown-it
        # itself it cannot be deferred.
        assert "defer" not in _script_line(html, PLUGIN_TAG), name
    # The share page replaces the head scripts block, so it lists the plugin
    # itself, ahead of the script that renders with it.
    assert SHARE_HTML.index(PLUGIN_TAG) < SHARE_HTML.index("js/share_view.js")


def test_both_renderers_register_the_plugin_when_it_is_present():
    for name, js in (("chat_ui.js", CHAT_JS), ("share_view.js", SHARE_JS)):
        assert REGISTER in js, name
        # The node tests evaluate pieces of these files without the plugin.
        assert 'typeof window.markdownItCjkFriendly === "function"' in js, name
    # Registered next to the instance, before anything renders with it.
    assert CHAT_JS.index("const md = window.markdownit({") < CHAT_JS.index(REGISTER) < CHAT_JS.index("md.validateLink =")
    assert SHARE_JS.index("var md = window.markdownit") < SHARE_JS.index(REGISTER) < SHARE_JS.index("function renderMarkdown(")


def test_plugin_is_a_self_contained_umd_file():
    # Browser global for the pages, module.exports for the node tests, and
    # the hook is the inline State class, as in markdown-it-cjk-friendly.
    assert "root.markdownItCjkFriendly = factory()" in PLUGIN_JS
    assert "module.exports = factory()" in PLUGIN_JS
    assert "md.inline.State = CjkFriendlyState" in PLUGIN_JS
    assert all(ord(ch) < 128 for ch in PLUGIN_JS), "keep the plugin ASCII; spell characters as \\uXXXX"


# --------------------------------------------------------------- behaviour

# Shapes that stayed literal before the plugin; expected output with it.
# Characters are spelled as escapes: U+FF1A full-width colon, U+3002
# ideographic full stop, U+FF08/U+FF09 full-width parentheses, U+300A/U+300B
# double angle brackets, U+3010/U+3011 lenticular brackets, U+201C/U+201D
# curly quotes, U+2705 check mark (an emoji, wide but not CJK).
CJK_GAIN_EMPHASIS = {
    # **label:**text, the shape in nearly every reply
    "**\u6807\u9898\uff1a**\u5185\u5bb9": "<p><strong>\u6807\u9898\uff1a</strong>\u5185\u5bb9</p>",
    # the same with an ASCII colon
    "**\u6807\u9898:**\u5185\u5bb9": "<p><strong>\u6807\u9898:</strong>\u5185\u5bb9</p>",
    # inside list items
    "1. **\u6b65\u9aa4\u4e00\uff1a**\u6253\u5f00\u9875\u9762": "<ol>\n<li><strong>\u6b65\u9aa4\u4e00\uff1a</strong>\u6253\u5f00\u9875\u9762</li>\n</ol>",
    "- **\u95ee\u9898\uff1a**URL \u8d85\u957f": "<ul>\n<li><strong>\u95ee\u9898\uff1a</strong>URL \u8d85\u957f</li>\n</ul>",
    # full stop inside, text after
    "**\u7ed3\u8bba\u3002**\u4e0b\u4e00\u6b65": "<p><strong>\u7ed3\u8bba\u3002</strong>\u4e0b\u4e00\u6b65</p>",
    "**\u6a19\u984c\u3002**\u672c\u6587": "<p><strong>\u6a19\u984c\u3002</strong>\u672c\u6587</p>",
    # curly quotes (not CJK themselves) between two ideographs
    "\u8bf4**\u201c\u5f15\u7528\u201d**\u8bdd": "<p>\u8bf4<strong>\u201c\u5f15\u7528\u201d</strong>\u8bdd</p>",
    # CJK brackets inside the run
    "**\uff08\u62ec\u53f7\uff09**\u5185\u5bb9": "<p><strong>\uff08\u62ec\u53f7\uff09</strong>\u5185\u5bb9</p>",
    "**\u300a\u4e66\u540d\u300b**\u5f88\u597d": "<p><strong>\u300a\u4e66\u540d\u300b</strong>\u5f88\u597d</p>",
    "\u6587\u5b57**\u3010\u6ce8\u3011**\u6587\u5b57": "<p>\u6587\u5b57<strong>\u3010\u6ce8\u3011</strong>\u6587\u5b57</p>",
    "\u4ef7\u683c**\uff08\u542b\u7a0e\uff09**\u3002": "<p>\u4ef7\u683c<strong>\uff08\u542b\u7a0e\uff09</strong>\u3002</p>",
    # a code span or an emoji as the last thing inside the run
    "**`code`**\u8bf4\u660e": "<p><strong><code>code</code></strong>\u8bf4\u660e</p>",
    "**\u5df2\u5b8c\u6210\u2705**\u4e0b\u4e00\u6b65": "<p><strong>\u5df2\u5b8c\u6210\u2705</strong>\u4e0b\u4e00\u6b65</p>",
    # Hangul (East Asian Width N, but the Hangul script counts)
    "**\uac15\uc870:**\ub0b4\uc6a9": "<p><strong>\uac15\uc870:</strong>\ub0b4\uc6a9</p>",
    # mixed-script label, and neighbours outside the BMP
    "**\u4e2d\u6587 english\uff1a**\u6df7\u5408": "<p><strong>\u4e2d\u6587 english\uff1a</strong>\u6df7\u5408</p>",
    "\U0001f600**\u8868\u60c5\uff1a**\u6587\u5b57": "<p>\U0001f600<strong>\u8868\u60c5\uff1a</strong>\u6587\u5b57</p>",
    "\U00020bb7**\u6269\u5c55\uff1a**\u6587\u5b57": "<p>\U00020bb7<strong>\u6269\u5c55\uff1a</strong>\u6587\u5b57</p>",
}

# CJK text that already worked, or must stay untouched (code), renders the
# same with and without the plugin.
CJK_UNCHANGED = {
    "**\u7c97\u4f53**": "<p><strong>\u7c97\u4f53</strong></p>",
    "\u8fd9\u662f**\u91cd\u70b9**\u3002": "<p>\u8fd9\u662f<strong>\u91cd\u70b9</strong>\u3002</p>",
    "**\u6807\u9898**\uff1a\u5185\u5bb9": "<p><strong>\u6807\u9898</strong>\uff1a\u5185\u5bb9</p>",
    "\u4e2d\u6587*\u659c\u4f53*\u4e2d\u6587": "<p>\u4e2d\u6587<em>\u659c\u4f53</em>\u4e2d\u6587</p>",
    "**\u6807\u9898\uff1a**\n\u4e0b\u4e00\u884c": "<p><strong>\u6807\u9898\uff1a</strong><br>\n\u4e0b\u4e00\u884c</p>",
    # a lone asterisk is still text
    "\u6ce8*\uff1a\u5355\u4e2a\u661f\u53f7": "<p>\u6ce8*\uff1a\u5355\u4e2a\u661f\u53f7</p>",
    # code spans and fences never see the emphasis rules
    "`**\u6807\u9898\uff1a**\u5185\u5bb9`": "<p><code>**\u6807\u9898\uff1a**\u5185\u5bb9</code></p>",
    "```\n**\u6807\u9898\uff1a**\u5185\u5bb9\n```": "<pre><code>**\u6807\u9898\uff1a**\u5185\u5bb9\n</code></pre>",
}

# No CJK character beside a run: the plugin must change nothing. Mostly the
# emphasis examples of the CommonMark spec, plus the shapes a reply uses.
LATIN_UNCHANGED = [
    "*foo bar*", "a * foo bar*", "a*\"foo\"*", "* a *", "foo*bar*", "5*6*78",
    "_foo bar_", "_ foo bar_", "a_\"foo\"_", "foo_bar_", "5_6_78", "foo-_(bar)_",
    "*foo*bar", "_foo_bar", "_foo_bar_baz_", "_(bar)_.", "**foo bar**", "** foo bar**",
    "a**\"foo\"**", "foo**bar**", "__foo bar__", "__ foo bar__", "__\nfoo bar__",
    "a__\"foo\"__", "foo__bar__", "5__6__78", "__foo, __bar__, baz__", "foo-__(bar)__",
    "**foo bar **", "**(**foo)", "*(**foo**)*",
    "**Gomphocarpus (*Gomphocarpus physocarpus*, syn.\n*Asclepias physocarpa*)**",
    "**foo \"*bar*\" foo**", "**foo**bar", "__foo bar __", "__(__foo)", "_(__foo__)_",
    "__foo__bar", "__foo__bar__baz__", "__(bar)__.", "*foo [bar](/url)*", "*foo\nbar*",
    "_foo __bar__ baz_", "_foo _bar_ baz_", "__foo_ bar_", "*foo *bar**", "*foo **bar** baz*",
    "*foo**bar**baz*", "*foo**bar*", "***foo** bar*", "*foo **bar***", "*foo**bar***",
    "foo***bar***baz", "foo******bar*********baz", "*foo **bar *baz bim** bam*",
    "**foo **bar baz**", "*foo *bar baz*", "*[bar*](/url)", "_foo [bar_](/url)",
    "*<img src=\"foo\" title=\"*\"/>", "**<a href=\"**\">", "__<a href=\"__\">", "*a `*`*",
    "_a `_`_", "**a<https://foo.bar/?q=**>", "__a<https://foo.bar/?q=__>",
    "**Note:**text", "**Note:** text", "**key**: value", "**key:** value", "** spaced **",
    "a**b**c", "**bold**, more", "**bold**.", "**(bold)**", "**\"quoted\"**", "x**\"quoted\"**y",
    "**bold**\u2014next", "l\u2019**\u00e9t\u00e9**",
    "**\u0417\u0430\u0433\u043e\u043b\u043e\u0432\u043e\u043a:**\u0442\u0435\u043a\u0441\u0442",
    "**\u2705 done**", "**done\u2705**next", "**done \u2705** next", "**\u2714\ufe0f**next",
    "*italic*\u3002", "__init__", "foo__bar__baz", "snake_case_name and *em*",
    "a * b * c", "2 * 3 * 4 = 24", "**", "****", "*", "***", "foo *", "* foo",
]

# Code points the classifier must sort correctly: ideograph, full-width
# colon, ASCII letter, emoji, Hangul syllable, ideograph outside the BMP,
# curly quote.
CLASSIFIED = {
    "\u4e2d": True,
    "\uff1a": True,
    "a": False,
    "\u2705": False,
    "\uac00": True,
    "\U00020bb7": True,
    "\u201c": False,
}

NODE_SCRIPT = r"""
const fs = require("node:fs");
const [bundlePath, pluginPath, casesPath] = process.argv.slice(2);
const markdownit = require(bundlePath);
const plugin = require(pluginPath);
const cases = JSON.parse(fs.readFileSync(casesPath, "utf8"));
// The Portal's options (chat_ui.js and share_view.js).
const options = { html: false, linkify: true, breaks: true, typographer: true };
const plain = markdownit(options);
const cjk = markdownit(options).use(plugin);
const out = { plain: {}, cjk: {}, classified: {} };
for (const src of cases.sources) {
  out.plain[src] = plain.render(src);
  out.cjk[src] = cjk.render(src);
}
for (const ch of cases.chars) out.classified[ch] = plugin.isCjkCodePoint(ch.codePointAt(0));
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping markdown behaviour test")
    work = tmp_path_factory.mktemp("cjk")
    script = work / "render.js"
    script.write_text(NODE_SCRIPT, encoding="utf-8")
    cases = work / "cases.json"
    sources = list(CJK_GAIN_EMPHASIS) + list(CJK_UNCHANGED) + LATIN_UNCHANGED
    cases.write_text(json.dumps({"sources": sources, "chars": list(CLASSIFIED)}), encoding="utf-8")
    result = subprocess.run(
        [node_bin, str(script), str(BUNDLE.resolve()), str(PLUGIN.resolve()), str(cases)],
        check=False, capture_output=True, encoding="utf-8",
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return json.loads(result.stdout)


def test_cjk_shapes_gain_their_emphasis(rendered):
    for src, expected in CJK_GAIN_EMPHASIS.items():
        assert "**" in rendered["plain"][src], f"{src!r} rendered without the plugin; the sample no longer proves anything"
        assert rendered["cjk"][src].strip() == expected, src


def test_cjk_text_that_already_worked_and_code_are_untouched(rendered):
    for src, expected in CJK_UNCHANGED.items():
        assert rendered["cjk"][src].strip() == expected, src
        assert rendered["cjk"][src] == rendered["plain"][src], src


def test_text_without_a_cjk_neighbour_renders_exactly_as_before(rendered):
    for src in LATIN_UNCHANGED:
        assert rendered["cjk"][src] == rendered["plain"][src], src
    # Spec-conformant, and what GitHub shows: the ASCII shape stays literal.
    assert rendered["cjk"]["**Note:**text"].strip() == "<p>**Note:**text</p>"


def test_classifier_sorts_scripts_and_emoji(rendered):
    for ch, expected in CLASSIFIED.items():
        assert rendered["classified"][ch] is expected, f"U+{ord(ch):04X}"
