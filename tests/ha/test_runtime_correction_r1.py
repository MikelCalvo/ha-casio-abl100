"""Independent, bounded public-HA probes; external wire/storage fault injection only."""
import asyncio
import json
import threading
from unittest.mock import patch

import pytest
from bleak_esphome.backend.scanner import ESPHomeScanner
from aioesphomeapi.api_pb2 import BluetoothLERawAdvertisementsResponse
from homeassistant.exceptions import HomeAssistantError

from tests.ha.test_native_alarm_runtime import native, SOURCE
from tests.ha.test_unified_sync_runtime import unified, QUERIES
from tests.ha.test_auto_unified_admission_runtime import producer, ProducerWire
from tests.ha.test_native_alarm_editing_runtime import editing, MemoryWire, service, spin_until
from tests.ha.test_native_interactive_ui_runtime import press, connect_ui
from tests.ha.test_auto_retained_runtime import enable, initial, connects, queries


class RetainedMemoryWire(MemoryWire, ProducerWire):
    """Compose existing SET-capable memory with compiled RAW ingress."""


@pytest.fixture
async def retained_editing(producer):
    p = producer
    p.remove_scanner()
    wire = RetainedMemoryWire()
    p.wire = p.api.wire = wire
    p.scanner = ESPHomeScanner(SOURCE, 'Synthetic', None, True)
    remove = wire.add_message_callback(p.scanner.async_on_raw_advertisements,
                                      (BluetoothLERawAdvertisementsResponse,))
    try:
        yield p
    finally:
        if p.session.interactive_session is not None:
            await p.session.close_interactive()
        if p.session.auto_reader is not None:
            await p.session.auto_reader.stop()
        remove()
        assert not wire.handlers


async def test_off_service_joins_original_cleanup(retained_editing):
    p = retained_editing
    c = await initial(p, await enable(p))
    p.wire.hold_at = 'disconnect'
    p.wire.entered.clear()
    stopper = asyncio.create_task(service(p, 'watch_connection', 'turn_off'))
    try:
        await spin_until(p.wire.entered.is_set)
        await asyncio.sleep(.01)
        returned_before_cleanup = stopper.done() and not c.root.done()
        print('OFF_BOUNDARY', json.dumps(dict(service_done=stopper.done(), root_done=c.root.done(),
            cleanup_in_progress=c.cleanup_in_progress, close_reason=c.close_reason,
            retained_option=p.watch.options['auto_connect_retained'])))
    finally:
        p.wire.release.set()
        await asyncio.gather(stopper, return_exceptions=True)
        await p.hass.async_block_till_done()
        p.session = p.watch.runtime_data
    assert c.root.done() and p.session.auto_reader is None
    before = list(p.wire.calls)
    await asyncio.sleep(.03)
    assert p.wire.calls == before
    assert not returned_before_cleanup, 'switch.turn_off returned success before original native cleanup joined'


async def test_tick_delays_held_apply_and_resumes_on_same_root(retained_editing, monkeypatch):
    from custom_components.casio_abl100 import native_interactive as module
    monkeypatch.setattr(module, 'PERIODIC_SECONDS', .10)
    p = retained_editing
    c = await initial(p, await enable(p))
    await service(p, 'alarm_1_time_draft', 'set_value', '01:02')
    p.wire.hold_readback = True
    task = asyncio.create_task(press(p, 'apply_alarm_draft'))
    try:
        await spin_until(lambda: bool(p.wire.pending_readback))
        before, cycles = list(p.wire.calls), c._cycles
        await asyncio.sleep(.16)
        assert p.wire.calls == before and c._cycles == cycles and not task.done()
        with pytest.raises(HomeAssistantError) as caught:
            await press(p, 'apply_alarm_draft')
        assert caught.value.translation_key == 'alarm_apply_busy'
        p.wire.release_readback()
        await task
        completed = list(p.wire.calls)
        await asyncio.sleep(.035)
        assert p.wire.calls == completed, 'tick must be delayed from Apply completion'
        await spin_until(lambda: c._cycles > cycles)
        assert len(connects(p)) == 1 and c.usable
        print('TICK_APPLY', json.dumps(dict(initial_cycles=cycles, later_cycles=c._cycles,
            connects=len(connects(p)), settings_packets=len(p.wire.sets))))
        await p.session.close_interactive()
        before = list(p.wire.calls)
        await asyncio.sleep(.12)
        assert p.wire.calls == before
    finally:
        p.wire.release_readback()
        await asyncio.gather(task, return_exceptions=True)


