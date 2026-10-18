"""Real HA entry/service and installed native methods; synthetic wire only."""
import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import pytest
from aioesphomeapi.api_pb2 import (
    BluetoothDeviceConnectionResponse, BluetoothDeviceRequest,
    BluetoothGATTGetServicesRequest, BluetoothGATTGetServicesResponse,
    BluetoothGATTGetServicesDoneResponse, BluetoothGATTNotifyDataResponse,
    BluetoothGATTNotifyRequest, BluetoothGATTNotifyResponse,
    BluetoothGATTWriteDescriptorRequest, BluetoothGATTWriteRequest,
    BluetoothGATTWriteResponse, BluetoothLERawAdvertisementsResponse,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from tests.ha.test_native_alarm_runtime import native, spin_until, ADDRESS, TARGET, FIRST, REST
from tests.ha.test_native_step_runtime import metadata
from tests.protocol.test_daily_history import history_record

BUTTON = 'button.casio_abl_100we_read_watch_data'
GROUPS = ('alarms', 'steps', 'zones', 'basic_settings', 'auto_time', 'timer')
QUERIES = (b'\x15', b'\x16', b'\x00\x11\x00\x00\x00', b'\x1d\x00', b'\x1e\x00', b'\x1e\x01', b'\x13', b'\x11', b'\x18')
FRAMES = {
    b'\x15': FIRST, b'\x16': REST,
    b'\x1d\x00': b'\x1d\0\0\x03' + bytes(11),
    b'\x1e\x00': b'\x1e\0\0\0\x04\x04\x01',
    b'\x1e\x01': b'\x1e\x01\0\0\xfc\0\x02',
    b'\x13': b'\x13\x01\x01' + bytes(9),
    b'\x11': b'\x11' + bytes(11) + b'\0\x21\0',
    b'\x18': b'\x18\x01\x02\x03' + bytes(11),
}


class SyncWire:
    """A single producer, honoring actual library append/stop predicates.

    No client methods or filtering are replaced. Wrong-address and wrong-handle
    replies are delivered to these predicates and real notification callbacks.
    """
    def __init__(self):
        self.calls = []
        self.handlers = set()
        self.failure_stage = None
        self.failure = RuntimeError('PRIVATE_WIRE_FAULT')
        self.hold_at = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.frames = dict(FRAMES)
        self.record_date = date(2026, 10, 18)
        self.mode = 'valid'
        self.before = None
        self.after = None
        self.missing_steps = False
        self.predicate_checks = []

    def add_message_callback(self, callback, types):
        key = (callback, types)
        self.handlers.add(key)
        return lambda: self.handlers.discard(key)

    def emit(self, message):
        for callback, types in tuple(self.handlers):
            if type(message) in types:
                callback(message)

    def raw(self, address=TARGET, address_type=1):
        message = BluetoothLERawAdvertisementsResponse()
        message.advertisements.add(address=address, address_type=address_type)
        self.emit(message)

    def notify(self, address, handle, data):
        self.emit(BluetoothGATTNotifyDataResponse(address=address, handle=handle, data=data))

    def send_message_callback_response(self, request, callback, types):
        remove = self.add_message_callback(callback, types)
        self.send_message(request)
        return remove

    def stage(self, request):
        if isinstance(request, BluetoothDeviceRequest):
            return 'disconnect' if int(request.request_type) == 1 else 'connect'
        if isinstance(request, BluetoothGATTGetServicesRequest):
            return 'services'
        if isinstance(request, BluetoothGATTNotifyRequest):
            return f'notify{request.handle}'
        if isinstance(request, BluetoothGATTWriteDescriptorRequest):
            return f'cccd{request.handle}'
        return 'step' if request.data == QUERIES[2] else f'get{bytes(request.data).hex()}'

    def responses(self, request):
        target = request.address
        if isinstance(request, BluetoothDeviceRequest):
            return [BluetoothDeviceConnectionResponse(address=target, connected=int(request.request_type) != 1, mtu=512)]
        if isinstance(request, BluetoothGATTGetServicesRequest):
            response = BluetoothGATTGetServicesResponse(address=target)
            def words(value):
                raw = UUID(value).int
                return [raw >> 64, raw & ((1 << 64) - 1)]
            for service in metadata().services:
                s = response.services.add(uuid=words(service.uuid), handle=service.handle)
                for char in service.characteristics:
                    if self.missing_steps and char.handle in (71, 81):
                        continue
                    c = s.characteristics.add(uuid=words(char.uuid), handle=char.handle, properties=char.properties)
                    for desc in char.descriptors:
                        c.descriptors.add(uuid=words(desc.uuid), handle=desc.handle)
            return [response, BluetoothGATTGetServicesDoneResponse(address=target)]
        if isinstance(request, BluetoothGATTNotifyRequest):
            return [BluetoothGATTNotifyResponse(address=target, handle=request.handle)]
        return [BluetoothGATTWriteResponse(address=target, handle=request.handle)]

    def send_message(self, request):
        self.calls.append(request)
        stage = self.stage(request)
        if self.before:
            self.before(stage, request)
        if self.failure_stage == stage:
            raise self.failure
        if isinstance(request, BluetoothGATTWriteRequest):
            if self.hold_at == 'response':
                self.entered.set()
                return
            if request.data == QUERIES[2]:
                raw = history_record(self.record_date)
                self.notify(request.address, 71, b'\0\x11\x90\x01\0')
                self.notify(request.address, 81, raw[:200])
                self.notify(request.address, 81, raw[200:])
                if self.mode == 'duplicate_step':
                    self.notify(request.address, 71, b'\0\x11\x90\x01\0')
            else:
                frame = self.frames[bytes(request.data)]
                self.notify(request.address + 1, 61, b'\xff')
                self.notify(request.address, 999, b'\xff')
                self.notify(request.address, 61, frame)
                if self.mode == f'duplicate{bytes(request.data).hex()}':
                    asyncio.get_running_loop().call_soon(self.notify, request.address, 61, frame)
        for response in self.responses(request):
            self.emit(response)
        if self.after:
            self.after(stage, request)

    async def send_messages_await_response_complex(self, requests, append, stop, types, timeout):
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        replies = []
        def receive(message):
            added, done = append(message), stop(message)
            self.predicate_checks.append((type(message).__name__, message.address, added, done))
            if added:
                replies.append(message)
            if done and not future.done():
                future.set_result(replies)
        remove = self.add_message_callback(receive, types)
        try:
            for request in requests:
                stage = self.stage(request)
                if self.hold_at == stage:
                    self.entered.set()
                    await self.release.wait()
                # Probe the real request-provided predicates, not a copied filter.
                for response in self.responses(request):
                    wrong = type(response)()
                    wrong.CopyFrom(response)
                    wrong.address += 1
                    self.emit(wrong)
                    if hasattr(wrong, 'handle'):
                        wrong.address = request.address
                        wrong.handle += 1000
                        self.emit(wrong)
                self.send_message(request)
            return await asyncio.wait_for(future, timeout)
        finally:
            remove()
            if not future.done():
                future.cancel()


@pytest.fixture
async def unified(native):
    wire = SyncWire()
    native.wire = wire
    native.api.wire = wire
    with patch('custom_components.casio_abl100.dt_util.now', return_value=datetime(2026, 10, 18, tzinfo=timezone.utc)):
        yield native


async def press(unified):
    await unified.hass.services.async_call('button', 'press', {'entity_id': BUTTON}, blocking=True)


async def armed(unified):
    task = asyncio.create_task(press(unified))
    await spin_until(lambda: task.done() or unified.session.sync_phase == 'waiting')
    if task.done():
        task.result()
    assert unified.session.sync_phase == 'waiting'
    return task


def assert_clean(unified):
    assert not unified.wire.handlers
    assert not unified.runtime.disconnect_callbacks
    assert not unified.session.tasks
    assert not unified.session.lock.locked()
    assert not unified.api._notify_callbacks
    live = [t for t in asyncio.all_tasks() if not t.done() and any(
        token in getattr(t.get_coro(), '__qualname__', '')
        for token in ('NativeRead.read', 'NativeSyncRead.read', 'NativeSyncAdapter.', '_primary.<locals>', 'read_step_group.<locals>'))]
    assert not live
    unified.bleak.assert_not_called()


@pytest.mark.asyncio
async def test_public_unified_action_is_registered_without_io(unified):
    registry = er.async_get(unified.hass)
    keys = {e.unique_id.removeprefix(ADDRESS + '_') for e in registry.entities.values() if e.config_entry_id == unified.watch.entry_id}
    assert 'read_all' in keys, 'Missing public unified action'
    assert not unified.wire.calls


async def drive(task, unified):
    """Feed fresh adverts while the public task waits; no fake readiness."""
    for _ in range(1000):
        if task.done():
            return await task
        unified.wire.raw()
        await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    pytest.fail('Public read did not finish')


@pytest.mark.asyncio
async def test_one_link_alarm_first_six_groups_through_real_button(unified):
    task = asyncio.create_task(press(unified))
    try:
        await drive(task, unified)
        queries = [bytes(x.data) for x in unified.wire.calls if isinstance(x, BluetoothGATTWriteRequest)]
        connects = [x for x in unified.wire.calls if isinstance(x, BluetoothDeviceRequest) and int(x.request_type) != 1]
        assert len(connects) == 1, 'Unified action must not reconnect for steps'
        assert queries == list(QUERIES)
        assert sum(isinstance(x, BluetoothGATTGetServicesRequest) for x in unified.wire.calls) == 1
        assert [(x.handle, x.enable) for x in unified.wire.calls if isinstance(x, BluetoothGATTNotifyRequest)] == [(61, True), (71, True), (81, True)]
        assert [x.handle for x in unified.wire.calls if isinstance(x, BluetoothGATTWriteDescriptorRequest)] == [62, 72, 82]
        assert sum(isinstance(x, BluetoothDeviceRequest) and int(x.request_type) == 1 for x in unified.wire.calls) == 1
        assert unified.session.sync_group_results == dict.fromkeys(GROUPS, 'success')
        assert unified.session.sync_result == 'success'
        assert unified.session.sync_cleanup_result == 'success'
        await unified.hass.async_block_till_done()
        assert unified.hass.states.get('sensor.casio_abl_100we_alarm_1').state == '07:35'
        state = unified.hass.states.get('sensor.casio_abl_100we_steps_today')
        assert state.state == '1485'
        button = unified.hass.states.get(BUTTON)
        assert {key: button.attributes[key] for key in ('phase', 'wait_expires_at', 'result', 'group_results', 'cleanup_result')} == {
            'phase': 'idle', 'wait_expires_at': None, 'result': 'success',
            'group_results': dict.fromkeys(GROUPS, 'success'), 'cleanup_result': 'success'}
        assert unified.wire.predicate_checks
        rejected = [row for row in unified.wire.predicate_checks if row[1] == TARGET + 1]
        assert rejected and all(row[2:] == (False, False) for row in rejected)
        assert_clean(unified)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_valid_alarm_is_committed_before_later_step_failure_and_outer_cleanup(unified):
    unified.wire.failure_stage = 'cccd72'
    unified.wire.hold_at = 'disconnect'
    task = asyncio.create_task(press(unified))
    try:
        for _ in range(500):
            unified.wire.raw()
            await asyncio.sleep(0)
            if unified.wire.entered.is_set() or task.done():
                break
        assert unified.wire.entered.is_set()
        assert unified.session.alarm_snapshot is not None, 'Validated alarms must commit before outer cleanup'
        assert unified.session.alarm_last_sync.tzinfo == timezone.utc
        assert unified.session.alarms_fresh
        unified.wire.release.set()
        with pytest.raises(HomeAssistantError):
            await drive(task, unified)
        assert unified.session.alarms_fresh
        assert unified.session.sync_result == 'partial'
        assert unified.session.sync_group_results == dict(zip(GROUPS, ('success', 'failed', 'not_attempted', 'not_attempted', 'not_attempted', 'not_attempted'), strict=True))
        assert_clean(unified)
    finally:
        unified.wire.release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_new_settings_entities_and_independent_observations(unified):
    keys = {e.unique_id.removeprefix(ADDRESS + '_') for e in er.async_get(unified.hass).entities.values() if e.config_entry_id == unified.watch.entry_id}
    from tests.ha.test_zone_entities import OLD_KEYS, ZONE_KEYS
    assert keys == OLD_KEYS | ZONE_KEYS | {'read_all', 'time_format', 'automatic_time_adjustment', 'timer_encoded_duration'}
    prefix = 'sensor.casio_abl_100we_'
    for key in ('time_format', 'automatic_time_adjustment', 'timer_encoded_duration'):
        assert unified.hass.states.get(prefix + key).state == 'unavailable'
    task = await armed(unified)
    unified.wire.raw()
    await task
    await unified.hass.async_block_till_done()
    time_format = unified.hass.states.get(prefix + 'time_format')
    assert time_format.state == '24h'
    assert {key: time_format.attributes[key] for key in ('button_tone_enabled', 'light_duration_mode', 'fresh')} == {'button_tone_enabled': True, 'light_duration_mode': 'long', 'fresh': True}
    assert 'power_saving_enabled' not in time_format.attributes
    auto = unified.hass.states.get(prefix + 'automatic_time_adjustment')
    assert auto.state == 'enabled' and auto.attributes['adjustment_minute'] == 33
    timer = unified.hass.states.get(prefix + 'timer_encoded_duration')
    assert timer.state == '3723' and timer.attributes['unit_of_measurement'] == 's'
    assert [timer.attributes[k] for k in ('encoded_hours', 'encoded_minutes', 'encoded_seconds')] == [1, 2, 3]
    for state in (time_format, auto, timer):
        assert datetime.fromisoformat(state.attributes['last_read']).tzinfo == timezone.utc


@pytest.mark.asyncio
async def test_unknown_auto_time_and_encoded_zero_are_not_invented(unified):
    unified.wire.frames[b'\x11'] = b'\x11' + bytes(11) + b'\x01\xff\0'
    unified.wire.frames[b'\x18'] = b'\x18' + bytes(14)
    task = await armed(unified)
    unified.wire.raw()
    await task
    await unified.hass.async_block_till_done()
    state = unified.hass.states.get('sensor.casio_abl_100we_automatic_time_adjustment')
    # HA omits extra attributes for unavailable entities; unknown is retained
    # in the actual observation, not converted into a disabled enum.
    assert state.state == 'unavailable'
    assert unified.session.auto_time_snapshot.enabled is None
    assert unified.session.auto_time_snapshot.adjustment_minute is None
    assert unified.hass.states.get('sensor.casio_abl_100we_timer_encoded_duration').state == '0'


@pytest.mark.asyncio
@pytest.mark.parametrize('stage,group', [('get15', 0), ('get16', 0), ('notify71', 1), ('cccd72', 1), ('step', 1), ('get1d00', 2), ('get1e00', 2), ('get1e01', 2), ('get13', 3), ('get11', 4), ('get18', 5)])
@pytest.mark.parametrize('cleanup_fault', [False, True])
async def test_each_failure_stops_remaining_groups_retains_last_good(unified, stage, group, cleanup_fault, caplog):
    task = await armed(unified)
    unified.wire.raw()
    await task
    from custom_components.casio_abl100.native_sync import DESTINATIONS
    before = [(getattr(unified.session, v), getattr(unified.session, t)) for v, t, _f in DESTINATIONS]
    unified.wire.calls.clear()
    unified.wire.failure_stage = stage
    if cleanup_fault:
        def fail_cleanup(boundary, request):
            if boundary == 'disconnect':
                raise RuntimeError('PRIVATE_CLEANUP')
        unified.wire.before = fail_cleanup
    task = await armed(unified)
    unified.wire.raw()
    with pytest.raises(HomeAssistantError) as caught:
        await task
    assert caught.value.translation_key == 'sync_read_failed'
    assert caught.value.__cause__ is unified.wire.failure
    assert 'PRIVATE' not in str(caught.value.args)
    assert 'PRIVATE' not in caplog.text
    for i, (v, t, f) in enumerate(DESTINATIONS):
        if i < group:
            assert getattr(unified.session, f) is True
            assert getattr(unified.session, t) >= before[i][1]
        else:
            assert getattr(unified.session, v) is before[i][0]
            assert getattr(unified.session, t) == before[i][1]
            assert getattr(unified.session, f) is False
    assert list(unified.session.sync_group_results.values()) == ['success'] * group + ['failed'] + ['not_attempted'] * (5 - group)
    assert unified.session.sync_result == ('partial' if group else 'failed')
    assert unified.session.sync_cleanup_result == ('failed' if cleanup_fault else 'success')
    assert_clean(unified)


@pytest.mark.asyncio
@pytest.mark.parametrize('variant', ['missing_metadata', 'stale_date', 'duplicate_step', 'duplicate15', 'duplicate16', 'duplicate1d00', 'duplicate1e00', 'duplicate1e01', 'duplicate13', 'duplicate11', 'duplicate18', 'malformed15', 'malformed16', 'malformed1e01', 'malformed13'])
async def test_group_validation_does_not_rollback_prior_groups(unified, variant):
    if variant == 'missing_metadata':
        unified.wire.missing_steps = True
    elif variant == 'stale_date':
        unified.wire.record_date = date(2026, 10, 17)
    elif variant.startswith('malformed'):
        query = bytes.fromhex(variant.removeprefix('malformed'))
        unified.wire.frames[query] = unified.wire.frames[query][:-1]
    else:
        unified.wire.mode = variant
    task = await armed(unified)
    unified.wire.raw()
    with pytest.raises(HomeAssistantError):
        await task
    assert unified.session.sync_result in ('partial', 'failed')
    results = list(unified.session.sync_group_results.values())
    assert results.count('failed') == 1
    failed = results.index('failed')
    assert results[:failed] == ['success'] * failed
    assert results[failed + 1:] == ['not_attempted'] * (5 - failed)
    if failed:
        assert unified.session.alarms_fresh and unified.session.alarm_last_sync is not None
    assert_clean(unified)


@pytest.mark.asyncio
async def test_cleanup_only_failure_keeps_all_committed_groups(unified):
    unified.wire.failure_stage = 'disconnect'
    task = await armed(unified)
    unified.wire.raw()
    with pytest.raises(HomeAssistantError):
        await task
    assert unified.session.sync_result == 'partial'
    assert unified.session.sync_cleanup_result == 'failed'
    assert unified.session.sync_group_results == dict.fromkeys(GROUPS, 'success')
    assert unified.session.alarms_fresh and unified.session.fresh and unified.session.timer_fresh
    assert_clean(unified)


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['notify61', 'cccd62', 'notify71', 'cccd72', 'step', 'disconnect'])
@pytest.mark.parametrize('prior_failure', [False, True])
async def test_repeated_caller_cancellation_joins_cleanup_and_preserves_prior_groups(unified, stage, prior_failure):
    unified.wire.hold_at = stage
    if prior_failure:
        unified.wire.failure_stage = 'get13' if stage == 'disconnect' else stage
    task = asyncio.create_task(unified.session.read_all())
    await spin_until(lambda: unified.session.sync_phase == 'waiting' or task.done())
    unified.wire.raw()
    await spin_until(lambda: unified.wire.entered.is_set() or task.done())
    assert unified.wire.entered.is_set()
    snapshot = unified.session.alarm_snapshot
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    unified.wire.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert unified.session.sync_result == 'cancelled'
    if snapshot is not None:
        assert unified.session.alarm_snapshot is snapshot and unified.session.alarms_fresh
    assert_clean(unified)


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['services', 'notify61', 'cccd62', 'get15', 'get16', 'notify71', 'step', 'get13', 'disconnect'])
async def test_direct_backend_cancel_is_failure_not_caller_cancellation(unified, stage):
    unified.wire.failure_stage = stage
    unified.wire.failure = asyncio.CancelledError('PRIVATE_BACKEND')
    task = asyncio.create_task(unified.session.read_all())
    await spin_until(lambda: unified.session.sync_phase == 'waiting')
    unified.wire.raw()
    with pytest.raises(HomeAssistantError) as caught:
        await task
    assert task.cancelling() == 0
    assert caught.value.translation_key == 'sync_read_failed'
    assert unified.session.sync_result in ('failed', 'partial')
    assert_clean(unified)


