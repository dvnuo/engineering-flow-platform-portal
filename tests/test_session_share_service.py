"""What a shared session's viewer gets, and who may open a share."""

from types import SimpleNamespace

import pytest

from app.services import session_share_service as service
from app.services.session_share_service import (
    SCOPE_ANYONE,
    SCOPE_MEMBERS,
    effective_scope,
    is_task_session_id,
    normalize_scope,
    project_shared_transcript,
    share_url,
    transcript_as_markdown,
    transcript_file_ids,
    viewer_may_open,
)


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


def test_project_opencode_canonical_messages_text_parts_only():
    payload = {
        "messages": [{"role": "user", "content": "legacy copy, ignored when canonical exists"}],
        "canonical_messages": [
            {"info": {"id": "m1", "role": "user"}, "parts": [{"type": "text", "text": "Hello"}]},
            {
                "info": {"id": "m2", "role": "assistant", "modelID": "gpt-6-luna"},
                "parts": [{"type": "reasoning", "text": "thinking"}, {"type": "text", "text": "Hi "}, {"type": "text", "text": "there"}],
            },
            {"info": {"id": "m3", "role": "assistant"}, "parts": [{"type": "tool", "tool": "bash"}]},
        ],
    }
    messages = project_shared_transcript(payload, assistant_name="Bot")
    assert [(m["id"], m["role"], m["markdown"]) for m in messages] == [("m1", "user", "Hello"), ("m2", "assistant", "Hi there")]
    assert messages[1]["model"] == "gpt-6-luna"
    assert messages[1]["author"] == "Bot"


def test_project_tolerates_garbage():
    assert project_shared_transcript({}) == []
    assert project_shared_transcript({"messages": "nope"}) == []
    assert project_shared_transcript({"messages": [None, 1, {"role": "assistant"}]}) == []


def test_transcript_file_ids_lists_only_images():
    messages = project_shared_transcript(NATIVE_PAYLOAD)
    assert transcript_file_ids(messages) == {"f-img"}


def test_transcript_as_markdown():
    messages = project_shared_transcript(NATIVE_PAYLOAD, assistant_name="Deploy helper")
    text = transcript_as_markdown("Release pipeline permissions", messages)
    assert text.startswith("# Release pipeline permissions\n\n**Jin**\n\nWhy does the deploy stage fail?")
    assert "[Tool pgsql]" not in text
    assert "Attachments: error.png, build.log" in text
    assert "Grant it with `grant deploy`." in text
    with_tools = transcript_as_markdown("t", messages, include_tools=True)
    assert "[Tool pgsql] select 1" in with_tools


def test_transcript_as_markdown_renders_code_and_tables():
    messages = [
        {
            "role": "assistant",
            "author": "Bot",
            "markdown": "",
            "display_blocks": [
                {"type": "code", "lang": "sql", "code": "select 1"},
                {"type": "table", "headers": ["a", "b"], "rows": [[1, 2]]},
            ],
            "attachments": [],
        }
    ]
    text = transcript_as_markdown("t", messages)
    assert "```sql\nselect 1\n```" in text
    assert "| a | b |\n| --- | --- |\n| 1 | 2 |" in text


@pytest.mark.parametrize("raw, expected", [("members", SCOPE_MEMBERS), (" Anyone ", SCOPE_ANYONE), ("", SCOPE_MEMBERS), (None, SCOPE_MEMBERS)])
def test_normalize_scope(raw, expected):
    assert normalize_scope(raw) == expected


def test_normalize_scope_rejects_unknown_audiences():
    with pytest.raises(ValueError):
        normalize_scope("public")


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
    assert is_task_session_id("task-1", ("task-",)) is True
    assert is_task_session_id("chat-1", ("task-",)) is False
    assert is_task_session_id(None, ("task-",)) is False
