"""Automatic retained source tests: real HA/native API, synthetic wire only."""
import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aioesphomeapi.api_pb2 import BluetoothDeviceConnectionResponse, BluetoothGATTWriteRequest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.casio_abl100 import DOMAIN
from custom_components.casio_abl100.auto_read import AutoStepReader
from tests.ha.test_native_alarm_runtime import native, spin_until, TARGET, SOURCE
from tests.ha.test_unified_sync_runtime import unified, QUERIES, GROUPS
from tests.ha.test_auto_unified_admission_runtime import producer, ProducerWire, admission


@contextmanager
def clock(now):
    with patch('custom_components.casio_abl100.auto_read.monotonic_time_coarse', return_value=now), patch(
        'custom_components.casio_abl100.native_sync.monotonic_time_coarse', return_value=now):
        yield


def connects(p):
    return [x for x in p.wire.calls if p.wire.stage(x) == 'connect']


def queries(p):
    return [bytes(x.data) for x in p.wire.calls if isinstance(x, BluetoothGATTWriteRequest)]


async def enable(p):
    form = await p.hass.config_entries.options.async_init(p.watch.entry_id)
    result = await p.hass.config_entries.options.async_configure(form['flow_id'], {
        'auto_read_steps': False, 'auto_read_all': False, 'auto_connect_retained': True})
    assert result['type'] is FlowResultType.CREATE_ENTRY
    await p.hass.async_block_till_done()
    p.session = p.watch.runtime_data
    reader = p.session.auto_reader
    assert reader is not None and reader.mode == 'retained'
    return reader


async def initial(p, reader):
    p.wire.raw()
    await spin_until(lambda: reader._task is None)
    c = p.session.interactive_session
    assert c is not None and c.usable and c.operation_result == 'success'
    return c


async def test_retained_mode_dispatches_one_connect_and_initial_full_read(producer):
    """Direct real observer isolates dispatch semantics from option API absence."""
    p = producer
    reader = AutoStepReader(p.hass, p.watch, p.session, mode='retained')
    p.session.auto_reader = reader
    reader.start()
    try:
        p.wire.raw(address_type=0)
        await spin_until(lambda: reader._task is None or p.session.step_phase == 'waiting')
        assert len(connects(p)) == 1, 'Retained RAW must connect immediately without a second packet'
        assert connects(p)[0].address_type == 0
        assert queries(p) == list(QUERIES)
        c = p.session.interactive_session
        assert c.group_results == dict.fromkeys(GROUPS, 'success') and c.usable
        assert c.root in p.session.tasks and p.session.lock.locked()
        assert not [x for x in p.wire.calls if p.wire.stage(x) == 'disconnect']
    finally:
        await p.session.close_interactive()
        await reader.stop()


async def test_options_select_retained_without_cached_work(producer):
    from tests.ha.test_auto_runtime import advertisement
    from bluetooth_data_tools import monotonic_time_coarse
    from habluetooth import get_manager
    p = producer
    get_manager().scanner_adv_received(advertisement(monotonic_time_coarse()))
    p.wire.raw()
    reader = await enable(p)
    assert not p.wire.calls and reader._task is None
    p.wire.raw()
    await spin_until(lambda: reader._task is None)
    assert len(connects(p)) == 0  # Setup's cached burst is already consumed.


@pytest.mark.parametrize('options', [
    {'auto_connect_retained': True, 'auto_read_steps': True},
    {'auto_connect_retained': True, 'auto_read_all': True},
    {'auto_connect_retained': 1}, {'auto_connect_retained': None}])
async def test_invalid_retained_setup_fails_before_platforms(hass, enable_custom_integrations, options):
    entry = MockConfigEntry(domain=DOMAIN, data={'address': 'AA:BB:CC:DD:EE:FF'},
                            unique_id='AA:BB:CC:DD:EE:FF', options=options)
    entry.add_to_hass(hass)
    with patch.object(hass.config_entries, 'async_forward_entry_setups') as forward:
        assert not await hass.config_entries.async_setup(entry.entry_id)
        assert forward.call_count == 0 and entry.runtime_data.closed


