"""Finite acceptance closures: real HA/native methods, synthetic external IO.

These characterize the pinned implementation; only the retained future regression
is causal RED for the narrow transport correction.
"""
import asyncio
import gc
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import pytest
from aioesphomeapi.api_pb2 import (
    BluetoothDeviceConnectionResponse, BluetoothDeviceRequest,
    BluetoothGATTGetServicesRequest, BluetoothGATTGetServicesResponse,
    BluetoothGATTNotifyDataResponse, BluetoothGATTNotifyRequest,
    BluetoothGATTNotifyResponse, BluetoothGATTWriteDescriptorRequest,
    BluetoothGATTWriteRequest, BluetoothGATTWriteResponse,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests.ha.test_native_alarm_runtime import NativeWire, SOURCE, spin_until, armed as armed_alarm
from tests.ha.test_native_step_runtime import (
    steps, native, TARGET, ADDRESS, BUTTON, SENSOR, armed, press, metadata,
    assert_clean,
)
from tests.protocol.test_daily_history import history_record


class LoopOwner:
    """Forward real HA ownership; inject only the observer scheduler."""

    def __init__(self, hass, loop):
        self.hass, self.loop = hass, loop

    def __getattr__(self, name):
        return getattr(self.hass, name)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['ordinary', 'backend_cancel', 'caller_cancel'])
async def test_invalid_response_preserves_write_failure_or_caller_priority(steps, fault, caplog):
    errors, counts = [], []
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: errors.append(context))
    thrown = RuntimeError('PRIVATE_ORIGINAL_WRITE') if fault == 'ordinary' else asyncio.CancelledError('PRIVATE_BACKEND')

    def fail():
        owner = asyncio.current_task()
        if fault == 'caller_cancel':
            owner.cancel()
        counts.append(owner.cancelling())
        raise thrown

    steps.wire.frame_mode = 'bad_header'
    steps.wire.after_write = fail
    try:
        task = asyncio.create_task(steps.session.read())
        await spin_until(lambda: steps.session.step_phase == 'waiting')
        steps.wire.raw()
        try:
            await task
        except asyncio.CancelledError:
            assert fault == 'caller_cancel'
        except HomeAssistantError as error:
            assert fault != 'caller_cancel'
            assert error.__cause__ is thrown, 'Response-future error replaced original write error'
            assert error.translation_key == 'read_failed'
            assert 'PRIVATE' not in str(error.args)
        else:
            pytest.fail('Invalid transaction succeeded')
        assert counts == [int(fault == 'caller_cancel')]
        # Drop traceback owners before asking the real loop to report failures.
        thrown.__traceback__ = None
        del task, thrown
        await asyncio.sleep(0)
        gc.collect()
        await asyncio.sleep(0)
        assert not errors
        assert 'PRIVATE' not in caplog.text
        assert steps.session.record is None and steps.session.last_sync is None
        assert_clean(steps)
    finally:
        loop.set_exception_handler(previous)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['ordinary', 'backend_cancel'])
async def test_step_observer_fault_cannot_undo_commit_or_skip_other_listener(steps, fault, caplog):
    seen = []

    def broken():
        if steps.session.fresh:
            seen.append(('broken', steps.session.record, asyncio.current_task()))
            if fault == 'backend_cancel':
                raise asyncio.CancelledError('PRIVATE_OBSERVER')
            raise RuntimeError('PRIVATE_OBSERVER')

    def healthy():
        if steps.session.fresh:
            seen.append(('healthy', steps.session.record, asyncio.current_task()))

    remove_broken = steps.session.subscribe(broken)
    remove_healthy = steps.session.subscribe(healthy)
    try:
        task = await armed(steps)
        steps.wire.raw()
        await task
        await steps.hass.async_block_till_done()
        assert steps.session.fresh and steps.session.last_sync is not None
        assert {item[0] for item in seen} == {'broken', 'healthy'}
        assert all(item[1] is steps.session.record and item[2] is not task for item in seen)
        assert steps.hass.states.get(SENSOR).state == '1485'
        assert steps.hass.states.get(SENSOR).attributes['fresh'] is True
        assert 'PRIVATE' not in caplog.text
        assert not steps.session.alarm_delivery_handles
        assert_clean(steps)
    finally:
        remove_broken()
        remove_healthy()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['ordinary', 'backend_cancel'])