@pytest.mark.asyncio
async def test_preentry_and_queued_cancellation_do_not_own_active_status(unified):
    async def already_cancelled():
        asyncio.current_task().cancel()
        await unified.session.read_all()
    task = asyncio.create_task(already_cancelled())
    with pytest.raises(asyncio.CancelledError):
        await task
    assert unified.session.sync_result == 'never' and not unified.wire.calls
    active = await armed(unified)
    previous = dict(unified.session.sync_group_results)
    expiry = unified.session.sync_wait_expires_at
    queued = asyncio.create_task(unified.session.read_all())
    await spin_until(lambda: queued in unified.session.tasks)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    assert unified.session.sync_phase == 'waiting'
    assert unified.session.sync_wait_expires_at == expiry
    assert unified.session.sync_group_results == previous
    unified.wire.raw()
    await active
    assert_clean(unified)


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['connect', 'services', 'notify61', 'cccd62', 'notify71', 'cccd72', 'step', 'disconnect'])
async def test_generation_replacement_never_receives_io(unified, stage):
    replacement = SyncWire()
    def replace(boundary, request):
        if boundary == stage:
            unified.api.wire = replacement
    unified.wire.before = replace
    task = await armed(unified)
    unified.wire.raw()
    with pytest.raises(HomeAssistantError):
        await task
    assert not replacement.calls and not replacement.handlers
    assert_clean(unified)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['observer', 'scheduler', 'observer_cancel', 'scheduler_cancel'])
