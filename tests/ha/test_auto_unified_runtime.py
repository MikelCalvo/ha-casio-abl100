"""Real HA automatic unified consumer, single compiled RAW ingress per burst."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casio_abl100 import DOMAIN
from tests.ha.test_native_alarm_runtime import native, spin_until, SOURCE, TARGET
from tests.ha.test_unified_sync_runtime import unified, GROUPS, QUERIES
from tests.ha.test_auto_unified_admission_runtime import producer, ProducerWire
from aioesphomeapi.api_pb2 import (BluetoothLERawAdvertisementsResponse,
    BluetoothDeviceRequest, BluetoothGATTGetServicesRequest, BluetoothGATTWriteRequest)


async def select_mode(p, mode='all'):
    options = {'auto_read_steps': mode == 'steps', 'auto_read_all': mode == 'all',
               'auto_connect_retained': mode == 'retained'}
    with patch.object(p.hass.config_entries, 'async_reload', wraps=p.hass.config_entries.async_reload) as reload:
        form = await p.hass.config_entries.options.async_init(p.watch.entry_id)
        result = await p.hass.config_entries.options.async_configure(form['flow_id'], options)
        assert result['type'] is FlowResultType.CREATE_ENTRY
        await p.hass.async_block_till_done()
        reload.assert_called_once_with(p.watch.entry_id)
    p.session = p.watch.runtime_data
    reader = p.session.auto_reader
    assert (reader is not None) == (mode != 'off'), 'Explicit all-mode must own one automatic consumer'
    return reader


async def finished(p):
    await spin_until(lambda: p.session.auto_reader._task is None)


def keys(p):
    return {e.unique_id: (e.entity_id, e.device_id) for e in er.async_get(p.hass).entities.values()
            if e.config_entry_id == p.watch.entry_id}


@pytest.mark.parametrize('address_type', [0, 1])
@pytest.mark.parametrize('order', ['scanner_first', 'consumer_first'])
async def test_first_raw_packet_completes_all_groups_no_reemission(producer, address_type, order):
    p = producer
    ids = keys(p)
    # Cached BLEDevice type deliberately disagrees. Only the live RAW can authorize type.
    p.device.details['address_type'] = 1 - address_type
    reader = await select_mode(p)
    assert keys(p) == ids and len(ids) == 37
    scanner = p.scanner.async_on_raw_advertisements
    raw = next(cb for cb, types in p.wire.handlers
               if cb != scanner)
    # Both handlers run in the requested order after actual compiled decoding
    # on the SAME external connection used for all native request/reply traffic.
    reader._cancel_callback()
    reader._cancel_callback = None
    p.remove_scanner()
    seen_order = []
    def ordered(message):
        for label, cb in ((('scanner', scanner), ('consumer', raw)) if order == 'scanner_first'
                          else (('consumer', raw), ('scanner', scanner))):
            seen_order.append(label)
            cb(message)
    remove = p.wire.add_message_callback(ordered, (BluetoothLERawAdvertisementsResponse,))
    try:
        p.wire.raw(address_type=address_type, wrong_first=True)
        await finished(p)
        assert seen_order == (['scanner', 'consumer'] if order == 'scanner_first' else ['consumer', 'scanner'])
        assert p.wire.raw_count == 1
        connects = [x for x in p.wire.calls if p.wire.stage(x) == 'connect']
        assert len(connects) == 1, 'Initial RAW packet must connect without a second advertisement'
        assert connects[0].address_type == address_type
        assert sum(isinstance(x, BluetoothGATTGetServicesRequest) for x in p.wire.calls) == 1
        assert [bytes(x.data) for x in p.wire.calls if isinstance(x, BluetoothGATTWriteRequest)] == list(QUERIES)
        assert sum(p.wire.stage(x) == 'disconnect' for x in p.wire.calls) == 1
        assert p.session.sync_group_results == dict.fromkeys(GROUPS, 'success')
        assert p.session.sync_result == p.session.sync_cleanup_result == 'success'
        assert not p.session.tasks and not p.session.lock.locked() and not p.api._notify_callbacks
    finally:
        remove()


async def test_one_initial_raw_through_unmodified_shared_dispatcher(producer):
    p = producer
    p.hass.config_entries.async_update_entry(p.watch, options={'auto_read_all': True})
    await p.hass.async_block_till_done()
    p.session = p.watch.runtime_data
    reader = p.session.auto_reader
    assert reader is not None, 'Explicit all-mode must own one automatic consumer'
    p.wire.raw()
    for _ in range(100):
        await asyncio.sleep(0)
    assert len([x for x in p.wire.calls if p.wire.stage(x) == 'connect']) == 1, 'Initial RAW must connect without reemission'
    assert p.wire.raw_count == 1
    assert p.session.sync_result == 'success'


async def test_options_conflict_never_mutates_or_reloads(hass, enable_custom_integrations):
    entry = MockConfigEntry(domain=DOMAIN, data={'address': 'AA:BB:CC:DD:EE:FF'},
                            unique_id='AA:BB:CC:DD:EE:FF', options={'auto_read_all': True, 'keep': [1]})
    entry.add_to_hass(hass)
    with patch.object(hass.config_entries, 'async_reload') as reload:
        form = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(form['flow_id'],
            {'auto_read_steps': True})
        assert result['type'] is FlowResultType.FORM
        assert result['errors'] == {'base': 'automatic_modes_conflict'}
        assert entry.options == {'auto_read_all': True, 'keep': [1]} and entry.data == {'address': entry.unique_id}
        reload.assert_not_called()


@pytest.mark.parametrize('options', [
    {'auto_read_steps': True, 'auto_read_all': True}, {'auto_read_all': 1},
    {'auto_read_steps': 0}, {'auto_read_all': 'true'}, {'auto_read_all': None}])
async def test_invalid_stored_modes_fail_before_forwarding(hass, enable_custom_integrations, options):
    entry = MockConfigEntry(domain=DOMAIN, data={'address': 'AA:BB:CC:DD:EE:FF'},
                            unique_id='AA:BB:CC:DD:EE:FF', options=options)
    entry.add_to_hass(hass)
    with patch.object(hass.config_entries, 'async_forward_entry_setups') as forward:
        assert await hass.config_entries.async_setup(entry.entry_id) is False
        forward.assert_not_called()
        assert entry.runtime_data.closed and entry.runtime_data.auto_reader is None
        assert not entry.update_listeners
        assert entry.state is ConfigEntryState.SETUP_ERROR


@pytest.mark.parametrize('bad', [2, 255])
async def test_unknown_raw_type_cannot_use_cached_device_type(producer, bad):
    p = producer
    await select_mode(p)
    p.wire.raw(address_type=bad)
    await p.hass.async_block_till_done()
    assert not p.wire.calls and p.session.sync_result == 'never'


@pytest.mark.parametrize('data', [b'', b'\x02\x01\x06', b'\xff\x09x', b'\x04\x09BAD'])
async def test_name_absent_or_malformed_raw_is_not_a_trigger(producer, data):
    p = producer
    await select_mode(p)
    p.wire.raw(data=data)
    await p.hass.async_block_till_done()
    assert not p.wire.calls


@pytest.mark.parametrize('block', ['lock', 'tasks', 'suspend', 'unloading', 'schedule'])
async def test_consumed_busy_burst_never_queues_or_retries(producer, block):
    p = producer
    reader = await select_mode(p)
    if block == 'lock': await p.session.lock.acquire()
    if block == 'tasks': p.session.tasks.add(object())
    if block == 'suspend': reader.suspend()
    if block == 'unloading': p.watch._async_set_state(p.hass, ConfigEntryState.UNLOAD_IN_PROGRESS, None)
    try:
        if block == 'schedule':
            with patch.object(type(p.watch), 'async_create_background_task', side_effect=RuntimeError('PRIVATE_SCHEDULE')):
                p.wire.raw()
        else:
            p.wire.raw()
        if block == 'lock': p.session.lock.release()
        if block == 'tasks': p.session.tasks.clear()
        if block == 'suspend': reader.resume()
        if block == 'unloading': p.watch._async_set_state(p.hass, ConfigEntryState.LOADED, None)
        p.wire.raw()
        reader._maintain_raw(None)
        await p.hass.async_block_till_done()
        assert not p.wire.calls and reader._claimed
    finally:
        p.watch._async_set_state(p.hass, ConfigEntryState.LOADED, None)


@pytest.mark.parametrize('change', ['unloaded', 'owner', 'generation', 'age'])
async def test_scheduled_task_rechecks_before_session_mutation(producer, change):
    p = producer
    reader = await select_mode(p)
    p.wire.raw()
    owner = p.watch.runtime_data
    if change == 'unloaded': p.watch._async_set_state(p.hass, ConfigEntryState.UNLOAD_IN_PROGRESS, None)
    if change == 'owner': p.watch.runtime_data = object()
    if change == 'generation': p.api.wire = ProducerWire()
    try:
        if change == 'age':
            with patch('custom_components.casio_abl100.auto_read.monotonic_time_coarse', return_value=reader._last_seen + 6):
                await finished(p)
        else:
            await finished(p)
        assert not p.wire.calls and not p.api.wire.calls
        assert p.session.sync_result == 'never'
    finally:
        p.api.wire = p.wire
        p.watch.runtime_data = owner
        p.watch._async_set_state(p.hass, ConfigEntryState.LOADED, None)


@pytest.mark.parametrize('failure', [False, True])
async def test_same_payload_fresh_later_burst_and_partial_retention(producer, failure):
    p = producer
    reader = await select_mode(p)
    if failure: p.wire.failure_stage = 'step'
    p.wire.raw()
    await finished(p)
    assert p.session.sync_result == ('partial' if failure else 'success')
    assert p.session.alarms_fresh and p.session.alarm_snapshot is not None
    calls = len(p.wire.calls)
    p.wire.raw()
    await p.hass.async_block_till_done()
    assert len(p.wire.calls) == calls
    now = reader._last_seen + 31
    with patch('custom_components.casio_abl100.auto_read.monotonic_time_coarse', return_value=now), patch(
        'custom_components.casio_abl100.native_sync.monotonic_time_coarse', return_value=now):
        p.wire.raw()
        await finished(p)
    assert len([x for x in p.wire.calls if p.wire.stage(x) == 'connect']) == 2


async def test_passive_rebind_keeps_source_claim_and_original_handle_ownership(producer):
    p = producer
    reader = await select_mode(p)
    reader.suspend()
    p.wire.raw()
    reader.resume()
    replacement = ProducerWire()
    p.api.wire = replacement
    reader._maintain_raw(None)
    assert len(p.wire.handlers) == 1  # Scanner only; old binding was removed.
    assert len(replacement.handlers) == 1
    assert not p.wire.calls and not replacement.calls
    replacement.raw()
    await finished(p)
    assert not replacement.calls  # Rebind is not a new burst or retry.
    await reader.stop()
    assert not replacement.handlers
    p.api.wire = p.wire


async def test_stop_joins_real_held_disconnect_under_repeated_caller_cancel(producer):
    p = producer
    reader = await select_mode(p)
    p.wire.hold_at = 'disconnect'
    p.wire.raw()
    await spin_until(p.wire.entered.is_set)
    owned = reader._task
    stop = asyncio.create_task(reader.stop())
    try:
        await asyncio.sleep(0)
        stop.cancel()
        await asyncio.sleep(0)
        stop.cancel()
        await asyncio.sleep(0)
        assert not stop.done() and not owned.done()
    finally:
        p.wire.release.set()
        await asyncio.gather(stop, return_exceptions=True)
    assert owned.done() and reader._task is None
    assert not p.session.tasks and not p.api._notify_callbacks


async def test_mode_transitions_single_reload_stable_identity_and_manual_off(producer):
    p = producer
    ids = keys(p)
    for mode in ('all', 'off', 'steps', 'all', 'steps', 'off'):
        old = p.session
        reader = await select_mode(p, mode)
        assert old.closed and not old.tasks
        assert keys(p) == ids
        assert not p.wire.calls
        if reader is not None: assert reader.mode == mode


@pytest.mark.parametrize('fault', ['raw', 'timer', 'disconnect'])
async def test_raw_setup_failure_unwinds_owned_resources(producer, fault, caplog):
    p = producer
    from custom_components.casio_abl100 import auto_read
    if fault == 'raw':
        ctx = patch.object(p.wire, 'add_message_callback', side_effect=RuntimeError('PRIVATE_SETUP'))
    elif fault == 'timer':
        ctx = patch.object(auto_read, 'async_track_time_interval', side_effect=RuntimeError('PRIVATE_SETUP'))
    else:
        class Refuse(set):
            def add(self, cb): raise RuntimeError('PRIVATE_SETUP')
        p.runtime.disconnect_callbacks = Refuse()
        ctx = patch.object(auto_read, 'async_track_time_interval', wraps=auto_read.async_track_time_interval)
    with ctx:
        p.hass.config_entries.async_update_entry(p.watch, options={'auto_read_all': True})
        await p.hass.async_block_till_done()
    p.session = p.watch.runtime_data
    assert p.watch.state is ConfigEntryState.SETUP_ERROR
    assert p.session.closed and not p.session.tasks
    assert not p.watch.update_listeners
    assert len(p.wire.handlers) == 1  # Original scanner only.
    assert not p.runtime.disconnect_callbacks and not p.wire.calls
    assert 'PRIVATE_SETUP' not in caplog.text

async def test_legacy_dispatch_also_requires_loaded_owner(producer):
    from tests.ha.test_auto_runtime import advertisement
    from habluetooth import get_manager
    from bluetooth_data_tools import monotonic_time_coarse
    p = producer
    reader = await select_mode(p, 'steps')
    with patch.object(p.session, 'read', wraps=p.session.read) as read:
        get_manager().scanner_adv_received(advertisement(monotonic_time_coarse()))
        p.watch._async_set_state(p.hass, ConfigEntryState.UNLOAD_IN_PROGRESS, None)
        try:
            await finished(p)
            read.assert_not_called()
            assert p.session.step_phase == 'idle' and p.session.record is None
            assert not p.wire.calls
        finally:
            p.watch._async_set_state(p.hass, ConfigEntryState.LOADED, None)


async def test_old_delivery_snapshot_cannot_borrow_rebound_generation(producer):
    p = producer
    reader = await select_mode(p)
    old_callback = next(cb for cb, types in p.wire.handlers
                        if cb != p.scanner.async_on_raw_advertisements)
    replacement = ProducerWire()
    p.api.wire = replacement
    reader._maintain_raw(None)
    # A copied producer callback can still be in delivery after removal. It must
    # never reinterpret its old packet as authority on the newly bound generation.
    old_ingress = ProducerWire()
    remove = old_ingress.add_message_callback(old_callback, (BluetoothLERawAdvertisementsResponse,))
    try:
        old_ingress.raw()
        await finished(p)
        assert not replacement.calls and p.session.sync_result == 'never'
    finally:
        remove()
        await reader.stop()
        p.api.wire = p.wire


async def test_disconnect_observation_revokes_queued_ticket_even_if_metadata_lags(producer):
    p = producer
    reader = await select_mode(p)
    p.wire.raw()
    for cb in tuple(p.runtime.disconnect_callbacks): cb()
    await finished(p)
    assert not p.wire.calls and p.session.sync_result == 'never'


async def test_two_automatic_watches_share_one_connection_and_unload_ownership(producer):
    from tests.ha.test_native_alarm_runtime import FIRST
    p = producer
    first = await select_mode(p)
    address = 'AA:BB:CC:DD:EE:01'
    other = MockConfigEntry(domain=DOMAIN, title='Other watch', data={'address': address},
                            unique_id=address, options={'auto_read_all': True})
    other.add_to_hass(p.hass)
    def lookup(_hass, address, **_kwargs):
        return SimpleNamespace(address=address, name='ABL-100WE', details={'source': SOURCE})
    with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address', side_effect=lookup):
        assert await p.hass.config_entries.async_setup(other.entry_id)
        await p.hass.async_block_till_done()
        second = other.runtime_data
        p.wire.hold_at = 'response'
        p.wire.raw()
        p.wire.raw(int(address.replace(':', ''), 16))
        try:
            await spin_until(lambda: len([x for x in p.wire.calls if isinstance(x, BluetoothGATTWriteRequest)]) == 2)
            assert p.session.alarm_snapshot is second.alarm_snapshot is None
            assert await p.hass.config_entries.async_unload(p.watch.entry_id)
            assert first._stopped and not first._task
            assert second.auto_reader._task is not None and p.wire.handlers
            target = int(address.replace(':', ''), 16)
            p.wire.hold_at = None
            p.wire.notify(TARGET, 61, FIRST)
            await asyncio.sleep(0)
            assert second.alarm_snapshot is None
            p.wire.notify(target, 61, FIRST)
            await spin_until(lambda: second.auto_reader._task is None)
            assert second.sync_result == 'success' and second.alarms_fresh
            assert p.session.alarm_snapshot is None
            assert await p.hass.config_entries.async_unload(other.entry_id)
            assert len(p.wire.handlers) == 1 and not p.runtime.disconnect_callbacks
        finally:
            if other.state is ConfigEntryState.LOADED:
                await p.hass.config_entries.async_unload(other.entry_id)


async def test_refused_unload_keeps_claim_and_source_observer(producer):
    from custom_components.casio_abl100 import async_unload_entry
    p = producer
    reader = await select_mode(p)
    entered, release = asyncio.Event(), asyncio.Event()
    async def refuse(*args):
        entered.set()
        await release.wait()
        return False
    with patch.object(p.hass.config_entries, 'async_unload_platforms', side_effect=refuse):
        unload = asyncio.create_task(async_unload_entry(p.hass, p.watch))
        await entered.wait()
        p.wire.raw()
        assert not p.wire.calls
        release.set()
        assert await unload is False
    assert not reader._stopped and reader._claimed and not reader._suspended
    p.wire.raw()
    await p.hass.async_block_till_done()
    assert not p.wire.calls and len(p.wire.handlers) == 2


async def test_unavailable_source_never_rebinds_to_another_proxy(producer):
    p = producer
    reader = await select_mode(p)
    p.runtime.available = False
    reader._maintain_raw(None)
    p.wire.raw()
    await p.hass.async_block_till_done()
    assert len(p.wire.handlers) == 1 and not p.wire.calls
    p.runtime.available = True
    reader._maintain_raw(None)
    assert len(p.wire.handlers) == 2 and not p.wire.calls
    p.wire.raw()
    await finished(p)
    assert p.session.sync_result == 'success'


async def test_generation_change_during_held_cleanup_never_uses_replacement(producer):
    p = producer
    reader = await select_mode(p)
    p.wire.hold_at = 'disconnect'
    p.wire.raw()
    await spin_until(p.wire.entered.is_set)
    replacement = ProducerWire()
    p.api.wire = replacement
    reader._maintain_raw(None)
    p.wire.release.set()
    try:
        await finished(p)
        assert not replacement.calls
        assert p.session.sync_result != 'success'
        assert p.session.alarms_fresh and p.session.timer_fresh
        assert not p.api._notify_callbacks
    finally:
        await reader.stop()
        p.api.wire = p.wire


async def test_metadata_maintenance_cannot_dispatch_and_no_ordinary_callback(producer):
    from tests.ha.test_auto_runtime import advertisement
    from habluetooth import get_manager
    from bluetooth_data_tools import monotonic_time_coarse
    p = producer
    with patch('custom_components.casio_abl100.auto_read.bluetooth.async_register_callback') as register:
        reader = await select_mode(p)
    register.assert_not_called()
    get_manager().scanner_adv_received(advertisement(monotonic_time_coarse()))
    reader._maintain_raw(None)
    await p.hass.async_block_till_done()
    assert not p.wire.calls


@pytest.mark.parametrize('option', ['auto_read_steps', 'auto_read_all', 'auto_connect_retained'])
async def test_malformed_automatic_options_block_reconfigure(hass, enable_custom_integrations, option):
    entry = MockConfigEntry(domain=DOMAIN, data={'address': 'AA:BB:CC:DD:EE:FF'},
                            unique_id='AA:BB:CC:DD:EE:FF', options={option: 0})
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={
        'source': 'reconfigure', 'entry_id': entry.entry_id})
    assert result['type'] is FlowResultType.ABORT and result['reason'] == 'auto_read_enabled'


async def test_old_disconnect_snapshot_cannot_detach_new_binding(producer):
    p = producer
    reader = await select_mode(p)
    old_disconnect = next(iter(p.runtime.disconnect_callbacks))
    replacement = ProducerWire()
    p.api.wire = replacement
    reader._maintain_raw(None)
    try:
        assert len(replacement.handlers) == 1
        old_disconnect()
        assert len(replacement.handlers) == 1, 'Removed old disconnect observer cannot detach new binding'
        replacement.raw()
        await finished(p)
        assert p.session.sync_result == 'success'
    finally:
        await reader.stop()
        p.api.wire = p.wire


async def test_older_public_resume_metadata_cannot_fault_raw_observer(producer):
    from bluetooth_data_tools import monotonic_time_coarse
    p = producer
    reader = await select_mode(p)
    now = monotonic_time_coarse() + 1
    cached = SimpleNamespace(address=p.session.address, connectable=True,
                             name='ABL-100WE', time=now - .1)
    with patch('custom_components.casio_abl100.auto_read.monotonic_time_coarse', return_value=now), patch(
        'custom_components.casio_abl100.auto_read.bluetooth.async_last_service_info', return_value=cached):
        reader.suspend()
        p.wire.raw()
        reader.resume()
    assert not reader._faulted, 'Older public scanner timestamp cannot invalidate live RAW time'
    assert reader._last_seen == now and reader._claimed
    replacement = ProducerWire()
    p.api.wire = replacement
    try:
        reader._maintain_raw(None)
        assert len(p.wire.handlers) == 1 and len(replacement.handlers) == 1
    finally:
        await reader.stop()
        p.api.wire = p.wire