@pytest.mark.parametrize('outcome', ['success', 'failure', 'caller_cancel'])
async def test_step_scheduler_failure_preserves_committed_or_failed_outcome(steps, fault, outcome, caplog):
    attempted, delivered = [], []

    class BrokenLoop:
        def call_soon(self, callback):
            attempted.append(callback)
            if fault == 'backend_cancel':
                raise asyncio.CancelledError('PRIVATE_SCHEDULER')
            raise RuntimeError('PRIVATE_SCHEDULER')

    owner = steps.session
    real_hass = owner.hass
    owner.hass = LoopOwner(real_hass, BrokenLoop())
    remove = owner.subscribe(lambda: delivered.append(owner.record))
    try:
        if outcome == 'failure':
            steps.wire.failure_stage = 'write'
            steps.wire.failure = RuntimeError('PRIVATE_WRITE')
        task = asyncio.create_task(press(steps))
        await spin_until(lambda: owner.step_phase == 'waiting')
        if outcome == 'caller_cancel':
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            steps.wire.raw()
            if outcome == 'success':
                await task
            else:
                with pytest.raises(HomeAssistantError) as error:
                    await task
                assert error.value.translation_key == 'read_failed'
                assert 'PRIVATE' not in str(error.value.args)
        assert owner.fresh is (outcome == 'success')
        assert (owner.record is not None) is (outcome == 'success')
        assert (owner.last_sync is not None) is (outcome == 'success')
        assert attempted and not delivered and not owner.alarm_delivery_handles
        assert owner.step_phase == 'idle' and not owner.lock.locked()
        assert 'PRIVATE' not in caplog.text
        assert_clean(steps)
    finally:
        remove()
        owner.hass = real_hass


@pytest.mark.asyncio
async def test_native_step_unload_cancels_delayed_delivery_and_suppresses_late_callbacks(steps):
    queued, delivered = [], []

    class HeldLoop:
        def call_soon(self, callback):
            handle = asyncio.Handle(callback, (), asyncio.get_running_loop())
            queued.append((handle, callback))
            return handle

    owner, real_hass = steps.session, steps.hass
    owner.hass = LoopOwner(real_hass, HeldLoop())
    owner.subscribe(lambda: delivered.append(owner.record))
    task = asyncio.create_task(press(steps))
    await spin_until(lambda: owner.step_phase == 'waiting')
    steps.wire.raw()
    await task
    assert owner.fresh and owner.record.steps == 1485
    assert queued and not delivered and owner.alarm_delivery_handles
    assert await real_hass.config_entries.async_unload(steps.watch.entry_id)
    assert owner.closed and not owner.listeners and not owner.alarm_delivery_handles
    assert all(handle.cancelled() for handle, _ in queued)
    for _, callback in queued:
        callback()  # Even an already-retained callback must recheck closure.
    assert not delivered
    assert_clean(steps)


class MultiplexWire(NativeWire):
    """One APIConnection, two producer-addressed watches; no consumer guards.

    Only the external wire is replaced. Installed APIClient filters every
    connection/notify response using its real address/handle callbacks.
    """

    def __init__(self):
        super().__init__()
        self.gate = {}
        self.arrived = asyncio.Event()
        self.release_a = asyncio.Event()
        self.pending_response = set()
        self.completed = []

    def deliver(self, target):
        raw = history_record(date(2026, 10, 18))
        for handle, value in [(71, b'\x00\x11\x90\x01\x00'), (81, raw[:200]), (81, raw[200:])]:
            self.emit(BluetoothGATTNotifyDataResponse(address=target, handle=handle, data=value))

    def send_message(self, request):
        self.calls.append(request)
        if not isinstance(request, BluetoothDeviceRequest):
            raise AssertionError(type(request).__name__)
        self.emit(BluetoothDeviceConnectionResponse(
            address=request.address, connected=request.request_type != 1, mtu=23,
        ))

    async def send_messages_await_response_complex(self, requests, append, stop, types, timeout):
        request, = requests
        self.calls.append(request)
        target = request.address
        if isinstance(request, BluetoothGATTGetServicesRequest):
            stage = 'services'
            response = BluetoothGATTGetServicesResponse(address=target)
            for service in metadata().services:
                def words(value):
                    number = UUID(value).int
                    return [number >> 64, number & ((1 << 64) - 1)]
                s = response.services.add(uuid=words(service.uuid), handle=service.handle)
                for char in service.characteristics:
                    c = s.characteristics.add(uuid=words(char.uuid), handle=char.handle, properties=char.properties)
                    for desc in char.descriptors:
                        c.descriptors.add(uuid=words(desc.uuid), handle=desc.handle)
        elif isinstance(request, BluetoothGATTNotifyRequest):
            stage = f'notify{request.handle}'
            response = BluetoothGATTNotifyResponse(address=target, handle=request.handle)
        elif isinstance(request, BluetoothGATTWriteDescriptorRequest):
            stage = 'cccd'
            response = BluetoothGATTWriteResponse(address=target, handle=request.handle)
        elif isinstance(request, BluetoothGATTWriteRequest):
            stage = 'write'
            if target not in self.pending_response:
                self.deliver(target)
            response = BluetoothGATTWriteResponse(address=target, handle=request.handle)
        elif isinstance(request, BluetoothDeviceRequest):
            stage = 'disconnect'
            response = BluetoothDeviceConnectionResponse(address=target, connected=False)
        else:
            raise AssertionError(type(request).__name__)
        if self.gate.get(target) == stage:
            self.arrived.set()
            await self.release_a.wait()
        self.completed.append((target, stage))
        return [response]


