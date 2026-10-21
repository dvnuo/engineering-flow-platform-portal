"""Read-only session shares: who may open one, and what the page gets to see.

A share is live: ``/share/{token}`` reads the session from the runtime on
every load and shows it as it is now. What viewers receive is the projection
built here from the runtime's ``GET /api/sessions/{id}`` payload: the user and
assistant turns with their visible text, display blocks and attachment names,
the answers a member gave to the assistant's questions, and nothing else (no
system prompt, metadata, runtime events or chat log).
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
import re
import threading
import time
from typing import Any, Iterable, Mapping, Optional

from app.config import get_settings

SCOPE_MEMBERS = "members"
SCOPE_ANYONE = "anyone"
SHARE_SCOPES = (SCOPE_MEMBERS, SCOPE_ANYONE)
DEFAULT_SCOPE = SCOPE_MEMBERS
SHARE_TITLE_MAX_CHARS = 255

# Assistant rows that are tool chatter rather than an answer; the page hides
# them unless the viewer asks for tool steps (same rule as the chat's
# decorateToolMessages).
_TOOL_ROW_PREFIXES = ("[Tool", "Tool ")
_RENDERABLE_BLOCK_TEXT_KEYS = ("content", "text", "message", "output", "result", "value", "code")
# Attachments the page may show inline. SVG is an image type too, but it is
# active markup; the chat's proxy forces it to download and the share page
# never inlines it.
RASTER_IMAGE_CONTENT_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/avif")
_AUTO_MODEL_ID = re.compile(r"^(?:[a-z0-9_.-]+/)?auto(?:-[a-z]+)?$")


def normalize_scope(value: Any) -> str:
    scope = str(value or "").strip().lower() or DEFAULT_SCOPE
    if scope not in SHARE_SCOPES:
        raise ValueError(f"scope must be one of {', '.join(SHARE_SCOPES)}")
    return scope


def normalize_share_title(value: Any) -> Optional[str]:
    """The name stored with the share: trimmed, bounded, None when blank."""

    title = " ".join(str(value or "").split())
    if not title:
        return None
    return title[:SHARE_TITLE_MAX_CHARS]


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


class ShareTranscriptCache:
    """One runtime read per share per window, however many viewers poll it.

    Entries expire after ``ttl_seconds``; expired entries are swept on every
    write and the ``max_entries`` most recently used are kept, so a worker
    that served many links over its lifetime does not keep every transcript
    in memory. ``invalidate`` drops a share's entry when it is revoked or its
    session deleted.
    """

    def __init__(self, ttl_seconds: float = 5.0, max_entries: int = 128, clock=time.monotonic) -> None:
        self.ttl_seconds = float(ttl_seconds)
        self.max_entries = int(max_entries)
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()

    def get(self, token: str) -> Optional[Any]:
        now = self._clock()
        with self._lock:
            entry = self._entries.get(token)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= now:
                self._entries.pop(token, None)
                return None
            self._entries.move_to_end(token)
            return value

    def put(self, token: str, value: Any) -> None:
        now = self._clock()
        with self._lock:
            for key in [key for key, (expires_at, _value) in self._entries.items() if expires_at <= now]:
                self._entries.pop(key, None)
            self._entries.pop(token, None)
            self._entries[token] = (now + self.ttl_seconds, value)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def invalidate(self, token: str) -> None:
        with self._lock:
            self._entries.pop(token, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


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
    projected = {key: value for key, value in block.items() if isinstance(key, str)}
    projected["type"] = block_type
    if block_type == "table":
        headers = block.get("headers") if isinstance(block.get("headers"), list) else block.get("columns")
        rows = block.get("rows") if isinstance(block.get("rows"), list) else []
        if (isinstance(headers, list) and headers) or rows:
            return projected
        # A table with only text renders as markdown, as in the chat.
        text = next((block[key] for key in _RENDERABLE_BLOCK_TEXT_KEYS if _is_meaningful(block.get(key))), None)
        return {"type": "markdown", "content": text} if text else None
    if block_type == "file":
        if not any(_is_meaningful(block.get(key)) for key in ("path", "file_path", "workspace_path", "name", "filename")):
            return None
        return projected
    if not any(_is_meaningful(block.get(key)) for key in _RENDERABLE_BLOCK_TEXT_KEYS):
        return None
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


def is_inline_image_type(content_type: Any) -> bool:
    base_type = str(content_type or "").split(";")[0].strip().lower()
    return base_type in RASTER_IMAGE_CONTENT_TYPES


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
        items.append(
            {
                "file_id": file_id,
                "name": name,
                "content_type": content_type,
                "type": "image" if is_inline_image_type(content_type) else "file",
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


def _iso_timestamp(value: Any) -> str:
    """ISO 8601 text for a timestamp the runtimes send as ISO text or as epoch seconds/milliseconds."""

    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1_000_000_000_000:
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return ""
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.isdigit():
            return _iso_timestamp(int(text))
        return text
    return ""


def _timestamp(message: Mapping[str, Any]) -> str:
    for key in ("timestamp", "created_at", "completed_at"):
        text = _iso_timestamp(message.get(key))
        if text:
            return text
    return ""


def _author(message: Mapping[str, Any], role: str, assistant_name: str) -> str:
    name = message.get("author_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return "User" if role == "user" else (assistant_name or "Assistant")


def _display_model(message: Mapping[str, Any]) -> str:
    """The model that answered, the way the chat's badge shows it: never an auto id, no provider prefix."""

    layers = [message, message.get("metadata") if isinstance(message.get("metadata"), Mapping) else {}]
    candidates = []
    for key in ("resolved_model", "model", "modelID", "model_id"):
        for layer in layers:
            value = layer.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())
    for candidate in candidates:
        if _AUTO_MODEL_ID.match(candidate.lower()):
            continue
        return candidate.split("/", 1)[1] if "/" in candidate else candidate
    return ""


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


