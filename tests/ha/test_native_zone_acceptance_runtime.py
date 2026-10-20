"""Finite affected-consumer acceptance, installed API methods and real HA."""
import asyncio
import gc
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aioesphomeapi.api_pb2 import (
    BluetoothDeviceRequest, BluetoothGATTGetServicesRequest,
    BluetoothGATTNotifyRequest, BluetoothGATTWriteDescriptorRequest,
    BluetoothGATTWriteRequest, BluetoothGATTNotifyDataResponse,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er, translation
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casio_abl100 import DOMAIN
from tests.ha.test_native_owner_priority_runtime import owner, Wire, clean, until, TARGET
from tests.ha.test_native_zone_runtime import KEYS
from tests.protocol.test_zone_transport import FRAMES, QUERIES
from tests.ha.test_zone_entities import OLD_KEYS, assert_zone_addition


@pytest.fixture
async def zone(owner):
    """Reuse real producer predicates from the accepted external bus."""
    wire = owner.wire
    wire.zone_frames = list(FRAMES)
    wire.missing = None
    wire.before_zone = None
    wire.after_zone = None
    wire.early = None
    wire.cleanup_frame = False
    original = wire.send_message
    def send(request):
        if isinstance(request, BluetoothGATTWriteRequest) and request.data in QUERIES:
            wire.calls.append(request)
            index = QUERIES.index(request.data)
            if wire.before_zone is not None:
                wire.before_zone(index)
            if wire.missing != index:
                wire.emit(BluetoothGATTNotifyDataResponse(address=request.address, handle=61, data=wire.zone_frames[index]))
            if wire.after_zone is not None:
                wire.after_zone(index)
            if wire.cancel_after_write:
                asyncio.current_task().cancel()
            wire.counts.append(asyncio.current_task().cancelling())
            if wire.write_error is not None:
                raise wire.write_error
        else:
            if isinstance(request, BluetoothDeviceRequest) and request.request_type == 1 and wire.cleanup_frame:
                wire.emit(BluetoothGATTNotifyDataResponse(address=request.address, handle=61, data=FRAMES[2]))
            original(request)
            if isinstance(request, (BluetoothGATTNotifyRequest, BluetoothGATTWriteDescriptorRequest)) and wire.early == type(request).__name__:
                wire.emit(BluetoothGATTNotifyDataResponse(address=request.address, handle=61, data=FRAMES[0]))
    wire.send_message = send
    yield owner


async def start(zone, service=True):
    task = asyncio.create_task(zone.hass.services.async_call('button', 'press', {'entity_id': 'button.casio_abl_100we_read_time_zones'}, blocking=True) if service else zone.session.read_time_zones())
    await until(lambda: zone.session.zone_phase == 'waiting' or task.done())
    assert not task.done()
    return task


@pytest.mark.asyncio
async def test_real_producer_filters_complete_group(zone):
    task = await start(zone)
    zone.wire.raw()
    await task
    await zone.hass.async_block_till_done()
    assert zone.session.zone_snapshot.home.standard_utc_offset_minutes == -330
    assert zone.session.zone_snapshot.secondary.standard_utc_offset_minutes == 345
    assert zone.session.zones_fresh
    clean(zone)


@pytest.mark.asyncio
@pytest.mark.parametrize('primary_kind', ['ordinary', 'caller_cancel'])
@pytest.mark.parametrize('cleanup', ['none', 'ordinary', 'backend_cancel'])
async def test_primary_fault_through_both_finalizers_and_real_gc(zone, primary_kind, cleanup, caplog):
    errors = []
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, ctx: errors.append(ctx))
    primary = RuntimeError('PRIVATE_PRIMARY')
    zone.wire.zone_frames[0] = b'\xff'
    zone.wire.write_error = primary
    zone.wire.cancel_after_write = primary_kind == 'caller_cancel'
    zone.wire.disconnect_error = {'none': None, 'ordinary': RuntimeError('PRIVATE_CLEANUP'), 'backend_cancel': asyncio.CancelledError('PRIVATE_CLEANUP')}[cleanup]
    try:
        task = await start(zone, service=False)
        zone.wire.raw()
        try:
            await task
        except asyncio.CancelledError:
            assert primary_kind == 'caller_cancel'
        except HomeAssistantError as error:
            assert primary_kind == 'ordinary' and error.__cause__ is primary
            assert error.translation_key == 'zone_read_failed'
            assert 'PRIVATE' not in str(error.args) + str(error.translation_placeholders)
        else:
            pytest.fail('Failed read committed')
        zone.wire.write_error = None
        primary.__traceback__ = None
        del primary, task
        await asyncio.sleep(0)
        gc.collect()
        await asyncio.sleep(0)
        assert not errors and 'PRIVATE' not in caplog.text
        assert zone.session.zone_snapshot is None and zone.session.zone_last_sync is None
        clean(zone)
    finally:
        loop.set_exception_handler(previous)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['ordinary', 'backend_cancel'])
