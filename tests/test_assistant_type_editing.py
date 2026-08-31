"""Tests for editing assistant types: the icon picker, per-type branches, and
the branch-lookup cache that keeps the panel from blocking on the network.
"""
import re
from pathlib import Path

import pytest

from app.services import git_branch_cache
from app.services.assistant_type_icons import (
    ASSISTANT_TYPE_ICONS,
    DEFAULT_ASSISTANT_TYPE_ICON,
    is_known_icon,
)


# ----------------------------------------------------------------- icon set


def _bundled_lucide_icon_names() -> set[str]:
    """PascalCase icon names present in the bundled Lucide build."""
    source = Path("app/static/lib/lucide.min.js").read_text(encoding="utf-8", errors="ignore")
    return set(re.findall(r"([A-Z][A-Za-z0-9]+):", source))


def _pascal_case(kebab: str) -> str:
    return "".join(part.capitalize() for part in kebab.split("-"))


@pytest.mark.parametrize("icon", ASSISTANT_TYPE_ICONS)
def test_every_offered_icon_exists_in_the_bundle(icon):
    # An icon name that is not in the bundle renders as nothing at all, which
    # looks like a broken picker rather than a typo.
    assert _pascal_case(icon) in _bundled_lucide_icon_names(), icon


def test_default_icon_is_offered():
    assert DEFAULT_ASSISTANT_TYPE_ICON in ASSISTANT_TYPE_ICONS


def test_icon_list_has_no_duplicates():
    assert len(set(ASSISTANT_TYPE_ICONS)) == len(ASSISTANT_TYPE_ICONS)


def test_is_known_icon_rejects_anything_not_offered():
    assert is_known_icon("bot") is True
    assert is_known_icon("not-a-real-icon") is False
    assert is_known_icon(None) is False


# -------------------------------------------------------------- branch cache


@pytest.fixture(autouse=True)
def _clear_branch_cache():
    git_branch_cache.clear_cache()
    yield
    git_branch_cache.clear_cache()


def test_branches_are_fetched_once_within_the_ttl(monkeypatch):
    calls = []

    def fake(repo_url):
        calls.append(repo_url)
        return ["master", "dev"], ""

    monkeypatch.setattr("app.api.git_repos.safe_fetch_repo_branches", fake)

    first = git_branch_cache.cached_branches("https://example.com/repo.git")
    second = git_branch_cache.cached_branches("https://example.com/repo.git")

    assert first == (["master", "dev"], "")
    assert second == first
    assert calls == ["https://example.com/repo.git"]


def test_expired_entry_is_refetched(monkeypatch):
    calls = []

    def fake(repo_url):
        calls.append(repo_url)
        return ["master"], ""

    monkeypatch.setattr("app.api.git_repos.safe_fetch_repo_branches", fake)
    monkeypatch.setattr(git_branch_cache, "BRANCH_CACHE_TTL_SECONDS", 0.0)

    git_branch_cache.cached_branches("https://example.com/repo.git")
    git_branch_cache.cached_branches("https://example.com/repo.git")

    assert len(calls) == 2


def test_failures_are_cached_too(monkeypatch):
    # An unreachable repository would otherwise make every panel open pay the
    # full lookup timeout.
    calls = []

    def fake(repo_url):
        calls.append(repo_url)
        return [], "auth failed"

    monkeypatch.setattr("app.api.git_repos.safe_fetch_repo_branches", fake)

    assert git_branch_cache.cached_branches("https://example.com/repo.git") == ([], "auth failed")
    assert git_branch_cache.cached_branches("https://example.com/repo.git") == ([], "auth failed")
    assert len(calls) == 1


def test_empty_repo_url_never_hits_the_network(monkeypatch):
    def explode(repo_url):
        raise AssertionError("should not be called")

    monkeypatch.setattr("app.api.git_repos.safe_fetch_repo_branches", explode)

    assert git_branch_cache.cached_branches(None) == ([], "")
    assert git_branch_cache.cached_branches("") == ([], "")


