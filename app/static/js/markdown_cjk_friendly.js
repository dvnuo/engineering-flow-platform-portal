/* CJK-friendly emphasis for markdown-it.
 *
 * CommonMark decides whether a run of `*` or `_` can open or close emphasis
 * from the characters on either side of it. A closing run that follows
 * punctuation must be followed by whitespace or punctuation, and an opening
 * run that is followed by punctuation must be preceded by whitespace or
 * punctuation. Chinese, Japanese and Korean text puts no space after the run
 * and keeps its own punctuation inside it, so a bold label ending in a
 * full-width colon (U+FF1A) and followed straight by the text, a bold
 * parenthesis (U+FF08 ... U+FF09) or a bold quotation (U+201C ... U+201D)
 * between two ideographs all rendered as literal asterisks.
 *
 * This is the CJK-friendly amendment to the flanking rules
 * (https://github.com/tats-u/markdown-cjk-friendly, specification.md): a
 * CJK character beside the run satisfies the "whitespace or punctuation"
 * escape, and CJK punctuation inside the run does not demand one. Runs with
 * no CJK character on either side parse exactly as before, so Latin text is
 * untouched; `**Note:**text` stays literal here as it does on GitHub.
 *
 * The npm plugin is ESM-only and pulls a Unicode width table, and the Portal
 * vendors its browser libraries by hand, so this is a self-contained UMD
 * file. The chat (chat_ui.js) and the share page (share_view.js) register it
 * on their markdown-it instance with md.use(window.markdownItCjkFriendly);
 * the node tests require() it.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.markdownItCjkFriendly = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // Code points with East Asian Width W, F or H (Unicode 15.1), minus the
  // emoji that are wide only for their emoji presentation, plus the Hangul
  // script. Generated from Python's unicodedata and merged into blocks; the
  // short gaps of unassigned code points inside CJK blocks are filled.
  // Ideographic space (U+3000) is inside but whitespace is tested first.
  var CJK_RANGES = [
    [0x1100, 0x11FF], [0x2329, 0x232A], [0x2E80, 0x2EF3], [0x2F00, 0x2FD5],
    [0x2FF0, 0x31E3], [0x31EF, 0x3247], [0x3250, 0x4DBF], [0x4E00, 0xA4C6],
    [0xA960, 0xA97C], [0xAC00, 0xD7A3], [0xD7B0, 0xD7FB], [0xF900, 0xFAFF],
    [0xFE10, 0xFE19], [0xFE30, 0xFE6B], [0xFF01, 0xFFEE], [0x16FE0, 0x16FE4],
    [0x16FF0, 0x16FF1], [0x17000, 0x187F7], [0x18800, 0x18CD5], [0x18D00, 0x18D08],
    [0x1AFF0, 0x1B122], [0x1B132, 0x1B132], [0x1B150, 0x1B155], [0x1B164, 0x1B167],
    [0x1B170, 0x1B2FB], [0x20000, 0x3FFFD]
  ];

  function isCjkCodePoint(cp) {
    var lo = 0;
    var hi = CJK_RANGES.length - 1;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      var range = CJK_RANGES[mid];
      if (cp < range[0]) hi = mid - 1;
      else if (cp > range[1]) lo = mid + 1;
      else return true;
    }
    return false;
  }

  // U+FE00..FE0F are the general-use variation selectors (U+FE0F asks for
  // emoji presentation); U+E0100..E01EF are the ideographic ones.
  function isVariationSelector(cp) {
    return (cp >= 0xFE00 && cp <= 0xFE0F) || (cp >= 0xE0100 && cp <= 0xE01EF);
  }

  // The code point that ends just before `index`, with its length in UTF-16
  // units, or null at the start of the string.
  function codePointBefore(src, index) {
    if (index <= 0) return null;
    var low = src.charCodeAt(index - 1);
    if (low >= 0xDC00 && low <= 0xDFFF && index >= 2) {
      var high = src.charCodeAt(index - 2);
      if (high >= 0xD800 && high <= 0xDBFF) {
        return { cp: ((high - 0xD800) << 10) + (low - 0xDC00) + 0x10000, length: 2 };
      }
    }
    return { cp: low, length: 1 };
  }

  var WHITESPACE = { ws: true, punct: false, cjk: false };
  var PLAIN = { ws: false, punct: false, cjk: false };

  function plugin(md) {
    var PreviousState = md.inline.State;
    var isWhiteSpace = md.utils.isWhiteSpace;
    var isMdAsciiPunct = md.utils.isMdAsciiPunct;
    var isPunctChar = md.utils.isPunctChar;

    function isPunct(cp) {
      return isMdAsciiPunct(cp) || isPunctChar(String.fromCodePoint(cp));
    }

    function classify(cp) {
      if (isWhiteSpace(cp)) return WHITESPACE;
      return { ws: false, punct: isPunct(cp), cjk: isCjkCodePoint(cp) };
    }

    // The character before `index`, read as a sequence: a variation selector
    // belongs to the character before it. The start of the line counts as
    // whitespace, as in CommonMark.
    function classifyBefore(src, index) {
      var unit = codePointBefore(src, index);
      if (!unit) return WHITESPACE;
      if (!isVariationSelector(unit.cp)) return classify(unit.cp);
      var base = codePointBefore(src, index - unit.length);
      if (!base) return PLAIN;
      // An ideographic variation sequence is a CJK character by definition.
      if (unit.cp >= 0xE0100) return { ws: false, punct: false, cjk: true };
      // Emoji presentation. The amendment counts the emoji as non-CJK
      // punctuation, but markdown-it never looked past the selector, so a
      // check mark (U+2714 U+FE0F) right before a closing run kept its
      // emphasis; stay with that so text without CJK characters renders
      // exactly as before.
      if (unit.cp === 0xFE0F) return PLAIN;
      // Text presentation: the base keeps its class, except that ambiguous
      // punctuation shown in its standardized fullwidth form counts as CJK.
      var punct = isPunct(base.cp);
      return { ws: false, punct: punct, cjk: punct || isCjkCodePoint(base.cp) };
    }

    class CjkFriendlyState extends PreviousState {
      scanDelims(start, canSplitWord) {
        var src = this.src;
        var max = this.posMax;
        var marker = src.charCodeAt(start);
        var pos = start;
        while (pos < max && src.charCodeAt(pos) === marker) pos++;
        var count = pos - start;

        var prev = classifyBefore(src, start);
        var next = pos < max ? classify(src.codePointAt(pos)) : WHITESPACE;

        var prevNonCjkPunct = prev.punct && !prev.cjk;
        var nextNonCjkPunct = next.punct && !next.cjk;

        // CommonMark's left- and right-flanking rules with the amendment: a
        // CJK neighbour on the outside satisfies the "whitespace or
        // punctuation" escape, and CJK punctuation on the inside never needs
        // it. With no CJK neighbour both reduce to the original rules.
        var leftFlanking = !next.ws && (!nextNonCjkPunct || prev.ws || prev.punct || prev.cjk);
        var rightFlanking = !prev.ws && (!prevNonCjkPunct || next.ws || next.punct || next.cjk);

        return {
          can_open: leftFlanking && (canSplitWord || !rightFlanking || prev.punct),
          can_close: rightFlanking && (canSplitWord || !leftFlanking || next.punct),
          length: count
        };
      }
    }

    md.inline.State = CjkFriendlyState;
  }

  plugin.isCjkCodePoint = isCjkCodePoint;
  return plugin;
});