async def test_delivery_fault_does_not_undo_committed_groups(unified, fault, caplog):
    delivered = []
    def broken():
        if 'cancel' in fault:
            raise asyncio.CancelledError('PRIVATE_OBSERVER')
        raise RuntimeError('PRIVATE_OBSERVER')
    if fault.startswith('scheduler'):
        class BrokenLoop:
            def call_soon(self, cb):
                broken()
        from tests.ha.test_native_step_acceptance_runtime import LoopOwner
        real_hass = unified.session.hass
        unified.session.hass = LoopOwner(real_hass, BrokenLoop())
    else:
        remove = unified.session.subscribe(broken)
    remove_healthy = unified.session.subscribe(lambda: delivered.append(unified.session.sync_result))
    try:
        task = asyncio.create_task(unified.session.read_all())
        await spin_until(lambda: unified.session.sync_phase == 'waiting')
        unified.wire.raw()
        await task
        await unified.hass.async_block_till_done()
        assert unified.session.sync_result == 'success'
        assert unified.session.alarms_fresh and unified.session.timer_fresh
        assert not unified.session.alarm_delivery_handles
        assert 'PRIVATE' not in caplog.text
        if fault.startswith('observer'):
            assert 'success' in delivered
        else:
            assert not delivered
        assert_clean(unified)
    finally:
        remove_healthy()
        if fault.startswith('scheduler'):
            unified.session.hass = real_hass
        else:
            remove()