async def test_cleanup_only_fault_never_commits(zone, fault):
    zone.wire.disconnect_error = RuntimeError('PRIVATE_CLEANUP') if fault == 'ordinary' else asyncio.CancelledError()
    task = await start(zone)
    zone.wire.raw()
    with pytest.raises(HomeAssistantError) as caught:
        await task
    assert caught.value.translation_key == 'zone_read_failed'
    assert zone.session.zone_snapshot is None
    clean(zone)


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['connect', 'notify', 'query0', 'query1', 'query2', 'cleanup'])
@pytest.mark.parametrize('replace', [False, True])
async def test_caller_cancel_and_replacement_at_every_affected_stage(zone, stage, replace):
    entered, release = asyncio.Event(), asyncio.Event()
    original = zone.wire.send_messages_await_response_complex
    async def exchange(requests, *args):
        request = requests[0]
        selected = ((stage == 'notify' and isinstance(request, BluetoothGATTNotifyRequest))
                    or (stage == 'cleanup' and isinstance(request, BluetoothDeviceRequest)))
        if selected:
            entered.set()
            await release.wait()
        return await original(requests, *args)
    zone.wire.send_messages_await_response_complex = exchange
    original_send = zone.wire.send_message
    def send(request):
        if stage == 'connect' and isinstance(request, BluetoothDeviceRequest) and request.request_type != 1:
            zone.wire.calls.append(request)
            entered.set()
            return
        return original_send(request)
    zone.wire.send_message = send
    if stage.startswith('query'):
        zone.wire.missing = int(stage[-1])
        zone.wire.before_zone = lambda index: entered.set() if index == zone.wire.missing else None
    task = await start(zone)
    zone.wire.raw()
    await asyncio.wait_for(entered.wait(), 2)
    retained = tuple(zone.wire.retained)
    replacement = Wire() if replace else None
    if replace:
        zone.client.wire = replacement
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert zone.session.zone_snapshot is None and zone.session.zone_last_sync is None
    clean(zone)
    if replace:
        assert not replacement.calls and not replacement.handlers
    for callback, types in retained:
        if BluetoothGATTNotifyDataResponse in types:
            callback(BluetoothGATTNotifyDataResponse(address=TARGET, handle=61, data=FRAMES[0]))
    assert zone.session.zone_snapshot is None


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['BluetoothGATTNotifyRequest', 'BluetoothGATTWriteDescriptorRequest', 'between', 'cleanup'])
async def test_early_between_and_cleanup_responses_invalidate_group(zone, boundary):
    if boundary in ('between', 'cleanup'):
        if boundary == 'cleanup':
            zone.wire.cleanup_frame = True
        else:
            def after(index):
                if index == 0:
                    asyncio.get_running_loop().call_soon(zone.wire.emit, BluetoothGATTNotifyDataResponse(address=TARGET, handle=61, data=FRAMES[1]))
            zone.wire.after_zone = after
    else:
        zone.wire.early = boundary
    task = await start(zone)
    zone.wire.raw()
    with pytest.raises(HomeAssistantError):
        await task
    assert zone.session.zone_snapshot is None
    clean(zone)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['name', 'address', 'source', 'nonnative', 'loaded'])
async def test_exact_validation_and_native_only_source_no_io(zone, fault):
    device = SimpleNamespace(address=zone.session.address, name='ABL-100WE', details={'source': '11:22:33:44:55:66'})
    if fault == 'name':
        device.name = 'Other'
    elif fault == 'address':
        device.address = '00:00:00:00:00:00'
    elif fault in ('source', 'nonnative'):
        device.details = {'source': '22:22:22:22:22:22'}
    else:
        zone.watch._async_set_state(zone.hass, ConfigEntryState.UNLOAD_IN_PROGRESS, None)
    scanner = SimpleNamespace(connector=SimpleNamespace(client=lambda: None)) if fault == 'nonnative' else None
    try:
        with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address', return_value=device), patch('homeassistant.components.bluetooth.async_scanner_by_source', return_value=scanner):
            with pytest.raises(HomeAssistantError):
                await zone.session.read_time_zones()
        assert not zone.wire.calls
        clean(zone)
    finally:
        if fault == 'loaded':
            zone.watch._async_set_state(zone.hass, ConfigEntryState.LOADED, None)


