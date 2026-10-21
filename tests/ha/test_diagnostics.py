"""Safe diagnostics omit identifying and raw BLE data."""
from datetime import date, datetime, timezone
from types import SimpleNamespace
import json
import pytest

from custom_components.casio_abl100 import WatchSession
from custom_components.casio_abl100.alarms import parse_alarms
from custom_components.casio_abl100.diagnostics import async_get_config_entry_diagnostics
from custom_components.casio_abl100.protocol import StepRecord
from tests.protocol.test_alarm_transport import FIRST, REMAINING


@pytest.mark.asyncio
async def test_diagnostics_are_bounded_and_do_not_include_mac_or_payload():
    manager = WatchSession(SimpleNamespace(), 'AA:BB:CC:DD:EE:FF')
    manager.record = StepRecord(date(2026, 8, 1), 1485)
    manager.last_sync = datetime(2026, 8, 1, tzinfo=timezone.utc)
    manager.alarm_snapshot = parse_alarms(FIRST, REMAINING)
    manager.alarm_last_sync = datetime(2026, 8, 2, tzinfo=timezone.utc)
    manager.alarms_fresh = True
    setattr(manager, 'private_canary', 'PRIVATE_ALARM_CANARY')
    result = await async_get_config_entry_diagnostics(None, SimpleNamespace(runtime_data=manager, data={'address': manager.address}))
    serialized = json.dumps(result)
    assert set(result) == {'model', 'has_successful_read', 'last_sync', 'record_date', 'fresh', 'window_observation'}
    assert all(token not in serialized for token in (
        'AA:BB', 'PRIVATE', '23:59', '09:30', 'alarm_snapshot',
        'alarm_last_sync', 'alarms_fresh', 'flags', 'aux', 'hourly_signal',
    ))
    assert result['has_successful_read'] is True
    assert result['window_observation'] is None
