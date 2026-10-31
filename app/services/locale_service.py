"""The member's language: a saved preference, else the browser, else English.

One setting lives in ``users.preferences_json``: ``ui_locale``, the Portal's
language (``auto``, ``en``, ``zh-Hans``, ``zh-Hant-HK``), where ``auto``
follows the browser's ``Accept-Language``. The assistant answers in the same
language; there is no second setting for that.

It is resolved per request into :class:`ResolvedLocale`: the page is rendered
with ``ui_locale`` (``<html lang>``, fonts and the catalogues) and the chat
proxy writes ``reply_language`` into the trusted ``metadata.portal_user`` the
runtimes turn into a "Reply language" prompt block. ``reply_language_strict``
tells the runtime whether the member chose the language (always answer in it)
or it came from the browser (a default the member's own messages may
override).

Traditional Chinese here means Hong Kong conventions (Standard Written Chinese
in Traditional characters with Hong Kong vocabulary): ``zh-HK``, ``zh-MO``,
``zh-TW``, any ``zh-Hant*`` and ``yue*`` all map to it, since it is the only
Traditional variant the Portal ships.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

UI_LOCALE_AUTO = "auto"
DEFAULT_LOCALE = "en"

# Order matters for the menu and for documentation; English is the source language.
SUPPORTED_LOCALES: tuple[str, ...] = ("en", "zh-Hans", "zh-Hant-HK")
UI_LOCALE_CHOICES: tuple[str, ...] = (UI_LOCALE_AUTO,) + SUPPORTED_LOCALES

_PREFERENCE_KEYS = ("ui_locale",)


@dataclass(frozen=True)
class LocalePreferences:
    """What the member saved (never resolved)."""

    ui_locale: str = UI_LOCALE_AUTO

    def as_dict(self) -> dict[str, str]:
        return {"ui_locale": self.ui_locale}


@dataclass(frozen=True)
class ResolvedLocale:
    """What one request renders with and what its chat requests carry."""

    ui_locale: str
    ui_source: str  # preference | browser | default
    reply_language: str
    reply_language_strict: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "ui_locale": self.ui_locale,
            "ui_source": self.ui_source,
            "reply_language": self.reply_language,
            "reply_language_strict": self.reply_language_strict,
        }


def parse_preferences_json(raw: Any) -> dict[str, Any]:
    """The stored JSON object, or ``{}`` for anything unreadable.

    Unknown keys are kept so a later Portal can add preferences without a
    migration and an older one does not wipe them on save.
    """
    if isinstance(raw, dict):
        return dict(raw)
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        logger.warning("users.preferences_json is not valid JSON; ignoring it")
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def locale_preferences_from_json(raw: Any) -> LocalePreferences:
    data = parse_preferences_json(raw)
    ui_locale = data.get("ui_locale")
    return LocalePreferences(ui_locale=ui_locale if ui_locale in UI_LOCALE_CHOICES else UI_LOCALE_AUTO)


def locale_preferences_for_user(user: Any) -> LocalePreferences:
    return locale_preferences_from_json(getattr(user, "preferences_json", None))


def merge_locale_preferences(raw: Any, **changes: Optional[str]) -> str:
    """The JSON to store after applying ``changes`` (``None`` leaves a key alone).

    Values are validated by the API schema before they get here; anything
    else is rejected rather than silently normalised, so a typo in a client
    never saves as "auto".
    """
    data = parse_preferences_json(raw)
    for key, value in changes.items():
        if key not in _PREFERENCE_KEYS or value is None:
            continue
        if value not in UI_LOCALE_CHOICES:
            raise ValueError(f"{key} must be one of {', '.join(UI_LOCALE_CHOICES)}")
        data[key] = value
    return json.dumps(data, sort_keys=True)


def locale_for_language_tag(tag: str) -> Optional[str]:
    """The supported locale a BCP 47 tag stands for, or ``None``.

    ``zh`` splits on script first, then region: a browser set to Chinese
    (Hong Kong) sends ``zh-HK`` and gets the Hong Kong Traditional UI; Chinese
    (Simplified) sends ``zh-CN`` or ``zh-Hans``. A bare ``zh`` is Simplified,
    which is what most browsers mean by it. Cantonese (``yue``) is served the
    Traditional UI: it is written with the same characters.
    """
    parts = [part for part in str(tag or "").strip().lower().replace("_", "-").split("-") if part]
    if not parts:
        return None
    language, subtags = parts[0], parts[1:]
    if language == "en":
        return "en"
    if language == "yue":
        return "zh-Hant-HK"
    if language != "zh":
        return None
    if "hans" in subtags:
        return "zh-Hans"
    if "hant" in subtags:
        return "zh-Hant-HK"
    if any(subtag in {"hk", "mo", "tw"} for subtag in subtags):
        return "zh-Hant-HK"
    return "zh-Hans"


def locale_from_accept_language(header: Optional[str]) -> Optional[str]:
    """The first supported locale in ``Accept-Language`` order of preference.

    Quality values are honoured (``zh-HK;q=0.8, en;q=0.9`` prefers English);
    equal weights keep the header's order, as browsers already list the
    languages in the member's order.
    """
    text = str(header or "").strip()
    if not text:
        return None
    ranked: list[tuple[float, int, str]] = []
    for index, item in enumerate(text.split(",")):
        pieces = [piece.strip() for piece in item.split(";")]
        tag = pieces[0]
        if not tag or tag == "*":
            continue
        quality = 1.0
        for param in pieces[1:]:
            if param.lower().startswith("q="):
                try:
                    quality = float(param[2:])
                except ValueError:
                    quality = 0.0
        if quality <= 0:
            continue
        ranked.append((-quality, index, tag))
    for _weight, _index, tag in sorted(ranked):
        locale = locale_for_language_tag(tag)
        if locale:
            return locale
    return None


def resolve_locale(preferences: LocalePreferences, accept_language: Optional[str]) -> ResolvedLocale:
    if preferences.ui_locale in SUPPORTED_LOCALES:
        ui_locale, ui_source = preferences.ui_locale, "preference"
    else:
        browser = locale_from_accept_language(accept_language)
        ui_locale, ui_source = (browser, "browser") if browser else (DEFAULT_LOCALE, "default")
    # The assistant answers in the Portal's language. A language the member
    # chose is strict (always answer in it); one taken from the browser is a
    # default the member's own messages may override.
    return ResolvedLocale(ui_locale, ui_source, ui_locale, ui_source == "preference")


def resolve_locale_for_user(user: Any, accept_language: Optional[str]) -> ResolvedLocale:
    """The locale for a signed-in member's request (``user`` may be ``None``)."""
    return resolve_locale(locale_preferences_for_user(user), accept_language)


def resolve_locale_for_request(request: Any, user: Any = None) -> ResolvedLocale:
    """Shorthand for routes: the request's ``Accept-Language`` plus the member's preference."""
    headers = getattr(request, "headers", None)
    accept_language = headers.get("accept-language") if headers is not None else None
    return resolve_locale_for_user(user, accept_language)
