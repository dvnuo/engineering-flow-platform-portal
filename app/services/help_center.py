"""Topics for the in-app help centre.

The prose lives in app/help/*.md, one file per topic, so editing a guide is
editing a markdown file; app/help/README.md documents the front matter. This
module only lists the files and merges in what a topic shares with a form.

Connection and connector topics are derived from CONNECTION_GUIDANCE and
CONNECTOR_GUIDANCE rather than restated, so the short steps shown beside a
field in Connections (and the troubleshooting lines under a Connector) and the
full guide here cannot drift apart. The markdown file adds what does not fit
next to a form field: what the connection is for, what goes wrong, and how to
tell.

The markdown is rendered in the browser (renderHelpMarkdown in chat_ui.js)
with the markdown-it build chat already loads, so a guide can hold tables,
code fences and mermaid diagrams. The files are read on every request: there
is nothing to restart after an edit.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.services.connection_guidance import CONNECTION_GUIDANCE, CONNECTOR_GUIDANCE

logger = logging.getLogger(__name__)

HELP_DIR = Path(__file__).resolve().parent.parent / "help"

# The authoring notes for the directory, not a topic.
NON_TOPIC_FILES = frozenset({"README.md"})

GROUP_ORDER = ("Getting started", "Connections", "Connectors", "Working")
DEFAULT_ICON = "circle-help"
DEFAULT_ORDER = 100


@dataclass(frozen=True)
class HelpTopic:
    id: str
    title: str
    summary: str
    group: str
    icon: str = DEFAULT_ICON
    # Markdown, rendered client-side.
    body: str = ""
    # Shared with the form the topic describes; only derived topics have these.
    steps: tuple[str, ...] = ()
    troubleshooting: tuple[str, ...] = ()
    help_url: str | None = None
    help_label: str | None = None
    connection_section: str | None = None
    order: int = DEFAULT_ORDER


@dataclass(frozen=True)
class HelpDocument:
    """One markdown file: its front matter and the body after it."""

    id: str
    meta: dict[str, str]
    body: str


def split_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Split a leading ``---`` block of ``key: value`` lines from the body.

    Deliberately not YAML: a topic needs five scalar keys, and a parser for
    that would be the only YAML dependency in the portal. Unknown keys are kept
    so a file can carry notes; a file without the block is all body.
    """

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text.strip("\n")
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            break
    else:
        return {}, text.strip("\n")
    meta: dict[str, str] = {}
    for line in lines[1:index]:
        key, separator, value = line.partition(":")
        if not separator or not key.strip():
            continue
        meta[key.strip().lower()] = value.strip().strip("'\"")
    return meta, "\n".join(lines[index + 1 :]).strip("\n")


def load_documents(directory: Path | None = None) -> dict[str, HelpDocument]:
    """Every topic file, keyed by id (the file name without .md)."""

    directory = HELP_DIR if directory is None else directory
    documents: dict[str, HelpDocument] = {}
    if not directory.is_dir():
        logger.warning("help directory %s is missing; the help centre is empty", directory)
        return documents
    for path in sorted(directory.glob("*.md")):
        if path.name in NON_TOPIC_FILES:
            continue
        try:
            # utf-8-sig: a BOM from a Windows editor must not end up in the front matter.
            text = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            logger.warning("help topic %s could not be read: %s", path.name, exc)
            continue
        meta, body = split_front_matter(text)
        documents[path.stem] = HelpDocument(id=path.stem, meta=meta, body=body)
    return documents


def _order_of(meta: dict[str, str]) -> int:
    try:
        return int(meta.get("order", DEFAULT_ORDER))
    except ValueError:
        return DEFAULT_ORDER


def topic_id_for_connection(section: str) -> str:
    """The help topic a Connections section links out to."""

    return f"connect-{section}"


def topic_id_for_connector(key: str) -> str:
    """The help topic a Connector panel links out to."""

    return f"{key.replace('_', '-')}-connector"


