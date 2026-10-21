"""What a shared session's viewer gets, who may open a share, and how the live transcript is cached."""

import asyncio
from types import SimpleNamespace

import pytest

from app.services import session_share_service as service
from app.services import share_transcripts
from app.services.session_share_service import (
    SCOPE_ANYONE,
    SCOPE_MEMBERS,
    SHARE_TITLE_MAX_CHARS,
    ShareTranscriptCache,
    effective_scope,
    is_inline_image_type,
    normalize_scope,
    normalize_share_title,
    project_shared_transcript,
    session_run_in_progress,
    share_url,
    transcript_file_ids,
    viewer_may_open,
)
from app.utils.task_sessions import is_task_session_id


NATIVE_PAYLOAD = {
    "session_id": "s-1",
    "name": "Release pipeline permissions",
    "messages": [
        {"role": "system", "content": "You are a helpful assistant."},
        {
            "id": "u-1",
            "role": "user",
            "content": "[attachment context]\n\nWhy does the deploy stage fail?",
            "display_content": "Why does the deploy stage fail?",
            "timestamp": "2026-10-20T08:00:00Z",
            "author_name": "Jin",
            "attachments": [
                {"file_id": "f-img", "name": "error.png", "content_type": "image/png", "type": "image", "size": 10},
                {"file_id": "f-svg", "name": "diagram.svg", "content_type": "image/svg+xml", "type": "image"},
                {"file_id": "f-log", "name": "build.log", "content_type": "text/plain", "type": "file"},
            ],
            "metadata": {"original_user_message": "ignored because display_content wins", "secret": "x"},
        },
        {"id": "t-1", "role": "assistant", "content": "[Tool pgsql] select 1", "timestamp": "2026-10-20T08:00:05Z"},
        {"role": "tool", "content": "raw tool output"},
        {
            "id": "a-1",
            "role": "assistant",
            "content": "The jenkins user lacks the deploy role.",
            "model": "gpt-6-sol",
            "timestamp": "2026-10-20T08:00:10Z",
            "display_blocks": [
                {"type": "markdown", "content": "Grant it with `grant deploy`."},
                {"type": "table", "headers": [], "rows": []},
                {"type": "file", "path": "reports/fix.md", "name": "fix.md"},
                {"type": "callout", "content": ""},
                {"type": "custom", "text": "kept because it has text"},
                "not a block",
            ],
        },
        {"id": "a-2", "role": "assistant", "content": "   ", "display_blocks": []},
        {"id": "u-2", "role": "user", "content": "", "attachments": []},
    ],
    "metadata": {"system_prompt": "never shown"},
}


def test_project_native_payload_keeps_only_what_a_viewer_may_see():
    messages = project_shared_transcript(NATIVE_PAYLOAD, assistant_name="Deploy helper")

    assert [m["id"] for m in messages] == ["u-1", "t-1", "a-1"]
    user = messages[0]
    assert user["role"] == "user"
    assert user["kind"] == "message"
    assert user["author"] == "Jin"
    assert user["markdown"] == "Why does the deploy stage fail?"
    assert user["timestamp"] == "2026-10-20T08:00:00Z"
    assert user["attachments"] == [
        {"file_id": "f-img", "name": "error.png", "content_type": "image/png", "type": "image"},
        # SVG is active markup: listed by name, never shown inline.
        {"file_id": "f-svg", "name": "diagram.svg", "content_type": "image/svg+xml", "type": "file"},
        {"file_id": "f-log", "name": "build.log", "content_type": "text/plain", "type": "file"},
    ]
    assert "metadata" not in user

    tool = messages[1]
    assert tool["kind"] == "tool"
    assert tool["author"] == "Deploy helper"

    answer = messages[2]
    assert answer["kind"] == "message"
    assert answer["model"] == "gpt-6-sol"
    assert [block["type"] for block in answer["display_blocks"]] == ["markdown", "file", "custom"]


