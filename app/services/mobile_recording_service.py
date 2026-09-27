"""Recording mobile test segments with the hosted Appium Inspector.

The assistant starts a BrowserStack session, holds it for the member, and
publishes it in its workspace (mobile/recording/active.json, written by
``mobile-auto inspector attach --out``). When the member opens the Inspector
from the Recording panel, Portal binds that one session to a recording and
proxies the Inspector's WebDriver traffic for it to BrowserStack with the
member's own credentials. The commands that change the app (and the finds
that say which element they hit) are logged; at the end of a segment Portal
writes the log to mobile/recordings/<segment>.wdlog.json in the workspace,
where ``mobile-auto inspector import`` compiles it into a replayable segment.

Only the member who owns the assistant can record on it, only the published
session can be reached, the hub is always the member's configured BrowserStack
hub (never a URL from the workspace file), and the Inspector cannot end the
assistant's session. Typed values of password fields never reach the log.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import xml.etree.ElementTree as ET
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.mobile_recording import MobileRecording, MobileRecordingEvent

HANDSHAKE_PATH = "mobile/recording/active.json"
RECORDINGS_DIR = "mobile/recordings"
WEBDRIVER_LOG_FORMAT = "efp-webdriver-log/v1"
_SEGMENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SESSION_ID = re.compile(r"^[A-Za-z0-9._-]{6,128}$")
_SECRET_HINT = re.compile(
    r"(password|passwd|passcode|pincode|pin_code|secret|(^|[^a-z])(pwd|pin|otp|cvv|cvc)([^a-z]|$))", re.IGNORECASE
)
# Element attributes worth keeping for the segment compiler.
_KEPT_ATTRIBUTES = ("class", "type", "resource-id", "content-desc", "text", "name", "label", "value", "hint", "password")


class RecordingError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def appium_inspector_dir() -> Optional[Path]:
    """The hosted Inspector's web build, if this deployment has one."""
    raw = (get_settings().appium_inspector_dir or "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if (path / "index.html").is_file() else None


def valid_segment_name(value: Optional[str]) -> Optional[str]:
    name = str(value or "").strip()
    return name if _SEGMENT_NAME.match(name) else None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(recording: MobileRecording, token: str) -> bool:
    return hmac.compare_digest(recording.token_hash, hash_token(token or ""))


def parse_handshake(raw: bytes | str) -> dict[str, Any]:
    """The session fields Portal takes from the assistant's handshake file."""
    try:
        doc = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise RecordingError("The recording session file is not valid JSON.", 409) from exc
    if not isinstance(doc, dict) or doc.get("format") != "efp-mobile-recording/v1":
        raise RecordingError("The recording session file is not an efp-mobile-recording/v1 document.", 409)
    session_id = str(doc.get("session_id") or "").strip()
    if not _SESSION_ID.match(session_id):
        raise RecordingError("The recording session file has no usable session id.", 409)
    platform = str(doc.get("platform") or "").strip().lower()
    return {
        "session_id": session_id,
        "platform": platform if platform in {"android", "ios"} else "",
        "device": str(doc.get("device") or "").strip()[:128] or None,
        "run_id": str(doc.get("run_id") or "").strip()[:128] or None,
        "hold_deadline": str(doc.get("hold_deadline") or "").strip() or None,
        "control_owner": str(doc.get("control_owner") or "").strip(),
    }


def synthetic_capabilities(recording: MobileRecording) -> dict[str, Any]:
    """What the Inspector learns about the session it attaches to.

    Answered by Portal rather than fetched from the hub, so attaching does not
    depend on whether BrowserStack serves GET /session/{id}.
    """
    ios = recording.platform == "ios"
    caps: dict[str, Any] = {
        "platformName": "iOS" if ios else "Android",
        "appium:automationName": "XCUITest" if ios else "UiAutomator2",
    }
    if recording.device:
        caps["appium:deviceName"] = recording.device
    return caps


class MobileRecordingService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def active_for(self, user, agent_id: str) -> Optional[MobileRecording]:
        query = (
            select(MobileRecording)
            .where(
                MobileRecording.owner_user_id == user.id,
                MobileRecording.agent_id == agent_id,
                MobileRecording.status == "active",
            )
            .order_by(MobileRecording.created_at.desc())
        )
        return self.db.scalars(query).first()

    def get_for_user(self, user, recording_id: str) -> Optional[MobileRecording]:
        row = self.db.get(MobileRecording, recording_id)
        return row if row is not None and row.owner_user_id == user.id else None

    def open(self, user, agent, handshake: dict[str, Any], hub_url: str, segment: Optional[str]) -> tuple[MobileRecording, str]:
        """Bind the published session to a new recording; returns it and its token.

        An earlier active recording of the same assistant is closed, so only the
        newest Inspector tab can drive the device.
        """
        for row in self.db.scalars(
            select(MobileRecording).where(
                MobileRecording.owner_user_id == user.id,
                MobileRecording.agent_id == agent.id,
                MobileRecording.status == "active",
            )
        ):
            row.status = "closed"
            row.closed_at = datetime.utcnow()
        token = secrets.token_urlsafe(24)
        row = MobileRecording(
            owner_user_id=user.id,
            agent_id=agent.id,
            session_id=handshake["session_id"],
            hub_url=hub_url,
            platform=handshake.get("platform") or "",
            device=handshake.get("device"),
            run_id=handshake.get("run_id"),
            token_hash=hash_token(token),
            segment=valid_segment_name(segment) or "segment-1",
            status="active",
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row, token

    def close(self, recording: MobileRecording) -> None:
        recording.status = "closed"
        recording.closed_at = datetime.utcnow()
        self.db.commit()

    # ----- command log -----------------------------------------------------

    def _next_seq(self, recording: MobileRecording) -> int:
        current = self.db.scalar(
            select(func.max(MobileRecordingEvent.seq)).where(
                MobileRecordingEvent.recording_id == recording.id,
                MobileRecordingEvent.segment == recording.segment,
            )
        )
        return int(current or 0) + 1

    def record(
        self,
        recording: MobileRecording,
        *,
        method: str,
        path: str,
        request: Any,
        status_code: int,
        element_id: Optional[str] = None,
        element_ids: Optional[list[str]] = None,
        element: Optional[dict[str, str]] = None,
        elements: Optional[list[Optional[dict[str, str]]]] = None,
        secret: bool = False,
    ) -> MobileRecordingEvent:
        row = MobileRecordingEvent(
            recording_id=recording.id,
            segment=recording.segment,
            seq=self._next_seq(recording),
            method=method.upper()[:8],
            path=path[:512],
            request_json=json.dumps(request) if request is not None else None,
            status_code=status_code,
            element_id=(element_id or None) and element_id[:128],
            element_json=json.dumps(element) if element else None,
            element_ids_json=json.dumps({"ids": element_ids or [], "elements": elements or []}) if element_ids else None,
            secret=bool(secret),
        )
        recording.last_event_at = datetime.utcnow()
        self.db.add(row)
        self.db.commit()
        return row

    def element_info(self, recording: MobileRecording, element_id: str) -> tuple[Optional[dict[str, str]], str]:
        """The attributes and selector of the find that returned element_id."""
        events = self.db.scalars(
            select(MobileRecordingEvent)
            .where(MobileRecordingEvent.recording_id == recording.id, MobileRecordingEvent.method == "POST")
            .order_by(MobileRecordingEvent.id.desc())
            .limit(200)
        )
        for event in events:
            locator_value = ""
            if event.request_json:
                try:
                    locator_value = str(json.loads(event.request_json).get("value") or "")
                except (ValueError, AttributeError):
                    locator_value = ""
            if event.element_id == element_id:
                return (json.loads(event.element_json) if event.element_json else None), locator_value
            if event.element_ids_json:
                many = json.loads(event.element_ids_json)
                ids = many.get("ids") or []
                if element_id in ids:
                    elements = many.get("elements") or []
                    index = ids.index(element_id)
                    return (elements[index] if index < len(elements) else None), locator_value
        return None, ""

    def set_viewport(self, recording: MobileRecording, width: Any, height: Any) -> None:
        try:
            w, h = int(width), int(height)
        except (TypeError, ValueError):
            return
        if w > 0 and h > 0 and (recording.viewport_width, recording.viewport_height) != (w, h):
            recording.viewport_width, recording.viewport_height = w, h
            self.db.commit()

    def segment_log(self, recording: MobileRecording, segment_name: str) -> dict[str, Any]:
        """The current segment's commands as an efp-webdriver-log/v1 document."""
        events = list(
            self.db.scalars(
                select(MobileRecordingEvent)
                .where(MobileRecordingEvent.recording_id == recording.id, MobileRecordingEvent.segment == recording.segment)
                .order_by(MobileRecordingEvent.seq.asc())
            )
        )
        out_events = []
        for event in events:
            item: dict[str, Any] = {"seq": event.seq, "method": event.method, "path": event.path, "status": event.status_code}
            if event.request_json:
                item["request"] = json.loads(event.request_json)
            if event.element_id:
                item["element_id"] = event.element_id
            if event.element_json:
                item["element"] = json.loads(event.element_json)
            if event.element_ids_json:
                many = json.loads(event.element_ids_json)
                item["element_ids"] = many.get("ids") or []
                item["elements"] = many.get("elements") or []
            if event.secret:
                item["secret"] = True
            out_events.append(item)
        doc: dict[str, Any] = {
            "format": WEBDRIVER_LOG_FORMAT,
            "session_id": recording.session_id,
            "platform": recording.platform,
            "segment": segment_name,
            "recorded_at": datetime.utcnow().isoformat() + "Z",
            "events": out_events,
        }
        if recording.viewport_width and recording.viewport_height:
            doc["viewport"] = {"width": recording.viewport_width, "height": recording.viewport_height}
        return doc

    def next_segment(self, recording: MobileRecording, next_name: Optional[str]) -> str:
        name = valid_segment_name(next_name)
        if not name:
            match = re.match(r"^(.*?)(\d+)$", recording.segment or "")
            name = f"{match.group(1)}{int(match.group(2)) + 1}" if match else "segment-2"
        recording.segment = name
        self.db.commit()
        return name


def summarize_log(doc: dict[str, Any]) -> dict[str, int]:
    actions = finds = secrets_count = 0
    for event in doc.get("events") or []:
        path = str(event.get("path") or "")
        if path.endswith("/element") or path.endswith("/elements"):
            finds += 1
        else:
            actions += 1
        if event.get("secret"):
            secrets_count += 1
    return {"actions": actions, "finds": finds, "secrets": secrets_count}


# ----- page source cache and element resolution -------------------------------

class _SourceCache:
    """The last page source each recording's Inspector fetched.

    The Inspector refreshes the source after every command, so the latest one
    describes the screen its next find runs against. Kept per process; a find
    served by another replica just goes without attributes and the compiler
    falls back to the recorded selector.
    """

    def __init__(self, limit: int = 64) -> None:
        self._limit = limit
        self._items: "OrderedDict[str, str]" = OrderedDict()
        self._lock = threading.Lock()

    def put(self, recording_id: str, source: str) -> None:
        with self._lock:
            self._items[recording_id] = source
            self._items.move_to_end(recording_id)
            while len(self._items) > self._limit:
                self._items.popitem(last=False)

    def get(self, recording_id: str) -> Optional[str]:
        with self._lock:
            return self._items.get(recording_id)


source_cache = _SourceCache()

_XPATH_INDEXED = re.compile(r"^\((.*)\)\[(\d+)\]$")
_XPATH_STEP = re.compile(r"^//([\w.]+|\*)(?:\[(.*)\])?$")
_XPATH_ATTR = re.compile(r"""^@([\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')$""")
_UI_SELECTOR_CALL = re.compile(r'\.(\w+)\(\s*(?:"((?:[^"\\]|\\.)*)"|(\d+))\s*\)')
_PREDICATE_CLAUSE = re.compile(r"""^(name|label|value|type)\s*==\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')$""")


def _all_elements(root: ET.Element) -> list[ET.Element]:
    return [node for node in root.iter() if node is not root or node.tag not in {"hierarchy", "AppiumAUT"}]


def _class_of(node: ET.Element) -> str:
    return node.attrib.get("class") or node.attrib.get("type") or node.tag


def _match_attrs(node: ET.Element, wanted: dict[str, str], tag: Optional[str]) -> bool:
    if tag and tag != "*" and node.tag != tag and _class_of(node) != tag:
        return False
    return all(node.attrib.get(key) == value for key, value in wanted.items())


def _nodes_for_locator(root: ET.Element, using: str, value: str, platform: str) -> list[ET.Element]:
    nodes = _all_elements(root)
    ios = platform == "ios"
    if using == "accessibility id":
        key = "name" if ios else "content-desc"
        return [n for n in nodes if n.attrib.get(key) == value]
    if using == "id":
        if ios:
            return [n for n in nodes if n.attrib.get("name") == value]
        return [
            n for n in nodes
            if n.attrib.get("resource-id") == value or (":" not in value and n.attrib.get("resource-id", "").endswith(":id/" + value))
        ]
    if using == "name":
        if ios:
            return [n for n in nodes if n.attrib.get("name") == value]
        return [n for n in nodes if n.attrib.get("content-desc") == value or n.attrib.get("text") == value]
    if using == "class name":
        return [n for n in nodes if _class_of(n) == value]
    if using == "xpath":
        index = 0
        expr = value.strip()
        indexed = _XPATH_INDEXED.match(expr)
        if indexed:
            expr, index = indexed.group(1).strip(), int(indexed.group(2))
        step = _XPATH_STEP.match(expr)
        if not step:
            return []
        wanted: dict[str, str] = {}
        if step.group(2):
            for part in step.group(2).split(" and "):
                attr = _XPATH_ATTR.match(part.strip())
                if not attr:
                    return []
                wanted[attr.group(1)] = attr.group(2) if attr.group(2) is not None else attr.group(3)
        matched = [n for n in nodes if _match_attrs(n, wanted, step.group(1))]
        if index:
            return matched[index - 1: index] if len(matched) >= index else []
        return matched
    if using == "-android uiautomator":
        wanted = {}
        instance = None
        for call, text, number in _UI_SELECTOR_CALL.findall(value):
            if call == "resourceId":
                wanted["resource-id"] = text
            elif call == "description":
                wanted["content-desc"] = text
            elif call == "text":
                wanted["text"] = text
            elif call == "className":
                wanted["class"] = text
            elif call == "instance":
                instance = int(number)
            else:
                return []
        if not wanted:
            return []
        matched = [n for n in nodes if all(n.attrib.get(k) == v for k, v in wanted.items())]
        return matched[instance: instance + 1] if instance is not None else matched
    if using == "-ios predicate string":
        wanted = {}
        for part in value.split(" AND "):
            clause = _PREDICATE_CLAUSE.match(part.strip())
            if not clause:
                return []
            wanted[clause.group(1)] = clause.group(2) if clause.group(2) is not None else clause.group(3)
        return [n for n in nodes if all(n.attrib.get(k) == v for k, v in wanted.items())]
    return []


def element_attributes(source: Optional[str], using: str, value: str, platform: str, count: int = 1) -> list[Optional[dict[str, str]]]:
    """Attributes of the elements a find returned, looked up in the page source.

    Returns one entry per returned element (None where unknown). Values of
    password fields are dropped.
    """
    if not source or count <= 0:
        return [None] * max(count, 0)
    try:
        root = ET.fromstring(source.encode("utf-8") if isinstance(source, str) else source)
    except ET.ParseError:
        return [None] * count
    matched = _nodes_for_locator(root, using, value, platform)
    out: list[Optional[dict[str, str]]] = []
    for index in range(count):
        if index >= len(matched):
            out.append(None)
            continue
        node = matched[index]
        attrs = {key: node.attrib[key] for key in _KEPT_ATTRIBUTES if node.attrib.get(key)}
        attrs.setdefault("class", node.tag)
        if is_secret_element(attrs):
            for key in ("text", "value"):
                attrs.pop(key, None)
        out.append(attrs)
    return out


def is_secret_element(attrs: Optional[dict[str, str]], locator_value: str = "") -> bool:
    attrs = attrs or {}
    if str(attrs.get("password") or "").lower() == "true":
        return True
    if "secure" in (str(attrs.get("type") or "") + str(attrs.get("class") or "")).lower():
        return True
    hints = [attrs.get(key, "") for key in ("resource-id", "content-desc", "name", "label", "hint")] + [locator_value]
    return any(hint and _SECRET_HINT.search(hint) for hint in hints)
