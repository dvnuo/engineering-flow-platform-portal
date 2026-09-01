"""Per-connection setup instructions shown next to each Connections section.

The seed fills in *what* a member's connection points at; this fills in *where
to get the credential and what to paste*. Both halves are needed before "pick a
type and start working" is true for someone who has never held an API token.

Guidance is static content, not deployment config: it changes when a vendor
changes its token page, not per install, so it lives in code and stays
reviewable. `user_fields` lists only what the member supplies themselves — the
rest of a section arrives prefilled from the seed.
"""
from __future__ import annotations

from typing import Any


CONNECTION_GUIDANCE: dict[str, dict[str, Any]] = {
    "llm": {
        "title": "Connect your model provider",
        "summary": "The assistant needs a model provider before it can answer anything.",
        "steps": [
            "Choose GitHub Copilot unless your administrator told you otherwise.",
            "Click Authorize GitHub Copilot and follow the three steps it shows.",
            "Come back here and click Save Settings to store the token.",
        ],
        "help_url": "https://github.com/settings/copilot",
        "help_label": "GitHub Copilot settings",
        "user_fields": ["api_key"],
    },
    "jira": {
        "title": "Connect Jira",
        "summary": "Lets the assistant read tickets, comment, and create issues as you.",
        "steps": [
            "Open your Atlassian account security page and create an API token.",
            "Name the token EFP so you can recognize it later.",
            "Paste your Atlassian account email as the username and the token below.",
        ],
        "help_url": "https://id.atlassian.com/manage-profile/security/api-tokens",
        "help_label": "Create an Atlassian API token",
        "user_fields": ["username", "token"],
    },
    "confluence": {
        "title": "Connect Confluence",
        "summary": "Lets the assistant read and publish pages in your spaces.",
        "steps": [
            "Confluence uses the same Atlassian API token as Jira.",
            "If you already created one for Jira, paste the same token here.",
            "Use your Atlassian account email as the username.",
        ],
        "help_url": "https://id.atlassian.com/manage-profile/security/api-tokens",
        "help_label": "Create an Atlassian API token",
        "user_fields": ["username", "token"],
    },
    "github": {
        "title": "Connect GitHub",
        "summary": "Lets the assistant read repositories, open pull requests, and review code.",
        "steps": [
            "Create a personal access token with repo scope.",
            "For GitHub Enterprise, create it on your company's GitHub host instead.",
            "Paste the token below and leave the base URL as your administrator set it.",
        ],
        "help_url": "https://github.com/settings/tokens",
        "help_label": "Create a GitHub token",
        "user_fields": ["api_token"],
    },
    "jenkins": {
        "title": "Connect Jenkins",
        "summary": "Lets the assistant read build results and diagnose failing jobs.",
        "steps": [
            "Open your Jenkins user page and go to Configure.",
            "Add an API token and copy the generated value.",
            "Paste your Jenkins username and the token below.",
        ],
        "help_url": None,
        "help_label": None,
        "user_fields": ["username", "token"],
    },
    "mobile": {
        "title": "Connect BrowserStack",
        "summary": "Lets the assistant run and inspect mobile automation sessions.",
        "steps": [
            "Open your BrowserStack account settings.",
            "Copy your username and access key.",
            "Paste both below.",
        ],
        "help_url": "https://www.browserstack.com/accounts/profile/details",
        "help_label": "BrowserStack account settings",
        "user_fields": ["username", "access_key"],
    },
    "aws": {
        "title": "Connect AWS",
        "summary": "Lets the assistant inspect AMIs, instances, and CloudWatch logs.",
        "steps": [
            "Use the AWS account your team already uses for this environment.",
            "Ask your administrator which domain value to enter if you are unsure.",
        ],
        "help_url": None,
        "help_label": None,
        "user_fields": ["username", "password"],
    },
    "proxy": {
        "title": "Network proxy",
        "summary": "Only needed if your network requires a proxy to reach the internet.",
        "steps": [
            "Leave this off unless your administrator told you to turn it on.",
            "Enter the proxy URL exactly as your network team provided it.",
        ],
        "help_url": None,
        "help_label": None,
        "user_fields": [],
    },
    "git": {
        "title": "Git identity",
        "summary": "The name and email that appear on commits the assistant makes for you.",
        "steps": [
            "Use the same name and email you use for your own commits.",
            "This is not a credential — it only labels authorship.",
        ],
        "help_url": None,
        "help_label": None,
        "user_fields": [],
    },
}


# Sections a member has to complete before the assistant can do useful work.
# Reported as a checklist so Connections reads as a task with an end, not an
# open-ended form.
TRACKED_SECTIONS = ("llm", "jira", "confluence", "github")


def guidance_for(section: str) -> dict[str, Any] | None:
    return CONNECTION_GUIDANCE.get(section)


def all_guidance() -> dict[str, dict[str, Any]]:
    return CONNECTION_GUIDANCE


def _section_has_credential(section: str, config: dict) -> bool:
    """Whether the member has supplied their own credential for this section."""

    value = config.get(section)
    if not isinstance(value, dict):
        return False
    if section == "llm":
        if str(value.get("provider") or "") == "ai_platform":
            auth = value.get("ai_platform", {}).get("auth", {}) if isinstance(value.get("ai_platform"), dict) else {}
            return bool(str(auth.get("password") or "").strip())
        return bool(str(value.get("api_key") or "").strip())
    if section == "github":
        return bool(str(value.get("api_token") or "").strip())
    instances = value.get("instances")
    if isinstance(instances, list):
        return any(
            isinstance(item, dict) and (str(item.get("token") or "").strip() or str(item.get("password") or "").strip())
            for item in instances
        )
    return False


def _section_is_offered(section: str, config: dict) -> bool:
    """Whether this section is worth asking about at all.

    The LLM is always required. Everything else only appears on the checklist
    when the admin seeded it or the member turned it on, so a team that does not
    use Confluence never sees an unfinishable step.
    """
    if section == "llm":
        return True
    value = config.get(section)
    if not isinstance(value, dict):
        return False
    if value.get("enabled"):
        return True
    instances = value.get("instances")
    return bool(isinstance(instances, list) and instances)


def connection_checklist(config: dict) -> dict[str, Any]:
    """Build the Connections progress checklist for one profile config."""

    config = config if isinstance(config, dict) else {}
    sections = []
    for section in TRACKED_SECTIONS:
        if not _section_is_offered(section, config):
            continue
        guidance = CONNECTION_GUIDANCE.get(section, {})
        sections.append(
            {
                "section": section,
                "label": guidance.get("title") or section.title(),
                "connected": _section_has_credential(section, config),
            }
        )
    connected = sum(1 for item in sections if item["connected"])
    return {
        # Deliberately not named "items": Jinja resolves dict.items to the
        # built-in method, which silently breaks the template loop.
        "sections": sections,
        "connected": connected,
        "total": len(sections),
        "complete": bool(sections) and connected == len(sections),
    }