@pytest.mark.asyncio
async def test_two_entries_share_wire_filter_and_unload_ownership(unified):
    from homeassistant.config_entries import ConfigEntryState
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from custom_components.casio_abl100 import DOMAIN
    from tests.ha.test_native_alarm_runtime import SOURCE
    other_address = 'AA:BB:CC:DD:EE:01'
    other = MockConfigEntry(domain=DOMAIN, title='Other watch', data={'address': other_address}, unique_id=other_address)
    other.add_to_hass(unified.hass)
    device = SimpleNamespace(address=other_address, name='ABL-100WE', details={'source': SOURCE})
    with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address', return_value=device):
        assert await unified.hass.config_entries.async_setup(other.entry_id)
        await unified.hass.async_block_till_done()
        # Both entries use the same real APIClient and single external wire.
        unified.wire.hold_at = 'response'
        # Dispatch selected per-address discovery values, not per-entry wires.
    def lookup(_hass, address, **_kw):
        return SimpleNamespace(address=address, name='ABL-100WE', details={'source': SOURCE})
    with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address', side_effect=lookup):
        a = asyncio.create_task(unified.session.read_all())
        b = asyncio.create_task(other.runtime_data.read_all())
        try:
            await spin_until(lambda: unified.session.sync_phase == other.runtime_data.sync_phase == 'waiting')
            unified.wire.raw(TARGET)
            unified.wire.raw(int(other_address.replace(':', ''), 16))
            await spin_until(lambda: len([x for x in unified.wire.calls if isinstance(x, BluetoothGATTWriteRequest)]) == 2)
            assert unified.session.alarm_snapshot is other.runtime_data.alarm_snapshot is None
            assert await unified.hass.config_entries.async_unload(unified.watch.entry_id)
            with pytest.raises(asyncio.CancelledError):
                await a
            assert not b.done() and unified.wire.handlers
            target_b = int(other_address.replace(':', ''), 16)
            unified.wire.hold_at = None
            # The outstanding first query for B is completed only by B's frame.
            unified.wire.notify(TARGET, 61, FIRST)
            unified.wire.notify(target_b, 999, FIRST)
            await asyncio.sleep(0)
            assert other.runtime_data.alarm_snapshot is None
            unified.wire.notify(target_b, 61, FIRST)
            await b
            assert other.runtime_data.sync_result == 'success'
            assert other.runtime_data.alarms_fresh
            assert unified.session.alarm_snapshot is None
            assert not unified.wire.handlers
            assert await unified.hass.config_entries.async_unload(other.entry_id)
        finally:
            unified.wire.release.set()
            for task in (a, b):
                if not task.done():
                    task.cancel()
            await asyncio.gather(a, b, return_exceptions=True)
            if other.state is ConfigEntryState.LOADED:
                await unified.hass.config_entries.async_unload(other.entry_id)


