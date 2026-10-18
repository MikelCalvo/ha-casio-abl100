"""Native HA reconfiguration and registry identity; synthetic BLE only."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_RECONFIGURE, ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casio_abl100 import DOMAIN

OLD = 'AA:BB:CC:DD:EE:FF'
NEW = '11:22:33:44:55:A6'
OTHER = '22:33:44:55:66:77'
SUFFIXES = {'steps_today', 'last_sync', 'read_steps', 'last_alarm_read',
            'read_alarms', 'hourly_signal', *(f'alarm_{n}' for n in range(1, 6)),
            'read_time_zones', 'home_standard_utc_offset',
            'secondary_standard_utc_offset', 'last_time_zone_read',
            'read_all', 'time_format', 'automatic_time_adjustment', 'timer_encoded_duration'}


def entry_for(hass, address=OLD, identity: str | None=OLD, options=None, title='My Casio'):
    entry = MockConfigEntry(domain=DOMAIN, title=title, source='user',
                            data={'address': address, 'future_data': {'keep': [1]}},
                            unique_id=identity, options=options or {})
    entry.add_to_hass(hass)
    return entry


async def clean_loaded(hass):
    await hass.async_block_till_done()
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            assert await hass.config_entries.async_unload(entry.entry_id)


def entities(hass, entry):
    return {item.unique_id: (item.entity_id, item.device_id, item.id)
            for item in er.async_get(hass).entities.values()
            if item.config_entry_id == entry.entry_id}


@pytest.mark.parametrize('identity', [OLD, None])
async def test_setup_separates_transport_address_from_registry_identity(
    hass, enable_custom_integrations, identity,
):
    entry = entry_for(hass, address=NEW, identity=identity)
    with patch('custom_components.casio_abl100.establish_connection') as connect, patch(
        'custom_components.casio_abl100.bluetooth.async_ble_device_from_address'
    ) as lookup:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        expected_identity = identity or NEW
        assert set(entities(hass, entry)) == {f'{expected_identity}_{key}' for key in SUFFIXES}
        devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        assert len(devices) == 1
        assert devices[0].identifiers == {(DOMAIN, expected_identity)}
        assert entry.runtime_data.address == NEW
        connect.assert_not_called()
        lookup.assert_not_called()
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize('selected', [False, True])
@pytest.mark.parametrize('language', ['en', 'es'])
async def test_native_reconfigure_preserves_eleven_ids_device_and_manual_route(
    hass, enable_custom_integrations, selected, language,
):
    from custom_components.casio_abl100.config_flow import CasioConfigFlow
    from tests.protocol.test_alarm_transport import Client, writes
    hass.config.language = language
    entry = entry_for(hass, options={'auto_read_steps': False, 'future_option': {'keep': [2]}})
    other = entry_for(hass, address=OTHER, identity=OTHER, title='Other Casio')
    with patch('custom_components.casio_abl100.config_flow.bluetooth.async_discovered_service_info',
               return_value=[SimpleNamespace(address=NEW, name='CASIO ABL-100WE\0')]), patch(
        'custom_components.casio_abl100.establish_connection', return_value=Client()
    ) as connect, patch(
        'custom_components.casio_abl100.bluetooth.async_ble_device_from_address',
        return_value=SimpleNamespace(address=NEW, name='CASIO ABL-100WE'),
    ) as lookup:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert other.runtime_data.address == OTHER
        before = entities(hass, entry)
        other_before = entities(hass, other)
        old_session, other_session = entry.runtime_data, other.runtime_data
        before_devices = {item.id: item.identifiers for item in dr.async_get(hass).devices}
        metadata = (entry.entry_id, entry.unique_id, entry.title, dict(entry.options), entry.version, entry.minor_version)
        assert callable(getattr(CasioConfigFlow, 'async_step_reconfigure', None)), 'Native reconfigure step is missing'
        form = await hass.config_entries.flow.async_init(DOMAIN, context={
            'source': SOURCE_RECONFIGURE, 'entry_id': entry.entry_id,
        })
        assert form['type'] is FlowResultType.FORM and form['step_id'] == 'reconfigure'
        assert entry.supports_reconfigure
        assert entry.data['address'] == OLD  # Discovery alone never rebinds.
        connect.assert_not_called()
        lookup.assert_not_called()
        with patch.object(hass.config_entries, 'async_reload', wraps=hass.config_entries.async_reload) as reload_entry:
            result = await hass.config_entries.flow.async_configure(form['flow_id'], {
                'discovered_address' if selected else 'address': NEW if selected else NEW.lower(),
            })
            assert result['type'] is FlowResultType.ABORT
            assert result['reason'] == 'reconfigure_successful'
            await hass.async_block_till_done()
            reload_entry.assert_called_once_with(entry.entry_id)
        assert entry.data == {'address': NEW, 'future_data': {'keep': [1]}}
        assert (entry.entry_id, entry.unique_id, entry.title, dict(entry.options), entry.version, entry.minor_version) == metadata
        assert entry.runtime_data is not old_session and entry.runtime_data.address == NEW
        assert entry.runtime_data.auto_reader is None
        assert entities(hass, entry) == before and len(before) == 11 + 4 + 4
        assert entities(hass, other) == other_before and other.runtime_data is other_session
        assert {item.id: item.identifiers for item in dr.async_get(hass).devices} == before_devices
        assert old_session.closed and not old_session.listeners and not old_session.tasks
        assert not old_session.alarm_delivery_handles
        connect.assert_not_called()
        lookup.assert_not_called()
        button = before[f'{OLD}_read_alarms'][0]
        await hass.services.async_call('button', 'press', {'entity_id': button}, blocking=True)
        await hass.async_block_till_done()
        lookup.assert_called_once_with(hass, NEW, connectable=True)
        assert connect.call_count == 1
        assert writes(connect.return_value) == [(b'\x15', False), (b'\x16', False)]
        assert connect.return_value.disconnected
        assert hass.states.get(before[f'{OLD}_alarm_1'][0]).state == '23:59'
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert await hass.config_entries.async_unload(other.entry_id)


@pytest.mark.parametrize('source', ['user', SOURCE_RECONFIGURE])
@pytest.mark.parametrize('conflict_field', ['address', 'unique_id'])
async def test_both_flows_reject_another_entry_transport_or_stable_identity(
    hass, enable_custom_integrations, source, conflict_field,
):
    entry = entry_for(hass)
    other = entry_for(hass, address=NEW if conflict_field == 'address' else OTHER,
                      identity=NEW if conflict_field == 'unique_id' else OTHER)
    before = [(item.entry_id, dict(item.data), item.unique_id, dict(item.options)) for item in (entry, other)]
    context = {'source': source}
    if source == SOURCE_RECONFIGURE:
        context['entry_id'] = entry.entry_id
    with patch('custom_components.casio_abl100.config_flow.bluetooth.async_discovered_service_info',
               return_value=[]), patch('custom_components.casio_abl100.establish_connection') as connect:
        form = await hass.config_entries.flow.async_init(DOMAIN, context=context)
        result = await hass.config_entries.flow.async_configure(form['flow_id'], {'address': ' ' + NEW.lower() + ' '})
        await clean_loaded(hass)
        assert result['type'] is FlowResultType.ABORT
        assert result['reason'] == 'already_configured'
        assert [(item.entry_id, dict(item.data), item.unique_id, dict(item.options)) for item in (entry, other)] == before
        connect.assert_not_called()


@pytest.mark.parametrize('enabled_at_start', [False, True])
async def test_reconfigure_requires_auto_read_off_including_submission_race(
    hass, enable_custom_integrations, enabled_at_start,
):
    entry = entry_for(hass, options={'auto_read_steps': enabled_at_start, 'keep': 1})
    with patch('custom_components.casio_abl100.config_flow.bluetooth.async_discovered_service_info',
               return_value=[]), patch('custom_components.casio_abl100.establish_connection') as connect:
        result = await hass.config_entries.flow.async_init(DOMAIN, context={
            'source': SOURCE_RECONFIGURE, 'entry_id': entry.entry_id,
        })
        if not enabled_at_start:
            assert result['type'] is FlowResultType.FORM
            hass.config_entries.async_update_entry(entry, options={'auto_read_steps': True, 'keep': 1})
            result = await hass.config_entries.flow.async_configure(result['flow_id'], {'address': NEW})
        await clean_loaded(hass)
        assert result['type'] is FlowResultType.ABORT
        assert result['reason'] == 'auto_read_enabled'
        assert entry.data['address'] == OLD and entry.unique_id == OLD
        assert entry.options == {'auto_read_steps': True, 'keep': 1}
        connect.assert_not_called()


async def test_reconfigure_refuses_missing_stable_identity_without_migration(
    hass, enable_custom_integrations,
):
    entry = entry_for(hass, identity=None)
    with patch('custom_components.casio_abl100.config_flow.bluetooth.async_discovered_service_info', return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={
            'source': SOURCE_RECONFIGURE, 'entry_id': entry.entry_id,
        })
        assert result['type'] is FlowResultType.ABORT
        assert result['reason'] == 'missing_identity'
        assert entry.data['address'] == OLD and entry.unique_id is None


@pytest.mark.parametrize('bad', ['', 'AA:BB:CC:DD:EE:GG', 'AA:BB:CC:DD:EE:ﬀ', 'ＡＡ:BB:CC:DD:EE:FF', '11:22:33:44:55:66\nforged'])
async def test_reconfigure_invalid_typed_address_never_mutates_or_reloads(
    hass, enable_custom_integrations, bad,
):
    entry = entry_for(hass)
    with patch('custom_components.casio_abl100.config_flow.bluetooth.async_discovered_service_info',
               return_value=[]), patch.object(hass.config_entries, 'async_schedule_reload') as reload_entry:
        form = await hass.config_entries.flow.async_init(DOMAIN, context={
            'source': SOURCE_RECONFIGURE, 'entry_id': entry.entry_id,
        })
        result = await hass.config_entries.flow.async_configure(form['flow_id'], {'address': bad})
        assert result['type'] is FlowResultType.FORM
        assert result['errors'] == {'address': 'invalid_address'}
        assert entry.data == {'address': OLD, 'future_data': {'keep': [1]}}
        assert entry.unique_id == OLD
        reload_entry.assert_not_called()


@pytest.mark.parametrize('source', ['user', SOURCE_RECONFIGURE])
async def test_discovery_selection_must_be_an_exact_current_matching_watch(
    hass, enable_custom_integrations, source,
):
    entry = entry_for(hass)
    context = {'source': source}
    if source == SOURCE_RECONFIGURE:
        context['entry_id'] = entry.entry_id
    discovery = [SimpleNamespace(address=NEW, name='CASIO ABL-100WE'),
                 SimpleNamespace(address=OTHER, name='Unrelated')]
    with patch('custom_components.casio_abl100.config_flow.bluetooth.async_discovered_service_info', return_value=discovery):
        form = await hass.config_entries.flow.async_init(DOMAIN, context=context)
        # The original form offers NEW; its cached advertisement disappears
        # before submission. Never silently treat the stale selection as typed.
        discovery.clear()
        result = await hass.config_entries.flow.async_configure(form['flow_id'], {'discovered_address': NEW})
        await clean_loaded(hass)
        assert result['type'] is FlowResultType.FORM
        assert result['errors'] == {'discovered_address': 'invalid_selection'}
        assert entry.data['address'] == OLD


@pytest.mark.parametrize('language,title,success', [
    ('en', 'Change watch Bluetooth address', 'Bluetooth address updated. Existing entities and device are unchanged.'),
    ('es', 'Cambiar la dirección Bluetooth del reloj', 'Dirección Bluetooth actualizada. Se conservan las entidades y el dispositivo existentes.'),
])
async def test_reconfigure_ui_catalog_loaded_by_real_ha(
    hass, enable_custom_integrations, language, title, success,
):
    from homeassistant.helpers import translation
    entry = entry_for(hass)
    with patch('custom_components.casio_abl100.config_flow.bluetooth.async_discovered_service_info', return_value=[]):
        await hass.config_entries.flow.async_init(DOMAIN, context={
            'source': SOURCE_RECONFIGURE, 'entry_id': entry.entry_id,
        })
    strings = await translation.async_get_translations(hass, language, 'config', {DOMAIN})
    prefix = f'component.{DOMAIN}.config'
    assert strings.get(f'{prefix}.step.reconfigure.title') == title
    assert strings.get(f'{prefix}.abort.reconfigure_successful') == success
    description = strings[f'{prefix}.step.reconfigure.description']
    assert '{discovered}' in description
    assert ('same physical watch' in description if language == 'en' else 'mismo reloj físico' in description)
    assert ('does not connect' in description if language == 'en' else 'no se conecta' in description)
    assert strings[f'{prefix}.step.reconfigure.data.address']
    assert strings[f'{prefix}.step.reconfigure.data.discovered_address']
    for key in ('already_configured', 'missing_identity', 'auto_read_enabled'):
        assert strings[f'{prefix}.abort.{key}']
    assert strings[f'{prefix}.error.invalid_selection']


@pytest.mark.parametrize('target', [OLD, NEW])
async def test_loaded_reconfigure_can_retain_address_or_return_to_own_identity(
    hass, enable_custom_integrations, target,
):
    entry = entry_for(hass, address=NEW)
    with patch('custom_components.casio_abl100.config_flow.bluetooth.async_discovered_service_info', return_value=[]), patch(
        'custom_components.casio_abl100.establish_connection'
    ) as connect:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        before = entities(hass, entry)
        form = await hass.config_entries.flow.async_init(DOMAIN, context={
            'source': SOURCE_RECONFIGURE, 'entry_id': entry.entry_id,
        })
        result = await hass.config_entries.flow.async_configure(form['flow_id'], {'address': target})
        assert result['reason'] == 'reconfigure_successful'
        await hass.async_block_till_done()
        assert entry.data['address'] == target and entry.unique_id == OLD
        assert entities(hass, entry) == before
        assert not entry.options and entry.runtime_data.auto_reader is None
        connect.assert_not_called()
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unloaded_entry_uses_native_reload_once_and_keeps_options_absent(
    hass, enable_custom_integrations,
):
    entry = entry_for(hass)
    with patch('custom_components.casio_abl100.config_flow.bluetooth.async_discovered_service_info', return_value=[]), patch(
        'custom_components.casio_abl100.establish_connection'
    ) as connect, patch.object(hass.config_entries, 'async_reload', wraps=hass.config_entries.async_reload) as reload_entry:
        form = await hass.config_entries.flow.async_init(DOMAIN, context={
            'source': SOURCE_RECONFIGURE, 'entry_id': entry.entry_id,
        })
        result = await hass.config_entries.flow.async_configure(form['flow_id'], {'address': NEW.lower()})
        assert result['reason'] == 'reconfigure_successful'
        await hass.async_block_till_done()
        reload_entry.assert_called_once_with(entry.entry_id)
        assert entry.runtime_data.address == NEW and entry.unique_id == OLD
        assert entry.options == {} and entry.runtime_data.auto_reader is None
        assert len(entities(hass, entry)) == 11 + 4 + 4
        connect.assert_not_called()
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_conflict_created_after_form_open_is_rechecked_before_update(
    hass, enable_custom_integrations,
):
    entry = entry_for(hass)
    with patch('custom_components.casio_abl100.config_flow.bluetooth.async_discovered_service_info', return_value=[]):
        form = await hass.config_entries.flow.async_init(DOMAIN, context={
            'source': SOURCE_RECONFIGURE, 'entry_id': entry.entry_id,
        })
        other = entry_for(hass, address=NEW.lower(), identity=OTHER)
        result = await hass.config_entries.flow.async_configure(form['flow_id'], {'address': NEW})
        assert result['reason'] == 'already_configured'
        assert entry.data['address'] == OLD and entry.unique_id == OLD
        assert other.data['address'] == NEW.lower() and other.unique_id == OTHER


async def test_reconfigure_reload_cancels_owned_manual_read_before_new_session(
    hass, enable_custom_integrations,
):
    import asyncio
    from tests.protocol.test_alarm_transport import Client
    entry = entry_for(hass)
    client = Client()
    client.query_gate = asyncio.Event()
    writing = asyncio.Event()
    write = client.write_gatt_char

    async def held_write(*args, **kwargs):
        writing.set()
        await write(*args, **kwargs)

    client.write_gatt_char = held_write
    with patch('custom_components.casio_abl100.config_flow.bluetooth.async_discovered_service_info', return_value=[]), patch(
        'custom_components.casio_abl100.establish_connection', return_value=client
    ) as connect, patch(
        'custom_components.casio_abl100.bluetooth.async_ble_device_from_address',
        return_value=SimpleNamespace(address=OLD, name='CASIO ABL-100WE'),
    ) as lookup:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        before = entities(hass, entry)
        old_session = entry.runtime_data
        task = asyncio.create_task(hass.services.async_call('button', 'press', {
            'entity_id': before[f'{OLD}_read_alarms'][0],
        }, blocking=True))
        try:
            await asyncio.wait_for(writing.wait(), 2)
            assert old_session.tasks
            form = await hass.config_entries.flow.async_init(DOMAIN, context={
                'source': SOURCE_RECONFIGURE, 'entry_id': entry.entry_id,
            })
            result = await hass.config_entries.flow.async_configure(form['flow_id'], {'address': NEW})
            assert result['reason'] == 'reconfigure_successful'
            outcome = await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 2)
            await hass.async_block_till_done()
            assert isinstance(outcome[0], asyncio.CancelledError)
            assert client.disconnected
            assert old_session.closed and not old_session.tasks and not old_session.listeners
            assert not old_session.alarm_delivery_handles and old_session.alarm_snapshot is None
            assert entry.runtime_data.address == NEW and entry.runtime_data.auto_reader is None
            assert entry.runtime_data.alarm_snapshot is None
            assert entities(hass, entry) == before
            lookup.assert_called_once_with(hass, OLD, connectable=True)
            assert connect.call_count == 1  # Only the explicit old-address manual read.
        finally:
            client.query_gate.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await clean_loaded(hass)


async def test_reconfigured_auto_off_ignores_both_addresses_then_manual_steps_uses_new(
    hass, enable_custom_integrations, enable_bluetooth,
):
    from datetime import datetime, timedelta
    from bleak.backends.device import BLEDevice
    from bluetooth_data_tools import monotonic_time_coarse
    from habluetooth import get_manager
    from homeassistant.components import bluetooth
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed
    from tests.protocol.test_transport import FakeClient
    from custom_components.casio_abl100.transport import DRSP, START

    def advert(address, timestamp):
        return bluetooth.BluetoothServiceInfoBleak(
            name='CASIO ABL-100WE', address=address, rssi=-50,
            manufacturer_data={}, service_data={}, service_uuids=[],
            source='00:00:00:00:00:01', device=BLEDevice(address, 'CASIO ABL-100WE', {'path': '/synthetic/local'}),
            advertisement=None, connectable=True, time=timestamp, tx_power=None,
        )

    entry = entry_for(hass, options={'auto_read_steps': False})
    client = FakeClient()
    with patch('custom_components.casio_abl100.establish_connection', return_value=client) as connect, patch(
        'custom_components.casio_abl100.dt_util.now', return_value=datetime(2026, 8, 1)
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        before = entities(hass, entry)
        form = await hass.config_entries.flow.async_init(DOMAIN, context={
            'source': SOURCE_RECONFIGURE, 'entry_id': entry.entry_id,
        })
        result = await hass.config_entries.flow.async_configure(form['flow_id'], {'address': NEW})
        assert result['reason'] == 'reconfigure_successful'
        await hass.async_block_till_done()
        manager = get_manager()
        for delta in (0, 60):
            for address in (OLD, NEW):
                manager.scanner_adv_received(advert(address, monotonic_time_coarse() + delta))
            async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=delta + 2))
            await hass.async_block_till_done()
        connect.assert_not_called()
        assert entry.options['auto_read_steps'] is False and entry.runtime_data.auto_reader is None
        await hass.services.async_call('button', 'press', {
            'entity_id': before[f'{OLD}_read_steps'][0],
        }, blocking=True)
        await hass.async_block_till_done()
        assert connect.call_count == 1 and connect.call_args.args[1].address == NEW
        assert client.writes == [(DRSP, START)] and client.disconnected
        assert hass.states.get(before[f'{OLD}_steps_today'][0]).state == '1485'
        assert entities(hass, entry) == before
        assert await hass.config_entries.async_unload(entry.entry_id)


def _reservation_snapshot(hass):
    """Include native ignored entries, whose public producer stores no data."""
    return [(entry.entry_id, entry.unique_id, entry.title, dict(entry.data),
             dict(entry.options), entry.source, entry.disabled_by)
            for entry in hass.config_entries.async_entries(DOMAIN, include_ignore=True)]


@pytest.mark.parametrize('source', ['user', SOURCE_RECONFIGURE])
async def test_native_ignored_identity_is_reserved_without_replacing_entry(
    hass, enable_custom_integrations, source,
):
    from homeassistant.config_entries import SOURCE_IGNORE
    entry = entry_for(hass)
    with patch('custom_components.casio_abl100.config_flow.bluetooth.async_discovered_service_info',
               return_value=[]), patch('custom_components.casio_abl100.establish_connection') as connect:
        created = await hass.config_entries.flow.async_init(
            DOMAIN, context={'source': SOURCE_IGNORE},
            data={'unique_id': NEW, 'title': 'Ignored watch'},
        )
        assert created['type'] is FlowResultType.CREATE_ENTRY
        ignored = created['result']
        assert ignored.source == SOURCE_IGNORE and ignored.unique_id == NEW
        assert dict(ignored.data) == {}
        before = _reservation_snapshot(hass)
        context = {'source': source}
        if source == SOURCE_RECONFIGURE:
            context['entry_id'] = entry.entry_id
        form = await hass.config_entries.flow.async_init(DOMAIN, context=context)
        result = await hass.config_entries.flow.async_configure(form['flow_id'], {'address': NEW})
        await clean_loaded(hass)
        assert result['type'] is FlowResultType.ABORT
        assert result['reason'] == 'already_configured'
        assert _reservation_snapshot(hass) == before
        assert hass.config_entries.async_get_entry(ignored.entry_id) is ignored
        connect.assert_not_called()


@pytest.mark.parametrize('source', ['user', SOURCE_RECONFIGURE])
@pytest.mark.parametrize('field', ['address', 'identity'])
@pytest.mark.parametrize('ignored', [False, True])
async def test_ignored_and_disabled_entries_keep_both_address_reservations(
    hass, enable_custom_integrations, source, field, ignored,
):
    from homeassistant.config_entries import SOURCE_IGNORE, ConfigEntryDisabler
    entry = entry_for(hass)
    other = MockConfigEntry(
        domain=DOMAIN, title='Reserved other watch',
        source=SOURCE_IGNORE if ignored else 'user',
        data={'address': NEW if field == 'address' else OTHER, 'keep': [1]},
        unique_id=NEW if field == 'identity' else OTHER,
        disabled_by=None if ignored else ConfigEntryDisabler.USER,
    )
    other.add_to_hass(hass)
    before = _reservation_snapshot(hass)
    with patch('custom_components.casio_abl100.config_flow.bluetooth.async_discovered_service_info',
               return_value=[]), patch('custom_components.casio_abl100.establish_connection') as connect:
        context = {'source': source}
        if source == SOURCE_RECONFIGURE:
            context['entry_id'] = entry.entry_id
        form = await hass.config_entries.flow.async_init(DOMAIN, context=context)
        result = await hass.config_entries.flow.async_configure(form['flow_id'], {'address': NEW})
        await clean_loaded(hass)
        assert result['type'] is FlowResultType.ABORT
        assert result['reason'] == 'already_configured'
        assert _reservation_snapshot(hass) == before
        assert hass.config_entries.async_get_entry(other.entry_id) is other
        connect.assert_not_called()
