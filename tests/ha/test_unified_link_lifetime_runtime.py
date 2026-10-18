"""Unified-only BLE lifetime regressions; real HA/native bodies, synthetic IO."""
import asyncio
from unittest.mock import patch

import pytest
from aioesphomeapi.api_pb2 import BluetoothDeviceConnectionResponse, BluetoothDeviceRequest
from homeassistant.exceptions import HomeAssistantError

from tests.ha.test_native_alarm_runtime import native, spin_until, TARGET
from tests.ha.test_unified_sync_runtime import unified, SyncWire, GROUPS, assert_clean


def state(wire, connected, error=0, address=TARGET):
    wire.emit(BluetoothDeviceConnectionResponse(
        address=address, connected=connected, mtu=512, error=error))


def lose(wire, error=0):
    """A later true is not a new CONNECT and cannot restore lost handlers."""
    state(wire, error != 0, error)
    state(wire, True)


def operational(calls):
    return [request for request in calls if not (
        isinstance(request, BluetoothDeviceRequest) and int(request.request_type) == 1)]


async def start(x):
    task = asyncio.create_task(x.session.read_all())
    await spin_until(lambda: x.session.sync_phase == 'waiting' or task.done())
    x.wire.raw()
    return task


@pytest.fixture
async def owned(unified):
    import custom_components.casio_abl100.native_sync as module
    original = module.NativeSyncRead
    readers = []

    class RecordedRead(original):
        def __init__(self, *args):
            super().__init__(*args)
            readers.append(self)

    with patch.object(module, 'NativeSyncRead', RecordedRead):
        yield unified, readers


def released(x, reader):
    assert_clean(x)
    assert reader.execution.done()
    assert not reader.api.connection.removers and not reader.api._notify_callbacks
    assert reader.remove_connection is reader.remove_raw is reader.timer is None
    if reader.adapter is not None:
        assert reader.adapter.receiver is reader.adapter.step_receivers is None
        assert not reader.adapter.step_removers and reader.adapter.remove_notify is None


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [0, 133])
async def test_loss_before_first_commit_never_restores_authority(owned, error):
    x, readers = owned
    cutoff = []
    handlers_removed = []

    def after(stage, _request):
        if stage == 'get16':
            # The installed API removes its primary notify callback on false.
            if not error:
                state(x.wire, False)
                handlers_removed.append(not readers[0].api._notify_callbacks)
                state(x.wire, True)
            else:
                lose(x.wire, error)
            cutoff.append(len(x.wire.calls))

    x.wire.after = after
    task = await start(x)
    with pytest.raises(HomeAssistantError) as caught:
        await task
    assert caught.value.translation_key == 'sync_read_failed'
    assert task.cancelling() == 0
    assert cutoff and not operational(x.wire.calls[cutoff[0]:])
    assert x.session.alarm_snapshot is x.session.alarm_last_sync is None
    assert not x.session.alarms_fresh and x.session.sync_result == 'failed'
    assert list(x.session.sync_group_results.values()) == ['failed'] + ['not_attempted'] * 5
    assert x.session.sync_cleanup_result == 'success'
    assert sum(x.wire.stage(r) == 'disconnect' for r in x.wire.calls) == 1
    if not error:
        assert handlers_removed == [True]
    released(x, readers[0])


@pytest.mark.asyncio
async def test_later_loss_preserves_only_prior_alarm_commit(owned):
    x, readers = owned
    committed = []
    cutoff = []

    import custom_components.casio_abl100.native_sync as module
    original = module.dt_util.utcnow

    def timestamp():
        result = original()
        if x.session.sync_group_results['steps'] == 'reading':
            committed.append((x.session.alarm_snapshot, x.session.alarm_last_sync))
            lose(x.wire)
            cutoff.append(len(x.wire.calls))
        return result

    with patch.object(module.dt_util, 'utcnow', timestamp):
        task = await start(x)
        with pytest.raises(HomeAssistantError):
            await task
    assert cutoff and not operational(x.wire.calls[cutoff[0]:])
    assert committed[0][0] is not None and committed[0][1] is not None
    assert x.session.alarm_snapshot is committed[0][0]
    assert x.session.alarm_last_sync == committed[0][1] and x.session.alarms_fresh
    assert x.session.record is x.session.last_sync is None and not x.session.fresh
    assert x.session.sync_group_results == dict(zip(
        GROUPS, ['success', 'failed'] + ['not_attempted'] * 4, strict=True))
    assert x.session.sync_result == 'partial' and x.session.sync_cleanup_result == 'success'
    assert sum(x.wire.stage(r) == 'connect' for r in x.wire.calls) == 1
    released(x, readers[0])


