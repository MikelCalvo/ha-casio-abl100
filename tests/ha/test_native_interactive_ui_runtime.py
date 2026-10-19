"""Native HA UI composition: real services/formatter/core, synthetic wire only."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aioesphomeapi.api_pb2 import BluetoothDeviceRequest, BluetoothGATTWriteRequest
from homeassistant.components.button import DATA_COMPONENT
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er, translation
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casio_abl100 import DOMAIN
from tests.ha.test_native_alarm_runtime import native, spin_until, ADDRESS, TARGET, SOURCE
from tests.ha.test_unified_sync_runtime import unified, drive, assert_clean, GROUPS, QUERIES
from tests.ha.test_zone_entities import OLD_KEYS, ZONE_KEYS, SYNC_KEYS
from tests.ha.test_auto_unified_admission_runtime import producer

INTERACTIVE_KEYS = {'connect_interactive', 'read_interactive', 'close_interactive', 'interactive_connected'}
OLD_BUTTONS = ('read_steps', 'read_alarms', 'read_time_zones', 'read_all')
NAMES = {
    'en': {
        'connect_interactive': ('connect_watch', 'Connect watch'),
        'read_interactive': ('refresh_connected_watch', 'Refresh connected watch'),
        'close_interactive': ('close_watch_connection', 'Close watch connection'),
        'interactive_connected': ('interactive_connection', 'Interactive connection'),
    },
    'es': {
        'connect_interactive': ('conectar_reloj', 'Conectar reloj'),
        'read_interactive': ('actualizar_reloj_conectado', 'Actualizar reloj conectado'),
        'close_interactive': ('cerrar_conexion_del_reloj', 'Cerrar conexión del reloj'),
        'interactive_connected': ('conexion_interactiva', 'Conexión interactiva'),
    },
}


def entities(p, entry=None):
    entry = entry or p.watch
    return {e.unique_id.removeprefix(entry.unique_id + '_'): e
            for e in er.async_get(p.hass).entities.values() if e.config_entry_id == entry.entry_id}


def button(p, key):
    return p.hass.data[DATA_COMPONENT].get_entity(entities(p)[key].entity_id)


def status(p, entry=None):
    key = entities(p, entry).get('interactive_connected')
    assert key is not None, 'Missing native interactive link-status entity'
    state = p.hass.states.get(key.entity_id)
    assert state is not None
    # State.as_dict is HA's actual REST/state formatter, not a private projection.
    return state.as_dict()


async def press(p, key, entry=None):
    key = entities(p, entry).get(key)
    assert key is not None, 'Missing native interactive button'
    await p.hass.services.async_call('button', 'press', {'entity_id': key.entity_id}, blocking=True)


async def connect_ui(p):
    task = asyncio.create_task(press(p, 'connect_interactive'))
    await drive(task, p)
    await p.hass.async_block_till_done()
    return p.session.interactive_session


def pristine(projected):
    assert projected['state'] == 'unknown'
    attrs = projected['attributes']
    expected = dict(phase='idle', link_state='unknown', usable=False,
                    wait_expires_at=None, idle_expires_at=None, session_expires_at=None,
                    operation_result='never', group_results=dict.fromkeys(GROUPS, 'not_attempted'),
                    cleanup_result='not_attempted', close_reason=None)
    assert {k: attrs[k] for k in expected} == expected
    assert type(attrs['usable']) is bool


@pytest.mark.parametrize('language', ['en', 'es'])
async def test_exact_identity_translated_names_ids_and_initial_formatter(hass, enable_custom_integrations, language):
    hass.config.language = language
    watch = MockConfigEntry(domain=DOMAIN, title='Casio ABL-100WE', data={'address': ADDRESS}, unique_id=ADDRESS)
    watch.add_to_hass(hass)
    p = SimpleNamespace(hass=hass, watch=watch)
    with patch('custom_components.casio_abl100.establish_connection') as bleak:
        assert await hass.config_entries.async_setup(watch.entry_id)
        await hass.async_block_till_done()
        mapping = entities(p)
        assert mapping.keys() == OLD_KEYS | ZONE_KEYS | SYNC_KEYS | INTERACTIVE_KEYS, 'Exactly old nineteen plus native four'
        assert len(mapping) == 23 and len({e.device_id for e in mapping.values()}) == 1
        assert len({e.unique_id for e in mapping.values()}) == 23
        for key, (suffix, name) in NAMES[language].items():
            domain = 'binary_sensor' if key == 'interactive_connected' else 'button'
            assert mapping[key].entity_id == f'{domain}.casio_abl_100we_{suffix}'
            assert mapping[key].original_name == name
            assert hass.states.get(mapping[key].entity_id).attributes['friendly_name'] == 'Casio ABL-100WE ' + name
        pristine(status(p))
        assert button(p, 'connect_interactive').available
        assert not button(p, 'read_interactive').available
        assert not button(p, 'close_interactive').available
        bleak.assert_not_called()
        errors = await translation.async_get_translations(hass, language, 'exceptions', {DOMAIN})
        for action in ('connect', 'read', 'close'):
            text = errors[f'component.{DOMAIN}.exceptions.interactive_{action}_failed.message']
            assert text and '{' not in text
        assert await hass.config_entries.async_unload(watch.entry_id)


async def test_old_buttons_refuse_core_owner_before_new_ui_exists(unified):
    # Independent semantic RED: accepted core already retains the link, while
    # the old public buttons must cease admitting hidden lock-queued work.
    task = asyncio.create_task(unified.session.connect_interactive())
    await drive(task, unified)
    try:
        assert all(not button(unified, key).available for key in OLD_BUTTONS), 'Legacy buttons must refuse interactive ownership'
        before = list(unified.wire.calls)
        for key in OLD_BUTTONS:
            with pytest.raises(HomeAssistantError) as caught:
                await button(unified, key).async_press()
            assert caught.value.translation_key == 'interactive_busy'
        assert unified.wire.calls == before and unified.session.interactive_session.usable
    finally:
        await unified.session.close_interactive()


async def test_direct_legacy_admission_is_immediate_not_a_queued_read(unified):
    p = unified
    task = asyncio.create_task(p.session.connect_interactive())
    await drive(task, p)
    pending = p.hass.async_create_task(button(p, 'read_all').async_press(), eager_start=True)
    try:
        assert pending.done(), 'Direct legacy press must refuse before waiting for lifetime lock'
        with pytest.raises(HomeAssistantError) as caught:
            await pending
        assert caught.value.translation_key == 'interactive_busy'
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await p.session.close_interactive()


async def test_real_services_connect_two_distinct_refreshes_close(unified):
    p, w = unified, unified.wire
    c = await connect_ui(p)
    assert status(p)['state'] == 'on' and status(p)['attributes']['usable'] is True
    for key in ('idle_expires_at', 'session_expires_at'):
        assert datetime.fromisoformat(status(p)['attributes'][key]).tzinfo == timezone.utc
    assert status(p)['attributes']['wait_expires_at'] is None
    assert not any(isinstance(x, BluetoothGATTWriteRequest) for x in w.calls)
    assert not button(p, 'connect_interactive').available and button(p, 'read_interactive').available
    await press(p, 'read_interactive')
    await p.hass.async_block_till_done()
    first, stamp = p.session.alarm_snapshot, p.session.alarm_last_sync
    assert p.hass.states.get('sensor.casio_abl_100we_alarm_1').state == '07:35'
    w.frames[b'\x15'] = b'\x15\x40\0\x06\x22'
    w.hold_at = 'response'
    task = asyncio.create_task(press(p, 'read_interactive'))
    await spin_until(w.entered.is_set)
    assert p.session.alarm_snapshot is first and not p.session.alarms_fresh
    assert c.active and not any(isinstance(x, BluetoothDeviceRequest) and x.request_type == 1 for x in w.calls)
    assert button(p, 'close_interactive').available and not button(p, 'read_interactive').available
    w.hold_at = None
    w.notify(TARGET, 61, w.frames[b'\x15'])
    await task
    await p.hass.async_block_till_done()
    assert p.session.alarm_snapshot != first and p.session.alarm_last_sync > stamp
    assert p.hass.states.get('sensor.casio_abl_100we_alarm_1').state == '06:34'
    assert [bytes(x.data) for x in w.calls if isinstance(x, BluetoothGATTWriteRequest)] == list(QUERIES) * 2
    assert status(p)['attributes']['group_results'] == dict.fromkeys(GROUPS, 'success')
    await press(p, 'close_interactive')
    await p.hass.async_block_till_done()
    assert status(p)['state'] == 'off' and status(p)['attributes']['link_state'] == 'disconnected'
    assert status(p)['attributes']['cleanup_result'] == 'success'
    assert c.root.done() and c.maintenance_handle is None
    assert len([x for x in w.calls if isinstance(x, BluetoothDeviceRequest) and x.request_type != 1]) == 1
    assert len([x for x in w.calls if isinstance(x, BluetoothDeviceRequest) and x.request_type == 1]) == 1
    assert_clean(p)


async def test_preroot_reservation_guard_direct_press_and_close(unified):
    from custom_components.casio_abl100.native_interactive import NativeInteractiveSession
    p, s = unified, unified.session
    c = NativeInteractiveSession(s)
    s.interactive_session = c
    c.start()  # eager_start=False: core reservation exists, lock is not acquired.
    assert not s.lock.locked() and not c.root.done()
    assert button(p, 'close_interactive').available
    assert not button(p, 'connect_interactive').available
    before = list(p.wire.calls)
    for key in OLD_BUTTONS:
        assert not button(p, key).available
        with pytest.raises(HomeAssistantError) as caught:
            await button(p, key).async_press()
        assert caught.value.translation_key == 'interactive_busy'
    await button(p, 'close_interactive').async_press()
    assert c.root.done() and p.wire.calls == before
    assert_clean(p)


@pytest.mark.parametrize('stage', ['notify61', 'response', 'disconnect'])
async def test_held_work_close_available_joins_and_never_publishes_false_off(unified, stage):
    p, w = unified, unified.wire
    c = await connect_ui(p)
    w.hold_at = stage
    pending = asyncio.create_task(press(p, 'close_interactive' if stage == 'disconnect' else 'read_interactive'))
    await spin_until(w.entered.is_set)
    await spin_until(lambda: status(p)['attributes']['phase'] == ('closing' if stage == 'disconnect' else 'reading'))
    assert button(p, 'close_interactive').available
    assert all(not button(p, key).available for key in OLD_BUTTONS)
    if stage == 'response':
        w.hold_at = 'disconnect'
        w.entered.clear()
    closer = asyncio.create_task(press(p, 'close_interactive'))
    await spin_until(lambda: status(p)['attributes']['phase'] == 'closing')
    assert not closer.done() and not c.usable
    assert status(p)['state'] == 'on' and status(p)['attributes']['usable'] is False
    before = list(w.calls)
    for key in OLD_BUTTONS:
        with pytest.raises(HomeAssistantError):
            await button(p, key).async_press()
    assert w.calls == before
    w.release.set()
    await closer
    if stage == 'disconnect':
        await pending
    else:
        with pytest.raises(HomeAssistantError) as caught:
            await pending
        assert caught.value.translation_key == 'interactive_read_failed'
    await p.hass.async_block_till_done()
    assert c.root.done() and status(p)['state'] == 'off'
    assert_clean(p)


async def test_cleanup_failure_unknown_visible_and_safe(unified, caplog):
    p = unified
    c = await connect_ui(p)
    p.wire.failure_stage = 'disconnect'
    with pytest.raises(HomeAssistantError) as caught:
        await press(p, 'close_interactive')
    assert caught.value.translation_key == 'interactive_close_failed'
    assert caught.value.translation_placeholders is None
    assert 'PRIVATE' not in str(caught.value) + str(caught.value.args) + caplog.text
    await p.hass.async_block_till_done()
    projected = status(p)
    assert projected['state'] == 'unknown'
    assert projected['attributes']['link_state'] == 'unknown'
    assert projected['attributes']['cleanup_result'] == 'failed'
    assert projected['attributes']['close_reason'] == 'explicit_close'
    assert projected['attributes']['usable'] is False and c.root.done()
    for key in ('wait_expires_at', 'idle_expires_at', 'session_expires_at'):
        assert key in projected['attributes'] and projected['attributes'][key] is None
    assert 'PRIVATE' not in str(projected)
    assert_clean(p)


@pytest.mark.parametrize('action', ['connect', 'read', 'close'])
async def test_external_lookalike_errors_are_per_action_safe(unified, action, caplog):
    p = unified
    if action != 'connect':
        await connect_ui(p)
    p.wire.failure_stage = {'connect': 'services', 'read': 'get13', 'close': 'disconnect'}[action]
    p.wire.failure = HomeAssistantError('PRIVATE_ARGS', translation_domain=DOMAIN,
        translation_key='interactive_busy', translation_placeholders={'private': 'PRIVATE_VALUE'})
    with pytest.raises(HomeAssistantError) as caught:
        if action == 'connect':
            task = asyncio.create_task(press(p, 'connect_interactive'))
            await drive(task, p)
        else:
            await press(p, action + '_interactive')
    assert caught.value.translation_key == 'interactive_' + action + '_failed'
    assert not caught.value.translation_placeholders
    assert 'PRIVATE' not in str(caught.value) + str(caught.value.args) + caplog.text
    await p.hass.async_block_till_done()
    assert 'PRIVATE' not in str(status(p))
    assert_clean(p)


async def test_admission_outcomes_are_local_and_caller_cancellation_preserved(unified):
    p = unified
    with pytest.raises(HomeAssistantError) as caught:
        await button(p, 'read_interactive').async_press()
    assert caught.value.translation_key == 'interactive_not_connected'
    c = await connect_ui(p)
    with pytest.raises(HomeAssistantError) as caught:
        await button(p, 'connect_interactive').async_press()
    assert caught.value.translation_key == 'interactive_busy' and c.usable
    p.wire.hold_at = 'notify61'
    task = asyncio.create_task(press(p, 'read_interactive'))
    await spin_until(p.wire.entered.is_set)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    p.wire.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert c.root.done() and c.close_reason == 'caller_cancelled'
    assert_clean(p)


async def test_unload_reload_options_keep_all_identities_and_reset_values(unified):
    p = unified
    old = dict(entities(p))
    registry = er.async_get(p.hass)
    registry.async_update_entity(old['read_interactive'].entity_id,
                                new_entity_id='button.my_watch_refresh', name='My refresh')
    ids = {e.unique_id: (e.entity_id, e.device_id) for e in entities(p).values()}
    c = await connect_ui(p)
    await press(p, 'read_interactive')
    original = p.session
    p.wire.hold_at = 'disconnect'
    reload = asyncio.create_task(p.hass.config_entries.async_reload(p.watch.entry_id))
    await spin_until(p.wire.entered.is_set)
    assert not reload.done() and p.watch.runtime_data is original
    p.wire.release.set()
    assert await reload
    await p.hass.async_block_till_done()
    assert c.root.done() and original.closed and not original.listeners
    p.session = p.watch.runtime_data
    assert p.session is not original and p.session.alarm_snapshot is None
    assert {e.unique_id: (e.entity_id, e.device_id) for e in entities(p).values()} == ids
    assert p.hass.states.get('button.my_watch_refresh').attributes['friendly_name'] == 'My refresh'
    pristine(status(p))
    form = await p.hass.config_entries.options.async_init(p.watch.entry_id)
    result = await p.hass.config_entries.options.async_configure(form['flow_id'],
        {'auto_read_steps': False, 'auto_read_all': False})
    assert result['type'] is FlowResultType.CREATE_ENTRY
    await p.hass.async_block_till_done()
    p.session = p.watch.runtime_data
    assert {e.unique_id: (e.entity_id, e.device_id) for e in entities(p).values()} == ids
    pristine(status(p))
    assert_clean(p)


async def test_two_entries_status_isolation_and_captured_old_controller(unified):
    p = unified
    address = 'AA:BB:CC:DD:EE:01'
    other = MockConfigEntry(domain=DOMAIN, title='Other watch', data={'address': address}, unique_id=address)
    other.add_to_hass(p.hass)
    def lookup(_hass, address, **kwargs):
        return SimpleNamespace(address=address, name='ABL-100WE', details={'source': SOURCE})
    with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address', side_effect=lookup):
        assert await p.hass.config_entries.async_setup(other.entry_id)
        await p.hass.async_block_till_done()
        old = await connect_ui(p)
        pristine(status(p, other))
        t = asyncio.create_task(press(p, 'connect_interactive', other))
        for _ in range(1000):
            if t.done():
                break
            p.wire.raw(int(address.replace(':', ''), 16))
            await asyncio.sleep(0)
        await t
        await p.hass.async_block_till_done()
        second = other.runtime_data.interactive_session
        assert status(p, other)['state'] == 'on'
        await press(p, 'close_interactive')
        newer = await connect_ui(p)
        old.reader.observe_link(False, 0)
        old.reader.invalidate()
        await p.hass.async_block_till_done()
        assert newer.usable and status(p)['state'] == status(p, other)['state'] == 'on'
        assert await p.hass.config_entries.async_unload(other.entry_id)
        assert second.root.done() and newer.usable
        await press(p, 'close_interactive')
    assert_clean(p)


@pytest.mark.parametrize('mode', ['steps', 'all'])
async def test_automatic_busy_opportunity_consumed_not_deferred(producer, mode):
    from tests.ha.test_auto_runtime import advertisement
    from homeassistant.components.bluetooth import BluetoothChange
    from bluetooth_data_tools import monotonic_time_coarse
    p = producer
    # Real options flow, entry reload, automatic observer and real RAW ingress.
    form = await p.hass.config_entries.options.async_init(p.watch.entry_id)
    result = await p.hass.config_entries.options.async_configure(form['flow_id'],
        {'auto_read_steps': mode == 'steps', 'auto_read_all': mode == 'all'})
    assert result['type'] is FlowResultType.CREATE_ENTRY
    await p.hass.async_block_till_done()
    p.session = p.watch.runtime_data
    reader = p.session.auto_reader
    assert reader is not None and reader._ready
    # Connect's own packet is name-absent and cannot trigger the automatic mode.
    task = asyncio.create_task(press(p, 'connect_interactive'))
    for _ in range(1000):
        if task.done():
            break
        p.wire.raw(data=b'')
        await asyncio.sleep(0)
    await task
    now = monotonic_time_coarse()
    before = list(p.wire.calls)
    try:
        if mode == 'all':
            p.wire.raw()
        else:
            reader._advertisement(advertisement(now), BluetoothChange.ADVERTISEMENT)
        await asyncio.sleep(0)
        assert reader._claimed and reader._last_seen is not None
        assert reader._task is None and p.wire.calls == before
        await press(p, 'close_interactive')
        after = list(p.wire.calls)
        if mode == 'all':
            p.wire.raw()
        else:
            reader._advertisement(advertisement(now), BluetoothChange.ADVERTISEMENT)
        await asyncio.sleep(0)
        assert reader._task is None and p.wire.calls == after
    finally:
        await p.session.close_interactive()
        await reader.stop()
    # The fixture's scanner remains owned until its own teardown.
    assert not p.session.tasks and not p.session.lock.locked() and not p.api._notify_callbacks
    assert not p.runtime.disconnect_callbacks
    p.bleak.assert_not_called()


def test_context_free_safe_error_fallback():
    from custom_components.casio_abl100.button import _error
    error = _error('interactive_connect_failed')
    assert str(error) == 'Interactive watch action unavailable or failed'
    assert error.translation_domain == DOMAIN and not error.translation_placeholders


async def test_generation_loss_unknown_is_visible_through_real_formatter(unified):
    from tests.ha.test_unified_sync_runtime import SyncWire
    p = unified
    c = await connect_ui(p)
    old = p.wire
    replacement = SyncWire()
    p.api.wire = replacement
    try:
        # Existing passive core maintenance callback, not a UI state injection.
        c.maintenance_handle.cancel()
        c._tick()
        await asyncio.wait({c.root})
        await p.hass.async_block_till_done()
        projected = status(p)
        assert projected['state'] == 'unknown'
        assert projected['attributes']['link_state'] == 'unknown'
        assert projected['attributes']['cleanup_result'] == 'generation_lost'
        assert projected['attributes']['usable'] is False
        assert not replacement.calls and not replacement.handlers
    finally:
        p.api.wire = old
    assert_clean(p)