@pytest.mark.parametrize('other', ['auto_read_steps', 'auto_read_all'])
async def test_retained_options_conflict_preserves_entry(hass, enable_custom_integrations, other):
    entry = MockConfigEntry(domain=DOMAIN, data={'address': 'AA:BB:CC:DD:EE:FF'},
                            options={'keep': [1]})
    entry.add_to_hass(hass)
    form = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(form['flow_id'], {
        'auto_read_steps': False, 'auto_read_all': False, 'auto_connect_retained': True, other: True})
    assert result['type'] is FlowResultType.FORM
    assert result['errors'] == {'base': 'automatic_modes_conflict'} and entry.options == {'keep': [1]}


async def test_admission_api_retains_without_application_read_and_is_single_use(producer):
    p = producer
    ticket = admission(p, address_type=0)
    await p.session.connect_interactive(admission=ticket, retain_until_disconnect=True)
    c = p.session.interactive_session
    assert ticket.consumed and c.usable and not queries(p)
    assert len(connects(p)) == 1 and connects(p)[0].address_type == 0
    await p.session.close_interactive()
    before = len(p.wire.calls)
    with pytest.raises(HomeAssistantError):
        await p.session.connect_interactive(admission=ticket, retain_until_disconnect=True)
    assert len(p.wire.calls) == before and p.session.interactive_session is c


@pytest.mark.parametrize('invalid', ['expired', 'future', 'type', 'target', 'source', 'generation',
    'runtime', 'owner', 'wrong_session', 'unavailable', 'model', 'fake', 'bounded_ticket', 'manual_retained',
    'client', 'proxy', 'callbacks', 'unloaded', 'nan'])
async def test_invalid_retained_permission_precedes_state_mutation_and_io(producer, invalid):
    p = producer
    args = {}
    from bluetooth_data_tools import monotonic_time_coarse
    if invalid == 'expired': args['seen'] = monotonic_time_coarse() - 6
    if invalid == 'future': args['seen'] = monotonic_time_coarse() + 1
    if invalid == 'nan': args['seen'] = float('nan')
    if invalid == 'type': args['address_type'] = 2
    if invalid == 'target': args['target'] = TARGET + 1
    ticket = admission(p, **args)
    owner = p.watch.runtime_data
    if invalid == 'source': p.device.details['source'] = '00:00:00:00:00:00'
    if invalid == 'generation': p.api.wire = ProducerWire()
    if invalid == 'runtime': p.esp.runtime_data = SimpleNamespace()
    if invalid == 'client': p.runtime.client = object()
    if invalid == 'proxy': p.runtime.bluetooth_device = object()
    if invalid == 'callbacks': p.runtime.disconnect_callbacks = set()
    if invalid == 'unloaded': p.watch._async_set_state(p.hass, ConfigEntryState.UNLOAD_IN_PROGRESS, None)
    if invalid == 'owner': p.watch.runtime_data = object()
    if invalid == 'wrong_session': ticket.session = object()
    if invalid == 'unavailable': p.runtime.available = False
    if invalid == 'model': p.device.name = 'OTHER'
    if invalid == 'fake': ticket = object()
    if invalid == 'manual_retained': ticket = None
    p.session.alarms_fresh = True
    try:
        with pytest.raises(HomeAssistantError):
            await p.session.connect_interactive(admission=ticket, retain_until_disconnect=invalid != 'bounded_ticket')
        assert p.session.interactive_session is None and p.session.alarms_fresh
        assert not p.wire.calls and not p.api.wire.calls
    finally:
        p.watch.runtime_data = owner
        p.esp.runtime_data = p.runtime
        p.runtime.client = p.api
        p.runtime.bluetooth_device = p.proxy
        p.watch._async_set_state(p.hass, ConfigEntryState.LOADED, None)
        p.runtime.available = True
        p.api.wire = p.wire


async def test_consumption_rechecks_permission_without_manual_fallback(producer):
    from custom_components.casio_abl100.native_interactive import _InteractiveRead
    p = producer
    ticket = admission(p)
    original = _InteractiveRead.wait
    async def revoke(reader):
        ticket.consumed = True
        return await original(reader)
    with patch.object(_InteractiveRead, 'wait', revoke):
        with pytest.raises(HomeAssistantError):
            await p.session.connect_interactive(admission=ticket, retain_until_disconnect=True)
    assert not p.wire.calls and not p.session.tasks