@pytest.mark.asyncio
@pytest.mark.parametrize('first', ['zones', 'alarms', 'steps'])
async def test_shared_mutex_registration_and_unload_joins_all_groups(zone, first):
    methods = {'zones': zone.session.read_time_zones, 'alarms': zone.session.read_alarms, 'steps': zone.session.read}
    active = asyncio.create_task(methods[first]())
    phase = {'zones': 'zone_phase', 'alarms': 'alarm_phase', 'steps': 'step_phase'}[first]
    await until(lambda: getattr(zone.session, phase) == 'waiting')
    queued = [asyncio.create_task(method()) for key, method in methods.items() if key != first]
    await until(lambda: len(zone.session.tasks) == 3)
    assert not zone.wire.calls and len(zone.wire.handlers) == 1
    assert await zone.hass.config_entries.async_unload(zone.watch.entry_id)
    for task in (active, *queued):
        with pytest.raises(asyncio.CancelledError):
            await task
    assert zone.session.zone_phase == 'closed' and zone.session.zone_wait_expires_at is None
    clean(zone)


@pytest.mark.asyncio
async def test_refused_unload_and_queued_cancel_preserve_zone_ready(zone):
    from custom_components.casio_abl100 import async_unload_entry
    active = await start(zone)
    expiry = zone.session.zone_wait_expires_at
    queued = asyncio.create_task(zone.session.read_time_zones())
    await until(lambda: len(zone.session.tasks) == 2)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    with patch.object(zone.hass.config_entries, 'async_unload_platforms', return_value=False):
        assert not await async_unload_entry(zone.hass, zone.watch)
    assert zone.session.zone_phase == 'waiting' and zone.session.zone_wait_expires_at == expiry
    zone.wire.raw()
    await active
    assert zone.session.zones_fresh
    clean(zone)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['ordinary', 'backend_cancel'])
