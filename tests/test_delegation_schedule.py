from datetime import datetime

from app.services.delegation_schedule import compute_next_run_at, preview_delegation_schedule, summarize_delegation_schedule


def test_cron_schedule_computes_next_run_in_timezone():
    schedule = {
        "type": "cron",
        "expression": "30 9 * * 1-5",
        "timezone": "Asia/Shanghai",
    }

    assert compute_next_run_at(schedule, after=datetime(2026, 6, 17, 0, 0, 0)) == datetime(2026, 6, 17, 1, 30, 0)
    assert compute_next_run_at(schedule, after=datetime(2026, 6, 17, 1, 31, 0)) == datetime(2026, 6, 18, 1, 30, 0)


def test_cron_schedule_preview_returns_summary_and_next_run():
    preview = preview_delegation_schedule(
        {
            "type": "cron",
            "expression": "30 9 * * 1-5",
            "timezone": "Asia/Shanghai",
        },
        after=datetime(2026, 6, 17, 0, 0, 0),
    )

    assert preview.valid is True
    assert preview.summary == "Every weekday at 09:30 (Asia/Shanghai)"
    assert preview.next_run_at == datetime(2026, 6, 17, 1, 30, 0)
    assert preview.next_run_local == "2026-06-17 09:30"


def test_cron_schedule_preview_rejects_non_standard_expression():
    preview = preview_delegation_schedule({"type": "cron", "expression": "0 9 * * * *", "timezone": "UTC"})

    assert preview.valid is False
    assert "5 fields" in preview.error


def test_interval_schedule_summary_keeps_legacy_rules_readable():
    assert summarize_delegation_schedule({"interval_seconds": 120}) == "Every 2 minutes"
