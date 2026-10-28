"""Portal localisation: English source strings, one JSON catalogue per locale, a request-bound t().

English is the source language: every user-visible string in a template, in
first-party JavaScript or in Python is written in English and wrapped in
``t()`` (``tn()`` for a count). The catalogues under ``app/locale/`` map that
English text to each locale's translation, so the source files stay ASCII and
readers of the source still see English; a missing or empty translation falls
back to the English key. ``scripts/i18n_check.py`` keeps catalogues and
sources in step and runs with the test suite.

The locale of the current request lives in a context variable: the HTTP
middleware binds it from ``Accept-Language`` for every request, and the web
routes rebind it from the member's saved preference once they know who is
asking (``app.web._session_user_access``). ``t()`` reads it at call time, so
templates and Python labels rendered inside the request see the right one.
base.html inlines the catalogue of the page's locale for ``i18n.js``, whose
``t()`` and ``tn()`` do the same in the browser.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
import json
import logging
from pathlib import Path
import re
from typing import Any, Mapping, Optional

from app.services.locale_service import DEFAULT_LOCALE, SUPPORTED_LOCALES

logger = logging.getLogger(__name__)

LOCALE_DIR = Path(__file__).resolve().parent / "locale"
# A plural pair is stored under one key: the English singular and plural
# joined by this separator ("one file|{n} files"). Chinese keeps one form.
PLURAL_SEPARATOR = "|"

_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_request_locale: ContextVar[str] = ContextVar("efp_request_locale", default=DEFAULT_LOCALE)
_catalogues: dict[str, dict[str, str]] = {}


def catalogue_path(locale: str) -> Path:
    return LOCALE_DIR / f"{locale}.json"


def load_catalogue(locale: str) -> dict[str, str]:
    """The stored catalogue for ``locale``: {English key: translation}. English and unknown locales are empty."""
    if locale == DEFAULT_LOCALE or locale not in SUPPORTED_LOCALES:
        return {}
    path = catalogue_path(locale)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("Could not read the %s catalogue at %s", locale, path)
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): value for key, value in data.items() if isinstance(value, str)}


def reload_catalogues() -> None:
    """Re-read every catalogue from disk (tests, and whoever edits a catalogue on a running Portal)."""
    _catalogues.clear()
    for locale in SUPPORTED_LOCALES:
        _catalogues[locale] = load_catalogue(locale)


def catalogue(locale: str) -> Mapping[str, str]:
    if not _catalogues:
        reload_catalogues()
    return _catalogues.get(locale, {})


def bind_locale(locale: str) -> Token:
    """Make ``locale`` the current request's; the token goes to ``reset_locale``."""
    return _request_locale.set(locale if locale in SUPPORTED_LOCALES else DEFAULT_LOCALE)


def reset_locale(token: Token) -> None:
    _request_locale.reset(token)


def current_locale() -> str:
    return _request_locale.get()


def interpolate(text: str, params: Mapping[str, Any]) -> str:
    """Fill ``{name}`` placeholders from ``params``; a placeholder with no value stays as written."""
    if not params or "{" not in text:
        return text

    def _replace(match: re.Match) -> str:
        name = match.group(1)
        return str(params[name]) if name in params else match.group(0)

    return _PLACEHOLDER_RE.sub(_replace, text)


def translate(locale: str, key: str, **params: Any) -> str:
    """``key`` in ``locale``, or the English key itself when the catalogue has no translation."""
    translated = catalogue(locale).get(key)
    text = translated if isinstance(translated, str) and translated.strip() else key
    return interpolate(text, params)


def translate_plural(locale: str, count: Any, one: str, other: str, **params: Any) -> str:
    """The form for ``count``: ``one`` or ``other`` in English, the catalogue's single entry elsewhere."""
    merged = {"n": count, **params}
    translated = catalogue(locale).get(f"{one}{PLURAL_SEPARATOR}{other}")
    if isinstance(translated, str) and translated.strip():
        return interpolate(translated, merged)
    return interpolate(one if count == 1 else other, merged)


def t(key: str, **params: Any) -> str:
    """``key`` in the current request's locale."""
    return translate(current_locale(), key, **params)


def tn(count: Any, one: str, other: str, **params: Any) -> str:
    """The plural form of ``one`` / ``other`` for ``count`` in the current request's locale."""
    return translate_plural(current_locale(), count, one, other, **params)


def N_(text: str) -> str:
    """Mark ``text`` as a catalogue key without translating it.

    For strings that live in data (module-level tables, dicts built at import
    time) where no request locale exists yet: the check script extracts the
    literal like a ``t()`` key, and the code that shows the value translates
    it at request time with ``tv()``.
    """
    return text


def tv(value: Any, **params: Any) -> str:
    """Translate a value holding a key declared elsewhere (``N_()`` or ``t()``).

    The value comes from data, so the check script cannot see it; values the
    catalogues do not know (ids, URLs, names) come back unchanged.
    """
    if value is None:
        return ""
    return translate(current_locale(), str(value), **params)


def bootstrap_payload(locale: Optional[str] = None) -> dict[str, Any]:
    resolved = locale if locale in SUPPORTED_LOCALES else (current_locale() if locale is None else DEFAULT_LOCALE)
    return {"locale": resolved, "messages": dict(catalogue(resolved))}


def i18n_bootstrap(locale: Optional[str] = None) -> str:
    """The JSON ``i18n.js`` reads, safe to inline in a ``<script>`` element."""
    payload = bootstrap_payload(locale)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def template_globals() -> dict[str, Any]:
    """What the Jinja environment exposes: ``t``, ``tn``, ``tv``, the locale and the inline catalogue."""
    return {"t": t, "tn": tn, "tv": tv, "current_locale": current_locale, "i18n_bootstrap": i18n_bootstrap}


__all__ = [
    "LOCALE_DIR",
    "N_",
    "PLURAL_SEPARATOR",
    "bind_locale",
    "bootstrap_payload",
    "catalogue",
    "catalogue_path",
    "current_locale",
    "i18n_bootstrap",
    "interpolate",
    "load_catalogue",
    "reload_catalogues",
    "reset_locale",
    "t",
    "template_globals",
    "tn",
    "translate",
    "translate_plural",
    "tv",
]