async def test_zone_observer_and_scheduler_faults_cannot_reverse_commit(zone, fault, caplog):
    seen = []
    def broken():
        if zone.session.zones_fresh:
            if fault == 'backend_cancel':
                raise asyncio.CancelledError('PRIVATE_OBSERVER')
            raise RuntimeError('PRIVATE_OBSERVER')
    remove = zone.session.subscribe(broken)
    remove_ok = zone.session.subscribe(lambda: seen.append(zone.session.zone_snapshot) if zone.session.zones_fresh else None)
    task = await start(zone)
    zone.wire.raw()
    await task
    await zone.hass.async_block_till_done()
    assert zone.session.zones_fresh and seen
    snap, stamp = zone.session.zone_snapshot, zone.session.zone_last_sync
    class BrokenLoop:
        def call_soon(self, callback):
            if fault == 'backend_cancel':
                raise asyncio.CancelledError('PRIVATE_SCHEDULER')
            raise RuntimeError('PRIVATE_SCHEDULER')
    real = zone.session.hass
    from tests.ha.test_native_step_acceptance_runtime import LoopOwner
    zone.session.hass = LoopOwner(real, BrokenLoop())
    try:
        task = await start(zone, service=False)
        zone.wire.raw()
        await task
        assert zone.session.zones_fresh and zone.session.zone_last_sync >= stamp
        assert zone.session.zone_snapshot == snap
        assert not zone.session.alarm_delivery_handles
    finally:
        zone.session.hass = real
        remove()
        remove_ok()
    assert 'PRIVATE' not in caplog.text
    clean(zone)


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['en', 'es'])
async def test_real_ha_names_and_measured_ids(language, hass, enable_custom_integrations):
    hass.config.language = language
    address = 'AA:BB:CC:DD:EE:AB'
    entry = MockConfigEntry(domain=DOMAIN, title='Casio ABL-100WE', data={'address': address}, unique_id=address)
    entry.add_to_hass(hass)
    with patch('custom_components.casio_abl100.establish_connection') as connect:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        connect.assert_not_called()
    assert_zone_addition(hass, entry)
    entities = {e.unique_id.removeprefix(address + '_'): e for e in er.async_get(hass).entities.values() if e.config_entry_id == entry.entry_id}
    names = dict(zip(['read_time_zones', 'home_standard_utc_offset', 'secondary_standard_utc_offset', 'last_time_zone_read'], ['Read time zones', 'Home standard UTC offset', 'Secondary standard UTC offset', 'Last time zone read'] if language == 'en' else ['Leer zonas horarias', 'Desfase UTC estándar principal', 'Desfase UTC estándar secundario', 'Última lectura de zonas horarias']))
    expected = {
        'en': {
            'steps_today': 'sensor.casio_abl_100we_steps_today',
            'last_sync': 'sensor.casio_abl_100we_last_synchronization',
            'read_steps': 'button.casio_abl_100we_read_steps',
            'read_alarms': 'button.casio_abl_100we_read_alarms',
            'hourly_signal': 'binary_sensor.casio_abl_100we_hourly_time_signal',
            'last_alarm_read': 'sensor.casio_abl_100we_last_alarm_read',
            **{f'alarm_{n}': f'sensor.casio_abl_100we_alarm_{n}' for n in range(1, 6)},
            'read_time_zones': 'button.casio_abl_100we_read_time_zones',
            'home_standard_utc_offset': 'sensor.casio_abl_100we_home_standard_utc_offset',
            'secondary_standard_utc_offset': 'sensor.casio_abl_100we_secondary_standard_utc_offset',
            'last_time_zone_read': 'sensor.casio_abl_100we_last_time_zone_read',
        },
        'es': {
            'steps_today': 'sensor.casio_abl_100we_pasos_de_hoy',
            'last_sync': 'sensor.casio_abl_100we_ultima_sincronizacion',
            'read_steps': 'button.casio_abl_100we_leer_pasos',
            'read_alarms': 'button.casio_abl_100we_leer_alarmas',
            'hourly_signal': 'binary_sensor.casio_abl_100we_senal_horaria',
            'last_alarm_read': 'sensor.casio_abl_100we_ultima_lectura_de_alarmas',
            **{f'alarm_{n}': f'sensor.casio_abl_100we_alarma_{n}' for n in range(1, 6)},
            'read_time_zones': 'button.casio_abl_100we_leer_zonas_horarias',
            'home_standard_utc_offset': 'sensor.casio_abl_100we_desfase_utc_estandar_principal',
            'secondary_standard_utc_offset': 'sensor.casio_abl_100we_desfase_utc_estandar_secundario',
            'last_time_zone_read': 'sensor.casio_abl_100we_ultima_lectura_de_zonas_horarias',
        },
    }[language]
    from tests.ha.test_zone_entities import SYNC_KEYS, INTERACTIVE_KEYS
    from tests.ha.test_zone_entities import ALARM_EDIT_KEYS
    assert {key: e.entity_id for key, e in entities.items() if key not in SYNC_KEYS | INTERACTIVE_KEYS | ALARM_EDIT_KEYS | {'watch_connection'}} == expected
    assert entities.keys() == expected.keys() | SYNC_KEYS | INTERACTIVE_KEYS | ALARM_EDIT_KEYS | {'watch_connection'}
    for key, name in names.items():
        assert hass.states.get(entities[key].entity_id).attributes['friendly_name'] == 'Casio ABL-100WE ' + name
        assert entities[key].disabled_by is None
    assert len({e.device_id for e in entities.values()}) == 1
    catalog = await translation.async_get_translations(hass, language, 'exceptions', {DOMAIN})
    assert catalog[f'component.{DOMAIN}.exceptions.zone_read_failed.message'] == ('Unable to read complete time zone configuration' if language == 'en' else 'No se pudo leer la configuración completa de las zonas horarias')
    original = {key: (e.entity_id, e.unique_id, e.device_id) for key,e in entities.items()}
    reg = er.async_get(hass)
    reg.async_update_entity(entities['home_standard_utc_offset'].entity_id, new_entity_id='sensor.custom_zone', name='Custom offset')
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert reg.async_get('sensor.custom_zone').unique_id == address + '_home_standard_utc_offset'
    assert reg.async_get('sensor.custom_zone').name == 'Custom offset'
    reloaded = {e.unique_id.removeprefix(address + '_'): e for e in reg.entities.values() if e.config_entry_id == entry.entry_id}
    assert {key: (e.entity_id, e.unique_id, e.device_id) for key, e in reloaded.items() if key in OLD_KEYS} == {key: value for key, value in original.items() if key in OLD_KEYS}
    # Reconfigure the transport through HA's manager-owned flow, retaining the
    # customized new entity and every immutable registry identity.
    from homeassistant.config_entries import SOURCE_RECONFIGURE
    from homeassistant.data_entry_flow import FlowResultType
    before = {key: (e.entity_id, e.unique_id, e.device_id, e.name) for key, e in reloaded.items()}
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={'source': SOURCE_RECONFIGURE, 'entry_id': entry.entry_id})
    result = await hass.config_entries.flow.async_configure(flow['flow_id'], {'address': 'AA:BB:CC:DD:EE:AC'})
    assert result['type'] is FlowResultType.ABORT and result['reason'] == 'reconfigure_successful'
    await hass.async_block_till_done()
    assert entry.runtime_data.address == 'AA:BB:CC:DD:EE:AC'
    assert entry.runtime_data.identity == address
    after = {e.unique_id.removeprefix(address + '_'): (e.entity_id, e.unique_id, e.device_id, e.name) for e in reg.entities.values() if e.config_entry_id == entry.entry_id}
    assert after == before
    assert await hass.config_entries.async_unload(entry.entry_id)

