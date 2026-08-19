from pathlib import Path


def test_context_usage_toolbar_panel_and_compact_contract_are_present():
    template = Path("app/templates/app.html").read_text(encoding="utf-8")
    source = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")

    assert 'id="btn-context"' in template
    assert 'id="context-usage-label"' in template
    for control_id in ("header-new-chat-btn", "btn-sessions", "btn-context", "btn-files", "detail-toggle"):
        assert f'id="{control_id}" class="portal-header-action' in template
    assert 'portal-header-action-label">Sessions<' in template
    assert 'portal-header-action-label">Files<' in template
    assert 'portal-header-action-label">Details<' in template
    assert '"context"' in source
    assert "function renderContextUsagePanel(" in source
    assert "/context-usage" in source
    assert "/compact" in source
    assert 'method: "POST"' in source
    assert "Compact conversation" in source
    assert "category?.label" in source
    assert "Coarse breakdown" in source
    assert 'title: "Compact conversation"' in source
    assert "await showConfirm({" in source
    assert "window.confirm(" not in source
