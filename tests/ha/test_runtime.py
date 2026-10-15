"""Opt-in real HA core runtime smoke, BLE boundary remains mocked."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
from homeassistant.exceptions import HomeAssistantError
from tests.protocol.test_alarm_transport import Client, FIRST, REMAINING, writes
from homeassistant.helpers import translation
from pytest_homeassistant_custom_component.common import MockConfigEntry
from homeassistant.core import HassJob, HomeAssistant
from homeassistant.util import dt as dt_util
import pytest

from custom_components.casio_abl100 import DOMAIN
from custom_components.casio_abl100.alarms import parse_alarms
from custom_components.casio_abl100 import sensor as sensors
from custom_components.casio_abl100.protocol import StepRecord
from tests.protocol.test_daily_history import history_record
from tests.protocol.test_transport import FakeClient
from custom_components.casio_abl100.transport import DRSP, START


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['en', 'es'])
async def test_step_history_real_service_keeps_ids_commands_failure_and_reload_boundaries(
    hass: HomeAssistant, enable_custom_integrations, language,
):
    """Runtime characterization of the test-first parser/entity increment."""
    from homeassistant.helpers import entity_registry as er
    hass.config.language = language
    address = 'AA:BB:CC:DD:EE:FF'
    entry = MockConfigEntry(domain=DOMAIN, title='Casio ABL-100WE',
                            data={'address': address}, source='user', unique_id=address)
    entry.add_to_hass(hass)
    raw = history_record()
    bad = bytearray(raw)
    bad[396] = 0xFA
    clients = [FakeClient([raw]), FakeClient([bytes(bad)])]
    device = SimpleNamespace(address=address, name='CASIO ABL-100WE\0')
    with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address',
               return_value=device), patch('custom_components.casio_abl100.establish_connection',
               side_effect=clients) as connect, patch.object(
                   sensors.dt_util, 'now', return_value=datetime(2026, 8, 1)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        registry = er.async_get(hass)
        def entity_ids():
            return {entity.unique_id.removeprefix(address + '_'): entity.entity_id
                    for entity in registry.entities.values()
                    if entity.config_entry_id == entry.entry_id}
        ids = entity_ids()
        assert len(ids) == 11
        connect.assert_not_called()
        step_id = ids['steps_today']
        assert hass.states.get(step_id).state == 'unavailable'
        assert 'daily_history' not in hass.states.get(step_id).attributes
        await hass.services.async_call('button', 'press',
                                       {'entity_id': ids['read_steps']}, blocking=True)
        await hass.async_block_till_done()
        state = hass.states.get(step_id)
        expected = {
            '2026-07-31': 1234, '2026-07-30': 0, '2026-07-29': None,
            '2026-07-28': 7654, '2026-07-27': 42, '2026-07-26': 99999999,
            '2026-07-25': 1,
        }
        assert state.state == '1485' and state.attributes['fresh'] is True
        assert 'daily_history' in state.attributes
        assert state.attributes['daily_history'] == expected
        stamp = hass.states.get(ids['last_sync']).state
        assert stamp not in ('unknown', 'unavailable')
        assert hass.states.get(ids['last_alarm_read']).state == 'unavailable'
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call('button', 'press',
                                           {'entity_id': ids['read_steps']}, blocking=True)
        await hass.async_block_till_done()
        state = hass.states.get(step_id)
        assert state.state == '1485' and state.attributes['fresh'] is False
        assert state.attributes['daily_history'] == expected
        assert hass.states.get(ids['last_sync']).state == stamp
        assert connect.call_count == 2
        assert all(client.writes == [(DRSP, START)] for client in clients)
        assert all(client.disconnected for client in clients)
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert entity_ids() == ids and connect.call_count == 2
        assert hass.states.get(step_id).state == 'unavailable'
        assert 'daily_history' not in hass.states.get(step_id).attributes
        assert await hass.config_entries.async_unload(entry.entry_id)


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
async def test_core_entry_setup_creates_eleven_entities_without_a_ble_read(hass: HomeAssistant, enable_custom_integrations):
    entry = MockConfigEntry(domain=DOMAIN, title='Casio ABL-100WE', data={'address': 'AA:BB:CC:DD:EE:FF'}, source='user', options={}, unique_id='AA:BB:CC:DD:EE:FF')
    entry.add_to_hass(hass)
    with patch('custom_components.casio_abl100.establish_connection') as connect:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        connect.assert_not_called()
    states = [state for state in hass.states.async_all() if state.domain in ('sensor', 'button', 'binary_sensor') and 'casio' in state.entity_id]
    assert len(states) == 11
    assert {state.entity_id for state in states} == {
        'sensor.casio_abl_100we_steps_today',
        'sensor.casio_abl_100we_last_synchronization',
        'button.casio_abl_100we_read_steps',
        *(f'sensor.casio_abl_100we_alarm_{n}' for n in range(1, 6)),
        'binary_sensor.casio_abl_100we_hourly_time_signal',
        'sensor.casio_abl_100we_last_alarm_read',
        'button.casio_abl_100we_read_alarms',
    }
    assert all(hass.states.get(f'sensor.casio_abl_100we_alarm_{n}').state == 'unavailable' for n in range(1, 6))
    assert hass.states.get('binary_sensor.casio_abl_100we_hourly_time_signal').state == 'unavailable'
    assert hass.states.get('sensor.casio_abl_100we_last_alarm_read').state == 'unavailable'
    from homeassistant.helpers import device_registry as dr, entity_registry as er
    registry = dr.async_get(hass)
    devices = [device for device in registry.devices if (DOMAIN, 'AA:BB:CC:DD:EE:FF') in device.identifiers]
    assert len(devices) == 1
    assert (devices[0].manufacturer, devices[0].model) == ('Casio', 'ABL-100WE')
    entities = [er.async_get(hass).async_get(state.entity_id) for state in states]
    assert all(entity.config_entry_id == entry.entry_id for entity in entities)
    assert len(entities) == 11
    assert len({entity.unique_id for entity in entities}) == 11
    assert {entity.unique_id for entity in entities} == {
        *(f'AA:BB:CC:DD:EE:FF_alarm_{n}' for n in range(1, 6)),
        *(f'AA:BB:CC:DD:EE:FF_{key}' for key in (
            'steps_today', 'last_sync', 'read_steps', 'hourly_signal',
            'last_alarm_read', 'read_alarms')),
    }
    assert {entity.device_id for entity in entities} == {devices[0].id}
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
    entry.runtime_data.alarm_snapshot = parse_alarms(FIRST, REMAINING)
    entry.runtime_data.alarm_last_sync = datetime(2026, 8, 1, tzinfo=timezone.utc)
    entry.runtime_data.alarms_fresh = True
    entry.runtime_data._notify()
    alarm_id = 'sensor.casio_abl_100we_alarm_1'
    assert hass.states.get(alarm_id).state == '23:59'
    entity_id = 'sensor.casio_abl_100we_steps_today'
    assert hass.states.get(entity_id).state == initial_state

    tomorrow = current + timedelta(days=1)
    with patch.object(sensors.dt_util, 'now', return_value=tomorrow):
        dispatch = hass.async_run_hass_job(HassJob(callbacks[0]), tomorrow)
        if dispatch is not None:
            await dispatch
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).state == rollover_state
        assert hass.states.get(alarm_id).state == '23:59'
        assert hass.states.get('sensor.casio_abl_100we_last_alarm_read').state == '2026-08-01T00:00:00+00:00'

    assert 'calls async_write_ha_state from a thread other than the event loop' not in caplog.text
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert cancellations[0].call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['malformed', 'connector'])
@pytest.mark.parametrize('language,alarm_label,signal_label,read_label', [
    ('en', 'Alarm 1', 'Hourly time signal', 'Read alarms'),
    ('es', 'Alarma 1', 'Señal horaria', 'Leer alarmas'),
])
async def test_real_service_read_failure_and_reload_keep_one_device_and_translated_names(
    hass: HomeAssistant, enable_custom_integrations, caplog,
    language, alarm_label, signal_label, read_label, failure,
):
    from homeassistant.helpers import device_registry as dr, entity_registry as er
    hass.config.language = language
    entry = MockConfigEntry(domain=DOMAIN, title='Casio ABL-100WE',
                            data={'address': 'AA:BB:CC:DD:EE:FF'}, source='user',
                            unique_id='AA:BB:CC:DD:EE:FF')
    entry.add_to_hass(hass)
    clients = [Client(), Client(remaining=REMAINING[:-1])]
    connection_results = [clients[0], clients[1] if failure == 'malformed'
                          else RuntimeError('SECRET_CONNECT_CANARY')]
    device = SimpleNamespace(address='AA:BB:CC:DD:EE:FF', name='ABL-100WE')
    with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address',
               return_value=device) as lookup, patch(
                   'custom_components.casio_abl100.establish_connection', side_effect=connection_results
               ) as connect:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        lookup.assert_not_called()
        connect.assert_not_called()
        alarm_id = ('sensor.casio_abl_100we_alarm_1' if language == 'en'
                    else 'sensor.casio_abl_100we_alarma_1')
        signal_id = ('binary_sensor.casio_abl_100we_hourly_time_signal' if language == 'en'
                     else 'binary_sensor.casio_abl_100we_senal_horaria')
        button_id = ('button.casio_abl_100we_read_alarms' if language == 'en'
                     else 'button.casio_abl_100we_leer_alarmas')
        assert hass.states.get(alarm_id).attributes['friendly_name'] == f'Casio ABL-100WE {alarm_label}'
        assert hass.states.get(signal_id).attributes['friendly_name'] == f'Casio ABL-100WE {signal_label}'
        assert hass.states.get(button_id).attributes['friendly_name'] == f'Casio ABL-100WE {read_label}'
        await hass.services.async_call('button', 'press', {'entity_id': button_id}, blocking=True)
        await hass.async_block_till_done()
        assert lookup.call_count == connect.call_count == 1
        assert writes(clients[0]) == [(b'\x15', False), (b'\x16', False)]
        ids = [state.entity_id for state in hass.states.async_all()
               if state.domain in ('sensor', 'binary_sensor', 'button') and 'casio' in state.entity_id]
        alarms = {er.async_get(hass).async_get(state_id).unique_id: hass.states.get(state_id)
                  for state_id in ids}
        for n in range(1, 6):
            label = f'Alarm {n}' if language == 'en' else f'Alarma {n}'
            assert alarms[f'AA:BB:CC:DD:EE:FF_alarm_{n}'].attributes['friendly_name'] == (
                f'Casio ABL-100WE {label}'
            )
        assert alarms['AA:BB:CC:DD:EE:FF_last_alarm_read'].attributes['friendly_name'] == (
            'Casio ABL-100WE Last alarm read' if language == 'en'
            else 'Casio ABL-100WE Última lectura de alarmas'
        )
        assert [alarms[f'AA:BB:CC:DD:EE:FF_alarm_{n}'].state for n in range(1, 6)] == [
            '23:59', '00:00', '09:30', '12:00', '16:59']
        assert [alarms[f'AA:BB:CC:DD:EE:FF_alarm_{n}'].attributes['enabled']
                for n in range(1, 6)] == [True, True, False, True, False]
        assert all(set(alarms[f'AA:BB:CC:DD:EE:FF_alarm_{n}'].attributes) ==
                   {'enabled', 'fresh', 'icon', 'friendly_name'} for n in range(1, 6))
        assert all(alarms[f'AA:BB:CC:DD:EE:FF_alarm_{n}'].attributes['fresh'] for n in range(1, 6))
        assert hass.states.get(signal_id).state == 'on'
        assert set(hass.states.get(signal_id).attributes) == {'fresh', 'icon', 'friendly_name'}
        assert hass.states.get(signal_id).attributes['fresh'] is True
        last = alarms['AA:BB:CC:DD:EE:FF_last_alarm_read'].state
        assert datetime.fromisoformat(last).tzinfo == timezone.utc
        assert len(ids) == 11
        assert all('Casio ABL-100WE Casio' not in state.attributes.get('friendly_name', '')
                   for state in (hass.states.get(state_id) for state_id in ids))
        assert alarms['AA:BB:CC:DD:EE:FF_steps_today'].state == 'unavailable'
        assert alarms['AA:BB:CC:DD:EE:FF_last_sync'].state == 'unavailable'
        with pytest.raises(HomeAssistantError) as exc:
            await hass.services.async_call('button', 'press', {'entity_id': button_id}, blocking=True)
        await hass.async_block_till_done()
        assert connect.call_count == 2
        assert 'SECRET' not in str(exc.value.args) + caplog.text
        assert 'AA:BB:CC:DD:EE:FF' not in str(exc.value.args) + caplog.text
        assert '23:59' not in caplog.text and '09:30' not in caplog.text
        assert exc.value.translation_key == 'alarm_read_failed'
        assert exc.value.translation_domain == DOMAIN
        # HA's exception __str__ is English by design; UI translation uses the
        # preserved domain/key and the selected language's exceptions catalog.
        assert str(exc.value) == 'Unable to read a complete alarm snapshot'
        catalog = await translation.async_get_translations(hass, language, 'exceptions', {DOMAIN})
        assert catalog[f'component.{DOMAIN}.exceptions.alarm_read_failed.message'] == (
            'Unable to read a complete alarm snapshot' if language == 'en'
            else 'No se pudo leer la configuración completa de las alarmas'
        )
        assert (exc.value.args == () or all('SECRET' not in str(arg) for arg in exc.value.args))
        assert [hass.states.get(alarms[f'AA:BB:CC:DD:EE:FF_alarm_{n}'].entity_id).state
                for n in range(1, 6)] == ['23:59', '00:00', '09:30', '12:00', '16:59']
        assert all(hass.states.get(alarms[f'AA:BB:CC:DD:EE:FF_alarm_{n}'].entity_id).attributes['fresh'] is False
                   for n in range(1, 6))
        assert [hass.states.get(alarms[f'AA:BB:CC:DD:EE:FF_alarm_{n}'].entity_id).attributes['enabled']
                for n in range(1, 6)] == [True, True, False, True, False]
        assert hass.states.get(signal_id).state == 'on'
        assert hass.states.get(signal_id).attributes['fresh'] is False
        assert hass.states.get(alarms['AA:BB:CC:DD:EE:FF_last_alarm_read'].entity_id).state == last
        session = entry.runtime_data
        assert len(session.listeners) == 9
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert session.closed and not session.listeners and not session.alarm_delivery_handles
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert lookup.call_count == 2
        assert connect.call_count == 2
        ids_after = {state.entity_id for state in hass.states.async_all()
                     if state.domain in ('sensor', 'binary_sensor', 'button') and 'casio' in state.entity_id}
        assert ids_after == set(ids)
        assert hass.states.get(alarm_id).state == 'unavailable'
        assert hass.states.get(signal_id).state == 'unavailable'
        assert hass.states.get(alarms['AA:BB:CC:DD:EE:FF_last_alarm_read'].entity_id).state == 'unavailable'
        assert len({device.id for device in dr.async_get(hass).devices
                    if (DOMAIN, 'AA:BB:CC:DD:EE:FF') in device.identifiers}) == 1
        assert len([entity for entity in er.async_get(hass).entities.values()
                    if entity.config_entry_id == entry.entry_id]) == 11
        assert len(entry.runtime_data.listeners) == 9
        reloaded_session = entry.runtime_data
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert not reloaded_session.listeners