@pytest.mark.asyncio
@pytest.mark.parametrize('method,handle', [
    ('bluetooth_gatt_start_notify', 71),
    ('bluetooth_gatt_write_descriptor', 72),
    ('bluetooth_gatt_write', 71),
])
async def test_native_submission_guard_closes_acquisition_gap(owned, method, handle):
    # Pause at public native entry, after the adapter's pre-scheduling guard.
    # Invoke the unchanged installed body after the callback; no test guard.
    import custom_components.casio_abl100.native_sync as module
    x, readers = owned
    original = getattr(module._SyncAPI, method)
    cutoff = []

    async def enter(api, address, actual_handle, *args, **kwargs):
        if actual_handle == handle:
            lose(x.wire)
            cutoff.append(len(x.wire.calls))
        return await original(api, address, actual_handle, *args, **kwargs)

    with patch.object(module._SyncAPI, method, enter):
        task = await start(x)
        with pytest.raises(HomeAssistantError):
            await task
    assert cutoff and not operational(x.wire.calls[cutoff[0]:])
    assert x.session.alarms_fresh and x.session.sync_result == 'partial'
    assert x.session.sync_cleanup_result == 'success'
    released(x, readers[0])


@pytest.mark.asyncio
@pytest.mark.parametrize('initial', ['none', 'false', 'error', 'wrong_address'])
async def test_initial_unconnected_and_wrong_address_are_not_established_loss(owned, initial):
    x, readers = owned

    def before(stage, _request):
        if stage == 'connect' and initial in ('false', 'error'):
            state(x.wire, False, 133 if initial == 'error' else 0)
        if stage == 'get16' and initial == 'wrong_address':
            state(x.wire, False, address=TARGET + 1)
            state(x.wire, True, 133, address=TARGET + 1)

    x.wire.before = before
    task = await start(x)
    await task
    assert x.session.sync_result == 'success'
    assert x.session.sync_group_results == dict.fromkeys(GROUPS, 'success')
    assert x.session.alarms_fresh and x.session.fresh
    released(x, readers[0])


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [0, 133])
async def test_unconfirmed_connect_still_refuses_discovery(owned, error):
    x, readers = owned
    original = x.wire.responses

    def responses(request):
        if x.wire.stage(request) == 'connect':
            return [BluetoothDeviceConnectionResponse(address=TARGET, connected=False, error=error)]
        return original(request)

    x.wire.responses = responses
    task = await start(x)
    with pytest.raises(HomeAssistantError):
        await task
    assert [x.wire.stage(r) for r in x.wire.calls] == ['connect', 'disconnect']
    assert x.session.sync_result == 'failed' and x.session.alarm_snapshot is None
    released(x, readers[0])


@pytest.mark.asyncio
async def test_loss_never_grants_replacement_generation_cleanup(owned):
    x, readers = owned
    replacement = SyncWire()

    def after(stage, _request):
        if stage == 'get16':
            lose(x.wire)
            x.api.wire = replacement

    x.wire.after = after
    task = await start(x)
    with pytest.raises(HomeAssistantError):
        await task
    assert not replacement.calls and not replacement.handlers
    assert not any(x.wire.stage(r) == 'disconnect' for r in x.wire.calls)
    assert x.session.sync_cleanup_result == 'not_attempted'
    assert x.session.alarm_snapshot is None
    released(x, readers[0])


@pytest.mark.asyncio
async def test_repeated_caller_cancel_during_postloss_cleanup_joins_original(owned):
    x, readers = owned
    x.wire.hold_at = 'disconnect'

    def after(stage, _request):
        if stage == 'step':
            lose(x.wire)

    x.wire.after = after
    task = await start(x)
    await spin_until(lambda: x.wire.entered.is_set() or task.done())
    assert x.wire.entered.is_set()
    alarm, stamp = x.session.alarm_snapshot, x.session.alarm_last_sync
    try:
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and x.session.lock.locked()
    finally:
        x.wire.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelling() == 2 and x.session.sync_result == 'cancelled'
    assert x.session.alarm_snapshot is alarm and x.session.alarm_last_sync == stamp
    assert alarm is not None and x.session.alarms_fresh
    assert x.session.record is None and x.session.sync_cleanup_result == 'success'
    released(x, readers[0])