def test_callers_cannot_mutate_the_cached_list(monkeypatch):
    monkeypatch.setattr("app.api.git_repos.safe_fetch_repo_branches", lambda url: (["master"], ""))

    branches, _ = git_branch_cache.cached_branches("https://example.com/repo.git")
    branches.append("tampered")

    assert git_branch_cache.cached_branches("https://example.com/repo.git")[0] == ["master"]


def test_multiple_repos_keep_input_order(monkeypatch):
    monkeypatch.setattr(
        "app.api.git_repos.safe_fetch_repo_branches",
        lambda url: ([url.rsplit("/", 1)[-1]], ""),
    )

    results = git_branch_cache.cached_branches_for(["https://x/agents", "https://x/skills"])

    assert [branches for branches, _ in results] == [["agents"], ["skills"]]


# -------------------------------------------------------------- panel markup


def _render_panel(**overrides):
    from jinja2 import Environment, FileSystemLoader

    class FakeType:
        def __init__(self, **kwargs):
            self.id = kwargs.get("id", "business")
            self.name = kwargs.get("name", "Business Assistant")
            self.description = kwargs.get("description", "")
            self.icon = kwargs.get("icon", "clipboard-list")
            self.runtime_type = kwargs.get("runtime_type", "native")
            self.agent_settings_branch = kwargs.get("agent_settings_branch")
            self.skill_branch = kwargs.get("skill_branch")
            self.sort_order = kwargs.get("sort_order", 0)
            self.is_active = kwargs.get("is_active", True)

    context = {
        "request": None,
        "assistant_types": [FakeType()],
        "runtime_types": ["native", "opencode"],
        "icon_choices": ASSISTANT_TYPE_ICONS,
        "default_icon": DEFAULT_ASSISTANT_TYPE_ICON,
        "default_agent_settings_repo_url": "https://x/agents",
        "default_skill_repo_url": "https://x/skills",
        "default_agent_settings_branch": "master",
        "default_skill_branch": "master",
        "agent_branches": ["master", "ba-behavior"],
        "skill_branches": ["master", "qa"],
        "agent_branch_error": "",
        "skill_branch_error": "",
    }
    context.update(overrides)
    if "assistant_types" in overrides and overrides["assistant_types"]:
        context["assistant_types"] = [FakeType(**item) for item in overrides["assistant_types"]]

    env = Environment(loader=FileSystemLoader("app/templates"))
    return env.get_template("partials/assistant_types_panel.html").render(**context)


def test_existing_types_render_an_edit_form():
    html = _render_panel()

    assert "data-assistant-type-edit-form" in html
    assert "data-assistant-type-toggle-edit" in html
    assert 'name="name"' in html
    assert 'name="runtime_type"' in html


def test_each_type_can_target_a_different_branch_per_repository():
    html = _render_panel(
        assistant_types=[{"agent_settings_branch": "ba-behavior", "skill_branch": "qa"}]
    )

    assert '<option value="ba-behavior" selected>ba-behavior</option>' in html
    assert '<option value="qa" selected>qa</option>' in html


def test_a_branch_missing_from_the_remote_is_still_shown():
    # Otherwise editing anything else on the type would silently reset the
    # branch to "configured default".
    html = _render_panel(assistant_types=[{"skill_branch": "deleted-branch"}])

    assert "deleted-branch (not on the remote)" in html


def test_icon_picker_is_rendered_for_create_and_edit():
    html = _render_panel()

    assert html.count("data-icon-picker") == 2
    assert html.count("data-icon-choice=") == len(ASSISTANT_TYPE_ICONS) * 2


def test_current_icon_is_preselected():
    html = _render_panel(assistant_types=[{"icon": "microscope"}])

    assert 'aria-checked="true"' in html
    assert 'data-icon-choice="microscope"' in html


def test_branch_lookup_failure_degrades_to_a_text_field():
    html = _render_panel(agent_branches=[], agent_branch_error="permission denied")

    assert 'name="agent_settings_branch" class="portal-form-input"' in html
    assert "permission denied" in html


def test_no_configured_repository_says_so():
    html = _render_panel(skill_branches=[], skill_branch_error="", default_skill_repo_url="")

    assert "No repository configured" in html