def test_project_user_text_falls_back_to_the_original_message_then_content():
    payload = {
        "messages": [
            {"role": "user", "content": "composed prompt", "metadata": {"original_user_message": "what I typed"}},
            {"role": "user", "content": "plain"},
        ]
    }
    messages = project_shared_transcript(payload)
    assert [m["markdown"] for m in messages] == ["what I typed", "plain"]
    assert messages[0]["author"] == "User"


def test_project_turns_question_answers_into_their_own_rows():
    payload = {
        "messages": [
            {"id": "a-1", "role": "assistant", "content": "Which environment?"},
            {
                "id": "t-1",
                "role": "tool",
                "timestamp": "2026-10-20T08:01:00Z",
                "parts": [{"type": "tool_result", "tool_result": {
                    "tool_name": "question",
                    "metadata": {
                        "questions": [{"question": "Which environment?"}, {"header": "Confirm"}],
                        "answers": [["staging", "prod"], "yes", []],
                    },
                }}],
            },
            {"id": "t-2", "role": "tool", "parts": [{"type": "tool_result", "tool_result": {"tool_name": "bash", "output": "secret output"}}]},
            {"id": "a-2", "role": "assistant", "content": "Deploying to staging and prod."},
        ]
    }
    messages = project_shared_transcript(payload)
    assert [(m["id"], m["kind"]) for m in messages] == [("a-1", "message"), ("t-1", "answer"), ("a-2", "message")]
    answer = messages[1]
    assert answer["role"] == "user"
    assert answer["pairs"] == [{"label": "Which environment?", "value": "staging, prod"}, {"label": "Confirm", "value": "yes"}]
    assert answer["timestamp"] == "2026-10-20T08:01:00Z"
    assert "secret output" not in str(messages)


@pytest.mark.parametrize(
    "message, expected",
    [
        ({"model": "gpt-6-sol"}, "gpt-6-sol"),
        ({"model": "github-copilot/gpt-6-luna"}, "gpt-6-luna"),
        ({"model": "auto"}, ""),
        ({"model": "github-copilot/auto-intelligence", "metadata": {"resolved_model": "gpt-6.1-sol"}}, "gpt-6.1-sol"),
        ({}, ""),
    ],
)
def test_project_shows_the_model_the_way_the_chat_badge_does(message, expected):
    payload = {"messages": [{"id": "a", "role": "assistant", "content": "hi", **message}]}
    assert project_shared_transcript(payload)[0]["model"] == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-10-20T08:00:00Z", "2026-10-20T08:00:00Z"),
        (1760947200, "2025-10-20T08:00:00+00:00"),
        (1760947200000, "2025-10-20T08:00:00+00:00"),
        ("1760947200", "2025-10-20T08:00:00+00:00"),
        (True, ""),
        (None, ""),
    ],
)
def test_project_normalizes_iso_and_epoch_timestamps(raw, expected):
    payload = {"messages": [{"id": "a", "role": "assistant", "content": "hi", "timestamp": raw}]}
    assert project_shared_transcript(payload)[0]["timestamp"] == expected


def test_project_renders_a_text_only_table_block_as_markdown():
    payload = {"messages": [{"id": "a", "role": "assistant", "content": "", "display_blocks": [
        {"type": "table", "headers": [], "rows": [], "content": "| a |\n| - |\n| 1 |"},
        {"type": "table", "columns": ["x"], "rows": [[1]]},
    ]}]}
    blocks = project_shared_transcript(payload)[0]["display_blocks"]
    assert blocks[0] == {"type": "markdown", "content": "| a |\n| - |\n| 1 |"}
    assert blocks[1]["type"] == "table"


