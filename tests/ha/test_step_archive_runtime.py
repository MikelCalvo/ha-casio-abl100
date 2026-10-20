"""Real HA commit/storage/reload/formatter; external wire is synthetic."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from homeassistant.helpers import entity_registry as er
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
import pytest

from tests.ha.test_native_alarm_runtime import native
from tests.ha.test_unified_sync_runtime import unified
from tests.ha.test_observations_runtime import read, reload

ENTITY = 'sensor.casio_abl_100we_steps_today'


async def test_archive_accumulates_overlap_restores_and_is_visible_after_midnight(unified):
    p = unified
    ids = {e.unique_id: e.entity_id for e in er.async_get(p.hass).entities.values()
           if e.config_entry_id == p.watch.entry_id}
    await read(p)
    state = p.hass.states.get(ENTITY).as_dict()
    assert 'archived_daily_steps' in state['attributes'], 'Observed step archive not published'
    first = dict(state['attributes']['archived_daily_steps'])
    assert len(first) == 8 and state['attributes']['archived_day_count'] == 8
    assert state['attributes']['archive_restored'] is False
    original_day = p.wire.record_date
    p.wire.record_date += timedelta(days=3)
    with patch('custom_components.casio_abl100.sync_transport.dt_util.now',
               return_value=datetime(2026, 10, 21, 12, tzinfo=timezone.utc)):
        await read(p)
    before = p.hass.states.get(ENTITY).as_dict()['attributes']['archived_daily_steps']
    assert len(before) == 11 and min(first) in before
    calls = list(p.wire.calls)
    await reload(p)
    state = p.hass.states.get(ENTITY).as_dict()
    assert state['attributes']['archived_daily_steps'] == before
    assert state['attributes']['archive_restored'] is True
    assert state['attributes']['archived_day_count'] == 11
    assert p.wire.calls == calls
    assert {e.unique_id: e.entity_id for e in er.async_get(p.hass).entities.values()
            if e.config_entry_id == p.watch.entry_id} == ids and len(ids) == 36
    # Real formatter hides extra attributes on unavailable sensors. Capability
    # attributes must keep history visible without pretending today's count exists.
    with patch('custom_components.casio_abl100.sensor.dt_util.now',
               return_value=dt_util.now().replace(year=2026, month=11, day=20)):
        sensor = next(e for e in p.hass.data['entity_components']['sensor'].entities
                      if e.entity_id == ENTITY)
        sensor.async_write_ha_state()
    state = p.hass.states.get(ENTITY).as_dict()
    assert state['state'] == 'unavailable'
    assert state['attributes']['archived_daily_steps'] == before
    assert p.wire.calls == calls


@pytest.mark.parametrize('stored_archive', ['legacy_absent', 'corrupt'])
async def test_legacy_bootstrap_and_corrupt_optional_archive_preserve_six_groups(unified, stored_archive):
    from copy import deepcopy
    from custom_components.casio_abl100.observations import ObservationStore
    p = unified
    await read(p)
    original = p.session.record
    owner = p.session.observations
    payload = {'schema': 1, 'binding': owner._binding, 'groups': deepcopy(owner._groups)}
    if stored_archive == 'legacy_absent':
        payload['groups'].pop('step_archive')
    else:
        payload['groups']['step_archive'][0]['steps'] = True
    calls = list(p.wire.calls)
    with patch.object(ObservationStore, 'async_load', return_value=payload):
        await reload(p)
    state = p.hass.states.get(ENTITY)
    assert p.session.record == original and p.session.alarm_snapshot is not None
    assert p.session.basic_settings_snapshot is not None and p.session.timer_snapshot is not None
    assert state.attributes['archived_day_count'] == (8 if stored_archive == 'legacy_absent' else 0)
    assert p.wire.calls == calls and p.session.fresh is False


async def test_later_group_failure_keeps_accepted_archive_and_reload(unified):
    p = unified
    p.wire.frames[b'\x13'] = b'\x13'
    with pytest.raises(HomeAssistantError):
        await read(p)
    state = p.hass.states.get(ENTITY).as_dict()
    assert state['attributes'].get('archived_day_count') == 8
    before = state['attributes']['archived_daily_steps']
    await reload(p)
    assert p.hass.states.get(ENTITY).attributes['archived_daily_steps'] == before
    assert p.session.basic_settings_snapshot is None
