"""Opt-in real HA core runtime smoke, BLE boundary remains mocked."""
from unittest.mock import patch
from pytest_homeassistant_custom_component.common import MockConfigEntry
from homeassistant.core import HomeAssistant
import pytest

from custom_components.casio_abl100 import DOMAIN


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
