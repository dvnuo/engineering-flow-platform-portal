# Portal catalogues

English is the Portal's source language. A user-visible string is written in English where it
is used and wrapped in `t()`; the files here map that English text to each language the
Portal ships:

| File | Language | Notes |
|---|---|---|
| `zh-Hans.json` | Simplified Chinese | mainland vocabulary (软件, 服务器, 默认, 登录, 设置) |
| `zh-Hant-HK.json` | Traditional Chinese, Hong Kong conventions | Standard Written Chinese in Traditional characters with Hong Kong vocabulary (軟件, 伺服器, 預設, 登入, 設定, 網絡), never Taiwan terms and never vernacular Cantonese |

There is no `en.json`: the key is the English text, and a missing or empty translation falls
back to it, so a new string ships in English until it is translated.

## Writing a string

- Templates: `{{ t("Sign out") }}`, attributes too: `title="{{ t('Assistants') }}"`.
- JavaScript: `t("Copied")`, a global from `app/static/js/i18n.js`, loaded before every
  first-party script. Never call it where a local `t` is in scope.
- Python: `from app.i18n import t` for labels that reach the page.
- Placeholders: `t("Updated {time}", time=value)` / `t("Updated {time}", { time: value })`.
  The translation keeps the same `{names}`.
- Counts: `tn(n, "one file", "{n} files")`. English picks the form by `n === 1`; the catalogue
  stores one entry under the key `one file|{n} files`, which Chinese fills with a single form.
- The first argument of `t()`/`tn()` is always a plain string literal: `scripts/i18n_check.py`
  extracts the keys from the source and cannot follow variables or template literals.
- Data tables (a module-level dict in Python, a label table built at parse time in JavaScript):
  mark each English string with `N_("AWS accounts")`, which returns it unchanged and only tells
  the check script it is a key, and translate where the value is shown with `tv(value)`
  (`{{ tv(value) }}` in a template). `tv()` passes values the catalogue does not know (ids,
  URLs, names) through unchanged. A table that tests parse as JSON can stay plain English
  when the same strings are wrapped with `t()` somewhere else (the server template it
  mirrors): the keys exist, and the display code uses `tv()`.
- Text the Portal sends to the model ("Continue", the attachment placeholder, task
  instructions) stays in English and is not wrapped.
- The catalogues and `app/help/<locale>/` are the only places where Chinese may appear in
  `app/`; the check script fails on a Chinese character anywhere else.

## Keeping the catalogues in step

```bash
python scripts/i18n_check.py          # what the test suite runs: errors on missing keys
python scripts/i18n_check.py --fill   # add every missing key to each catalogue with an empty value
```

A slice of the UI is translated in the same pull request that wraps its strings: wrap, run
`--fill`, translate the empty values (both locales directly from the English, with
`GLOSSARY.md` open; never derive the Hong Kong file from the Simplified one), and have a native
reader look at the catalogue diff. Changing an English string changes its key, so the old
translation shows up as an orphan in the report and the new key as missing.