async def test_retained_outlives_old_deadlines_but_refresh_still_finite(producer, monkeypatch):
    from custom_components.casio_abl100 import native_interactive as module
    p = producer
    monkeypatch.setattr(module, 'CHECK_SECONDS', .005)
    monkeypatch.setattr(module, 'IDLE_SECONDS', .2)
    monkeypatch.setattr(module, 'SESSION_SECONDS', .3)
    monkeypatch.setattr(module, 'REFRESH_SECONDS', .3)
    reader = await enable(p)
    c = await initial(p, reader)
    assert c._idle_deadline is c._session_deadline is None
    assert c.idle_expires_at is c.session_expires_at is None
    await asyncio.sleep(.35)
    assert c.usable and not c.root.done()
    await p.session.read_interactive()
    assert len(connects(p)) == 1 and queries(p) == list(QUERIES) * 2
    p.wire.hold_at = 'response'
    with pytest.raises(HomeAssistantError):
        await p.session.read_interactive()
    assert c.root.done() and c.close_reason == 'refresh_expired' and c.cleanup_result == 'success'


async def test_terminal_cleanup_starts_quiet_gap_and_busy_packets_extend_it(producer):
    p = producer
    reader = await enable(p)
    from bluetooth_data_tools import monotonic_time_coarse
    now = monotonic_time_coarse()
    with clock(now):
        c = await initial(p, reader)
    p.wire.hold_at = 'disconnect'
    with clock(now + 100):
        close = asyncio.create_task(p.session.close_interactive())
        await p.wire.entered.wait()
        p.wire.release.set()
        await close
        assert c.root.done()
        p.wire.raw()  # Queued old closing-burst delivery.
        await spin_until(lambda: reader._task is None)
        assert len(connects(p)) == 1
    with clock(now + 129):
        p.wire.raw()  # Extends last-observed/post-terminal watermark.
    with clock(now + 158):
        p.wire.raw()
        await spin_until(lambda: reader._task is None)
        assert len(connects(p)) == 1
    with clock(now + 189):
        p.wire.hold_at = None
        await initial(p, reader)
        assert len(connects(p)) == 2
        await p.session.close_interactive()


async def test_busy_time_raw_cannot_advance_terminal_rearm_boundary(producer):
    p = producer
    reader = await enable(p)
    from bluetooth_data_tools import monotonic_time_coarse
    now = monotonic_time_coarse()
    with clock(now):
        c = await initial(p, reader)
    with clock(now + 90):
        p.wire.raw()  # Already occupied, never deferred.
    with clock(now + 100):
        await p.session.close_interactive()
        assert c.root.done()
    with clock(now + 120):
        p.wire.raw()  # >QUIET_GAP from busy packet, <QUIET_GAP from terminal.
        await spin_until(lambda: reader._task is None)
        assert len(connects(p)) == 1
    with clock(now + 151):
        await initial(p, reader)
        assert len(connects(p)) == 2
        await p.session.close_interactive()


@pytest.mark.parametrize('raw', ['wrong_watch', 'bad_type', 'bad_model', 'no_name'])
async def test_retained_raw_requires_exact_live_identity(producer, raw):
    p = producer
    reader = await enable(p)
    if raw == 'wrong_watch':
        p.wire.raw(TARGET + 1)
    elif raw == 'bad_type':
        p.wire.raw(address_type=2)
    else:
        p.wire.raw(data=b'\x04\x09BAD' if raw == 'bad_model' else b'\x02\x01\x06')
    await spin_until(lambda: reader._task is None)
    assert not p.wire.calls and p.session.interactive_session is None


@pytest.mark.parametrize('boundary', ['suspend', 'stop', 'source_loss'])
async def test_pending_observer_permission_revoked(producer, boundary):
    p = producer
    reader = await enable(p)
    p.wire.raw()
    if boundary == 'suspend':
        reader.suspend(); reader.resume()
    elif boundary == 'stop':
        await reader.stop()
    else:
        for cb in tuple(p.runtime.disconnect_callbacks): cb()
    await spin_until(lambda: reader._task is None)
    assert not connects(p) and p.session.interactive_session is None