def test_project_opencode_canonical_messages_text_parts_only():
    payload = {
        "messages": [{"role": "user", "content": "legacy copy, ignored when canonical exists"}],
        "canonical_messages": [
            {"info": {"id": "m1", "role": "user", "time": {"created": 1760947200000}}, "parts": [{"type": "text", "text": "Hello"}]},
            {
                "info": {"id": "m2", "role": "assistant", "modelID": "gpt-6-luna"},
                "parts": [{"type": "reasoning", "text": "thinking"}, {"type": "text", "text": "Hi "}, {"type": "text", "text": "there"}],
            },
            {"info": {"id": "m3", "role": "assistant"}, "parts": [{"type": "tool", "tool": "bash"}]},
        ],
    }
    messages = project_shared_transcript(payload, assistant_name="Bot")
    assert [(m["id"], m["role"], m["markdown"]) for m in messages] == [("m1", "user", "Hello"), ("m2", "assistant", "Hi there")]
    assert messages[0]["timestamp"] == "2025-10-20T08:00:00+00:00"
    assert messages[1]["model"] == "gpt-6-luna"
    assert messages[1]["author"] == "Bot"


def test_project_tolerates_garbage():
    assert project_shared_transcript({}) == []
    assert project_shared_transcript({"messages": "nope"}) == []
    assert project_shared_transcript({"messages": [None, 1, {"role": "assistant"}, {"role": "tool"}]}) == []


def test_transcript_file_ids_lists_only_inline_images():
    messages = project_shared_transcript(NATIVE_PAYLOAD)
    assert transcript_file_ids(messages) == {"f-img"}


@pytest.mark.parametrize("content_type, inline", [
    ("image/png", True), ("image/jpeg; charset=binary", True), ("IMAGE/WEBP", True),
    ("image/svg+xml", False), ("text/html", False), ("", False), (None, False),
])
def test_is_inline_image_type(content_type, inline):
    assert is_inline_image_type(content_type) is inline


@pytest.mark.parametrize("raw, expected", [("members", SCOPE_MEMBERS), (" Anyone ", SCOPE_ANYONE), ("", SCOPE_MEMBERS), (None, SCOPE_MEMBERS)])
def test_normalize_scope(raw, expected):
    assert normalize_scope(raw) == expected


def test_normalize_scope_rejects_unknown_audiences():
    with pytest.raises(ValueError):
        normalize_scope("public")


def test_normalize_share_title_trims_collapses_and_cuts():
    assert normalize_share_title("  Release   pipeline\n permissions ") == "Release pipeline permissions"
    assert normalize_share_title("") is None
    assert normalize_share_title(None) is None
    assert normalize_share_title("   ") is None
    assert len(normalize_share_title("x" * 400)) == SHARE_TITLE_MAX_CHARS


def test_viewer_may_open(monkeypatch):
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(session_share_anyone_enabled=True))
    anyone = SimpleNamespace(scope="anyone")
    members = SimpleNamespace(scope="members")
    member = SimpleNamespace(id=1)

    assert viewer_may_open(anyone, None) is True
    assert viewer_may_open(members, None) is False
    assert viewer_may_open(members, member) is True
    assert effective_scope(anyone) == SCOPE_ANYONE

    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(session_share_anyone_enabled=False))
    assert effective_scope(anyone) == SCOPE_MEMBERS
    assert viewer_may_open(anyone, None) is False
    assert viewer_may_open(anyone, member) is True


def test_share_url_and_task_sessions():
    assert share_url("https://portal.example.com/", "tok") == "https://portal.example.com/share/tok"
    assert share_url("", "tok") == "/share/tok"
    assert is_task_session_id("task-1") is True
    assert is_task_session_id("agent-task:abc") is True
    assert is_task_session_id("delegation-7") is True
    assert is_task_session_id("chat-1") is False
    assert is_task_session_id(None) is False


@pytest.mark.parametrize("metadata, running", [
    ({"last_runtime_status": "running"}, True),
    ({"latest_event_state": "queued"}, True),
    ({"completion_state": "in_progress"}, True),
    ({"chatlog_status": "accepted"}, True),
    ({"last_runtime_status": "completed", "latest_event_state": "success"}, False),
    ({}, False),
    ("not a mapping", False),
])
def test_session_run_in_progress(metadata, running):
    assert session_run_in_progress(metadata) is running


