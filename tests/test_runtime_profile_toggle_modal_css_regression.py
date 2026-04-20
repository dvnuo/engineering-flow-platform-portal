from pathlib import Path
import re

import pytest


TARGET_SECTIONS = [
    ("proxy", "proxy_enabled", "Proxy"),
    ("jira", "jira_enabled", "Jira"),
    ("confluence", "confluence_enabled", "Confluence"),
    ("github", "github_enabled", "GitHub"),
    ("debug", "debug_enabled", "Debug"),
]


def _section_html(template_html: str, section_name: str) -> str:
    marker = f'data-managed-section="{section_name}"'
    start = template_html.index(marker)
    rest = template_html[start:]
    end_match = re.search(r"</section>", rest)
    assert end_match, f"section {section_name} should close"
    return rest[: end_match.end()]


@pytest.mark.parametrize(
    "template_path",
    [
        "app/templates/partials/runtime_profile_panel.html",
        "app/templates/partials/settings_panel.html",
    ],
)
def test_top_level_runtime_provider_enabled_toggles_are_left_of_titles(template_path):
    html = Path(template_path).read_text(encoding="utf-8")

    for section_name, input_name, title in TARGET_SECTIONS:
        section = _section_html(html, section_name)
        assert "portal-settings-section-head--leading-toggle" in section
        assert "portal-settings-title-with-toggle" in section
        assert f'name="{input_name}"' in section
        assert f"<h6>{title}</h6>" in section
        assert section.index(f'name="{input_name}"') < section.index(f"<h6>{title}</h6>")


def test_leading_toggle_css_classes_exist():
    css = Path("app/static/css/app.css").read_text(encoding="utf-8")

    assert ".portal-settings-section-head--leading-toggle" in css
    assert ".portal-settings-title-with-toggle" in css
    assert ".portal-section-enable-switch" in css
    assert ".portal-section-enable-text" in css
