"""Opt-in real HA core runtime smoke, BLE boundary remains mocked."""
from datetime import timedelta
from unittest.mock import Mock, patch
from pytest_homeassistant_custom_component.common import MockConfigEntry
from homeassistant.core import HassJob, HomeAssistant
from homeassistant.util import dt as dt_util
import pytest

from custom_components.casio_abl100 import DOMAIN
from custom_components.casio_abl100 import sensor as sensors
from custom_components.casio_abl100.protocol import StepRecord


@pytest.mark.asyncio
async def test_failed_core_unload_keeps_runtime_session_open(hass: HomeAssistant, enable_custom_integrations):
    entry = MockConfigEntry(domain=DOMAIN, title='Casio ABL-100WE', data={'address': 'AA:BB:CC:DD:EE:FF'}, source='user', options={}, unique_id='AA:BB:CC:DD:EE:FF')
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    from custom_components.casio_abl100 import async_unload_entry
    with patch.object(hass.config_entries, 'async_unload_platforms', return_value=False):
        assert await async_unload_entry(hass, entry) is False
    assert not entry.runtime_data.closed
    assert entry.runtime_data.listeners
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_core_entry_setup_creates_three_entities_without_a_ble_read(hass: HomeAssistant, enable_custom_integrations):
    entry = MockConfigEntry(domain=DOMAIN, title='Casio ABL-100WE', data={'address': 'AA:BB:CC:DD:EE:FF'}, source='user', options={}, unique_id='AA:BB:CC:DD:EE:FF')
    entry.add_to_hass(hass)
    with patch('custom_components.casio_abl100.establish_connection') as connect:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        connect.assert_not_called()
    states = [state for state in hass.states.async_all() if state.domain in ('sensor', 'button') and 'casio' in state.entity_id]
    assert len(states) == 3
    assert {state.entity_id for state in states} == {'sensor.casio_abl_100we_steps_today', 'sensor.casio_abl_100we_last_synchronization', 'button.casio_abl_100we_read_steps'}
    from homeassistant.helpers import device_registry as dr, entity_registry as er
    registry = dr.async_get(hass)
    devices = [device for device in registry.devices if (DOMAIN, 'AA:BB:CC:DD:EE:FF') in device.identifiers]
    assert len(devices) == 1
    assert (devices[0].manufacturer, devices[0].model) == ('Casio', 'ABL-100WE')
    entities = [er.async_get(hass).async_get(state.entity_id) for state in states]
    assert all(entity.config_entry_id == entry.entry_id for entity in entities)
    assert len(entities) == 3
    assert len({entity.unique_id for entity in entities}) == 3
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('record_day_offset', 'steps', 'initial_state', 'rollover_state'),
    [
        (0, 1485, '1485', 'unavailable'),
        (0, 0, '0', 'unavailable'),
        (1, 42, 'unavailable', '42'),
        (-1, 1485, 'unavailable', 'unavailable'),
        (None, None, 'unavailable', 'unavailable'),
    ],
)
async def test_core_midnight_scheduler_publishes_date_valid_steps_without_thread_violation(
    hass: HomeAssistant, enable_custom_integrations, caplog,
    record_day_offset, steps, initial_state, rollover_state,
):
    """HA's real HassJob dispatcher must run the registered timer on the loop."""
    entry = MockConfigEntry(
        domain=DOMAIN, title='Casio ABL-100WE',
        data={'address': 'AA:BB:CC:DD:EE:FF'}, source='user',
        unique_id='AA:BB:CC:DD:EE:FF',
    )
    entry.add_to_hass(hass)
    callbacks = []
    cancellations = []
    original_track = sensors.async_track_time_change

    def capture_track(hass_obj, action, **kwargs):
        callbacks.append(action)
        cancel = original_track(hass_obj, action, **kwargs)
        tracked_cancel = Mock(wraps=cancel)
        cancellations.append(tracked_cancel)
        return tracked_cancel

    with patch.object(sensors, 'async_track_time_change', side_effect=capture_track):
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert len(callbacks) == len(cancellations) == 1
    assert cancellations[0].call_count == 0

    current = dt_util.now()
    if record_day_offset is not None:
        entry.runtime_data.record = StepRecord(
            (current + timedelta(days=record_day_offset)).date(), steps,
        )
        entry.runtime_data.fresh = True
        entry.runtime_data._notify()
    entity_id = 'sensor.casio_abl_100we_steps_today'
    assert hass.states.get(entity_id).state == initial_state

    tomorrow = current + timedelta(days=1)
    with patch.object(sensors.dt_util, 'now', return_value=tomorrow):
        dispatch = hass.async_run_hass_job(HassJob(callbacks[0]), tomorrow)
        if dispatch is not None:
            await dispatch
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).state == rollover_state

    assert 'calls async_write_ha_state from a thread other than the event loop' not in caplog.text
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert cancellations[0].call_count == 1
