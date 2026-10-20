"""Real HA services and installed APIClient; synthetic external wire only."""
import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aioesphomeapi.api_pb2 import (
    BluetoothDeviceRequest, BluetoothGATTGetServicesRequest,
    BluetoothGATTNotifyRequest, BluetoothGATTNotifyDataResponse,
    BluetoothGATTWriteDescriptorRequest, BluetoothGATTWriteRequest,
    BluetoothGATTWriteResponse,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from tests.ha.test_native_alarm_runtime import (
    native, NativeWire, TARGET, ADDRESS, spin_until, assert_clean,
    press as press_alarm, armed as armed_alarm,
)
from tests.protocol.test_native_step_adapter import metadata as step_metadata
from tests.protocol.test_native_alarm_adapter import metadata as alarm_metadata
from tests.protocol.test_daily_history import history_record
from custom_components.casio_abl100.transport import START

BUTTON = 'button.casio_abl_100we_read_steps'
SENSOR = 'sensor.casio_abl_100we_steps_today'


def metadata():
    steps = step_metadata()
    steps.services[0].characteristics.append(alarm_metadata().services[0].characteristics[1])
    return steps


@pytest.fixture
async def steps(native):
    original = native.wire.send_messages_await_response_complex
    native.wire.frame_mode = 'valid'
    native.wire.failure_stage = None
    native.wire.failure = None
    native.wire.before = None
    native.wire.after_write = None

    def deliver(mode=None):
        mode = mode or native.wire.frame_mode
        raw = history_record(date(2026, 10, 18))
        if mode == 'malformed':
            raw = bytes([0xFF]) + raw[1:]
        header = b'\x00\x11\x90\x01\x00' if mode != 'bad_header' else b'\x00\x11\x91\x01\x00'
        def emit(handle, data):
            native.wire.emit(BluetoothGATTNotifyDataResponse(address=TARGET, handle=handle, data=data))
        if mode != 'no_header':
            emit(71, header)
        emit(81, raw[:200])
        if mode != 'incomplete':
            emit(81, raw[200:])
        if mode == 'duplicate':
            emit(71, header)
        if mode == 'extra':
            emit(81, b'\x00')

    async def exchange(requests, *args):
        request = requests[0]
        if isinstance(request, BluetoothGATTWriteRequest):
            stage = 'write'
        elif isinstance(request, BluetoothGATTNotifyRequest):
            stage = f'notify{request.handle}'
        elif isinstance(request, BluetoothGATTWriteDescriptorRequest):
            stage = f'cccd{request.handle}'
        elif isinstance(request, BluetoothDeviceRequest):
            stage = 'disconnect'
        elif isinstance(request, BluetoothGATTGetServicesRequest):
            stage = 'services'
        else:
            stage = None
        if native.wire.before is not None:
            native.wire.before(stage)
        if stage and (native.wire.hold_at == stage or native.wire.failure_stage == stage):
            if not isinstance(request, BluetoothGATTWriteRequest):
                # Run the real external response producer after the gate.
                pass
            if native.wire.hold_at == stage:
                native.wire.entered.set()
                await native.wire.release.wait()
            if native.wire.failure_stage == stage:
                raise native.wire.failure
        if isinstance(request, BluetoothGATTWriteRequest):
            native.wire.calls.append(request)
            if native.wire.hold_at == 'response':
                native.wire.entered.set()
            else:
                deliver()
            if native.wire.after_write is not None:
                result = native.wire.after_write()
                if result is not None:
                    await result
            return [BluetoothGATTWriteResponse(address=TARGET, handle=request.handle)]
        with patch('tests.ha.test_native_alarm_runtime.metadata', metadata):
            # Avoid applying the superclass generic gate twice.
            old = native.wire.hold_at
            if stage == old:
                native.wire.hold_at = None
            try:
                return await original(requests, *args)
            finally:
                native.wire.hold_at = old

    native.wire.send_messages_await_response_complex = exchange
    native.deliver = deliver
    with patch('custom_components.casio_abl100.dt_util.now', return_value=datetime(2026, 10, 18, tzinfo=timezone.utc)):
        yield native


async def press(steps):
    await steps.hass.services.async_call('button', 'press', {'entity_id': BUTTON}, blocking=True)


async def armed(steps):
    task = asyncio.create_task(press(steps))
    await spin_until(lambda: task.done() or steps.hass.states.get(BUTTON).attributes.get('phase') == 'waiting')
    if task.done():
        task.exception()
    assert steps.hass.states.get(BUTTON).attributes.get('phase') == 'waiting', 'Read steps must arm the native route, not call Bleak'
    return task


@pytest.mark.asyncio
async def test_real_step_button_waits_for_fresh_exact_advert_then_publishes_history(steps):
    registry = er.async_get(steps.hass)
    ids = {e.entity_id: (e.unique_id, e.device_id) for e in registry.entities.values() if e.config_entry_id == steps.watch.entry_id}
    assert len(ids) == 11 + 4 + 4 + 4 + 13 + 1 and len({x[1] for x in ids.values()}) == 1
    from tests.ha.test_zone_entities import assert_zone_addition
    assert_zone_addition(steps.hass, steps.watch)
    assert not steps.wire.calls
    steps.bleak.assert_not_called()
    assert steps.hass.states.get(SENSOR).state == 'unavailable'
    task = await armed(steps)
    state = steps.hass.states.get(BUTTON)
    expiry = datetime.fromisoformat(state.attributes['wait_expires_at'])
    assert expiry.tzinfo == timezone.utc
    assert 0 < (expiry - datetime.now(timezone.utc)).total_seconds() <= 360
    assert steps.session.alarm_phase == 'idle'
    steps.wire.raw(TARGET - 1)
    await asyncio.sleep(0)
    assert not steps.wire.calls
    steps.wire.raw()
    await task
    await steps.hass.async_block_till_done()
    state = steps.hass.states.get(SENSOR)
    assert state.state == '1485' and state.attributes['fresh'] is True
    assert state.attributes['daily_history'] == {'2026-10-17': 1234, '2026-10-16': 0, '2026-10-15': None, '2026-10-14': 7654, '2026-10-13': 42, '2026-10-12': 99999999, '2026-10-11': 1}
    assert steps.session.last_sync.tzinfo == timezone.utc
    assert steps.session.alarm_snapshot is None and steps.session.alarm_last_sync is None
    assert not steps.session.alarms_fresh
    assert steps.hass.states.get(BUTTON).attributes['phase'] == 'idle'
    assert steps.hass.states.get(BUTTON).attributes['wait_expires_at'] is None
    calls = steps.wire.calls
    assert [type(x).__name__ for x in calls] == ['BluetoothDeviceRequest', 'BluetoothGATTGetServicesRequest', 'BluetoothGATTNotifyRequest', 'BluetoothGATTWriteDescriptorRequest', 'BluetoothGATTNotifyRequest', 'BluetoothGATTWriteDescriptorRequest', 'BluetoothGATTNotifyRequest', 'BluetoothGATTWriteDescriptorRequest', 'BluetoothGATTWriteRequest', 'BluetoothDeviceRequest']
    assert calls[0].address_type == 1 and calls[0].has_address_type
    assert [(x.handle, x.data, x.response) for x in calls if isinstance(x, BluetoothGATTWriteRequest)] == [(71, START, True)]
    assert [(x.handle, x.data) for x in calls if isinstance(x, BluetoothGATTWriteDescriptorRequest)] == [(62, b'\x01\x00'), (72, b'\x01\x00'), (82, b'\x01\x00')]
    assert_clean(steps)
    previous = steps.session
    assert await steps.hass.config_entries.async_reload(steps.watch.entry_id)
    await steps.hass.async_block_till_done()
    assert previous.closed and not previous.listeners and not previous.tasks
    assert {e.entity_id: (e.unique_id, e.device_id) for e in registry.entities.values() if e.config_entry_id == steps.watch.entry_id} == ids
    assert len([d for d in dr.async_get(steps.hass).devices if (steps.watch.domain, ADDRESS) in d.identifiers]) == 1
    assert steps.hass.states.get(SENSOR).state == '1485'
    assert steps.hass.states.get(SENSOR).attributes['fresh'] is False
    assert steps.watch.runtime_data.record == previous.record
    assert steps.watch.runtime_data.last_sync == previous.last_sync
    assert steps.hass.states.get(SENSOR).attributes['daily_history'] == state.attributes['daily_history']
    assert len(steps.wire.calls) == 10


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['negative', 'connect_error', 'metadata', 'malformed', 'duplicate', 'extra'])
async def test_failed_native_step_refresh_retains_value_timestamp_and_alarm_freshness(steps, failure, caplog):
    task = await armed(steps)
    steps.wire.raw()
    await task
    previous, stamp = steps.session.record, steps.session.last_sync
    if failure == 'negative':
        steps.wire.connected = False
    elif failure == 'connect_error':
        steps.wire.error = 133
    elif failure == 'metadata':
        steps.wire.bad_metadata = True
    else:
        steps.wire.frame_mode = failure
    task = await armed(steps)
    steps.wire.raw()
    with pytest.raises(HomeAssistantError) as error:
        await task
    await steps.hass.async_block_till_done()
    assert steps.session.record is previous and steps.session.last_sync == stamp
    assert not steps.session.fresh and not steps.session.alarms_fresh
    assert steps.hass.states.get(SENSOR).state == '1485'
    assert steps.hass.states.get(SENSOR).attributes['fresh'] is False
    assert 'PRIVATE' not in str(error.value.args) + caplog.text
    assert ADDRESS not in str(error.value.args) + caplog.text
    assert_clean(steps)