@pytest.mark.asyncio
@pytest.mark.parametrize('individual,first', [('read_alarms', True), ('read_alarms', False), ('read', True), ('read', False)])
async def test_shared_mutex_serializes_both_orders_without_false_readiness(unified, individual, first):
    session = unified.session
    active = asyncio.create_task(getattr(session, individual)() if first else session.read_all())
    active_phase = 'alarm_phase' if individual == 'read_alarms' else 'step_phase'
    await spin_until(lambda: getattr(session, active_phase if first else 'sync_phase') == 'waiting')
    queued = asyncio.create_task(session.read_all() if first else getattr(session, individual)())
    await spin_until(lambda: len(session.tasks) == 2)
    assert getattr(session, 'sync_phase' if first else active_phase) == 'idle'
    assert not unified.wire.calls
    unified.wire.raw()
    await active
    await spin_until(lambda: getattr(session, 'sync_phase' if first else active_phase) == 'waiting')
    unified.wire.raw()
    await queued
    assert session.sync_result == 'success'
    assert_clean(unified)


@pytest.mark.asyncio
@pytest.mark.parametrize('cleanup', ['success', 'timeout', 'backend_cancel'])
async def test_no_reply_timeout_stops_groups_and_preserves_original_failure(unified, cleanup):
    # Shorten only the fixed group's response future wait in a synthetic test.
    import custom_components.casio_abl100.sync_transport as transport
    original = asyncio.wait_for
    async def bounded(operation, timeout):
        if timeout == 12 and isinstance(operation, asyncio.Future):
            timeout = .01
        return await original(operation, timeout)
    unified.wire.hold_at = 'response'
    if cleanup != 'success':
        def fail(stage, request):
            if stage == 'disconnect':
                if cleanup == 'timeout':
                    raise TimeoutError('PRIVATE_CLEANUP_TIMEOUT')
                raise asyncio.CancelledError('PRIVATE_CLEANUP_CANCEL')
        unified.wire.before = fail
    with patch.object(transport.asyncio, 'wait_for', bounded):
        task = await armed(unified)
        unified.wire.raw()
        with pytest.raises(HomeAssistantError) as caught:
            await task
    assert isinstance(caught.value.__cause__, TimeoutError)
    assert unified.session.sync_result == 'failed'
    assert unified.session.sync_group_results['alarms'] == 'failed'
    assert list(unified.session.sync_group_results.values())[1:] == ['not_attempted'] * 5
    assert unified.session.sync_cleanup_result == ('success' if cleanup == 'success' else 'failed')
    assert_clean(unified)


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['get15', 'step', 'get13', 'disconnect'])
@pytest.mark.parametrize('kind', ['raw', 'translated_lookalike'])
async def test_private_failures_are_not_trusted_by_framework_error_shape(unified, stage, kind, caplog):
    from custom_components.casio_abl100.native_sync import _SyncReadFailed
    error = RuntimeError('PRIVATE_RAW') if kind == 'raw' else _SyncReadFailed(
        'PRIVATE_LOOKALIKE', translation_domain='casio_abl100',
        translation_key='sync_read_failed', translation_placeholders={'private': 'PRIVATE_PLACEHOLDER'})
    unified.wire.failure_stage = stage
    unified.wire.failure = error
    task = await armed(unified)
    unified.wire.raw()
    with pytest.raises(HomeAssistantError) as caught:
        await task
    assert caught.value is not error
    assert caught.value.__cause__ is error
    assert caught.value.translation_key == 'sync_read_failed'
    assert not caught.value.translation_placeholders
    assert 'PRIVATE' not in str(caught.value.args)
    assert 'PRIVATE' not in str(caught.value)
    assert 'PRIVATE' not in caplog.text
    assert_clean(unified)