@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['notify', 'query', 'cleanup'])
async def test_generation_replacement_without_caller_cancel_never_commits(zone, stage):
    entered, release = asyncio.Event(), asyncio.Event()
    original = zone.wire.send_messages_await_response_complex
    async def exchange(requests, *args):
        request = requests[0]
        if ((stage == 'notify' and isinstance(request, BluetoothGATTNotifyRequest))
                or (stage == 'cleanup' and isinstance(request, BluetoothDeviceRequest))):
            entered.set()
            await release.wait()
        return await original(requests, *args)
    zone.wire.send_messages_await_response_complex = exchange
    if stage == 'query':
        def swap(index):
            if index == 0:
                zone.client.wire = replacement
        zone.wire.before_zone = swap
    replacement = Wire()
    task = await start(zone)
    zone.wire.raw()
    if stage != 'query':
        await asyncio.wait_for(entered.wait(), 2)
        zone.client.wire = replacement
        release.set()
    with pytest.raises(HomeAssistantError):
        await task
    assert not replacement.calls and not replacement.handlers
    assert zone.session.zone_snapshot is None
    clean(zone)


@pytest.mark.asyncio
@pytest.mark.parametrize('other', ['alarms', 'steps'])
@pytest.mark.parametrize('zones_first', [False, True])
async def test_successful_lock_orders_keep_three_observations_independent(zone, other, zones_first):
    from tests.ha.test_native_alarm_runtime import FIRST, REST
    original = zone.wire.send_message
    def send(request):
        if isinstance(request, BluetoothGATTWriteRequest) and request.data in (b'\x15', b'\x16'):
            zone.wire.calls.append(request)
            zone.wire.emit(BluetoothGATTNotifyDataResponse(address=request.address, handle=61, data=FIRST if request.data == b'\x15' else REST))
        else:
            original(request)
    zone.wire.send_message = send
    other_method = zone.session.read_alarms if other == 'alarms' else zone.session.read
    other_phase = 'alarm_phase' if other == 'alarms' else 'step_phase'
    first_method = zone.session.read_time_zones if zones_first else other_method
    second_method = other_method if zones_first else zone.session.read_time_zones
    first_phase = 'zone_phase' if zones_first else other_phase
    second_phase = other_phase if zones_first else 'zone_phase'
    active = asyncio.create_task(first_method())
    await until(lambda: getattr(zone.session, first_phase) == 'waiting')
    queued = asyncio.create_task(second_method())
    await until(lambda: len(zone.session.tasks) == 2)
    assert not zone.wire.calls
    zone.wire.raw()
    await active
    await until(lambda: getattr(zone.session, second_phase) == 'waiting')
    first_stamp = zone.session.zone_last_sync if zones_first else (zone.session.alarm_last_sync if other == 'alarms' else zone.session.last_sync)
    assert first_stamp is not None
    zone.wire.raw()
    await queued
    assert zone.session.zones_fresh
    assert zone.session.alarms_fresh if other == 'alarms' else zone.session.fresh
    assert (zone.session.zone_last_sync if zones_first else (zone.session.alarm_last_sync if other == 'alarms' else zone.session.last_sync)) == first_stamp
    snapshot, stamp = zone.session.zone_snapshot, zone.session.zone_last_sync
    # Date rollover, including a failed stale step read, does not affect zones.
    if other == 'steps':
        from datetime import date
        zone.wire.day = date(2026, 10, 17)
        failed = asyncio.create_task(zone.session.read())
        await until(lambda: zone.session.step_phase == 'waiting')
        zone.wire.raw()
        with pytest.raises(HomeAssistantError) as caught:
            await failed
        assert caught.value.translation_key == 'stale_date'
    assert zone.session.zone_snapshot is snapshot and zone.session.zone_last_sync == stamp and zone.session.zones_fresh
    clean(zone)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['direct', 'child', 'lookalike'])