STAGES = ['waiting', 'connect', 'services', 'notify61', 'cccd62', 'notify71', 'cccd72', 'notify81', 'cccd82', 'write', 'response', 'disconnect']


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', STAGES)
async def test_caller_cancel_at_every_boundary_joins_all_owned_handlers(steps, stage):
    task = await armed(steps)
    if stage != 'waiting':
        steps.wire.hold_at = stage
        steps.wire.raw()
        await asyncio.wait_for(steps.wire.entered.wait(), 2)
    task.cancel()
    await asyncio.sleep(0)
    if stage.startswith('notify') or stage == 'disconnect':
        task.cancel()
    steps.wire.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert steps.session.record is None and steps.session.last_sync is None
    assert_clean(steps)


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', STAGES)
async def test_replacement_generation_receives_no_step_or_cleanup_requests(steps, stage):
    task = await armed(steps)
    if stage != 'waiting':
        steps.wire.hold_at = stage
        steps.wire.raw()
        await asyncio.wait_for(steps.wire.entered.wait(), 2)
    replacement = NativeWire()
    steps.api.wire = replacement
    for callback in tuple(steps.runtime.disconnect_callbacks):
        callback()
    steps.wire.release.set()
    with pytest.raises((HomeAssistantError, asyncio.CancelledError)):
        await task
    assert not replacement.calls and not replacement.handlers
    assert steps.session.record is None
    assert_clean(steps)


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['services', 'notify61', 'notify71', 'notify81', 'cccd62', 'cccd72', 'cccd82', 'write', 'disconnect'])
@pytest.mark.parametrize('fault', ['timeout', 'ordinary', 'backend_cancel'])
async def test_backend_faults_are_safe_failures_not_caller_cancel(steps, stage, fault, caplog):
    steps.wire.failure_stage = stage
    steps.wire.failure = {'timeout': TimeoutError('PRIVATE_TIMEOUT'), 'ordinary': RuntimeError('PRIVATE_BACKEND'), 'backend_cancel': asyncio.CancelledError()}[fault]
    task = await armed(steps)
    steps.wire.raw()
    with pytest.raises(HomeAssistantError) as error:
        await task
    assert steps.session.record is None and not steps.session.fresh
    assert 'PRIVATE' not in str(error.value.args) + caplog.text
    assert ADDRESS not in str(error.value.args) + caplog.text
    assert_clean(steps)


