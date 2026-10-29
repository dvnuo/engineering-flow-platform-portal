#!/usr/bin/env python
"""Keep the Portal's English source strings and the catalogues under app/locale in step.

    python scripts/i18n_check.py          # report; exit 1 on errors (the test suite runs this)
    python scripts/i18n_check.py --fill   # add every missing key to each catalogue with an empty value
    python scripts/i18n_check.py --list   # print the English keys in use

Checks:

- the first argument of every ``t("...")`` / ``N_("...")`` and the two forms
  of every ``tn(n, "...", "...")`` in templates, first-party JavaScript and
  Python are plain string literals, each one a whole argument (no
  concatenation, condition or adjacent literal after it), since the keys are
  extracted from the source (``tv(value)`` translates a value at run time and
  is not a key);
- every key in use has a non-empty translation in each catalogue (missing
  keys are errors, so a slice is translated in the PR that wraps it);
- a translation has exactly the English key's ``{placeholders}`` (``{n}`` is
  implied for a plural key);
- catalogue entries nothing uses any more are reported as orphans (warnings);
- Chinese characters appear only in the catalogues and under
  ``app/help/<locale>/``: the source stays English and ASCII.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOCALES = ("zh-Hans", "zh-Hant-HK")
PLURAL_SEPARATOR = "|"

SOURCE_SETS = (("app/templates", "*.html"), ("app/static/js", "*.js"), ("app", "*.py"))
EXCLUDED_DIR_NAMES = {"lib", "locale", "__pycache__", "node_modules"}
EXCLUDED_SOURCE_FILES = {Path("app/i18n.py"), Path("app/static/js/i18n.js")}
TEXT_SUFFIXES = {".html", ".js", ".py", ".css", ".md", ".json", ".yaml", ".yml", ".txt", ".svg"}

# t("key"), tn(count, "one", "other"), and N_("key"), which marks a literal in
# a data table as a key without translating it (shown later through tv()).
CALL_RE = re.compile(r"(?<![\w.$])(tn|t|N_)\(")
PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
CJK_RE = re.compile("[\u3000-\u303f\u3040-\u30ff\u4e00-\u9fff\uff00-\uffef]")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "'": "'", '"': '"'}


class Key:
    __slots__ = ("text", "file", "line", "plural")

    def __init__(self, text: str, file: Path, line: int, plural: bool = False) -> None:
        self.text, self.file, self.line, self.plural = text, file, line, plural


def _iter_source_files(repo: Path):
    seen: set[Path] = set()
    for root, pattern in SOURCE_SETS:
        base = repo / root
        if not base.exists():
            continue
        for path in sorted(base.rglob(pattern)):
            relative = path.relative_to(repo)
            if any(part in EXCLUDED_DIR_NAMES for part in relative.parts) or relative in EXCLUDED_SOURCE_FILES:
                continue
            if path in seen:
                continue
            seen.add(path)
            yield path


def _skip_ws(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    return index


def _ends_argument(text: str, index: int) -> bool:
    """Whether a literal that ends at ``index`` is a whole argument.

    Anything else after it (``+ name``, ``~ name``, ``if ... else "..."``, an
    adjacent literal that Python would join to it) makes the key the page
    looks up differ from the one read here.
    """
    index = _skip_ws(text, index)
    return index < len(text) and text[index] in ",)"


_HEX = "0123456789abcdefABCDEF"


def _decode_hex_escape(text: str, index: int):
    """``(character, end)`` for ``\\uXXXX``, ``\\u{X...}`` or ``\\xXX`` at ``index``, else ``None``.

    The page sees the decoded character, so the key must too: a key written
    as "Loading\\u2026" is the same key as one written with the ellipsis
    character itself.
    """
    kind = text[index + 1] if index + 1 < len(text) else ""
    if kind == "x":
        digits = text[index + 2:index + 4]
        if len(digits) == 2 and all(c in _HEX for c in digits):
            return chr(int(digits, 16)), index + 4
        return None
    if kind != "u":
        return None
    if text[index + 2:index + 3] == "{":
        end = text.find("}", index + 3)
        digits = text[index + 3:end] if end > 0 else ""
        if digits and len(digits) <= 6 and all(c in _HEX for c in digits):
            return chr(int(digits, 16)), end + 1
        return None
    digits = text[index + 2:index + 6]
    if len(digits) == 4 and all(c in _HEX for c in digits):
        return chr(int(digits, 16)), index + 6
    return None


def _parse_string_literal(text: str, index: int):
    """``(value, end)`` for a quoted literal starting at ``index``, else ``None``."""
    if index >= len(text) or text[index] not in "'\"":
        return None
    quote = text[index]
    out: list[str] = []
    i = index + 1
    while i < len(text):
        char = text[i]
        if char == "\\" and i + 1 < len(text):
            decoded = _decode_hex_escape(text, i)
            if decoded is not None:
                out.append(decoded[0])
                i = decoded[1]
                continue
            out.append(_ESCAPES.get(text[i + 1], text[i + 1]))
            i += 2
            continue
        if char == quote:
            return "".join(out), i + 1
        if char == "\n":
            return None
        out.append(char)
        i += 1
    return None


def _skip_argument(text: str, index: int):
    """The index of the top-level comma after the argument starting at ``index``, else ``None``."""
    depth = 0
    i = index
    while i < len(text):
        char = text[i]
        if char in "'\"":
            parsed = _parse_string_literal(text, i)
            if parsed is None:
                return None
            i = parsed[1]
            continue
        if char == "`":
            end = text.find("`", i + 1)
            if end < 0:
                return None
            i = end + 1
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                return None
            depth -= 1
        elif char == "," and depth == 0:
            return i
        elif char == "\n" and depth == 0:
            return None
        i += 1
    return None


def extract_keys(repo: Path = REPO):
    """``(keys, errors)``: every key the sources use, and the calls whose keys cannot be read."""
    keys: list[Key] = []
    errors: list[str] = []
    for path in _iter_source_files(repo):
        text = path.read_text(encoding="utf-8", errors="replace")
        relative = path.relative_to(repo).as_posix()
        for match in CALL_RE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            name = match.group(1)
            index = _skip_ws(text, match.end())
            if index < len(text) and text[index] == ")":
                # "t()" with no argument is prose about the helper (a comment,
                # a docstring), not a call: every real call has a key.
                continue
            if name in ("t", "N_"):
                parsed = _parse_string_literal(text, index)
                if parsed is None or not _ends_argument(text, parsed[1]):
                    errors.append(f"{relative}:{line}: {name}() needs a plain string literal as its key")
                    continue
                keys.append(Key(parsed[0], path, line))
                continue
            comma = _skip_argument(text, index)
            if comma is None:
                errors.append(f"{relative}:{line}: tn() needs a count, then two plain string literals")
                continue
            one = _parse_string_literal(text, _skip_ws(text, comma + 1))
            if one is None:
                errors.append(f"{relative}:{line}: tn() needs a plain string literal for its singular form")
                continue
            after_one = _skip_ws(text, one[1])
            if after_one >= len(text) or text[after_one] != ",":
                errors.append(f"{relative}:{line}: tn() needs a plural form after the singular")
                continue
            other = _parse_string_literal(text, _skip_ws(text, after_one + 1))
            if other is None or not _ends_argument(text, other[1]):
                errors.append(f"{relative}:{line}: tn() needs a plain string literal for its plural form")
                continue
            keys.append(Key(f"{one[0]}{PLURAL_SEPARATOR}{other[0]}", path, line, plural=True))
    return keys, errors


def load_catalogue(repo: Path, locale: str) -> dict:
    path = repo / "app" / "locale" / f"{locale}.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def write_catalogue(repo: Path, locale: str, data: dict) -> None:
    path = repo / "app" / "locale" / f"{locale}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {key: data[key] for key in sorted(data, key=lambda item: (item.casefold(), item))}
    path.write_text(json.dumps(ordered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER_RE.findall(text))


def check_catalogues(repo: Path, keys: list[Key]):
    """``(errors, warnings)`` for the catalogues against the keys in use."""
    errors: list[str] = []
    warnings: list[str] = []
    used = {key.text: key for key in keys}
    for locale in LOCALES:
        data = load_catalogue(repo, locale)
        for text, key in sorted(used.items()):
            value = data.get(text)
            if not isinstance(value, str) or not value.strip():
                where = f"{key.file.relative_to(repo).as_posix()}:{key.line}"
                errors.append(f"{locale}: missing translation for {text!r} ({where})")
                continue
            allowed = _placeholders(text) | ({"n"} if key.plural else set())
            extra = _placeholders(value) - allowed
            if extra:
                errors.append(f"{locale}: translation of {text!r} uses placeholders the key has not: {sorted(extra)}")
            dropped = _placeholders(text) - _placeholders(value)
            if dropped:
                errors.append(f"{locale}: translation of {text!r} drops placeholders the key has: {sorted(dropped)}")
        for text in sorted(data):
            if text not in used:
                warnings.append(f"{locale}: orphan entry {text!r} (nothing uses it)")
    return errors, warnings


def _cjk_allowed(repo: Path, path: Path) -> bool:
    relative = path.relative_to(repo)
    parts = relative.parts
    if len(parts) >= 2 and parts[0] == "app" and parts[1] == "locale":
        return True
    if len(parts) >= 3 and parts[0] == "app" and parts[1] == "help" and parts[2] in LOCALES:
        return True
    return False


def check_cjk(repo: Path) -> list[str]:
    errors: list[str] = []
    app_dir = repo / "app"
    if not app_dir.exists():
        return errors
    for path in sorted(app_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        relative = path.relative_to(repo)
        if any(part in EXCLUDED_DIR_NAMES for part in relative.parts) or _cjk_allowed(repo, path):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            if CJK_RE.search(line):
                errors.append(f"{relative.as_posix()}:{number}: Chinese characters belong in app/locale, not in the source")
                break
    return errors


def fill_catalogues(repo: Path, keys: list[Key]) -> dict[str, int]:
    added: dict[str, int] = {}
    for locale in LOCALES:
        data = load_catalogue(repo, locale)
        count = 0
        for key in keys:
            if key.text not in data:
                data[key.text] = ""
                count += 1
        write_catalogue(repo, locale, data)
        added[locale] = count
    return added


def run(argv: list[str] | None = None, repo: Path = REPO, out=None) -> int:
    if out is None:
        out = sys.stdout
        # Keys may carry characters a cp1252 console cannot show (an em dash,
        # a "nearly equal" sign); the report must still print on Windows.
        reconfigure = getattr(out, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fill", action="store_true", help="add missing keys to each catalogue with an empty value")
    parser.add_argument("--list", action="store_true", help="print the English keys in use")
    args = parser.parse_args(argv)

    keys, errors = extract_keys(repo)
    if args.list:
        for text in sorted({key.text for key in keys}, key=lambda item: (item.casefold(), item)):
            print(text, file=out)
    if args.fill:
        for locale, count in fill_catalogues(repo, keys).items():
            print(f"{locale}: {count} key(s) added", file=out)
    catalogue_errors, warnings = check_catalogues(repo, keys)
    errors.extend(catalogue_errors)
    errors.extend(check_cjk(repo))
    for warning in warnings:
        print(f"WARN {warning}", file=out)
    for error in errors:
        print(f"ERROR {error}", file=out)
    distinct = len({key.text for key in keys})
    print(f"{distinct} key(s) in use, {len(errors)} error(s), {len(warnings)} warning(s)", file=out)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(run())