def _connection_topics(documents: dict[str, HelpDocument]) -> list[HelpTopic]:
    topics = []
    for section, guidance in CONNECTION_GUIDANCE.items():
        document = documents.pop(topic_id_for_connection(section), None)
        meta = document.meta if document else {}
        topics.append(
            HelpTopic(
                id=topic_id_for_connection(section),
                title=guidance["title"],
                summary=guidance["summary"],
                group="Connections",
                icon=meta.get("icon") or "plug",
                body=document.body if document else "",
                steps=tuple(guidance.get("steps") or ()),
                help_url=guidance.get("help_url"),
                help_label=guidance.get("help_label"),
                connection_section=section,
                order=_order_of(meta),
            )
        )
    return topics


def _connector_topics(documents: dict[str, HelpDocument]) -> list[HelpTopic]:
    """Connectors (Local browser, ...) get one guide each, derived like connections."""

    topics = []
    for key, guidance in CONNECTOR_GUIDANCE.items():
        document = documents.pop(topic_id_for_connector(key), None)
        meta = document.meta if document else {}
        topics.append(
            HelpTopic(
                id=topic_id_for_connector(key),
                title=guidance["title"],
                summary=guidance["summary"],
                group="Connectors",
                icon=meta.get("icon") or "globe",
                body=document.body if document else "",
                steps=tuple(guidance.get("steps") or ()),
                troubleshooting=tuple(guidance.get("troubleshooting") or ()),
                help_url=guidance.get("help_url"),
                help_label=guidance.get("help_label"),
                order=_order_of(meta),
            )
        )
    return topics


def _concept_topics(documents: dict[str, HelpDocument]) -> list[HelpTopic]:
    """Every file that is not a derived topic; its front matter is the listing."""

    topics = []
    for document in documents.values():
        title = document.meta.get("title", "").strip()
        group = document.meta.get("group", "").strip()
        if not title or not group:
            logger.warning(
                "help topic %s.md needs title and group in its front matter; skipped", document.id
            )
            continue
        topics.append(
            HelpTopic(
                id=document.id,
                title=title,
                summary=document.meta.get("summary", "").strip(),
                group=group,
                icon=document.meta.get("icon") or DEFAULT_ICON,
                body=document.body,
                order=_order_of(document.meta),
            )
        )
    topics.sort(key=lambda topic: (topic.order, topic.title.lower()))
    return topics


def all_topics() -> tuple[HelpTopic, ...]:
    documents = load_documents()
    connections = _connection_topics(documents)
    connectors = _connector_topics(documents)
    concepts = _concept_topics(documents)
    return tuple(concepts + connections + connectors)


def topics_by_group() -> list[tuple[str, list[HelpTopic]]]:
    """Topics grouped for the sub-menu.

    The known groups come in a deliberate order, then any group a markdown
    file introduced; within a group, by ``order`` (stable, so derived topics
    keep the guidance order).
    """

    grouped: dict[str, list[HelpTopic]] = {}
    for topic in all_topics():
        grouped.setdefault(topic.group, []).append(topic)
    names = [name for name in GROUP_ORDER if grouped.get(name)]
    names += [name for name in grouped if name not in GROUP_ORDER]
    return [(name, sorted(grouped[name], key=lambda topic: topic.order)) for name in names]


def get_topic(topic_id: str | None) -> HelpTopic | None:
    if not topic_id:
        return None
    for topic in all_topics():
        if topic.id == topic_id:
            return topic
    return None


def default_topic() -> HelpTopic:
    """What opens when Help is opened without a topic: the first of the first group."""

    for _name, topics in topics_by_group():
        if topics:
            return topics[0]
    return HelpTopic(
        id="help",
        title="Help",
        summary="No help topics are installed.",
        group=GROUP_ORDER[0],
        body="Add a markdown file to app/help to publish a topic here.",
    )