def _canonical_timestamp(info: Mapping[str, Any]) -> str:
    time_info = info.get("time") if isinstance(info.get("time"), Mapping) else {}
    for value in (info.get("created"), time_info.get("created"), time_info.get("completed")):
        text = _iso_timestamp(value)
        if text:
            return text
    return ""


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
                "requested_model": str(item.get("requested_model") or ""),
                "timestamp": _canonical_timestamp(info),
            }
        )
    return messages


def _question_answer_pairs(message: Mapping[str, Any]) -> list[dict[str, str]]:
    """What a member answered when the assistant asked (the chat's question_answer rows)."""

    if str(message.get("role") or "").lower() != "tool":
        return []
    parts = message.get("parts") if isinstance(message.get("parts"), list) else []
    result = next((part.get("tool_result") for part in parts if isinstance(part, Mapping) and isinstance(part.get("tool_result"), Mapping)), {})
    tool_name = str(result.get("tool_name") or message.get("tool_name") or "")
    if tool_name != "question":
        return []
    source = result.get("metadata") if isinstance(result.get("metadata"), Mapping) else result.get("output")
    if not isinstance(source, Mapping):
        return []
    questions = source.get("questions") if isinstance(source.get("questions"), list) else []
    answers = source.get("answers") if isinstance(source.get("answers"), list) else []
    pairs = []
    for index, answer in enumerate(answers):
        chosen = [str(value).strip() for value in (answer if isinstance(answer, list) else [answer]) if value is not None and str(value).strip()]
        if not chosen:
            continue
        question = questions[index] if index < len(questions) and isinstance(questions[index], Mapping) else {}
        pairs.append({"label": str(question.get("question") or question.get("header") or "").strip(), "value": ", ".join(chosen)})
    return pairs


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
        message_id = str(message.get("id") or message.get("message_id") or f"m-{index}")
        if role == "tool":
            pairs = _question_answer_pairs(message)
            if pairs:
                projected.append(
                    {
                        "id": message_id,
                        "role": "user",
                        "kind": "answer",
                        "author": "User",
                        "timestamp": _timestamp(message),
                        "markdown": "",
                        "pairs": pairs,
                        "display_blocks": [],
                        "attachments": [],
                        "model": "",
                    }
                )
            continue
        if role not in ("user", "assistant"):
            continue
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
                "model": _display_model(message),
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


_RUNNING_STATES = {"running", "accepted", "queued", "in_progress"}


def session_run_in_progress(metadata: Any) -> bool:
    """Whether the runtime says a turn is still being answered (native and OpenCode spell it differently)."""

    if not isinstance(metadata, Mapping):
        return False
    for key in ("last_runtime_status", "latest_event_state", "completion_state", "chatlog_status"):
        if str(metadata.get(key) or "").strip().lower() in _RUNNING_STATES:
            return True
    return False
