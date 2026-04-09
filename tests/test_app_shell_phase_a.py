from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_phase_a_shell_copy_regressions():
    app_html = _read("app/templates/app.html")

    assert "Assistants" in app_html
    assert "Select an assistant" in app_html
    assert 'placeholder="Message assistant"' in app_html
    assert 'id="btn-more"' in app_html

    assert "Active Agent" not in app_html
    assert 'placeholder="Type message, / for skills"' not in app_html

    assert 'id="btn-sessions"' not in app_html
    assert 'id="btn-thinking"' not in app_html
    assert 'id="btn-files"' not in app_html
    assert 'id="top-settings"' not in app_html

    assert "Create Agent" not in app_html
    assert "Edit Agent" not in app_html
    assert "Agent Name" not in app_html
    assert "Create assistant" in app_html
    assert "Edit assistant" in app_html
    assert "Assistant name" in app_html


def test_phase_a_partial_copy_regressions():
    files_panel = _read("app/templates/partials/files_panel.html")
    sessions_panel = _read("app/templates/partials/sessions_panel.html")
    activity_panel = _read("app/templates/partials/thinking_process_panel.html")
    settings_panel = _read("app/templates/partials/settings_panel.html")

    assert "My Uploads" not in files_panel
    assert "Files" in files_panel

    assert "Recent Sessions" not in sessions_panel
    assert "Chat history" in sessions_panel

    assert "Thinking Process" not in activity_panel
    assert "Thinking Events" not in activity_panel
    assert "Activity Events" in activity_panel
    assert "No thinking data available" not in activity_panel
    assert "No activity data available" in activity_panel

    assert "Please select an agent first" not in settings_panel
    assert "Please select an assistant first" in settings_panel
