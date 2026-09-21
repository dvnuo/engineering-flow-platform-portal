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
    "nexus": {
        "title": "Connect Nexus Repository",
        "summary": "Lets the assistant look up artifacts and their versions in Nexus. Read-only.",
        "steps": [
            "Enter the Nexus base URL, for example https://nexus.example.com.",
            "Add a read-only account: its username with either a user token or the password. Leave both blank for anonymous read access.",
            "Name each instance; with several, name the one assistants should use by default.",
        ],
        "help_url": None,
        "help_label": None,
        "user_fields": ["username", "token"],
    },
    "splunk": {
        "title": "Connect Splunk",
        "summary": "Lets the assistant run searches over your logs when it troubleshoots. Read-only.",
        "steps": [
            "Enter the Splunk management API URL with its port, usually https://splunk.example.com:8089 (not the web UI port).",
            "Create an authentication token in Splunk (Settings, Tokens) and paste it; a username and password work too.",
            "Optionally set the default index, the default earliest time (for example -1h) and the largest result count a search may return.",
        ],
        "help_url": None,
        "help_label": None,
        "user_fields": ["token"],
    },
    "appd": {
        "title": "Connect AppDynamics",
        "summary": "Lets the assistant read application health, slow or failing transaction snapshots and health-rule violations. Read-only.",
        "steps": [
            "Enter the controller URL, for example https://appd-controller.example.com, and the account name shown on its login page.",
            "Create an API Client in the controller (Administration, API Clients) with a read-only role; enter its name as the username and its client secret as the token.",
            "Or choose Basic and enter a read-only user's name and password instead.",
        ],
        "help_url": None,
        "help_label": None,
        "user_fields": ["username", "token"],
    },
    "pgsql": {
        "title": "Connect PostgreSQL",
        "summary": "Lets the assistant inspect a database's schema and query it. The role and endpoint you enter decide whether it can also change anything.",
        "steps": [
            "Enter the host, port (5432 unless your DBA says otherwise) and database name.",
            "The role is the control: a login with SELECT and nothing else makes this instance read-only, whatever the assistant is asked to do.",
            "Use a role that may write only for an instance you intend the assistant to change things through.",
            "Keep SSL on require unless the server offers a CA you can verify.",
        ],
        "help_url": None,
        "help_label": None,
        "user_fields": ["username", "password"],
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
        "summary": "Lets the assistant inspect AWS accounts and EKS clusters through the read-only roles you list here.",
        "steps": [
            "Enter the directory account aws-auth signs in with: the domain, username and password your team uses for AWS.",
            "Choose the provider your organisation uses. adfs-assume is the default; only saml2aws needs the IdP URL, and only assume-role needs a source profile.",
            "Add one row per AWS account with its 12-digit account id, a read-only role and the regions it uses, then name one of them as the default account.",
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


# Connectors are not runtime-profile sections (they never enter a pod), so they
# get their own table. The panel under Connectors and the Help topic render the
# same entry, which is why the steps and the troubleshooting lines live here.
CONNECTOR_GUIDANCE: dict[str, dict[str, Any]] = {
    "local_browser": {
        "title": "Local browser connector",
        "summary": "Lets an assistant read and operate pages in a Chrome window on your own PC, with your logins.",
        "steps": [
            "Download the EFP browser bridge package for your system from Connectors → Local browser (Windows, macOS, or Linux; it holds only the browser program, the installer, and a README) and unzip it into the bin folder of your home directory (%USERPROFILE%\\bin on Windows, ~/bin on macOS or Linux), overwriting any files already there.",
            "Run the installer in the unzipped folder (double-click install-bridge.cmd on Windows and enter the Portal address when it asks; ./install-bridge.sh <portal origin> on macOS or Linux), then click Start bridge on the panel and allow the efp-bridge link when Chrome asks. A Chrome window titled EFP opens with the Portal as its only tab.",
            "Click Test connection. It lists the tabs of the EFP browser window; sign in to your work sites in that window once.",
            "Switch Enabled on and save. New chats show a Browser toggle in the composer.",
        ],
        "troubleshooting": [
            "Bridge not detected: it is not running. Repeat step 2; if the efp-bridge link does nothing, run install-bridge.cmd (Windows) or install-bridge.sh (macOS, Linux) again.",
            "Start bridge seems to do nothing: the launcher runs without a window on purpose and writes each attempt to .efp/browser/logs/bridge-serve.log in your home folder. Read the last lines there.",
            "The EFP browser window is closed but the bridge shows as running: the bridge outlives the window. Click Start bridge to reopen it, or send a message; the next browser action reopens the window.",
            "Bridge stays undetected after a Portal address change: a bridge started for the old address is still holding the port and rejects this page. Stop it (end the browser process in Task Manager, or close the terminal it runs in) and click Start bridge again.",
            "devtools_unavailable: Chrome refused its DevTools port. Check chrome://policy for RemoteDebuggingAllowed.",
            "origin_denied or a CORS error: the bridge was started for another Portal address. Restart it with --origin set to this Portal's address.",
            "Windows SmartScreen may warn the first time browser.exe runs. Choose More info, then Run anyway.",
            "The EFP browser window keeps its own logins; sites protected by device-based sign-in may ask you to sign in there once.",
        ],
        "help_url": None,
        "help_label": None,
    },
}


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
