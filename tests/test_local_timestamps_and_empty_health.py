"""Server timestamps render in the reader's zone; empty overviews say so.

Two display bugs this locks down:

1. Panels rendered ``datetime.utcnow()`` straight through ``strftime``. Someone
   signing in at 22:16 local (UTC-7) saw "Last sign-in: 2026-08-25 05:16" — the
   wrong time and the wrong day.
2. ``_health_summary`` scored an empty system 100/100 "Healthy" and captioned it
   "Tasks are running smoothly", which reads as real data rather than no data.
"""

import re
from datetime import datetime
from pathlib import Path

from app.services.work_overview import _health_summary
from app.web import local_datetime

TEMPLATES = Path("app/templates/partials")
JS = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")


def test_no_panel_renders_a_bare_server_side_timestamp():
    """Server-side strftime may only emit an ISO instant for JS to localize.

    A display format like '%Y-%m-%d %H:%M' rendered straight into the page is
    the original bug. An ISO instant parked in a data attribute is not: it
    carries its zone and the browser formats it.
    """
    for template in TEMPLATES.glob("*.html"):
        source = template.read_text(encoding="utf-8")
        for call in re.findall(r"strftime\(\s*'([^']+)'", source):
            assert call.endswith("Z") and "T" in call, f"{template.name}: {call}"


def test_local_datetime_emits_an_iso_instant_and_a_utc_labelled_fallback():
    rendered = str(local_datetime(datetime(2026, 8, 25, 5, 16, 30)))
    assert 'datetime="2026-08-25T05:16:30Z"' in rendered
    assert 'data-local-datetime="datetime"' in rendered
    # Fallback text is only seen without scripting, so it must name its zone.
    assert ">2026-08-25 05:16 UTC<" in rendered


def test_local_datetime_date_style_drops_the_clock():
    rendered = str(local_datetime(datetime(2026, 8, 25, 5, 16, 30), style="date"))
    assert 'data-local-datetime="date"' in rendered
    assert ">2026-08-25 UTC<" in rendered


def test_local_datetime_renders_the_caller_supplied_placeholder_when_missing():
    assert ">Never<" in str(local_datetime(None, empty="Never"))
    assert "<time" not in str(local_datetime(None, empty="Never"))


def test_client_localizes_timestamps_on_load_and_after_htmx_swaps():
    assert "function formatLocalTimestamps(" in JS
    assert "toLocaleString(" in JS
    assert "formatLocalTimestamps(target || document);" in JS
    assert "formatLocalTimestamps(document);" in JS


def test_empty_overview_reports_no_data_rather_than_perfect_health():
    health = _health_summary(critical=0, warning=0, subject="Tasks", total=0)
    assert health["empty"] is True
    assert health["score"] is None
    assert health["label"] == "No data"
    assert health["headline"] == "No tasks yet"
    assert health["tone"] == "neutral"


def test_populated_overview_still_scores_normally():
    healthy = _health_summary(critical=0, warning=0, subject="Tasks", total=3)
    assert healthy["empty"] is False
    assert healthy["score"] == 100
    assert healthy["label"] == "Healthy"

    degraded = _health_summary(critical=2, warning=1, subject="Delegations", total=9)
    assert degraded["score"] == 100 - 2 * 14 - 6
    assert degraded["label"] == "Needs attention"


def test_overview_templates_do_not_print_a_score_for_an_empty_system():
    for name in ("my_tasks_panel.html", "delegations_panel.html"):
        source = (TEMPLATES / name).read_text(encoding="utf-8")
        assert "{% if overview.health.empty %}&mdash;{% else %}{{ overview.health.score }}{% endif %}" in source, name
