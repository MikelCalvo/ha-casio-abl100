"""Dated snapshot attributes; synthetic records, no device access."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from custom_components.casio_abl100 import WatchSession
from custom_components.casio_abl100.protocol import parse_steps
from custom_components.casio_abl100.sensor import StepsSensor
from tests.protocol.test_daily_history import history_record


def test_history_attributes_preserve_today_identity_and_unknown_before_read():
    session = WatchSession(SimpleNamespace(), 'AA:BB:CC:DD:EE:FF')
    sensor = StepsSensor(session)
    assert sensor.extra_state_attributes is None
    assert sensor.unique_id == 'AA:BB:CC:DD:EE:FF_steps_today'
    session.record = parse_steps(history_record())
    session.fresh = True
    with patch('custom_components.casio_abl100.sensor.dt_util.now', return_value=datetime(2026, 8, 1)):
        assert sensor.native_value == 1485 and sensor.available
        attrs = sensor.extra_state_attributes
    assert 'daily_history' in attrs, 'Steps today must expose the decoded dated snapshot'
    assert attrs == {
        'record_date': '2026-08-01', 'fresh': True,
        'daily_history': {
            '2026-07-31': 1234, '2026-07-30': 0, '2026-07-29': None,
            '2026-07-28': 7654, '2026-07-27': 42, '2026-07-26': 99999999,
            '2026-07-25': 1,
        },
    }


def test_history_dates_do_not_move_at_midnight_or_refresh_unrelated_observations():
    session = WatchSession(SimpleNamespace(), 'AA:BB:CC:DD:EE:FF')
    session.record = parse_steps(history_record())
    session.fresh = True
    session.alarm_snapshot = object()
    session.alarm_last_sync = object()
    session.alarms_fresh = True
    sensor = StepsSensor(session)
    initial = sensor.extra_state_attributes
    assert 'daily_history' in initial, 'Steps today must expose the decoded dated snapshot'
    with patch('custom_components.casio_abl100.sensor.dt_util.now', return_value=datetime(2026, 8, 2)):
        assert sensor.native_value is None and not sensor.available
        assert sensor.extra_state_attributes['daily_history'] == initial['daily_history']
    session.fresh = False
    assert sensor.extra_state_attributes['fresh'] is False
    assert sensor.extra_state_attributes['daily_history'] == initial['daily_history']
    assert session.alarms_fresh is True and session.alarm_last_sync is not None


def test_modifying_returned_attributes_cannot_change_the_snapshot():
    session = WatchSession(SimpleNamespace(), 'AA:BB:CC:DD:EE:FF')
    session.record = parse_steps(history_record())
    sensor = StepsSensor(session)
    attrs = sensor.extra_state_attributes
    assert 'daily_history' in attrs, 'Steps today must expose the decoded dated snapshot'
    attrs['daily_history']['2026-07-31'] = 999
    assert sensor.extra_state_attributes['daily_history']['2026-07-31'] == 1234
