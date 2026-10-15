"""Read-only alarm entity contract against the approved session."""
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import asyncio
import pytest

from custom_components.casio_abl100 import WatchSession
from custom_components.casio_abl100.alarms import parse_alarms
from custom_components.casio_abl100.protocol import StepRecord
from homeassistant.exceptions import HomeAssistantError
from tests.protocol.test_alarm_transport import FIRST, REMAINING

ADDR = 'AA:BB:CC:DD:EE:FF'
SNAPSHOT = parse_alarms(FIRST, REMAINING)


def entities(session):
    from custom_components.casio_abl100.sensor import AlarmSensor, LastAlarmReadSensor
    from custom_components.casio_abl100.binary_sensor import HourlySignalSensor
    from custom_components.casio_abl100.button import ReadAlarmsButton
    return ([AlarmSensor(session, index) for index in range(1, 6)],
            HourlySignalSensor(session), LastAlarmReadSensor(session), ReadAlarmsButton(session))


def test_unread_snapshot_is_unknown_not_disabled_and_identifiers_are_stable():
    session = WatchSession(SimpleNamespace(), ADDR)
    alarms, signal, last, button = entities(session)
    assert [entity.unique_id for entity in alarms] == [f'{ADDR}_alarm_{i}' for i in range(1, 6)]
    assert [entity.unique_id for entity in (signal, last, button)] == [
        f'{ADDR}_hourly_signal', f'{ADDR}_last_alarm_read', f'{ADDR}_read_alarms']
    assert [entity.entity_description.translation_key for entity in alarms] == [f'alarm_{i}' for i in range(1, 6)]
    assert all(entity.has_entity_name and entity.device_info['name'] == 'Casio ABL-100WE'
               for entity in [*alarms, signal, last, button])
    assert all(entity.native_value is None and not entity.available for entity in [*alarms, last])
    assert signal.is_on is None and not signal.available
    assert all(entity.extra_state_attributes is None for entity in alarms)


def test_all_slots_are_times_not_datetimes_and_disabled_still_has_observed_time():
    session = WatchSession(SimpleNamespace(), ADDR)
    alarms, signal, last, _ = entities(session)
    session.alarm_snapshot = SNAPSHOT
    session.alarm_last_sync = datetime(2026, 8, 1, 2, 3, tzinfo=timezone.utc)
    session.alarms_fresh = True
    assert [entity.native_value for entity in alarms] == ['23:59', '00:00', '09:30', '12:00', '16:59']
    assert [entity.extra_state_attributes for entity in alarms] == [
        {'enabled': enabled, 'fresh': True} for enabled in (True, True, False, True, False)]
    assert all(entity.available for entity in alarms)
    assert signal.is_on is True and signal.extra_state_attributes == {'fresh': True}
    assert last.native_value == session.alarm_last_sync and last.available
    session.alarm_snapshot = parse_alarms(bytes.fromhex('15 40 00 00 00'), REMAINING)
    assert signal.is_on is False and signal.available


@pytest.mark.asyncio
async def test_step_notifications_and_failed_alarm_refresh_never_change_last_observation():
    session = WatchSession(SimpleNamespace(), ADDR)
    alarms, signal, last, _ = entities(session)
    session.alarm_snapshot = SNAPSHOT
    session.alarm_last_sync = datetime(2026, 8, 1, tzinfo=timezone.utc)
    session.alarms_fresh = True
    with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address', return_value=None):
        with pytest.raises(HomeAssistantError):
            await session.read_alarms()
    await asyncio.sleep(0)  # deferred session observer delivery
    assert [entity.native_value for entity in alarms] == ['23:59', '00:00', '09:30', '12:00', '16:59']
    assert all(entity.extra_state_attributes['fresh'] is False for entity in alarms)
    assert signal.is_on is True and signal.extra_state_attributes == {'fresh': False}
    assert last.native_value == datetime(2026, 8, 1, tzinfo=timezone.utc)
    session.record = StepRecord(date(2026, 8, 2), 42)
    session.fresh = True
    session.last_sync = datetime(2026, 8, 2, tzinfo=timezone.utc)
    session._notify()
    assert [entity.native_value for entity in alarms] == ['23:59', '00:00', '09:30', '12:00', '16:59']
    assert all(entity.extra_state_attributes['fresh'] is False for entity in alarms)
    assert last.native_value == datetime(2026, 8, 1, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_button_delegates_one_manual_read_without_step_read():
    session = WatchSession(SimpleNamespace(), ADDR)
    _, _, _, button = entities(session)
    session.read_alarms = AsyncMock()
    session.read = AsyncMock()
    await button.async_press()
    session.read_alarms.assert_awaited_once_with()
    session.read.assert_not_awaited()