async def test_backend_cancel_and_framework_lookalike_are_sanitized(zone, fault, caplog):
    if fault == 'lookalike':
        zone.wire.write_error = HomeAssistantError('PRIVATE_ARGS', translation_domain=DOMAIN, translation_key='unavailable', translation_placeholders={'private': 'PRIVATE_VALUE'})
    elif fault == 'direct':
        zone.wire.write_error = asyncio.CancelledError('PRIVATE_CANCEL')
    else:
        original = zone.wire.send_messages_await_response_complex
        async def child_cancel(requests, *args):
            if isinstance(requests[0], BluetoothGATTNotifyRequest):
                async def child():
                    asyncio.current_task().cancel()
                    await asyncio.sleep(0)
                await asyncio.create_task(child())
            return await original(requests, *args)
        zone.wire.send_messages_await_response_complex = child_cancel
    task = await start(zone, service=False)
    zone.wire.raw()
    with pytest.raises(HomeAssistantError) as caught:
        await task
    assert caught.value.translation_key == 'zone_read_failed'
    assert not caught.value.translation_placeholders
    assert 'PRIVATE' not in str(caught.value.args) + str(caught.value) + caplog.text
    assert task.cancelling() == 0
    clean(zone)


@pytest.mark.asyncio
async def test_two_watches_one_api_connection_unload_a_leaves_b_response_owned(zone):
    from aioesphomeapi.api_pb2 import BluetoothLERawAdvertisementsResponse
    address_b = 'AA:BB:CC:DD:EE:01'
    target_b = int(address_b.replace(':', ''), 16)
    entry_b = MockConfigEntry(domain=DOMAIN, title='Watch B', data={'address': address_b}, unique_id=address_b)
    entry_b.add_to_hass(zone.hass)
    devices = {zone.session.address: SimpleNamespace(address=zone.session.address, name='ABL-100WE', details={'source': '11:22:33:44:55:66'}),
               address_b: SimpleNamespace(address=address_b, name='ABL-100WE', details={'source': '11:22:33:44:55:66'})}
    original = zone.wire.send_message
    pending = []
    def send(request):
        if isinstance(request, BluetoothGATTWriteRequest) and request.address == target_b:
            zone.wire.calls.append(request)
            pending.append(request)
        else:
            original(request)
    zone.wire.send_message = send
    def raw(target):
        msg = BluetoothLERawAdvertisementsResponse()
        msg.advertisements.add(address=target, address_type=1)
        zone.wire.emit(msg)
    zone.runtime.bluetooth_device.ble_connections_free = zone.runtime.bluetooth_device.ble_connections_limit = 2
    with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address', side_effect=lambda _h, address, **kw: devices[address]):
        assert await zone.hass.config_entries.async_setup(entry_b.entry_id)
        await zone.hass.async_block_till_done()
        b = entry_b.runtime_data
        a_task = await start(zone, service=False)
        b_task = asyncio.create_task(b.read_time_zones())
        await until(lambda: b.zone_phase == 'waiting')
        raw(target_b)
        await until(lambda: len(pending) == 1)
        assert zone.session.zone_phase == 'waiting'
        assert not any(r.address == TARGET for r in zone.wire.calls)
        assert await zone.hass.config_entries.async_unload(zone.watch.entry_id)
        with pytest.raises(asyncio.CancelledError):
            await a_task
        # A-address frames cannot satisfy the installed B address filters.
        zone.wire.emit(BluetoothGATTNotifyDataResponse(address=TARGET, handle=61, data=FRAMES[0]))
        await asyncio.sleep(0)
        assert not b_task.done() and b.zone_snapshot is None
        for index in range(3):
            await until(lambda: len(pending) == index + 1)
            assert pending[index].data == QUERIES[index]
            zone.wire.emit(BluetoothGATTNotifyDataResponse(address=target_b, handle=61, data=FRAMES[index]))
        await b_task
        await zone.hass.async_block_till_done()
        assert b.zones_fresh and zone.session.zone_snapshot is None
        sensor_b = next(e.entity_id for e in er.async_get(zone.hass).entities.values() if e.config_entry_id == entry_b.entry_id and e.unique_id == address_b + '_home_standard_utc_offset')
        assert zone.hass.states.get(sensor_b).state == '-330'
        assert await zone.hass.config_entries.async_unload(entry_b.entry_id)
    clean(zone)

