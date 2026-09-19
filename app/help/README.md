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
- `group` — `Getting started`, `Connections`, `Connectors` or `Working`. A new
  name creates a new group, listed after those. Required.
- `summary` — one line shown under the label.
- `icon` — a [lucide](https://lucide.dev/icons) icon name.
- `order` — position within the group, lowest first (default 100).

## Connection and connector topics

`connect-<section>.md` (llm, jira, confluence, github, jenkins, nexus, splunk,
appd, pgsql, mobile, aws, proxy, git) and `<connector>-connector.md`
(local-browser) hold the long-form
guide for a Connections section or a Connector. Their title, summary, setup
steps, setup link and troubleshooting lines come from
`app/services/connection_guidance.py`, which the form next to the field also
renders, so the two cannot drift apart. The file adds only `icon` and the
body: what the connection is for, what goes wrong, and how to tell.