@pytest.fixture
async def two_steps(steps):
    wire = MultiplexWire()
    steps.api.wire = steps.wire = wire
    steps.proxy.ble_connections_limit = steps.proxy.ble_connections_free = 2
    address_b = 'AA:BB:CC:DD:EE:01'
    target_b = int(address_b.replace(':', ''), 16)
    device_b = SimpleNamespace(address=address_b, name='ABL-100WE', details={'source': SOURCE})
    watch_b = MockConfigEntry(domain=steps.watch.domain, title='Watch B', data={'address': address_b}, unique_id=address_b)
    watch_b.add_to_hass(steps.hass)
    devices = {ADDRESS: steps.device, address_b: device_b}
    with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address', side_effect=lambda _hass, address, **kw: devices[address]):
        assert await steps.hass.config_entries.async_setup(watch_b.entry_id)
        await steps.hass.async_block_till_done()
        registry = er.async_get(steps.hass)
        entities = [e for e in registry.entities.values() if e.config_entry_id == watch_b.entry_id]
        button_b = next(e.entity_id for e in entities if e.unique_id.endswith('_read_steps'))
        sensor_b = next(e.entity_id for e in entities if e.domain == 'sensor' and e.unique_id.endswith('_steps_today'))
        assert len(entities) == 11 + 4 + 4 + 4 + 13 and len({e.device_id for e in entities}) == 1
        from tests.ha.test_zone_entities import assert_zone_addition
        assert_zone_addition(steps.hass, watch_b)
        assert {e.device_id for e in entities}.isdisjoint({e.device_id for e in registry.entities.values() if e.config_entry_id == steps.watch.entry_id})
        yield SimpleNamespace(a=steps, b=watch_b, target_b=target_b, button_b=button_b, sensor_b=sensor_b)
        if watch_b.state is ConfigEntryState.LOADED:
            assert await steps.hass.config_entries.async_unload(watch_b.entry_id)


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['waiting', 'notify71', 'disconnect'])
@pytest.mark.parametrize('replace', [False, True])
async def test_two_native_entries_keep_callbacks_and_publication_isolated_on_a_unload(two_steps, stage, replace):
    pair, a = two_steps, two_steps.a
    wire, b = a.wire, pair.b.runtime_data
    wire.pending_response.add(pair.target_b)
    original_notifications = []
    unrelated = wire.add_message_callback(original_notifications.append, (BluetoothGATTNotifyDataResponse,))
    original_disconnect = lambda: None
    a.runtime.disconnect_callbacks.add(original_disconnect)
    task_a = await armed(a)
    task_b = asyncio.create_task(a.hass.services.async_call('button', 'press', {'entity_id': pair.button_b}, blocking=True))
    await spin_until(lambda: b.step_phase == 'waiting')
    wire.raw(pair.target_b)
    await spin_until(lambda: any(isinstance(r, BluetoothGATTWriteRequest) and r.address == pair.target_b for r in wire.calls))
    assert a.session.step_phase == 'waiting' and not task_a.done()
    assert not any(r.address == TARGET for r in wire.calls), 'B advertisement started A'
    handlers_b = set(wire.handlers)
    # A's raw callback is the sole A handler while waiting; B keeps its own
    # connection and all three notify callbacks as A acquires and closes.
    raw_a = next(item for item in handlers_b if getattr(item[0], '__name__', '') == 'raw')
    handlers_b.remove(raw_a)
    if stage != 'waiting':
        wire.gate[TARGET] = stage
        wire.raw(TARGET)
        await asyncio.wait_for(wire.arrived.wait(), 2)
    replacement = NativeWire() if replace else None
    if replace:
        a.api.wire = replacement
    unloading = asyncio.create_task(a.hass.config_entries.async_unload(a.watch.entry_id))
    await spin_until(lambda: a.session.closed)
    wire.release_a.set()
    assert await unloading
    with pytest.raises(asyncio.CancelledError):
        await task_a
    assert wire.handlers == handlers_b
    assert original_disconnect in a.runtime.disconnect_callbacks
    assert b.record is None and b.last_sync is None and not b.fresh
    assert a.hass.states.get(pair.sensor_b).state == 'unavailable'
    wire.deliver(TARGET)  # A-address frames cannot complete B's installed filters.
    await asyncio.sleep(0)
    assert not task_b.done() and b.record is None
    wire.deliver(pair.target_b)
    if replace:
        # B also captured the original generation. It must refuse the record
        # after its generation changed, without touching the replacement API.
        with pytest.raises(HomeAssistantError):
            await task_b
        assert not b.fresh and b.record is None and b.last_sync is None
        assert a.hass.states.get(pair.sensor_b).state == 'unavailable'
        assert not replacement.calls and not replacement.handlers
    else:
        await task_b
        await a.hass.async_block_till_done()
        assert b.fresh and a.hass.states.get(pair.sensor_b).state == '1485'
    assert a.session.record is None and a.session.last_sync is None
    assert all(item.address in (TARGET, pair.target_b) for item in wire.calls)
    assert original_notifications  # Unrelated callback remained operational.
    assert wire.handlers == {(original_notifications.append, (BluetoothGATTNotifyDataResponse,))}
    assert a.runtime.disconnect_callbacks == {original_disconnect}
    unrelated()
    a.runtime.disconnect_callbacks.discard(original_disconnect)
    assert not b.tasks and not b.lock.locked()
    assert_clean(a)