async def test_verified_apply_clear_write_failure_does_not_resurrect(editing):
    from custom_components.casio_abl100 import alarm_draft_storage as module
    p = editing
    c = await connect_ui(p)
    await service(p, 'alarm_1_time_draft', 'set_value', '01:02')
    original = module._atomic_write
    failed_clear = []
    def fail_clear(path, record):
        if not record['times'] and not record['enabled'] and record['signal'] is None:
            failed_clear.append(record)
            raise OSError('INDEPENDENT_CLEAR_FAILURE')
        return original(path, record)
    with patch.object(module, '_atomic_write', fail_clear):
        with pytest.raises(HomeAssistantError) as caught:
            await press(p, 'apply_alarm_draft')
    assert failed_clear and len(p.wire.sets) == 2 and c.root.done()
    live = dict(result=p.session.alarm_draft.outcome.result,
                blocked=p.session.alarm_draft.needs_reconciliation,
                pending=p.session.alarm_draft.pending)
    journal = json.loads(p.session.alarm_safety.path.read_text())
    local = json.loads(p.session.draft_storage.path.read_text())
    await p.hass.config_entries.async_reload(p.watch.entry_id)
    p.session = p.watch.runtime_data
    restored = dict(result=p.session.alarm_draft.outcome.result,
                    blocked=p.session.alarm_draft.needs_reconciliation,
                    pending=p.session.alarm_draft.pending)
    print('CLEAR_FAILURE', json.dumps(dict(service_error=caught.value.translation_key, live=live,
        persisted_journal=journal, persisted_draft=local, restored=restored), default=str))
    assert not restored['pending'] and (not live['blocked'] or restored['blocked']), (
        'verified native Apply plus failed local clear resurrected old draft and/or lost new uncertainty')
    assert caught.value.translation_key == 'alarm_draft_save_failed'
    assert live['result'] == restored['result'] == 'verified'
    assert not live['blocked'] and not restored['blocked']
    before = list(p.wire.calls)
    assert await p.hass.config_entries.async_unload(p.watch.entry_id)
    p.session = None
    assert await p.hass.config_entries.async_setup(p.watch.entry_id)
    p.session = p.watch.runtime_data
    assert not p.session.alarm_draft.pending and p.wire.calls == before
    # A genuinely new edit must survive; retirement is revision-specific.
    await service(p, 'alarm_2_time_draft', 'set_value', '03:04')
    await p.hass.config_entries.async_reload(p.watch.entry_id)
    p.session = p.watch.runtime_data
    assert set(p.session.alarm_draft.times) == {2}


async def test_cancel_during_verified_clear_preserves_new_uncertainty(editing):
    from custom_components.casio_abl100 import alarm_draft_storage as module
    p = editing
    await connect_ui(p)
    await service(p, 'alarm_1_time_draft', 'set_value', '01:02')
    original = module._atomic_write
    entered, release = threading.Event(), threading.Event()
    def hold_clear(path, record):
        if not record['times'] and not record['enabled'] and record['signal'] is None:
            entered.set()
            if not release.wait(3):
                raise TimeoutError('probe release missing')
        return original(path, record)
    with patch.object(module, '_atomic_write', hold_clear):
        task = asyncio.create_task(press(p, 'apply_alarm_draft'))
        try:
            await spin_until(entered.is_set)
            task.cancel()
            await asyncio.sleep(.01)
            joined_before_return = not task.done()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    live = dict(result=p.session.alarm_draft.outcome.result,
                blocked=p.session.alarm_draft.needs_reconciliation)
    journal = json.loads(p.session.alarm_safety.path.read_text())
    await p.hass.config_entries.async_reload(p.watch.entry_id)
    p.session = p.watch.runtime_data
    restored = dict(result=p.session.alarm_draft.outcome.result,
                    blocked=p.session.alarm_draft.needs_reconciliation,
                    pending=p.session.alarm_draft.pending)
    print('CANCEL_CLEAR', json.dumps(dict(joined_executor=joined_before_return, live=live,
        persisted_journal=journal, restored=restored)))
    assert joined_before_return
    assert not live['blocked'] or restored['blocked'], 'new unknown/reconciliation barrier vanished on reload'
    assert live['result'] == restored['result'] == 'verified'
    assert not live['blocked'] and not restored['blocked'] and not restored['pending']