@pytest.mark.parametrize('phase', ['initial', 'later'])
async def test_progressive_failure_retains_prior_observations(producer, phase):
    p = producer
    reader = await enable(p)
    if phase == 'later':
        await initial(p, reader)
    previous = p.session.basic_settings_snapshot
    previous_time = p.session.basic_settings_last_sync
    p.wire.failure_stage = 'get13'
    if phase == 'initial':
        p.wire.raw()
        await spin_until(lambda: reader._task is None)
    else:
        with pytest.raises(HomeAssistantError):
            await p.session.read_interactive()
    c = p.session.interactive_session
    assert c.root.done() and c.operation_result == 'partial'
    assert p.session.alarms_fresh and p.session.zones_fresh
    assert p.session.basic_settings_snapshot is previous and p.session.basic_settings_last_sync == previous_time
    assert not p.session.basic_settings_fresh and not p.session.tasks


@pytest.mark.parametrize('terminal', ['release', 'generation', 'close', 'unload', 'failed_unload', 'stop'])
async def test_retained_root_terminal_ownership_and_observer_cleanup(producer, terminal, monkeypatch):
    from custom_components.casio_abl100 import native_interactive as module, async_unload_entry
    p = producer
    monkeypatch.setattr(module, 'CHECK_SECONDS', .005)
    reader = await enable(p)
    c = await initial(p, reader)
    assert reader._task is None and not c.root.done()
    replacement = ProducerWire()
    try:
        if terminal == 'release':
            p.wire.emit(BluetoothDeviceConnectionResponse(address=TARGET, connected=False))
            p.wire.emit(BluetoothDeviceConnectionResponse(address=TARGET, connected=True, mtu=512))
        elif terminal == 'generation':
            p.api.wire = replacement
            reader._maintain_raw(None)
        elif terminal == 'close':
            await p.session.close_interactive()
        elif terminal == 'unload':
            assert await p.hass.config_entries.async_unload(p.watch.entry_id)
        elif terminal == 'failed_unload':
            with patch.object(p.hass.config_entries, 'async_unload_platforms', return_value=False):
                assert not await async_unload_entry(p.hass, p.watch)
            assert not reader._suspended and not reader._stopped
            p.wire.raw()
            await spin_until(lambda: reader._task is None)
            assert len(connects(p)) == 1
        else:
            p.hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
            await p.hass.async_block_till_done()
            assert p.session.closed and reader._stopped and not p.session.listeners
        await asyncio.wait_for(asyncio.wait({c.root}), 2)
        assert c.root.done() and not c.usable and not p.session.tasks and not p.session.lock.locked()
        assert not p.api._notify_callbacks and not replacement.calls
        assert c.close_reason == {'release': 'link_lost', 'generation': 'generation_lost', 'close': 'explicit_close',
            'unload': 'unload', 'failed_unload': 'unload', 'stop': 'shutdown'}[terminal]
    finally:
        await reader.stop()
        p.api.wire = p.wire


async def test_cancel_between_connect_handoff_and_initial_read_closes_retained_owner(producer):
    p = producer
    reader = await enable(p)
    original = p.session.connect_interactive
    async def cancel_at_handoff(**kwargs):
        await original(**kwargs)
        asyncio.current_task().cancel()
    with patch.object(p.session, 'connect_interactive', new=cancel_at_handoff):
        p.wire.raw()
        await spin_until(lambda: reader._task is None)
    c = p.session.interactive_session
    try:
        assert c.root.done() and not c.usable and not p.session.tasks
        assert len(connects(p)) == 1 and not queries(p)
    finally:
        await p.session.close_interactive()


async def test_stop_cancelled_initial_read_joins_original_cleanup(producer):
    p = producer
    reader = await enable(p)
    p.wire.hold_at = 'response'
    p.wire.raw()
    await p.wire.entered.wait()
    c = p.session.interactive_session
    p.wire.entered.clear()
    p.wire.hold_at = 'disconnect'
    stopper = asyncio.create_task(reader.stop())
    await p.wire.entered.wait()
    stopper.cancel()
    await asyncio.sleep(0)
    assert not stopper.done() and not c.root.done()
    p.wire.release.set()
    await asyncio.gather(stopper, return_exceptions=True)
    assert c.root.done() and not p.session.tasks and not p.api._notify_callbacks