@pytest.mark.asyncio
async def test_held_cleanup_response_prevents_commit(zone):
    entered, release = asyncio.Event(), asyncio.Event()
    original = zone.wire.send_messages_await_response_complex
    async def exchange(requests, *args):
        if isinstance(requests[0], BluetoothDeviceRequest):
            entered.set()
            await release.wait()
        return await original(requests, *args)
    zone.wire.send_messages_await_response_complex = exchange
    task = await start(zone)
    zone.wire.raw()
    await entered.wait()
    assert zone.session.zone_snapshot is None
    zone.wire.emit(BluetoothGATTNotifyDataResponse(address=TARGET, handle=61, data=FRAMES[2]))
    release.set()
    with pytest.raises(HomeAssistantError):
        await task
    assert zone.session.zone_snapshot is None
    clean(zone)


@pytest.mark.asyncio
@pytest.mark.parametrize('index', range(3))
async def test_frame_before_scheduled_public_query_entry_is_rejected(zone, index):
    from custom_components.casio_abl100 import zone_transport
    original = zone_transport._stage
    writes = 0
    async def scheduled(label, operation, timeout):
        nonlocal writes
        if label == 'write zone GET':
            current = writes
            writes += 1
            if current == index:
                zone.wire.emit(BluetoothGATTNotifyDataResponse(address=TARGET, handle=61, data=FRAMES[index]))
        return await original(label, operation, timeout)
    with patch.object(zone_transport, '_stage', scheduled):
        task = await start(zone)
        zone.wire.raw()
        with pytest.raises(HomeAssistantError):
            await task
    assert len([x for x in zone.wire.calls if isinstance(x, BluetoothGATTWriteRequest)]) == index
    clean(zone)


@pytest.mark.asyncio
async def test_unload_before_observer_delivery_and_populated_diagnostics_whitelist(zone):
    from custom_components.casio_abl100.diagnostics import async_get_config_entry_diagnostics
    from tests.ha.test_native_step_acceptance_runtime import LoopOwner
    queued, seen = [], []
    class HeldLoop:
        def call_soon(self, cb):
            handle = asyncio.get_running_loop().call_later(3600, cb)
            queued.append((handle, cb))
            return handle
    real = zone.session.hass
    zone.session.hass = LoopOwner(real, HeldLoop())
    zone.session.subscribe(lambda: seen.append(zone.session.zone_snapshot))
    task = await start(zone, service=False)
    zone.wire.raw()
    await task
    assert zone.session.zones_fresh and zone.session.zone_snapshot is not None
    assert not seen
    diagnostics = await async_get_config_entry_diagnostics(real, zone.watch)
    assert set(diagnostics) == {'model', 'has_successful_read', 'last_sync', 'record_date', 'fresh'}
    assert '-330' not in json.dumps(diagnostics) and 'zone' not in json.dumps(diagnostics)
    assert await real.config_entries.async_unload(zone.watch.entry_id)
    assert all(handle.cancelled() for handle, _ in queued)
    for _, callback in queued:
        callback()
    assert not seen and not zone.session.alarm_delivery_handles
    zone.session.hass = real
    clean(zone)

@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['success', 'ordinary', 'backend_cancel'])
async def test_repeated_cancel_during_finite_notify_acquisition_retains_remover(zone, fault):
    entered, release = asyncio.Event(), asyncio.Event()
    children = []
    original = zone.wire.send_messages_await_response_complex
    async def exchange(requests, *args):
        if isinstance(requests[0], BluetoothGATTNotifyRequest):
            children.append(asyncio.current_task())
            assert args[-1] == 10
            entered.set()
            await release.wait()
            if fault == 'ordinary':
                raise RuntimeError('PRIVATE_ACQUIRE')
            if fault == 'backend_cancel':
                raise asyncio.CancelledError('PRIVATE_ACQUIRE')
        return await original(requests, *args)
    zone.wire.send_messages_await_response_complex = exchange
    task = await start(zone)
    zone.wire.raw()
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert children and all(child.done() for child in children)
    assert zone.session.zone_snapshot is None
    clean(zone)


