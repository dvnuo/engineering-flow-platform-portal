"""Chat composer attachment policy: which files the chatbox may upload.

The single source of truth is ``Settings.chat_upload_extensions``
(``EFP_CHAT_UPLOAD_EXTENSIONS``) plus ``Settings.max_upload_mb``
(``EFP_MAX_UPLOAD_MB``). This module turns that pair into

- the ``accept`` attribute of the composer's file picker,
- the JSON the browser reads for its client-side check,
- the server-side check the upload proxy applies before forwarding, and
- the env values handed to every agent pod so the runtime agrees.

The runtime applies the same normalization (see
``src/utils/file_parser/validators.py`` in the runtime repository).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

# Non-visual by design: the default model has no vision, so images are not
# offered unless a deployment adds them (jpg, jpeg, png, webp, gif) to
# EFP_CHAT_UPLOAD_EXTENSIONS for a model that can see.
DEFAULT_CHAT_UPLOAD_EXTENSIONS: Tuple[str, ...] = (
    "pdf", "docx", "xlsx", "csv", "txt", "log", "pptx", "zip", "md", "yaml", "yml", "json", "xml",
)
DEFAULT_MAX_UPLOAD_MB = 25

# Extensions the runtime hands to the model as images (everything else on the
# allowlist is projected to text).
IMAGE_EXTENSION_MIME_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
}

# MIME types for the document formats the runtime parses; listed so the file
# picker can offer them by type as well as by extension. Any other allowed
# extension is UTF-8 text as far as the runtime is concerned.
DOCUMENT_EXTENSION_MIME_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "zip": "application/zip",
    "csv": "text/csv",
    "txt": "text/plain",
    "log": "text/plain",
    "md": "text/markdown",
    "markdown": "text/markdown",
    "rst": "text/x-rst",
    "tsv": "text/tab-separated-values",
    "json": "application/json",
    "jsonl": "application/x-ndjson",
    "ipynb": "application/json",
    "yaml": "application/yaml",
    "yml": "application/yaml",
    "toml": "application/toml",
    "xml": "application/xml",
    "html": "text/html",
    "htm": "text/html",
}

_EXTENSION_TOKEN = re.compile(r"^[a-z0-9]+$")


def parse_chat_upload_extensions(raw: Optional[str]) -> List[str]:
    """Normalize a comma/semicolon/whitespace separated extension list.

    Lowercases, strips leading dots and whitespace, drops tokens that are not
    plain alphanumerics and de-duplicates while keeping the configured order.
    An empty or all-invalid value yields the defaults so a typo in the env var
    never locks the chatbox.
    """
    seen = set()
    result: List[str] = []
    for token in re.split(r"[,;\s]+", str(raw or "")):
        ext = token.strip().lower().lstrip(".")
        if not ext or not _EXTENSION_TOKEN.match(ext) or ext in seen:
            continue
        seen.add(ext)
        result.append(ext)
    return result or list(DEFAULT_CHAT_UPLOAD_EXTENSIONS)


def file_extension(filename: Optional[str]) -> str:
    """Lowercase extension of ``filename`` without the dot ('' when absent)."""
    name = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[1].strip().lower()


@dataclass(frozen=True)
class ChatUploadPolicy:
    extensions: Tuple[str, ...]
    max_upload_mb: int

    @property
    def image_extensions(self) -> Tuple[str, ...]:
        return tuple(ext for ext in self.extensions if ext in IMAGE_EXTENSION_MIME_TYPES)

    @property
    def document_extensions(self) -> Tuple[str, ...]:
        return tuple(ext for ext in self.extensions if ext not in IMAGE_EXTENSION_MIME_TYPES)

    @property
    def mime_types(self) -> Tuple[str, ...]:
        """Known MIME types for the configured extensions (order preserved)."""
        seen = set()
        result: List[str] = []
        for ext in self.extensions:
            mime = IMAGE_EXTENSION_MIME_TYPES.get(ext) or DOCUMENT_EXTENSION_MIME_TYPES.get(ext)
            if mime and mime not in seen:
                seen.add(mime)
                result.append(mime)
        return tuple(result)

    @property
    def accept(self) -> str:
        """Value for the file input's ``accept`` attribute.

        Every extension as ``.ext`` plus the MIME types of the configured
        images, which lets mobile pickers offer the camera roll.
        """
        tokens = [f".{ext}" for ext in self.extensions]
        for ext in self.extensions:
            mime = IMAGE_EXTENSION_MIME_TYPES.get(ext)
            if mime and mime not in tokens:
                tokens.append(mime)
        return ",".join(tokens)

    @property
    def summary(self) -> str:
        """Human-readable list for toasts and error messages."""
        return ", ".join(self.extensions)

    @property
    def env_value(self) -> str:
        """Value of EFP_CHAT_UPLOAD_EXTENSIONS for agent pods."""
        return ",".join(self.extensions)

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def is_allowed(self, filename: Optional[str]) -> bool:
        """The extension decides, exactly as the runtime judges it."""
        ext = file_extension(filename)
        return bool(ext) and ext in self.extensions

    def rejection_detail(self, filename: Optional[str]) -> str:
        ext = file_extension(filename)
        if not ext:
            return f"Files without an extension are not allowed. Allowed: {self.summary}"
        return f"File type .{ext} is not allowed. Allowed: {self.summary}"

    def to_client_dict(self) -> dict:
        return {
            "extensions": list(self.extensions),
            "accept": self.accept,
            "max_upload_mb": self.max_upload_mb,
        }

    def to_client_json(self) -> str:
        # The value lands in an HTML attribute; the template autoescapes the
        # quotes and the browser hands the original JSON back to JSON.parse.
        return json.dumps(self.to_client_dict(), separators=(",", ":"))


def build_chat_upload_policy(raw_extensions: Optional[str], max_upload_mb: object) -> ChatUploadPolicy:
    try:
        mb = int(max_upload_mb)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        mb = DEFAULT_MAX_UPLOAD_MB
    if mb <= 0:
        mb = DEFAULT_MAX_UPLOAD_MB
    return ChatUploadPolicy(extensions=tuple(parse_chat_upload_extensions(raw_extensions)), max_upload_mb=mb)


def get_chat_upload_policy() -> ChatUploadPolicy:
    """The policy for the running Portal (from Settings)."""
    from app.config import get_settings

    settings = get_settings()
    return build_chat_upload_policy(
        getattr(settings, "chat_upload_extensions", ""),
        getattr(settings, "max_upload_mb", DEFAULT_MAX_UPLOAD_MB),
    )


__all__ = [
    "DEFAULT_CHAT_UPLOAD_EXTENSIONS",
    "DEFAULT_MAX_UPLOAD_MB",
    "DOCUMENT_EXTENSION_MIME_TYPES",
    "IMAGE_EXTENSION_MIME_TYPES",
    "ChatUploadPolicy",
    "build_chat_upload_policy",
    "file_extension",
    "get_chat_upload_policy",
    "parse_chat_upload_extensions",
]
