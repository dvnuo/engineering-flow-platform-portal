# Help centre topics

Every `*.md` file in this directory is one topic in the Portal's Help section.
Edit the text, save, and reload the page: the server reads the files on each
request, so there is nothing to rebuild or restart. This README is the one
file that is not a topic.

The file name is the topic id and its address: `getting-started.md` opens at
`#/help/getting-started`. The body is markdown, rendered in the browser with
the same renderer chat replies use, so headings, lists, tables, links, code
fences and ```mermaid diagrams all work. Link to another topic with
`[text](#/help/<id>)`; links to other sites open in a new tab. Write `{mod}`
for the platform modifier key and the reader sees Ctrl or ⌘.

## A concept topic

The block between the `---` lines at the top lists the topic in the sub-menu;
everything after it is the page.

```
---
title: Creating your first assistant
summary: What an assistant is and how to get one working.
group: Getting started
icon: rocket
order: 10
---
## How to start
...
```

- `title` — the sub-menu label and the page heading. Required.
- `group` — `Getting started`, `Connectors` or `Working`. A new
  name creates a new group, listed after those. Required.
- `summary` — one line shown under the label.
- `icon` — a [lucide](https://lucide.dev/icons) icon name.
- `order` — position within the group, lowest first (default 100).

## Connection and connector topics

`connect-<section>.md` (llm, jira, confluence, github, jenkins, nexus, splunk,
pgsql, mobile, aws, proxy, git) and `<connector>-connector.md`
(local-bridge) hold the long-form
guide for a connector. Their title, summary, setup
steps, setup link and troubleshooting lines come from
`app/services/connection_guidance.py`, which the form next to the field also
renders, so the two cannot drift apart. The file adds only `icon` and the
body: what the connection is for, what goes wrong, and how to tell.

## Translations

A translated topic is a file with the same name under `app/help/<locale>/`
(`zh-Hans`, `zh-Hant-HK`). The page uses it for members who read the Portal
in that language and falls back to the English file, with a note saying so,
for a topic that has no translation yet.

- Translate `title`, `summary` and the body. `group`, `icon` and `order` are
  always taken from the English file, so keep them as they are.
- Keep code, commands, paths, URLs, `{mod}` and anything a program parses
  byte for byte, and name UI elements exactly as `app/locale/<locale>.json`
  translates them. `app/locale/GLOSSARY.md` fixes the product nouns.
- Connection and connector topics take their title, summary, steps and
  troubleshooting lines from the catalogues; the translated file adds only
  the body.
- Change the English file first; the translations follow in the same pull
  request (`tests/test_help_translations.py` checks that every translation
  still has its English topic, the same code blocks and inline code).