@pytest.mark.asyncio
@pytest.mark.parametrize('stage,backend_bound', [('notify81', 10), ('disconnect', 5)])
@pytest.mark.parametrize('replace', [False, True])
async def test_finite_late_acquisition_or_disconnect_joins_repeated_cancellation(steps, stage, backend_bound, replace):
    # Compress only the synthetic external backend's finite clock. Do not alter
    # a production timeout: require its exact 10/5-second request budget below.
    original = steps.wire.send_messages_await_response_complex
    entered, release = asyncio.Event(), asyncio.Event()
    completed, removals, children = [], [], []
    original_add = steps.wire.add_message_callback

    def add(callback, types):
        remove = original_add(callback, types)
        def owned_remove():
            removals.append(callback)
            remove()
        return owned_remove
    steps.wire.add_message_callback = add
    unrelated = steps.wire.add_message_callback(lambda _msg: None, (BluetoothGATTNotifyDataResponse,))
    preserved = set(steps.wire.handlers)
    disconnect_callback = lambda: None
    steps.runtime.disconnect_callbacks.add(disconnect_callback)

    async def finite(requests, *args):
        request = requests[0]
        selected = (isinstance(request, BluetoothGATTNotifyRequest) and request.handle == 81) if stage == 'notify81' else isinstance(request, BluetoothDeviceRequest)
        if selected:
            children.append(asyncio.current_task())
            completed.append(('budget', args[-1]))
            entered.set()
            # Healthy completion at 90% of this external fake's enforced bound.
            asyncio.get_running_loop().call_later(0.09, release.set)
            await asyncio.wait_for(release.wait(), 0.1)
            completed.append(('finished', True))
        return await original(requests, *args)

    steps.wire.send_messages_await_response_complex = finite
    task = await armed(steps)
    steps.wire.raw()
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done(), 'Owner returned before its finite backend joined'
    replacement = NativeWire() if replace else None
    if replace:
        steps.api.wire = replacement
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ('budget', backend_bound) in completed and ('finished', True) in completed
    assert children and all(child.done() and child is not task for child in children)
    assert removals and steps.wire.handlers == preserved
    assert steps.runtime.disconnect_callbacks == {disconnect_callback}
    assert steps.session.record is None and steps.session.last_sync is None
    assert not steps.session.tasks and not steps.session.lock.locked()
    assert not steps.api._notify_callbacks
    if replace:
        assert not replacement.calls and not replacement.handlers
    unrelated()
    steps.runtime.disconnect_callbacks.discard(disconnect_callback)
    assert_clean(steps)


@pytest.mark.asyncio
async def test_failed_steps_do_not_stale_a_successful_alarm_observation(steps):
    alarm = await armed_alarm(steps)
    steps.wire.raw()
    await alarm
    snapshot, alarm_stamp = steps.session.alarm_snapshot, steps.session.alarm_last_sync
    successful = await armed(steps)
    steps.wire.raw()
    await successful
    record, step_stamp = steps.session.record, steps.session.last_sync
    steps.wire.frame_mode = 'malformed'
    failed = await armed(steps)
    steps.wire.raw()
    with pytest.raises(HomeAssistantError):
        await failed
    await steps.hass.async_block_till_done()
    assert not steps.session.fresh and steps.session.record is record
    assert steps.session.last_sync == step_stamp
    assert steps.session.alarms_fresh and steps.session.alarm_snapshot is snapshot
    assert steps.session.alarm_last_sync == alarm_stamp
    assert steps.hass.states.get(SENSOR).attributes['fresh'] is False
    assert steps.hass.states.get('sensor.casio_abl_100we_alarm_1').attributes['fresh'] is True
    assert_clean(steps)