@pytest.mark.asyncio
async def test_preentry_cancel_registers_no_io_and_wins(zone):
    async def cancelled():
        asyncio.current_task().cancel()
        await zone.session.read_time_zones()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.create_task(cancelled())
    assert not zone.wire.calls
    clean(zone)


@pytest.mark.asyncio
async def test_native_zone_transport_owner_is_unchanged_shared_class(zone):
    from custom_components.casio_abl100.native_alarms import NativeRead, WAIT_SECONDS
    from custom_components.casio_abl100.native_zones import NativeZoneRead
    assert NativeZoneRead.__bases__ == (NativeRead,)
    assert WAIT_SECONDS == 360
    task = await start(zone)
    zone.wire.raw()
    await task
    expected = ['BluetoothDeviceRequest', 'BluetoothGATTGetServicesRequest', 'BluetoothGATTNotifyRequest', 'BluetoothGATTWriteDescriptorRequest', *(['BluetoothGATTWriteRequest'] * 3), 'BluetoothDeviceRequest']
    assert [type(call).__name__ for call in zone.wire.calls] == expected
    clean(zone)

@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['notify', 'cccd', 'query0', 'query1', 'query2', 'cleanup'])
@pytest.mark.parametrize('kind', ['ordinary', 'backend_cancel'])
async def test_external_faults_never_publish_partial_group(zone, fault, kind):
    original = zone.wire.send_messages_await_response_complex
    error = RuntimeError('PRIVATE_FAULT') if kind == 'ordinary' else asyncio.CancelledError('PRIVATE_FAULT')
    async def exchange(requests, *args):
        request = requests[0]
        if ((fault == 'notify' and isinstance(request, BluetoothGATTNotifyRequest))
                or (fault == 'cccd' and isinstance(request, BluetoothGATTWriteDescriptorRequest))
                or (fault == 'cleanup' and isinstance(request, BluetoothDeviceRequest))):
            raise error
        return await original(requests, *args)
    zone.wire.send_messages_await_response_complex = exchange
    if fault.startswith('query'):
        def before(index):
            if index == int(fault[-1]):
                raise error
        zone.wire.before_zone = before
    task = await start(zone, service=False)
    zone.wire.raw()
    with pytest.raises(HomeAssistantError) as caught:
        await task
    assert caught.value.translation_key == 'zone_read_failed'
    assert task.cancelling() == 0
    assert zone.session.zone_snapshot is None and zone.session.zone_last_sync is None
    clean(zone)


@pytest.mark.asyncio
@pytest.mark.parametrize('index', range(3))
@pytest.mark.parametrize('kind', ['short', 'wrong_index', 'duplicate', 'missing'])
async def test_real_service_rejects_each_bad_response(zone, index, kind):
    from custom_components.casio_abl100 import native_zones
    original = native_zones.read_zone_settings
    async def short(adapter, timeout):
        return await original(adapter, timeout=.02)
    if kind == 'short':
        zone.wire.zone_frames[index] = FRAMES[index][:-1]
    elif kind == 'wrong_index':
        zone.wire.zone_frames[index] = FRAMES[(index + 1) % 3]
    elif kind == 'missing':
        zone.wire.missing = index
    else:
        def extra(i):
            if i == index:
                asyncio.get_running_loop().call_soon(zone.wire.emit, BluetoothGATTNotifyDataResponse(address=TARGET, handle=61, data=FRAMES[index]))
        zone.wire.after_zone = extra
    with patch.object(native_zones, 'read_zone_settings', short):
        task = await start(zone)
        zone.wire.raw()
        with pytest.raises(HomeAssistantError):
            await task
    assert zone.session.zone_snapshot is None and not zone.session.zones_fresh
    assert len([call for call in zone.wire.calls if isinstance(call, BluetoothGATTWriteRequest)]) == index + 1
    clean(zone)

@pytest.mark.asyncio
async def test_owner_cancel_becoming_backend_error_still_cancels(zone):
    entered = asyncio.Event()
    original = zone.wire.send_messages_await_response_complex
    async def exchange(requests, *args):
        if isinstance(requests[0], BluetoothGATTWriteDescriptorRequest):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise RuntimeError('PRIVATE_CONVERTED_CANCEL') from None
        return await original(requests, *args)
    zone.wire.send_messages_await_response_complex = exchange
    task = await start(zone, service=False)
    zone.wire.raw()
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert zone.session.zone_snapshot is None
    clean(zone)