@pytest.mark.parametrize('fault', ['listen', 'on_unload'])
async def test_stop_listener_setup_fault_cleans_owned_resources(producer, fault):
    p = producer
    original = type(p.hass.bus).async_listen_once
    def listen(bus, event, cb):
        if event == EVENT_HOMEASSISTANT_STOP:
            raise RuntimeError('PRIVATE_STOP')
        return original(bus, event, cb)
    ctx = (patch.object(type(p.hass.bus), 'async_listen_once', new=listen) if fault == 'listen'
           else patch.object(type(p.watch), 'async_on_unload', side_effect=RuntimeError('PRIVATE_STOP')))
    with ctx:
        p.hass.config_entries.async_update_entry(p.watch, options={'auto_connect_retained': True})
        await p.hass.async_block_till_done()
    p.session = p.watch.runtime_data
    assert p.watch.state is ConfigEntryState.SETUP_ERROR and p.session.closed
    assert not p.session.tasks and not p.watch.update_listeners and len(p.wire.handlers) == 1


@pytest.mark.parametrize('block', ['lock', 'tasks'])
async def test_retained_busy_burst_is_consumed_without_queue(producer, block):
    p = producer
    reader = await enable(p)
    if block == 'lock':
        await p.session.lock.acquire()
    else:
        p.session.tasks.add(object())
    p.wire.raw()
    if block == 'lock':
        p.session.lock.release()
    else:
        p.session.tasks.clear()
    p.wire.raw()
    await spin_until(lambda: reader._task is None)
    assert not p.wire.calls and reader._claimed


async def test_second_retained_watch_does_not_borrow_first_target_or_cleanup(producer):
    p = producer
    reader = await enable(p)
    address = 'AA:BB:CC:DD:EE:01'
    other = MockConfigEntry(domain=DOMAIN, title='Other watch', data={'address': address},
                            unique_id=address, options={'auto_connect_retained': True})
    other.add_to_hass(p.hass)
    def lookup(_hass, address, **kwargs):
        return SimpleNamespace(address=address, name='ABL-100WE', details={'source': SOURCE})
    with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address', side_effect=lookup):
        assert await p.hass.config_entries.async_setup(other.entry_id)
        second = other.runtime_data
        try:
            await initial(p, reader)
            assert second.interactive_session is None
            p.wire.raw(int(address.replace(':', ''), 16))
            await spin_until(lambda: second.auto_reader._task is None)
            assert second.interactive_session.usable
            assert await p.hass.config_entries.async_unload(p.watch.entry_id)
            assert second.interactive_session.usable and not p.session.tasks
            assert {x.address for x in connects(p)} == {TARGET, int(address.replace(':', ''), 16)}
        finally:
            assert await p.hass.config_entries.async_unload(other.entry_id)
    assert len(p.wire.handlers) == 1 and not p.runtime.disconnect_callbacks


@pytest.mark.parametrize('timeout', [False, True])
async def test_retained_explicit_apply_uses_original_owner_and_uncertain_barrier(producer, monkeypatch, timeout):
    from datetime import time
    from custom_components.casio_abl100 import native_interactive as module
    from tests.ha.test_native_alarm_editing_runtime import MemoryWire
    p = producer
    class RetainedMemoryWire(MemoryWire, ProducerWire):
        pass
    p.remove_scanner()
    wire = RetainedMemoryWire()
    p.wire = p.api.wire = wire
    # This case needs no scanner; compiled RAW observer remains the dispatch path.
    monkeypatch.setattr(module, 'REFRESH_SECONDS', .3)
    monkeypatch.setattr(module, 'CHECK_SECONDS', .005)
    reader = await enable(p)
    c = await initial(p, reader)
    assert not wire.sets
    p.session.stage_alarm_time(5, time(1, 2))
    wire.hold_readback = timeout
    if timeout:
        with pytest.raises(HomeAssistantError):
            await p.session.apply_alarm_draft()
        assert c.close_reason == 'refresh_expired' and c.root.done()
        assert p.session.alarm_draft.needs_reconciliation
        assert p.session.alarm_safety.blocked
    else:
        await p.session.apply_alarm_draft()
        assert c.usable and p.session.alarm_draft.outcome.result == 'verified'
    assert [bytes(x.data)[0] for x in wire.sets] == [0x15, 0x16] and len(connects(p)) == 1
    wire.release_readback()
    await p.session.close_interactive()
    await reader.stop()
    assert not wire.handlers and not p.session.tasks