@pytest.mark.asyncio
@pytest.mark.parametrize('first', ['steps', 'alarms'])
async def test_both_native_operation_lock_orders_and_independent_freshness(steps, first):
    if first == 'steps':
        active = await armed(steps)
        queued = asyncio.create_task(press_alarm(steps))
        phase = 'alarm_phase'
    else:
        active = await armed_alarm(steps)
        queued = asyncio.create_task(press(steps))
        phase = 'step_phase'
    await spin_until(lambda: len(steps.session.tasks) == 2)
    assert len(steps.wire.handlers) == 1 and not steps.wire.calls
    steps.wire.raw()
    await active
    await spin_until(lambda: getattr(steps.session, phase) == 'waiting')
    if first == 'steps':
        record, stamp = steps.session.record, steps.session.last_sync
    else:
        record, stamp = steps.session.alarm_snapshot, steps.session.alarm_last_sync
    steps.wire.raw()
    await queued
    assert steps.session.fresh and steps.session.alarms_fresh
    if first == 'steps':
        assert steps.session.record is record and steps.session.last_sync == stamp
    else:
        assert steps.session.alarm_snapshot is record and steps.session.alarm_last_sync == stamp
    assert_clean(steps)


@pytest.mark.asyncio
@pytest.mark.parametrize('active', ['waiting', 'response'])
async def test_unload_owns_queued_and_active_step_requests(steps, active):
    task = await armed(steps)
    if active == 'response':
        steps.wire.hold_at = active
        steps.wire.raw()
        await asyncio.wait_for(steps.wire.entered.wait(), 2)
    queued = asyncio.create_task(press_alarm(steps))
    await spin_until(lambda: len(steps.session.tasks) == 2)
    assert await steps.hass.config_entries.async_unload(steps.watch.entry_id)
    for item in (task, queued):
        with pytest.raises(asyncio.CancelledError):
            await item
    assert steps.session.closed and not steps.session.listeners
    assert not steps.session.lock.locked()
    assert steps.session.step_phase == 'closed'
    assert_clean(steps)