@pytest.mark.asyncio
async def test_invalid_immediate_frame_then_write_failure_has_no_unretrieved_future(unified, caplog):
    import gc
    loop = asyncio.get_running_loop()
    old = loop.get_exception_handler()
    errors = []
    loop.set_exception_handler(lambda _loop, context: errors.append(context))
    original = unified.wire.send_message
    error = RuntimeError('PRIVATE_AFTER_MALFORMED')
    def send(request):
        if isinstance(request, BluetoothGATTWriteRequest) and request.data == b'\x15':
            unified.wire.calls.append(request)
            unified.wire.notify(request.address, 61, b'\x15')
            raise error
        return original(request)
    unified.wire.send_message = send
    try:
        task = asyncio.create_task(unified.session.read_all())
        await spin_until(lambda: unified.session.sync_phase == 'waiting')
        unified.wire.raw()
        try:
            await task
        except HomeAssistantError as caught:
            assert caught.__cause__ is error
            caught.__traceback__ = None
        else:
            pytest.fail('Malformed transaction succeeded')
        error.__traceback__ = None
        del task, error
        await asyncio.sleep(0)
        gc.collect()
        await asyncio.sleep(0)
        assert not errors
        assert 'PRIVATE' not in caplog.text
        assert_clean(unified)
    finally:
        loop.set_exception_handler(old)


