"""Interactive core through real HA entries/native API bodies; wire synthetic."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from aioesphomeapi.api_pb2 import (BluetoothDeviceRequest, BluetoothDeviceConnectionResponse,
    BluetoothGATTGetServicesRequest, BluetoothGATTWriteRequest,
    BluetoothGATTNotifyRequest, BluetoothGATTWriteDescriptorRequest)
from homeassistant.exceptions import HomeAssistantError

from tests.ha.test_native_alarm_runtime import native, spin_until, TARGET  # noqa: F401
from tests.ha.test_unified_sync_runtime import unified, drive, assert_clean, QUERIES, GROUPS  # noqa: F401


async def test_interactive_methods_exist(unified):
    for name in ('connect_interactive', 'read_interactive', 'close_interactive'):
        assert callable(getattr(unified.session, name, None)), f'Missing new interactive API: {name}'


def disconnects(wire):
    return [x for x in wire.calls if isinstance(x, BluetoothDeviceRequest) and int(x.request_type) == 1]


async def connect(unified):
    task = asyncio.create_task(unified.session.connect_interactive())
    await drive(task, unified)
    return unified.session.interactive_session


async def test_connect_refresh_refresh_close_retains_one_original_link(unified):
    s, w = unified.session, unified.wire
    c = await connect(unified)
    try:
        assert c.active and c.usable and c.phase == 'connected' and c.link_state == 'connected'
        assert c.root in s.tasks and s.lock.locked()
        assert not any(isinstance(x, BluetoothGATTWriteRequest) for x in w.calls)
        assert c.idle_expires_at.tzinfo == timezone.utc and c.session_expires_at.tzinfo == timezone.utc
        assert c.wait_expires_at is None
        assert s.sync_result == 'never' and s.sync_phase == 'idle'
        first_time = datetime(2026, 10, 19, tzinfo=timezone.utc)
        with patch('custom_components.casio_abl100.native_interactive.dt_util.utcnow', return_value=first_time):
            await s.read_interactive()
        first = s.alarm_snapshot
        assert s.alarm_last_sync == first_time and s.alarms_fresh
        await unified.hass.async_block_till_done()
        assert unified.hass.states.get('sensor.casio_abl_100we_alarm_1').state == '07:35'
        w.frames[b'\x15'] = b'\x15\x40\0\x06\x22'
        w.frames[b'\x18'] = b'\x18\x02\x03\x04' + bytes(11)
        w.hold_at = 'response'
        refresh = asyncio.create_task(s.read_interactive())
        await spin_until(lambda: w.entered.is_set() or refresh.done())
        assert w.entered.is_set() and not refresh.done()
        assert s.alarm_snapshot is first and s.alarm_last_sync == first_time and not s.alarms_fresh
        assert not disconnects(w) and c.active and c.link_state == 'connected'
        w.hold_at = None
        second_time = first_time + timedelta(seconds=2)
        with patch('custom_components.casio_abl100.native_interactive.dt_util.utcnow', return_value=second_time):
            w.notify(TARGET, 61, w.frames[b'\x15'])
            await refresh
        assert s.alarm_snapshot != first and s.alarm_last_sync == second_time and s.alarms_fresh
        await unified.hass.async_block_till_done()
        assert unified.hass.states.get('sensor.casio_abl_100we_alarm_1').state == '06:34'
        assert s.timer_snapshot.hours == 2
        assert [bytes(x.data) for x in w.calls if isinstance(x, BluetoothGATTWriteRequest)] == list(QUERIES) * 2
        assert len([x for x in w.calls if isinstance(x, BluetoothDeviceRequest) and int(x.request_type) != 1]) == 1
        assert sum(isinstance(x, BluetoothGATTGetServicesRequest) for x in w.calls) == 1
        assert [x.handle for x in w.calls if isinstance(x, BluetoothGATTNotifyRequest)] == [61, 71, 81]
        assert [x.handle for x in w.calls if isinstance(x, BluetoothGATTWriteDescriptorRequest)] == [62, 72, 82]
        assert c.group_results == dict.fromkeys(GROUPS, 'success') and c.operation_result == 'success'
        assert s.sync_result == 'never' and s.sync_group_results == dict.fromkeys(GROUPS, 'not_attempted')
        await s.close_interactive()
        assert len(disconnects(w)) == 1 and c.phase == 'closed' and c.link_state == 'disconnected'
        assert not c.active and not c.usable and c.close_reason == 'explicit_close'
        assert c.cleanup_result == 'success' and c.maintenance_handle is None
        assert c.idle_expires_at is c.session_expires_at is c.wait_expires_at is None
        assert_clean(unified)
    finally:
        await s.close_interactive()


@pytest.mark.parametrize('fault', ['missing_steps', 'malformed', 'duplicate', 'write_after_frame', 'backend_cancel'])
async def test_failed_refresh_closes_and_retains_progressive_values(unified, fault):
    c = await connect(unified)
    w, s = unified.wire, unified.session
    if fault == 'missing_steps':
        c.reader.adapter.metadata.services[0].characteristics[:] = [
            char for char in c.reader.adapter.metadata.services[0].characteristics if char.handle not in (71, 81)]
    elif fault == 'malformed':
        w.frames[b'\x13'] = b'\x13'
    elif fault == 'duplicate':
        w.mode = 'duplicate13'
    elif fault == 'backend_cancel':
        w.failure_stage, w.failure = 'get13', asyncio.CancelledError('PRIVATE')
    else:
        def after(stage, request):
            if stage == 'get13':
                raise RuntimeError('PRIVATE')
        w.after = after
    with pytest.raises(HomeAssistantError):
        await s.read_interactive()
    assert c.phase == 'closed' and c.operation_result == 'partial'
    assert s.alarm_snapshot is not None and s.alarms_fresh and not s.basic_settings_fresh
    assert c.close_reason == 'read_failed' and c.cleanup_result == 'success'
    assert asyncio.current_task().cancelling() == 0
    assert_clean(unified)


@pytest.mark.parametrize('stage', ['waiting', 'connect', 'services', 'notify61', 'notify71', 'response'])
async def test_concurrent_explicit_close_bypasses_lock_and_joins_owned_work(unified, stage):
    s, w = unified.session, unified.wire
    if stage in ('waiting', 'connect', 'services'):
        if stage != 'waiting':
            w.hold_at = stage
        if stage == 'connect':
            original_send = w.send_message
            def held_connect(request):
                if w.stage(request) == 'connect':
                    w.calls.append(request)
                    w.entered.set()
                else:
                    original_send(request)
            w.send_message = held_connect
        caller = asyncio.create_task(s.connect_interactive())
        await spin_until(lambda: s.interactive_session is not None and s.interactive_session.phase == 'waiting')
        if stage != 'waiting':
            w.raw()
            await spin_until(w.entered.is_set)
    else:
        await connect(unified)
        w.hold_at = stage
        caller = asyncio.create_task(s.read_interactive())
        await spin_until(w.entered.is_set)
    c = s.interactive_session
    closers = [asyncio.create_task(s.close_interactive()) for _ in range(2)]
    await asyncio.sleep(0)
    assert not c.usable and c.close_reason == 'explicit_close'
    w.release.set()
    await asyncio.gather(*closers)
    with pytest.raises(HomeAssistantError):
        await caller
    assert c.phase == 'closed'
    assert_clean(unified)


@pytest.mark.parametrize('stage', ['notify61', 'notify71', 'disconnect'])
async def test_admitted_caller_cancellation_joins_acquisition_and_cleanup(unified, stage):
    c = await connect(unified)
    s, w = unified.session, unified.wire
    w.hold_at = stage
    if stage == 'disconnect':
        w.failure_stage = 'get13'
    caller = asyncio.create_task(s.read_interactive())
    await spin_until(w.entered.is_set)
    caller.cancel()
    await asyncio.sleep(0)
    caller.cancel()
    await asyncio.sleep(0)
    assert not caller.done()
    w.release.set()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert c.phase == 'closed' and c.cleanup_result == 'success'
    assert_clean(unified)


async def test_busy_refusals_and_preentry_cancel_do_not_kill_owner(unified):
    c = await connect(unified)
    before = list(unified.wire.calls)
    with pytest.raises(HomeAssistantError):
        await unified.session.connect_interactive()
    async def cancelled_refusal():
        asyncio.current_task().cancel()
        await unified.session.connect_interactive()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.create_task(cancelled_refusal())
    assert c.usable and unified.wire.calls == before
    await unified.session.close_interactive()
    assert_clean(unified)


@pytest.mark.parametrize('kind', ['idle', 'hard', 'refresh', 'generation', 'ble_loss'])
async def test_independent_expiries_and_terminal_generation_or_ble_loss(unified, monkeypatch, kind):
    from custom_components.casio_abl100 import native_interactive as module
    monkeypatch.setattr(module, 'CHECK_SECONDS', 0.01)
    if kind == 'idle':
        monkeypatch.setattr(module, 'IDLE_SECONDS', 0.03)
    if kind == 'hard':
        monkeypatch.setattr(module, 'SESSION_SECONDS', 0.03)
    if kind == 'refresh':
        monkeypatch.setattr(module, 'REFRESH_SECONDS', 0.03)
    c = await connect(unified)
    old = unified.wire
    if kind == 'generation':
        from tests.ha.test_unified_sync_runtime import SyncWire
        unified.api.wire = SyncWire()
    elif kind == 'ble_loss':
        old.emit(BluetoothDeviceConnectionResponse(address=TARGET, connected=False))
        old.emit(BluetoothDeviceConnectionResponse(address=TARGET, connected=True, mtu=512))
    elif kind == 'refresh':
        old.hold_at = 'response'
        with pytest.raises(HomeAssistantError):
            await unified.session.read_interactive()
    await asyncio.wait_for(asyncio.wait({c.root}), 2)
    assert c.root.done() and not c.usable and c.phase == 'closed'
    assert c.close_reason == {'idle':'idle_expired', 'hard':'session_expired', 'refresh':'refresh_expired',
                              'generation':'generation_lost', 'ble_loss':'link_lost'}[kind]
    if kind == 'generation':
        assert c.link_state == 'unknown' and not unified.api.wire.calls
        assert not disconnects(old)
        unified.api.wire = old
    else:
        assert c.link_state == 'disconnected'
    assert_clean(unified)


async def test_close_intent_is_not_disconnection_and_cancelled_closer_joins(unified):
    c = await connect(unified)
    w = unified.wire
    w.hold_at = 'disconnect'
    close = asyncio.create_task(unified.session.close_interactive())
    await spin_until(w.entered.is_set)
    assert c.phase == 'closing' and not c.usable and c.link_state == 'connected'
    close.cancel()
    await asyncio.sleep(0)
    close.cancel()
    await asyncio.sleep(0)
    assert not close.done()
    w.release.set()
    with pytest.raises(asyncio.CancelledError):
        await close
    await unified.session.close_interactive()
    assert c.close_reason == 'explicit_close' and c.link_state == 'disconnected'
    assert_clean(unified)


async def test_cleanup_failure_is_shared_and_never_fabricates_off(unified):
    c = await connect(unified)
    unified.wire.failure_stage = 'disconnect'
    for _ in range(2):
        with pytest.raises(HomeAssistantError):
            await unified.session.close_interactive()
    assert c.cleanup_result == 'failed' and c.link_state == 'unknown' and c.close_reason == 'explicit_close'
    assert_clean(unified)


@pytest.mark.parametrize('mode', ['preentry_close', 'preentry_owner_cancel', 'timestamp_cancel', 'ble_before_commit'])
async def test_entry_and_commit_authority(unified, mode):
    s, w = unified.session, unified.wire
    if mode.startswith('preentry'):
        from custom_components.casio_abl100.native_interactive import NativeInteractiveSession
        c = NativeInteractiveSession(s)
        s.interactive_session = c
        c.start()
        if mode == 'preentry_owner_cancel':
            c.root.cancel()
        else:
            c.request_close('explicit_close')
        await asyncio.wait({c.root})
        await asyncio.sleep(0)
        assert not c.active and c.phase == 'closed' and not w.calls
        assert_clean(unified)
        return
    c = await connect(unified)
    caller = None
    from custom_components.casio_abl100 import native_interactive as module
    original = module.dt_util.utcnow
    def timestamp():
        value = original()
        if c.group_results['basic_settings'] == 'reading':
            if mode == 'timestamp_cancel':
                caller.cancel()
            else:
                w.emit(BluetoothDeviceConnectionResponse(address=TARGET, connected=False))
                w.emit(BluetoothDeviceConnectionResponse(address=TARGET, connected=True, mtu=512))
        return value
    with patch.object(module.dt_util, 'utcnow', side_effect=timestamp):
        caller = asyncio.create_task(s.read_interactive())
        with pytest.raises(asyncio.CancelledError if mode == 'timestamp_cancel' else HomeAssistantError):
            await caller
    assert s.alarm_snapshot is not None and s.alarms_fresh and s.basic_settings_snapshot is None
    assert c.close_reason == ('caller_cancelled' if mode == 'timestamp_cancel' else 'link_lost')
    assert_clean(unified)


async def test_idle_and_prequery_frames_never_publish_and_old_controller_cannot_close_new(unified):
    s, w = unified.session, unified.wire
    c = await connect(unified)
    await s.read_interactive()
    first, stamp, expiry = s.alarm_snapshot, s.alarm_last_sync, c.idle_expires_at
    for frame in (b'\xff', w.frames[b'\x15'], b''):
        w.notify(TARGET, 61, frame)
    assert s.alarm_snapshot is first and s.alarm_last_sync == stamp and c.idle_expires_at == expiry
    old_callback = c.reader.observe_link
    await s.close_interactive()
    newer = await connect(unified)
    old_callback(False, 0)
    c.reader.invalidate()
    assert newer.usable and newer.close_reason is None
    await s.close_interactive()
    assert_clean(unified)


@pytest.mark.parametrize('boundary', ['notify61', 'disconnect'])
async def test_generation_change_during_acquisition_or_cleanup_is_original_only(unified, boundary):
    from tests.ha.test_unified_sync_runtime import SyncWire
    s, w = unified.session, unified.wire
    c = await connect(unified)
    w.hold_at = boundary
    task = asyncio.create_task(s.read_interactive() if boundary == 'notify61' else s.close_interactive())
    await spin_until(w.entered.is_set)
    replacement = SyncWire()
    unified.api.wire = replacement
    w.release.set()
    with pytest.raises(HomeAssistantError):
        await task
    assert not replacement.calls and not replacement.handlers and c.link_state == 'unknown'
    assert c.close_reason == ('generation_lost' if boundary == 'notify61' else 'explicit_close')
    unified.api.wire = w
    assert_clean(unified)


@pytest.mark.parametrize('outcome', ['success', 'refusal', 'exception'])
async def test_real_manager_unload_attempt_closes_even_when_refused(unified, outcome):
    from homeassistant.config_entries import ConfigEntryState
    from custom_components.casio_abl100 import async_unload_entry
    c = await connect(unified)
    if outcome == 'success':
        assert await unified.hass.config_entries.async_unload(unified.watch.entry_id)
        assert unified.watch.state is ConfigEntryState.NOT_LOADED and unified.session.closed
    else:
        with patch.object(unified.hass.config_entries, 'async_unload_platforms',
                          **({'return_value': False} if outcome == 'refusal' else
                             {'side_effect': RuntimeError('PRIVATE_PLATFORM')})):
            assert not await unified.hass.config_entries.async_unload(unified.watch.entry_id)
        assert unified.watch.state is ConfigEntryState.FAILED_UNLOAD and not unified.session.closed
        # Real producer refuses recovery. Direct integration teardown only:
        assert await async_unload_entry(unified.hass, unified.watch)
        unified.watch._async_set_state(unified.hass, ConfigEntryState.NOT_LOADED, None)
    assert c.phase == 'closed' and c.close_reason == 'unload'
    assert not unified.session.tasks and not unified.wire.handlers
    assert not unified.session.listeners and not unified.session.alarm_delivery_handles


async def test_shutdown_cancellation_still_stops_other_owners_and_listeners(unified):
    from unittest.mock import AsyncMock
    c = await connect(unified)
    s, w = unified.session, unified.wire
    s.auto_reader = type('StopOwner', (), {'stop': AsyncMock()})()
    s.subscribe(lambda: None)
    w.hold_at = 'disconnect'
    stop = asyncio.create_task(s.shutdown())
    await spin_until(w.entered.is_set)
    stop.cancel()
    await asyncio.sleep(0)
    stop.cancel()
    assert not stop.done()
    w.release.set()
    with pytest.raises(asyncio.CancelledError):
        await stop
    s.auto_reader.stop.assert_awaited_once()
    s.auto_reader = None
    assert s.closed and not s.listeners and not s.alarm_delivery_handles
    assert c.cleanup_result == 'success'
    assert_clean(unified)


async def test_two_real_entries_share_one_native_generation_and_unload_is_scoped(unified):
    from types import SimpleNamespace
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from homeassistant.config_entries import ConfigEntryState
    from custom_components.casio_abl100 import DOMAIN
    from tests.ha.test_native_alarm_runtime import SOURCE, FIRST
    s, w = unified.session, unified.wire
    address = 'AA:BB:CC:DD:EE:01'
    target = int(address.replace(':', ''), 16)
    other = MockConfigEntry(domain=DOMAIN, title='Other watch', data={'address': address}, unique_id=address)
    other.add_to_hass(unified.hass)
    def lookup(_hass, address, **_kwargs):
        return SimpleNamespace(address=address, name='ABL-100WE', details={'source': SOURCE})
    with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address', side_effect=lookup):
        assert await unified.hass.config_entries.async_setup(other.entry_id)
        await unified.hass.async_block_till_done()
        second = other.runtime_data
        a = await connect(unified)
        connecting = asyncio.create_task(second.connect_interactive())
        for _ in range(200):
            if connecting.done():
                break
            w.raw(target)
            await asyncio.sleep(0)
        await connecting
        b = second.interactive_session
        w.hold_at = 'response'
        refresh = asyncio.create_task(second.read_interactive())
        await spin_until(w.entered.is_set)
        try:
            assert await unified.hass.config_entries.async_unload(unified.watch.entry_id)
            assert a.phase == 'closed' and b.active and w.handlers
            w.hold_at = None
            w.notify(TARGET, 61, FIRST)
            await asyncio.sleep(0)
            assert second.alarm_snapshot is None
            w.notify(target, 61, FIRST)
            await refresh
            assert b.operation_result == 'success' and second.alarms_fresh
            assert s.alarm_snapshot is None
        finally:
            if other.state is ConfigEntryState.LOADED:
                await unified.hass.config_entries.async_unload(other.entry_id)
        assert not w.handlers and not unified.runtime.disconnect_callbacks


async def test_repeated_root_owner_cancellation_cannot_interrupt_joined_cleanup(unified):
    c = await connect(unified)
    w = unified.wire
    w.hold_at = 'disconnect'
    c.root.cancel()
    await spin_until(w.entered.is_set)
    c.root.cancel()
    await asyncio.sleep(0)
    c.root.cancel()
    await asyncio.sleep(0)
    held = not c.root.done()
    w.release.set()
    await asyncio.wait({c.root})
    assert held, 'Repeated owner cancellation must not abandon execution finalizer'
    assert c.cleanup_result == 'success' and c.close_reason == 'owner_cancelled'
    assert_clean(unified)


@pytest.mark.parametrize('phase', ['waiting', 'services'])
async def test_connect_caller_cancellation_closes_and_joins_admitted_stream(unified, phase):
    s, w = unified.session, unified.wire
    if phase == 'services':
        w.hold_at = phase
    caller = asyncio.create_task(s.connect_interactive())
    await spin_until(lambda: s.interactive_session is not None and s.interactive_session.phase == 'waiting')
    if phase == 'services':
        w.raw()
        await spin_until(w.entered.is_set)
    c = s.interactive_session
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert c.phase == 'closed' and c.close_reason == 'caller_cancelled'
    assert_clean(unified)


async def test_busy_refresh_cancellation_and_no_link_refusal_never_reset_observations(unified):
    s, w = unified.session, unified.wire
    with pytest.raises(HomeAssistantError):
        await s.read_interactive()
    c = await connect(unified)
    await s.read_interactive()
    old, stamp = s.alarm_snapshot, s.alarm_last_sync
    w.hold_at = 'response'
    owner = asyncio.create_task(s.read_interactive())
    await spin_until(w.entered.is_set)
    with pytest.raises(HomeAssistantError):
        await s.read_interactive()
    async def refused():
        asyncio.current_task().cancel()
        await s.read_interactive()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.create_task(refused())
    assert not owner.done() and c.active and c.close_reason is None
    assert s.alarm_snapshot is old and s.alarm_last_sync == stamp
    w.hold_at = None
    w.notify(TARGET, 61, w.frames[b'\x15'])
    await owner
    await s.close_interactive()
    assert_clean(unified)


async def test_primary_failure_reason_is_fixed_before_cleanup_and_later_close(unified):
    c = await connect(unified)
    s, w = unified.session, unified.wire
    w.failure_stage = 'get13'
    w.hold_at = 'disconnect'
    caller = asyncio.create_task(s.read_interactive())
    await spin_until(w.entered.is_set)
    reason_at_cleanup = c.close_reason
    closer = asyncio.create_task(s.close_interactive())
    await asyncio.sleep(0)
    w.release.set()
    await closer
    with pytest.raises(HomeAssistantError):
        await caller
    assert reason_at_cleanup == 'read_failed', 'Primary failure must be fixed before finalizer suspension'
    assert c.close_reason == 'read_failed' and c.cleanup_result == 'success'
    assert_clean(unified)


async def test_failed_second_refresh_preserves_last_good_unattempted_values(unified):
    from custom_components.casio_abl100.native_sync import DESTINATIONS
    s, w = unified.session, unified.wire
    c = await connect(unified)
    await s.read_interactive()
    before = [(getattr(s, value), getattr(s, timestamp)) for value, timestamp, _ in DESTINATIONS]
    w.failure_stage = 'get13'
    with pytest.raises(HomeAssistantError):
        await s.read_interactive()
    for index, (value, timestamp, freshness) in enumerate(DESTINATIONS):
        assert getattr(s, freshness) is (index < 3)
        if index >= 3:
            assert getattr(s, value) is before[index][0] and getattr(s, timestamp) == before[index][1]
    assert c.operation_result == 'partial' and c.group_results['auto_time'] == 'not_attempted'
    assert_clean(unified)


async def test_prequery_repeated_frame_does_not_satisfy_next_query(unified):
    c = await connect(unified)
    s, w = unified.session, unified.wire
    await s.read_interactive()
    first = s.alarm_snapshot
    observed = []
    original = c.reader.adapter.start_primary
    async def prequery():
        # Receiver exists but query invocation has not started. Do not alter
        # production framing or inject any identity/filtering in this adapter.
        w.notify(TARGET, 61, w.frames[b'\x15'])
        observed.append(c.reader.adapter.query_index)
        await original()
    with patch.object(c.reader.adapter, 'start_primary', side_effect=prequery):
        with pytest.raises(HomeAssistantError):
            await s.read_interactive()
    assert observed == [0] and s.alarm_snapshot is first and not s.alarms_fresh
    assert_clean(unified)


@pytest.mark.parametrize('mode', ['steps', 'all'])
async def test_real_automatic_admission_skips_while_interactive_owner_holds_link(unified, mode):
    from custom_components.casio_abl100.auto_read import AutoStepReader
    from tests.ha.test_auto_runtime import advertisement
    from bluetooth_data_tools import monotonic_time_coarse
    s, w = unified.session, unified.wire
    c = await connect(unified)
    reader = AutoStepReader(unified.hass, unified.watch, s, mode=mode)
    now = monotonic_time_coarse()
    reader.start()
    before = list(w.calls)
    try:
        assert reader._busy()
        if mode == 'all':
            w.raw()
        else:
            reader._advertisement(advertisement(now), None)
        await asyncio.sleep(0)
        assert reader._task is None and w.calls == before
        await s.close_interactive()
        # The same observed opportunity must not become deferred work.
        if mode == 'all':
            w.raw()
        else:
            reader._advertisement(advertisement(now), None)
        await asyncio.sleep(0)
        assert reader._task is None and w.calls[:-1] == before
    finally:
        await reader.stop()
    assert_clean(unified)
