from pathlib import Path



def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _chat_ui_js_source() -> str:
    chat_ui_path = _repo_root() / "app" / "static" / "js" / "chat_ui.js"
    return chat_ui_path.read_text(encoding="utf-8")

def test_main_header_eyebrow_removed_from_template():
    html = Path("app/templates/app.html").read_text(encoding="utf-8")
    assert '<div class="portal-eyebrow">Assistant</div>' not in html


def test_js_no_longer_updates_removed_main_header_eyebrow():
    js_source = _chat_ui_js_source()
    assert ".portal-main-header-copy .portal-eyebrow" not in js_source


def test_my_space_section_renders_without_inner_eyebrow_title():
    js_source = _chat_ui_js_source()

    assert 'renderSection("My Space", mine);' not in js_source
    assert 'renderSection("My Space", mine, { showTitle: false });' in js_source
    assert 'renderSection("Shared", shared);' in js_source
    assert 'renderSection("Public", publicAgents);' in js_source