@pytest.mark.asyncio
async def test_cold_exact_address_has_no_cached_device_requirement(steps):
    with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address', return_value=None):
        task = await armed(steps)
        assert not steps.wire.calls
        steps.wire.raw()
        await task
    assert steps.session.fresh
    assert_clean(steps)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['no_header', 'incomplete', 'wait_expired'])
async def test_missing_response_and_wait_expiry_are_finite(steps, mode):
    from custom_components.casio_abl100 import native_steps, native_alarms
    original = native_steps.read_record

    async def short_record(adapter, timeout):
        return await original(adapter, timeout=0.02)

    steps.wire.frame_mode = mode
    with patch.object(native_steps, 'read_record', short_record), patch.object(native_alarms, 'WAIT_SECONDS', 0.02 if mode == 'wait_expired' else 360):
        task = await armed(steps)
        if mode != 'wait_expired':
            steps.wire.raw()
        with pytest.raises(HomeAssistantError):
            await asyncio.wait_for(task, 2)
    assert steps.session.record is None
    assert_clean(steps)


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['notify71', 'cccd72', 'write', 'disconnect', 'after_closed'])
async def test_notification_phase_guards_before_start_during_cleanup_and_after_close(steps, boundary):
    def notify(stage):
        if stage == boundary:
            if boundary in ('notify71', 'cccd72'):
                steps.wire.emit(BluetoothGATTNotifyDataResponse(address=TARGET, handle=71, data=b'\x00\x11\x90\x01\x00'))
            else:
                steps.deliver('extra' if boundary == 'disconnect' else 'valid')
    steps.wire.before = notify
    task = await armed(steps)
    steps.wire.raw()
    if boundary == 'after_closed':
        await task
        record, stamp = steps.session.record, steps.session.last_sync
        steps.deliver('extra')
        assert steps.session.record is record and steps.session.last_sync == stamp
    else:
        with pytest.raises(HomeAssistantError):
            await task
        assert steps.session.record is None
    assert_clean(steps)


@pytest.mark.asyncio
async def test_write_failure_after_immediate_valid_response_does_not_publish(steps):
    def fail():
        raise RuntimeError('PRIVATE_WRITE_AFTER_RESPONSE')
    steps.wire.after_write = fail
    task = await armed(steps)
    steps.wire.raw()
    with pytest.raises(HomeAssistantError):
        await task
    assert steps.session.record is None
    assert_clean(steps)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['before_entry', 'child_cancel', 'replacement_error'])