@pytest.mark.asyncio
@pytest.mark.parametrize('variant', ['cancel', 'wrong_owner', 'closed'])
async def test_final_group_commit_checks_after_timestamp_acquisition(unified, variant):
    original = __import__('custom_components.casio_abl100.native_sync', fromlist=['dt_util']).dt_util.utcnow
    entry_owner = unified.watch.runtime_data
    def timestamp():
        value = original()
        if unified.session.sync_group_results['basic_settings'] == 'reading':
            if variant == 'cancel':
                next(iter(unified.session.tasks)).cancel()
            elif variant == 'closed':
                unified.session.closed = True
            else:
                unified.watch.runtime_data = SimpleNamespace()
        return value
    task = await armed(unified)
    try:
        with patch('custom_components.casio_abl100.native_sync.dt_util.utcnow', side_effect=timestamp):
            unified.wire.raw()
            if variant in ('cancel', 'closed'):
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                with pytest.raises(HomeAssistantError):
                    await task
        assert unified.session.basic_settings_snapshot is None
        assert unified.session.alarm_snapshot is not None and unified.session.alarms_fresh
        assert unified.session.auto_time_snapshot is unified.session.timer_snapshot is None
        assert_clean(unified)
    finally:
        unified.watch.runtime_data = entry_owner
        # Synthetic close flag exercises the no-commit gate; let real unload
        # still own platform cleanup rather than pretending it ran here.


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['get15', 'step', 'get13'])
async def test_backend_link_loss_is_failed_or_partial_not_caller_abandonment(unified, stage):
    def lose(boundary, request):
        if boundary == stage:
            for invalidate in tuple(unified.runtime.disconnect_callbacks):
                invalidate()
    unified.wire.before = lose
    task = await armed(unified)
    unified.wire.raw()
    with pytest.raises(HomeAssistantError):
        await task
    assert unified.session.sync_result == ('failed' if stage == 'get15' else 'partial')
    assert task.cancelling() == 0
    if stage != 'get15':
        assert unified.session.alarm_snapshot is not None and unified.session.alarms_fresh
    assert_clean(unified)


@pytest.mark.asyncio
async def test_registration_fault_and_wrong_watch_owner_fail_closed(unified):
    original = unified.wire.add_message_callback
    def refuse(callback, types):
        raise RuntimeError('PRIVATE_REGISTER')
    unified.wire.add_message_callback = refuse
    with pytest.raises(HomeAssistantError):
        await unified.session.read_all()
    assert unified.session.sync_result == 'failed' and not unified.wire.calls
    assert_clean(unified)
    unified.wire.add_message_callback = original
    task = await armed(unified)
    owner = unified.watch.runtime_data
    unified.watch.runtime_data = SimpleNamespace()
    unified.wire.raw()
    try:
        with pytest.raises(HomeAssistantError):
            await task
        assert not unified.wire.calls
        assert owner.sync_result == 'failed'
        assert_clean(unified)
    finally:
        unified.watch.runtime_data = owner


