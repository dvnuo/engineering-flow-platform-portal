from app.web import _status_tone_from_value


def test_status_tone_extended_values():
    assert _status_tone_from_value('pending_restart') == 'warning'
    assert _status_tone_from_value('cancel_failed') == 'error'
    assert _status_tone_from_value('cancelled') == 'info'
