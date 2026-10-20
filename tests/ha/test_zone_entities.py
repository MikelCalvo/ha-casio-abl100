"""Additive entity invariants; old eleven remain an exact subset."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from custom_components.casio_abl100 import WatchSession
from custom_components.casio_abl100.settings import parse_zone_settings
from custom_components.casio_abl100.sensor import ZoneOffsetSensor, LastTimeZoneReadSensor
from tests.protocol.test_zone_transport import FRAMES

ZONE_KEYS = {'read_time_zones', 'home_standard_utc_offset', 'secondary_standard_utc_offset', 'last_time_zone_read'}
SYNC_KEYS = {'read_all', 'time_format', 'automatic_time_adjustment', 'timer_encoded_duration'}
INTERACTIVE_KEYS = {'connect_interactive', 'read_interactive', 'close_interactive', 'interactive_connected'}
ALARM_EDIT_KEYS = {*(f'alarm_{n}_time_draft' for n in range(1, 6)),
                   *(f'alarm_{n}_enabled_draft' for n in range(1, 6)),
                   'hourly_signal_draft', 'apply_alarm_draft', 'discard_alarm_draft'}
LISTEN_KEYS = {'watch_connection'}
OLD_KEYS = {'steps_today', 'last_sync', 'read_steps', 'last_alarm_read', 'read_alarms', 'hourly_signal', *(f'alarm_{n}' for n in range(1, 6))}


def assert_zone_addition(hass, entry):
    from homeassistant.helpers import entity_registry as er
    entities = [e for e in er.async_get(hass).entities.values() if e.config_entry_id == entry.entry_id]
    keys = {e.unique_id.removeprefix((entry.unique_id or entry.data['address']) + '_') for e in entities}
    assert keys - ZONE_KEYS - SYNC_KEYS - INTERACTIVE_KEYS - ALARM_EDIT_KEYS - LISTEN_KEYS == OLD_KEYS
    assert keys & INTERACTIVE_KEYS == INTERACTIVE_KEYS
    assert keys & SYNC_KEYS == SYNC_KEYS
    assert keys & ZONE_KEYS == ZONE_KEYS
    assert len(entities) == len(OLD_KEYS) + len(ZONE_KEYS) + len(SYNC_KEYS) + len(INTERACTIVE_KEYS) + len(ALARM_EDIT_KEYS) + len(LISTEN_KEYS)


def old_listeners(session, exclude_steps=False):
    return {listener for listener in session.listeners
            if getattr(getattr(listener, '__self__', None), 'entity_description', SimpleNamespace(key=None)).key
            not in (ZONE_KEYS | SYNC_KEYS | INTERACTIVE_KEYS | ALARM_EDIT_KEYS | LISTEN_KEYS | ({'read_steps'} if exclude_steps else set()))}


@pytest.mark.asyncio
async def test_zone_entities_observation_not_clock_and_stable_identity():
    session = WatchSession(SimpleNamespace(), 'AA:BB:CC:DD:EE:FF', identity='stable')
    home, second, last = ZoneOffsetSensor(session), ZoneOffsetSensor(session, True), LastTimeZoneReadSensor(session)
    assert not any(e.available for e in (home, second, last))
    session.zone_snapshot = parse_zone_settings(*FRAMES)
    session.zones_fresh = True
    assert home.native_value == -330 and second.native_value == 345
    assert home.unique_id == 'stable_home_standard_utc_offset'
    assert second.extra_state_attributes == {'configured_dst_increment_minutes': 30, 'dst_rule_code': 127, 'fresh': True}
    with patch('custom_components.casio_abl100.sensor.dt_util.now', side_effect=AssertionError('No host clock allowed')):
        assert home.native_value == -330 and home.available
    session.zones_fresh = False
    assert home.native_value == -330 and home.extra_state_attributes['fresh'] is False
    await session.shutdown()