@pytest.mark.parametrize('noop', [False, True])
async def test_retirement_fence_precedes_local_clear_and_cold_setup(editing, noop):
    from custom_components.casio_abl100 import alarm_draft_storage as module
    p = editing
    await connect_ui(p)
    await service(p, 'alarm_1_time_draft', 'set_value', '06:17' if noop else '01:02')
    saved = p.session.draft_storage.path.read_bytes()
    original = module._atomic_write
    def fail_clear(path, record):
        if 'times' in record and not record['times'] and not record['enabled'] and record['signal'] is None:
            raise OSError('LOCAL_CLEAR_ONLY')
        return original(path, record)
    with patch.object(module, '_atomic_write', fail_clear):
        with pytest.raises(HomeAssistantError):
            await press(p, 'apply_alarm_draft')
    before = list(p.wire.calls)
    assert p.session.draft_storage.path.read_bytes() == saved
    assert await p.hass.config_entries.async_unload(p.watch.entry_id)
    p.session = None
    assert await p.hass.config_entries.async_setup(p.watch.entry_id)
    p.session = p.watch.runtime_data
    assert not p.session.alarm_draft.pending, 'cold setup resurrected a retired revision'
    assert not p.session.alarm_draft.needs_reconciliation
    assert p.session.alarm_draft.outcome.result == ('unchanged' if noop else 'verified')
    assert len(p.wire.sets) == (0 if noop else 2) and p.wire.calls == before


async def test_prewrite_conflict_does_not_leave_retirement_barrier(editing):
    from tests.ha.test_native_alarm_runtime import TARGET
    p = editing
    await connect_ui(p)
    # Populate the observed anchor through the existing no-op Apply path.
    await service(p, 'alarm_1_time_draft', 'set_value', '06:17')
    await press(p, 'apply_alarm_draft')
    await service(p, 'alarm_2_time_draft', 'set_value', '11:12')
    p.wire.memory(TARGET)[b'\x15'] = bytes.fromhex('15 40 07 05 11')
    with pytest.raises(HomeAssistantError) as caught:
        await press(p, 'apply_alarm_draft')
    assert caught.value.translation_key == 'alarm_apply_conflict'
    assert not p.wire.sets and p.session.alarm_draft.pending
    assert not p.session.alarm_draft.needs_reconciliation
    # Reload owns normal native disconnect; close first to isolate storage IO.
    await p.session.close_interactive()
    before = list(p.wire.calls)
    await p.hass.config_entries.async_reload(p.watch.entry_id)
    p.session = p.watch.runtime_data
    assert p.session.alarm_draft.pending
    assert not p.session.alarm_draft.needs_reconciliation
    assert p.wire.calls == before


async def test_failed_retirement_metadata_keeps_known_result_and_cold_barrier(editing):
    from custom_components.casio_abl100 import alarm_draft_storage as module
    p = editing
    await connect_ui(p)
    await service(p, 'alarm_1_time_draft', 'set_value', '01:02')
    original = module._atomic_write
    def fail_retire(path, record):
        if record.get('status') == 'retired':
            raise OSError('RETIREMENT_DISK_FAILURE')
        return original(path, record)
    with patch.object(module, '_write_metadata', fail_retire):
        with pytest.raises(HomeAssistantError) as caught:
            await press(p, 'apply_alarm_draft')
    assert caught.value.translation_key == 'alarm_draft_save_failed'
    assert p.session.alarm_draft.outcome.result == 'verified'
    await p.hass.config_entries.async_reload(p.watch.entry_id)
    p.session = p.watch.runtime_data
    assert p.session.alarm_draft.pending and p.session.alarm_draft.needs_reconciliation
    before = list(p.wire.calls)
    await connect_ui(p)
    with pytest.raises(HomeAssistantError) as caught:
        await press(p, 'apply_alarm_draft')
    assert caught.value.translation_key == 'alarm_apply_reconciliation'
    assert len(p.wire.sets) == 2
    with patch('custom_components.casio_abl100.dt_util.now', return_value=__import__('datetime').datetime(2026, 10, 18)):
        await press(p, 'read_interactive')
    assert not p.session.alarm_draft.needs_reconciliation
    assert len(p.wire.sets) == 2 and len(p.wire.calls) > len(before)
    await p.session.close_interactive()
    await p.hass.config_entries.async_reload(p.watch.entry_id)
    p.session = p.watch.runtime_data
    assert p.session.alarm_draft.pending and not p.session.alarm_draft.needs_reconciliation