async def test_cancellation_provenance_at_public_entry_and_backend(steps, mode):
    if mode == 'before_entry':
        async def pending():
            asyncio.current_task().cancel()
            await steps.session.read()
        task = asyncio.create_task(pending())
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not steps.wire.calls
    elif mode == 'child_cancel':
        async def cancel_child():
            # wait_for in Python 3.14 runs this write in the owner task;
            # use an actual backend-owned child to test distinct provenance.
            async def child():
                asyncio.current_task().cancel()
                await asyncio.sleep(0)
            await asyncio.create_task(child())
        steps.wire.after_write = cancel_child
        task = await armed(steps)
        steps.wire.raw()
        with pytest.raises(HomeAssistantError):
            await task
    else:
        original = steps.wire.send_messages_await_response_complex
        async def replaced(requests, *args):
            if isinstance(requests[0], BluetoothGATTWriteRequest):
                steps.wire.entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    raise RuntimeError('PRIVATE_REPLACED_CANCELLATION') from None
            return await original(requests, *args)
        steps.wire.send_messages_await_response_complex = replaced
        task = await armed(steps)
        steps.wire.raw()
        await asyncio.wait_for(steps.wire.entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert steps.session.record is None
    assert_clean(steps)


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['notify61', 'notify71', 'notify81'])
@pytest.mark.parametrize('outcome', ['ordinary', 'backend_cancel'])
async def test_notify_race_failure_after_caller_cancel_joins_real_api_acquisition(steps, stage, outcome):
    steps.wire.hold_at = steps.wire.failure_stage = stage
    steps.wire.failure = RuntimeError('PRIVATE_NOTIFY_RACE') if outcome == 'ordinary' else asyncio.CancelledError()
    task = await armed(steps)
    steps.wire.raw()
    await asyncio.wait_for(steps.wire.entered.wait(), 2)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    steps.wire.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert_clean(steps)


@pytest.mark.asyncio
async def test_refused_unload_preserves_native_wait_and_queued_cancel_does_not_clear_readiness(steps):
    from custom_components.casio_abl100 import async_unload_entry
    task = await armed(steps)
    queued = asyncio.create_task(press(steps))
    await spin_until(lambda: len(steps.session.tasks) == 2)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    assert steps.session.step_phase == 'waiting'
    expiry = steps.session.step_wait_expires_at
    with patch.object(steps.hass.config_entries, 'async_unload_platforms', return_value=False):
        assert not await async_unload_entry(steps.hass, steps.watch)
    assert not steps.session.closed and steps.session.listeners
    assert steps.session.step_phase == 'waiting' and steps.session.step_wait_expires_at == expiry
    steps.wire.raw()
    await task
    assert_clean(steps)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['name', 'address', 'identity', 'loaded'])
async def test_wrong_identity_or_owner_refuses_before_any_io(steps, fault):
    from homeassistant.config_entries import ConfigEntryState
    if fault == 'name':
        steps.device.name = 'Other model'
    elif fault == 'address':
        steps.device.address = '00:00:00:00:00:00'
    elif fault == 'identity':
        steps.device.details = {'source': '00:00:00:00:00:00'}
    else:
        steps.watch._async_set_state(steps.hass, ConfigEntryState.UNLOAD_IN_PROGRESS, None)
    try:
        with pytest.raises(HomeAssistantError):
            await press(steps)
        assert not steps.wire.calls
        assert_clean(steps)
    finally:
        if fault == 'loaded':
            steps.watch._async_set_state(steps.hass, ConfigEntryState.LOADED, None)


@pytest.mark.asyncio
async def test_non_native_step_route_is_still_selected_for_known_local_scanner(steps):
    from tests.protocol.test_transport import FakeClient
    steps.device.details = {'source': '22:22:22:22:22:22'}
    steps.bleak.side_effect = None
    client = FakeClient([history_record(date(2026, 10, 18))])
    steps.bleak.return_value = client
    scanner = SimpleNamespace(connector=SimpleNamespace(client=lambda: None))
    with patch('homeassistant.components.bluetooth.async_scanner_by_source', return_value=scanner):
        await press(steps)
    assert steps.bleak.call_count == 1 and not steps.wire.calls
    assert steps.session.fresh and client.disconnected


@pytest.mark.asyncio
async def test_invalid_frame_and_failed_write_leave_no_unretrieved_future(steps):
    errors = []
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: errors.append(context))
    try:
        steps.wire.frame_mode = 'bad_header'
        def fail():
            raise RuntimeError('PRIVATE_INVALID_WRITE')
        steps.wire.after_write = fail
        task = asyncio.create_task(steps.session.read())
        await spin_until(lambda: steps.session.step_phase == 'waiting')
        steps.wire.raw()
        try:
            await task
        except HomeAssistantError:
            pass
        else:
            pytest.fail('Invalid transaction unexpectedly succeeded')
        del task
        await asyncio.sleep(0)
        import gc
        gc.collect()
        await asyncio.sleep(0)
        assert not errors, 'Native failed response future leaked to event loop'
        assert_clean(steps)
    finally:
        loop.set_exception_handler(previous)
