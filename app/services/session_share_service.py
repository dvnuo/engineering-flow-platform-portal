"""Read-only session shares: who may open one, and what the page gets to see.

A share is live: ``/share/{token}`` reads the session from the runtime on
every load and shows it as it is now. What viewers receive is the projection
built here from the runtime's ``GET /api/sessions/{id}`` payload: the user and
assistant turns with their visible text, display blocks and attachment names,
and nothing else (no system prompt, metadata, runtime events or chat log).
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from app.config import get_settings

SCOPE_MEMBERS = "members"
SCOPE_ANYONE = "anyone"
SHARE_SCOPES = (SCOPE_MEMBERS, SCOPE_ANYONE)
DEFAULT_SCOPE = SCOPE_MEMBERS

# Assistant rows that are tool chatter rather than an answer; the page hides
# them unless the viewer asks for tool steps (same rule as the chat's
# decorateToolMessages).
_TOOL_ROW_PREFIXES = ("[Tool", "Tool ")
_RENDERABLE_BLOCK_TEXT_KEYS = ("content", "text", "message", "output", "result", "value", "code")


def normalize_scope(value: Any) -> str:
    scope = str(value or "").strip().lower() or DEFAULT_SCOPE
    if scope not in SHARE_SCOPES:
        raise ValueError(f"scope must be one of {', '.join(SHARE_SCOPES)}")
    return scope


def anyone_scope_enabled() -> bool:
    return bool(get_settings().session_share_anyone_enabled)


def effective_scope(share) -> str:
    """The scope the page enforces: an ``anyone`` share serves as ``members`` once the deployment turns that audience off."""

    scope = str(getattr(share, "scope", "") or DEFAULT_SCOPE)
    if scope == SCOPE_ANYONE and not anyone_scope_enabled():
        return SCOPE_MEMBERS
    return scope


def viewer_may_open(share, user) -> bool:
    """``user`` is the signed-in, allowlisted member (None for a visitor)."""

    if effective_scope(share) == SCOPE_ANYONE:
        return True
    return user is not None


def share_path(token: str) -> str:
    return f"/share/{token}"


def share_url(base_url: str, token: str) -> str:
    return f"{str(base_url or '').rstrip('/')}{share_path(token)}"


def is_task_session_id(session_id: Any, prefixes: Iterable[str]) -> bool:
    return isinstance(session_id, str) and session_id.strip().startswith(tuple(prefixes))


# --- the transcript the page gets ---------------------------------------------


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _is_meaningful(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _renderable_block(block: Any) -> Optional[dict[str, Any]]:
    if not isinstance(block, Mapping):
        return None
    block_type = str(block.get("type") or "").strip().lower()
    if not block_type:
        return None
    if block_type == "table":
        headers = block.get("headers") if isinstance(block.get("headers"), list) else block.get("columns")
        rows = block.get("rows") if isinstance(block.get("rows"), list) else []
        if not (isinstance(headers, list) and headers) and not rows:
            return None
    elif block_type == "file":
        if not any(_is_meaningful(block.get(key)) for key in ("path", "file_path", "workspace_path", "name", "filename")):
            return None
    elif not any(_is_meaningful(block.get(key)) for key in _RENDERABLE_BLOCK_TEXT_KEYS):
        return None
    projected = {key: value for key, value in block.items() if isinstance(key, str)}
    projected["type"] = block_type
    return projected


def _display_blocks(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = message.get("display_blocks")
    if not isinstance(raw, list):
        return []
    blocks = []
    for block in raw:
        projected = _renderable_block(block)
        if projected is not None:
            blocks.append(projected)
    return blocks


def _attachments(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = message.get("attachments")
    if not isinstance(raw, list):
        return []
    items = []
    for attachment in raw:
        if not isinstance(attachment, Mapping):
            continue
        file_id = str(attachment.get("file_id") or attachment.get("fileId") or attachment.get("id") or "").strip()
        name = str(attachment.get("name") or attachment.get("filename") or attachment.get("file_name") or file_id or "attachment")
        content_type = str(attachment.get("content_type") or attachment.get("mime") or attachment.get("contentType") or "")
        kind = str(attachment.get("type") or "").lower()
        is_image = kind == "image" or content_type.lower().startswith("image/")
        items.append(
            {
                "file_id": file_id,
                "name": name,
                "content_type": content_type,
                "type": "image" if is_image else "file",
            }
        )
    return items


def _user_visible_text(message: Mapping[str, Any]) -> str:
    metadata = message.get("metadata") if isinstance(message.get("metadata"), Mapping) else {}
    for value in (
        message.get("display_content"),
        message.get("displayContent"),
        metadata.get("original_user_message"),
        message.get("content"),
    ):
        if isinstance(value, str):
            return value
    return ""


def _timestamp(message: Mapping[str, Any]) -> str:
    for key in ("timestamp", "created_at", "completed_at"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _author(message: Mapping[str, Any], role: str, assistant_name: str) -> str:
    name = message.get("author_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return "User" if role == "user" else (assistant_name or "Assistant")


def _canonical_text(message: Mapping[str, Any]) -> str:
    parts = message.get("parts") if isinstance(message.get("parts"), list) else []
    chunks = []
    for part in parts:
        if not isinstance(part, Mapping):
            continue
        part_type = str(part.get("type") or "").lower()
        text = ""
        for key in ("text", "content", "markdown"):
            if isinstance(part.get(key), str):
                text = part[key]
                break
        if (part_type in ("text", "markdown") or (not part_type and text)) and text:
            chunks.append(text)
    return "".join(chunks)


def _legacy_messages_from_canonical(canonical: list[Any]) -> list[dict[str, Any]]:
    messages = []
    for index, item in enumerate(canonical):
        if not isinstance(item, Mapping):
            continue
        info = item.get("info") if isinstance(item.get("info"), Mapping) else {}
        role = str(item.get("role") or info.get("role") or "").lower() or "assistant"
        message_id = str(item.get("message_id") or info.get("id") or f"canonical-{index}")
        content = _canonical_text(item)
        messages.append(
            {
                "id": message_id,
                "role": role,
                "content": content,
                "display_content": content,
                "model": str(item.get("model") or info.get("modelID") or info.get("model_id") or ""),
                "timestamp": str(info.get("created") or info.get("time", {}).get("created") if isinstance(info.get("time"), Mapping) else info.get("created") or ""),
            }
        )
    return messages


def project_shared_transcript(payload: Mapping[str, Any], *, assistant_name: str = "") -> list[dict[str, Any]]:
    """The viewer-safe transcript from a runtime session payload (native or OpenCode)."""

    canonical = payload.get("canonical_messages")
    if not isinstance(canonical, list):
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
        canonical = metadata.get("canonical_messages") if isinstance(metadata.get("canonical_messages"), list) else []
    source = _legacy_messages_from_canonical(canonical) if canonical else payload.get("messages")
    if not isinstance(source, list):
        return []

    projected: list[dict[str, Any]] = []
    for index, message in enumerate(source):
        if not isinstance(message, Mapping):
            continue
        role = str(message.get("role") or "").lower()
        if role not in ("user", "assistant"):
            continue
        message_id = str(message.get("id") or message.get("message_id") or f"m-{index}")
        if role == "user":
            text = _user_visible_text(message)
            attachments = _attachments(message)
            if not text.strip() and not attachments:
                continue
            projected.append(
                {
                    "id": message_id,
                    "role": "user",
                    "kind": "message",
                    "author": _author(message, role, assistant_name),
                    "timestamp": _timestamp(message),
                    "markdown": text,
                    "display_blocks": [],
                    "attachments": attachments,
                    "model": "",
                }
            )
            continue
        text = _text(message.get("content"))
        blocks = _display_blocks(message)
        if not text.strip() and not blocks:
            continue
        kind = "tool" if text.lstrip().startswith(_TOOL_ROW_PREFIXES) else "message"
        projected.append(
            {
                "id": message_id,
                "role": "assistant",
                "kind": kind,
                "author": _author(message, role, assistant_name),
                "timestamp": _timestamp(message),
                "markdown": text,
                "display_blocks": blocks,
                "attachments": [],
                "model": _text(message.get("model")),
            }
        )
    return projected


def transcript_file_ids(messages: Iterable[Mapping[str, Any]]) -> set[str]:
    """The attachment ids the page may ask for through ``/share/{token}/files/{file_id}``."""

    ids: set[str] = set()
    for message in messages:
        for attachment in message.get("attachments") or []:
            if isinstance(attachment, Mapping) and attachment.get("type") == "image" and attachment.get("file_id"):
                ids.add(str(attachment["file_id"]))
    return ids


def transcript_as_markdown(title: str, messages: Iterable[Mapping[str, Any]], *, include_tools: bool = False) -> str:
    """The transcript as a Markdown document (the page's "Copy as Markdown")."""

    lines = [f"# {title}".rstrip(), ""]
    for message in messages:
        if message.get("kind") == "tool" and not include_tools:
            continue
        author = str(message.get("author") or ("User" if message.get("role") == "user" else "Assistant"))
        lines.append(f"**{author}**")
        lines.append("")
        text = str(message.get("markdown") or "").strip()
        if text:
            lines.append(text)
        for block in message.get("display_blocks") or []:
            if not isinstance(block, Mapping):
                continue
            block_type = str(block.get("type") or "")
            if block_type == "code":
                code = next((block[key] for key in ("code", "content", "text") if isinstance(block.get(key), str)), "")
                language = str(block.get("lang") or block.get("language") or "")
                lines.extend(["", f"```{language}", code, "```"])
            elif block_type == "table":
                headers = block.get("headers") or block.get("columns") or []
                rows = block.get("rows") or []
                if headers:
                    lines.extend(["", "| " + " | ".join(str(h) for h in headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"])
                for row in rows:
                    cells = row if isinstance(row, list) else []
                    lines.append("| " + " | ".join(str(cell) for cell in cells) + " |")
            else:
                block_text = next((block[key] for key in _RENDERABLE_BLOCK_TEXT_KEYS if isinstance(block.get(key), str)), "")
                if block_text.strip():
                    lines.extend(["", block_text.strip()])
        attachments = [str(item.get("name")) for item in message.get("attachments") or [] if isinstance(item, Mapping)]
        if attachments:
            lines.extend(["", "Attachments: " + ", ".join(attachments)])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
