from app.services.delegation_reply_service import DelegationReplyService


def _reply_target(body: str) -> dict:
    return {
        "provider": "github",
        "kind": "pr_comment",
        "owner": "acme",
        "repo": "portal",
        "pull_number": 2,
        "reply_mode": "quote_reply",
        "comment_html_url": "https://github.com/acme/portal/pull/2#issuecomment-100",
        "comment_author": "alice",
        "comment_body": body,
    }


def test_github_quote_reply_preserves_marker_at_top():
    marker = "<!-- efp:delegation-reply delegation_id=rule-1 event_id=event-1 -->"

    formatted = DelegationReplyService.format_github_quote_reply_body(
        reply_target=_reply_target("@octocat please summarize"),
        text=f"{marker}\n\nFinal response",
    )

    assert formatted.startswith(f"{marker}\n\nReplying to @alice's [comment]")
    assert formatted.endswith("\n\nFinal response")


def test_github_quote_reply_quotes_multiline_source_comment():
    formatted = DelegationReplyService.format_github_quote_reply_body(
        reply_target=_reply_target("line one\n\nline three"),
        text="Final response",
    )

    assert "> line one\n> \n> line three" in formatted


def test_github_quote_reply_truncates_long_source_comment():
    formatted = DelegationReplyService.format_github_quote_reply_body(
        reply_target=_reply_target("abcdefghijklmnopqrstuvwxyz"),
        text="Final response",
        max_quote_chars=10,
    )

    assert "> abcdefghij\n> [truncated]" in formatted
    assert "klmnopqrstuvwxyz" not in formatted