# --- the transcript cache ---------------------------------------------------------


def test_cache_expires_entries_and_keeps_the_most_recently_used():
    clock = {"now": 100.0}
    cache = ShareTranscriptCache(ttl_seconds=5.0, max_entries=2, clock=lambda: clock["now"])
    cache.put("a", {"n": 1})
    cache.put("b", {"n": 2})
    assert cache.get("a") == {"n": 1}
    cache.put("c", {"n": 3})  # evicts b, the least recently used
    assert cache.get("b") is None
    assert cache.get("a") == {"n": 1}
    assert len(cache) == 2

    clock["now"] = 106.0
    assert cache.get("a") is None  # expired
    cache.put("d", {"n": 4})
    cache.invalidate("d")
    assert cache.get("d") is None
    cache.put("e", {"n": 5})
    cache.clear()
    assert len(cache) == 0


def _share(token="tok", session_id="chat-1"):
    return SimpleNamespace(token=token, session_id=session_id)


def _agent(status="running"):
    return SimpleNamespace(id="agent-1", name="Deploy helper", status=status)


def test_load_share_transcript_serves_concurrent_viewers_from_one_runtime_read():
    share_transcripts.clear_share_transcripts()
    calls = []

    async def forward(**kwargs):
        calls.append(kwargs["subpath"])
        await asyncio.sleep(0.01)
        return 200, b'{"name": "Live name", "messages": [{"id": "u", "role": "user", "content": "hi"}], "metadata": {"last_runtime_status": "running"}}', "application/json"

    async def scenario():
        results = await asyncio.gather(*(share_transcripts.load_share_transcript(_share(), _agent(), forward) for _ in range(3)))
        cached = await share_transcripts.load_share_transcript(_share(), _agent(), forward)
        fresh = await share_transcripts.load_share_transcript(_share(), _agent(), forward, fresh=True)
        return results, cached, fresh

    results, cached, fresh = asyncio.run(scenario())
    assert calls == ["api/sessions/chat-1", "api/sessions/chat-1"]  # one for the three viewers, one for fresh
    assert all(result["status"] == "ok" for result in results)
    assert results[0]["name"] == "Live name"
    assert results[0]["running"] is True
    assert results[0]["file_ids"] == set()
    assert cached is results[0]
    assert fresh["status"] == "ok"
    share_transcripts.clear_share_transcripts()


def test_load_share_transcript_hides_runtime_errors_and_never_calls_a_stopped_assistant():
    share_transcripts.clear_share_transcripts()
    calls = []

    async def failing(**kwargs):
        calls.append(kwargs["subpath"])
        return 502, b"upstream 10.0.0.7:8000 refused", "text/plain"

    async def exploding(**kwargs):
        calls.append(kwargs["subpath"])
        raise RuntimeError("socket closed by 10.0.0.7")

    async def scenario():
        stopped = await share_transcripts.load_share_transcript(_share(), _agent(status="stopped"), failing)
        failed = await share_transcripts.load_share_transcript(_share("t2"), _agent(), failing)
        exploded = await share_transcripts.load_share_transcript(_share("t3"), _agent(), exploding)
        return stopped, failed, exploded

    stopped, failed, exploded = asyncio.run(scenario())
    assert stopped["status"] == share_transcripts.STATUS_ASSISTANT_STOPPED
    assert failed["status"] == exploded["status"] == share_transcripts.STATUS_RUNTIME_UNREACHABLE
    assert failed["error"] == exploded["error"] == share_transcripts.RUNTIME_UNREACHABLE_MESSAGE
    assert "10.0.0.7" not in str(failed) + str(exploded)
    assert calls == ["api/sessions/chat-1", "api/sessions/chat-1"]
    share_transcripts.clear_share_transcripts()