@pytest.mark.asyncio
async def test_unload_held_cleanup_blocks_late_commit_and_reload_keeps_identity(unified):
    ids = {e.unique_id: (e.entity_id, e.device_id) for e in er.async_get(unified.hass).entities.values() if e.config_entry_id == unified.watch.entry_id}
    unified.wire.failure_stage = 'step'
    unified.wire.hold_at = 'disconnect'
    task = await armed(unified)
    unified.wire.raw()
    await spin_until(lambda: unified.wire.entered.is_set())
    snap = unified.session.alarm_snapshot
    assert snap is not None and unified.session.alarms_fresh
    unload = asyncio.create_task(unified.hass.config_entries.async_unload(unified.watch.entry_id))
    await spin_until(lambda: unified.session.closed)
    unified.wire.notify(TARGET, 61, REST)
    unified.wire.notify(TARGET, 71, b'\0\x11\x90\x01\0')
    assert not unload.done()
    unified.wire.release.set()
    assert await unload
    with pytest.raises(asyncio.CancelledError):
        await task
    assert unified.session.alarm_snapshot is snap and unified.session.record is None
    assert not unified.session.alarm_delivery_handles and not unified.session.listeners
    assert_clean(unified)
    unified.wire.failure_stage = unified.wire.hold_at = None
    calls = len(unified.wire.calls)
    assert await unified.hass.config_entries.async_setup(unified.watch.entry_id)
    await unified.hass.async_block_till_done()
    assert {e.unique_id: (e.entity_id, e.device_id) for e in er.async_get(unified.hass).entities.values() if e.config_entry_id == unified.watch.entry_id} == ids
    assert len(unified.wire.calls) == calls
    assert unified.watch.runtime_data.sync_result == 'never'
    assert unified.watch.runtime_data.alarm_snapshot is None


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['en', 'es'])
async def test_actual_translated_new_names_and_default_registry_ids(hass, enable_custom_integrations, language):
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from custom_components.casio_abl100 import DOMAIN
    hass.config.language = language
    watch = MockConfigEntry(domain=DOMAIN, title='Casio ABL-100WE', data={'address': ADDRESS}, unique_id=ADDRESS)
    watch.add_to_hass(hass)
    names = {
        'en': {'read_all': ('read_watch_data', 'Read watch data'), 'time_format': ('time_format', 'Time format'), 'automatic_time_adjustment': ('automatic_time_adjustment', 'Automatic time adjustment'), 'timer_encoded_duration': ('timer_encoded_duration', 'Timer encoded duration')},
        'es': {'read_all': ('leer_datos_del_reloj', 'Leer datos del reloj'), 'time_format': ('formato_horario', 'Formato horario'), 'automatic_time_adjustment': ('ajuste_automatico_de_hora', 'Ajuste automático de hora'), 'timer_encoded_duration': ('duracion_codificada_del_temporizador', 'Duración codificada del temporizador')},
    }
    with patch('custom_components.casio_abl100.establish_connection') as bleak:
        assert await hass.config_entries.async_setup(watch.entry_id)
        await hass.async_block_till_done()
        entities = {e.unique_id.removeprefix(ADDRESS + '_'): e for e in er.async_get(hass).entities.values() if e.config_entry_id == watch.entry_id}
        assert len(entities) == 19 and len({e.device_id for e in entities.values()}) == 1
        for key, (suffix, label) in names[language].items():
            domain = 'button' if key == 'read_all' else 'sensor'
            assert entities[key].entity_id == f'{domain}.casio_abl_100we_{suffix}'
            assert hass.states.get(entities[key].entity_id).attributes['friendly_name'] == 'Casio ABL-100WE ' + label
        bleak.assert_not_called()
        assert await hass.config_entries.async_unload(watch.entry_id)


@pytest.mark.asyncio
@pytest.mark.parametrize('timing', ['waiting', 'connect', 'response'])
async def test_cancel_while_waiting_connecting_or_awaiting_reply(unified, timing):
    if timing == 'connect':
        # Keep the installed connection method waiting on its actual callback.
        original = unified.wire.responses
        unified.wire.responses = lambda request: [] if unified.wire.stage(request) == 'connect' else original(request)
    elif timing == 'response':
        unified.wire.hold_at = 'response'
    task = asyncio.create_task(unified.session.read_all())
    await spin_until(lambda: unified.session.sync_phase == 'waiting')
    if timing != 'waiting':
        unified.wire.raw()
        await spin_until(lambda: any(unified.wire.stage(x) == ('get15' if timing == 'response' else 'connect') for x in unified.wire.calls))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert unified.session.sync_result == 'cancelled'
    assert unified.session.alarm_snapshot is None
    assert_clean(unified)


@pytest.mark.asyncio
async def test_committed_alarm_callbacks_cannot_be_invalidated_by_later_notifications(unified):
    unified.wire.hold_at = 'cccd72'
    task = await armed(unified)
    unified.wire.raw()
    await spin_until(lambda: unified.wire.entered.is_set())
    snapshot, stamp = unified.session.alarm_snapshot, unified.session.alarm_last_sync
    assert snapshot is not None
    for frame in (b'\xff', FIRST, REST, b''):
        unified.wire.notify(TARGET, 61, frame)
    unified.wire.release.set()
    await task
    assert unified.session.sync_result == 'success'
    assert unified.session.alarm_snapshot is snapshot and unified.session.alarm_last_sync == stamp
    assert unified.session.alarms_fresh
    assert_clean(unified)


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['en', 'es'])
async def test_safe_error_and_enum_translation_catalog(unified, language):
    from homeassistant.helpers import translation
    catalog = await translation.async_get_translations(unified.hass, language, 'entity', {'casio_abl100'})
    base = 'component.casio_abl100.entity.sensor.automatic_time_adjustment.state.'
    assert catalog[base + 'enabled'] == ('Enabled' if language == 'en' else 'Activado')
    assert catalog[base + 'disabled'] == ('Disabled' if language == 'en' else 'Desactivado')
    errors = await translation.async_get_translations(unified.hass, language, 'exceptions', {'casio_abl100'})
    assert errors['component.casio_abl100.exceptions.sync_read_failed.message'] == (
        'Unable to read all watch data; completed groups are retained' if language == 'en' else
        'No se pudieron leer todos los datos del reloj; se conservan los grupos completados')
