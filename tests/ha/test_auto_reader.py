"""Entry-owned automatic requests with public HA-shaped metadata, no radio I/O."""
import asyncio
import importlib.util
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError

ADDRESS = 'AA:BB:CC:DD:EE:FF'
MODULE = 'custom_components.casio_abl100.auto_read'


def info(timestamp=100.0, **kwargs):
    return SimpleNamespace(address=ADDRESS, name='CASIO ABL-100WE\0',
                           connectable=True, time=timestamp, **kwargs)


@pytest.fixture
def harness():
    assert importlib.util.find_spec(MODULE) is not None, 'Native automatic reader API is missing'
    from custom_components.casio_abl100.auto_read import AutoStepReader
    state = SimpleNamespace(now=100.0, cached=None)
    owner = SimpleNamespace(address=ADDRESS, closed=False, lock=asyncio.Lock(),
                            tasks=set(), read=AsyncMock())
    tasks = []
    def create(hass, coro, name, eager_start=False):
        assert eager_start is False
        task = asyncio.create_task(coro)
        tasks.append(task)
        return task
    entry = SimpleNamespace(async_create_background_task=Mock(side_effect=create),
                            state=ConfigEntryState.LOADED, runtime_data=owner)
    hass = SimpleNamespace()
    with patch(MODULE + '.monotonic_time_coarse', side_effect=lambda: state.now), patch(
        MODULE + '.bluetooth.async_last_service_info', side_effect=lambda *a, **k: state.cached
    ), patch(MODULE + '.bluetooth.async_register_callback', return_value=Mock()) as register, patch(
        MODULE + '.async_track_time_interval', return_value=Mock()
    ) as timer:
        reader = AutoStepReader(hass, entry, owner)
        yield SimpleNamespace(reader=reader, owner=owner, state=state, entry=entry,
                              register=register, timer=timer, tasks=tasks)
    # Async cleanup belongs to each caller; no pending child may be hidden here.
    assert all(task.done() for task in tasks)


async def drain(h):
    if h.tasks:
        await asyncio.gather(*h.tasks, return_exceptions=True)


def advertise(h, timestamp=None):
    h.state.cached = info(h.state.now if timestamp is None else timestamp)
    h.register.call_args.args[1](h.state.cached, bluetooth.BluetoothChange.ADVERTISEMENT)


@pytest.mark.asyncio
async def test_start_is_passive_no_cached_replay_or_connection(harness):
    h = harness
    h.state.cached = info(99)
    h.reader.start()
    assert h.register.call_args.args[2] == {'address': ADDRESS, 'connectable': True}
    assert h.register.call_args.args[3] is bluetooth.BluetoothScanningMode.PASSIVE
    assert h.register.call_args.kwargs['replay'] is bluetooth.BluetoothCallbackReplay.DISABLED
    advertise(h)
    h.timer.call_args.args[1](None)
    await drain(h)
    h.owner.read.assert_not_called()
    await h.reader.stop()
    h.register.return_value.assert_called_once()
    h.timer.return_value.assert_called_once()


@pytest.mark.asyncio
async def test_fresh_burst_claims_once_and_later_quiet_gap_allows_one(harness):
    h = harness
    h.reader.start()
    for _ in range(30):
        advertise(h)
    await drain(h)
    h.owner.read.assert_awaited_once()
    h.state.now = 110
    advertise(h)
    await drain(h)
    assert h.owner.read.await_count == 1
    h.state.now = 141
    advertise(h)
    await drain(h)
    assert h.owner.read.await_count == 2
    await h.reader.stop()


@pytest.mark.asyncio
async def test_metadata_timer_handles_identical_payloads_without_callback_or_cache_clear(harness):
    h = harness
    h.reader.start()
    advertise(h)
    await drain(h)
    for timestamp in (110, 120, 130):
        h.state.now = timestamp
        h.state.cached = info(timestamp)
        h.timer.call_args.args[1](None)
    await drain(h)
    assert h.owner.read.await_count == 1
    h.state.now = 170
    h.state.cached = info(170)
    h.timer.call_args.args[1](None)
    await drain(h)
    assert h.owner.read.await_count == 2
    await h.reader.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize('busy', ['lock', 'tasks', 'closed'])
async def test_busy_window_is_consumed_without_queueing(harness, busy):
    h = harness
    h.reader.start()
    if busy == 'lock':
        await h.owner.lock.acquire()
    elif busy == 'tasks':
        h.owner.tasks.add(object())
    else:
        h.owner.closed = True
    advertise(h)
    if busy == 'lock':
        h.owner.lock.release()
    elif busy == 'tasks':
        h.owner.tasks.clear()
    else:
        h.owner.closed = False
    h.state.now += 1
    advertise(h)
    await drain(h)
    h.owner.read.assert_not_called()
    await h.reader.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['closed', 'busy', 'stale', 'clock', 'suspended'])
async def test_task_rechecks_opportunity_before_entering_session(harness, change):
    h = harness
    h.reader.start()
    advertise(h)
    if change == 'closed':
        h.owner.closed = True
    elif change == 'busy':
        await h.owner.lock.acquire()
    elif change == 'stale':
        h.state.now += 6
    elif change == 'clock':
        h.state.now = float('nan')
    else:
        h.reader.suspend()
    await drain(h)
    h.owner.read.assert_not_called()
    if change == 'busy':
        h.owner.lock.release()
    await h.reader.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize('timestamp', [99, 94, 101, float('nan'), float('inf'), True, 10**400])
async def test_old_future_or_malformed_metadata_cannot_dispatch(harness, timestamp):
    h = harness
    h.state.cached = info(99)
    h.reader.start()
    advertise(h, timestamp)
    await drain(h)
    h.owner.read.assert_not_called()
    await h.reader.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize('clock', [float('nan'), float('inf'), True, 10**400, 99])
async def test_clock_fault_disables_reader_until_reload(harness, clock):
    h = harness
    h.reader.start()
    h.state.now = clock
    advertise(h, 100)
    h.state.now = 200
    advertise(h, 200)
    await drain(h)
    h.owner.read.assert_not_called()
    await h.reader.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('address', '11:22:33:44:55:66'),
                                         ('name', 'OTHER'), ('connectable', False)])
async def test_other_devices_and_nonconnectable_metadata_do_not_claim(harness, field, value):
    h = harness
    h.reader.start()
    wrong = info()
    setattr(wrong, field, value)
    h.register.call_args.args[1](wrong, bluetooth.BluetoothChange.ADVERTISEMENT)
    advertise(h)
    await drain(h)
    h.owner.read.assert_awaited_once()
    await h.reader.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [HomeAssistantError('PRIVATE_CANARY'), RuntimeError('PRIVATE_CANARY'),
                                     asyncio.CancelledError('PRIVATE_CANARY')])
async def test_background_failures_are_consumed_sanitized_and_not_retried(harness, caplog, failure):
    h = harness
    h.reader.start()
    h.owner.read.side_effect = failure
    advertise(h)
    await drain(h)
    h.state.now += 1
    advertise(h)
    await drain(h)
    assert h.owner.read.await_count == 1
    assert 'PRIVATE_CANARY' not in caplog.text
    await h.reader.stop()


@pytest.mark.asyncio
async def test_stop_before_task_entry_cancels_without_read_and_late_callback_is_inert(harness):
    h = harness
    h.reader.start()
    advertise(h)
    await h.reader.stop()
    h.state.now += 100
    advertise(h)
    await drain(h)
    h.owner.read.assert_not_called()
    await h.reader.stop()
    h.register.return_value.assert_called_once()
    h.timer.return_value.assert_called_once()


@pytest.mark.asyncio
async def test_stop_awaits_owned_read_cleanup(harness):
    h = harness
    entered, cleaned = asyncio.Event(), asyncio.Event()
    async def read():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()
    h.owner.read.side_effect = read
    h.reader.start()
    advertise(h)
    await asyncio.wait_for(entered.wait(), 1)
    await h.reader.stop()
    assert cleaned.is_set() and all(t.done() for t in h.tasks)


@pytest.mark.asyncio
async def test_task_creation_fault_closes_coroutine_and_does_not_retry_same_window(harness):
    h = harness
    h.reader.start()
    h.entry.async_create_background_task.side_effect = RuntimeError('PRIVATE_CANARY')
    advertise(h)
    advertise(h)
    await drain(h)
    assert h.entry.async_create_background_task.call_count == 1
    await h.reader.stop()


@pytest.mark.asyncio
async def test_resume_ignores_current_burst_but_preserves_future_opportunity(harness):
    h = harness
    h.reader.start()
    h.reader.suspend()
    advertise(h)
    h.reader.resume()
    h.state.now += 1
    advertise(h)
    await drain(h)
    h.owner.read.assert_not_called()
    h.state.now += 40
    advertise(h)
    await drain(h)
    h.owner.read.assert_awaited_once()
    await h.reader.stop()


@pytest.mark.asyncio
async def test_target_timestamp_regression_disables_future_requests(harness):
    h = harness
    h.reader.start()
    advertise(h)
    await drain(h)
    h.state.now = 101
    advertise(h, 99)
    h.state.now = 200
    advertise(h)
    await drain(h)
    assert h.owner.read.await_count == 1
    await h.reader.stop()


@pytest.mark.asyncio
async def test_synchronous_replayed_callback_is_suppressed_even_if_producer_ignores_flag(harness):
    h = harness
    def register(hass, callback, matcher, mode, **kwargs):
        callback(info(), bluetooth.BluetoothChange.ADVERTISEMENT)
        return Mock()
    h.register.side_effect = register
    h.reader.start()
    await drain(h)
    h.owner.read.assert_not_called()
    await h.reader.stop()


@pytest.mark.asyncio
async def test_native_timer_setup_failure_removes_partial_registration(harness):
    h = harness
    h.timer.side_effect = RuntimeError('PRIVATE_CANARY')
    with pytest.raises(RuntimeError):
        h.reader.start()
    h.register.return_value.assert_called_once()
    advertise(h)
    await drain(h)
    h.owner.read.assert_not_called()
    await h.reader.stop()


@pytest.mark.asyncio
async def test_cleanup_backend_cancellation_cannot_skip_other_observers(harness):
    h = harness
    h.reader.start()
    h.register.return_value.side_effect = asyncio.CancelledError('PRIVATE_CANARY')
    await h.reader.stop()
    h.timer.return_value.assert_called_once()
    h.owner.read.assert_not_called()


async def loop_checkpoint():
    """One event-loop pump, not a timing delay; flush preceding ready callbacks."""
    future = asyncio.get_running_loop().create_future()
    asyncio.get_running_loop().call_soon(future.set_result, None)
    await future


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['normal', 'preentry', 'repeated', 'concurrent', 'private'])
async def test_stop_preserves_held_cooperative_cleanup(harness, caplog, mode):
    h = harness
    entered, cleaning, release, cleaned = (asyncio.Event() for _ in range(4))
    loop = asyncio.get_running_loop()
    errors = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda loop, context: errors.append(context))
    stops = []

    async def read():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            cleaned.set()
            if mode == 'private':
                raise RuntimeError('PRIVATE_CANARY')

    async def stop():
        if mode == 'preentry':
            caller = asyncio.current_task()
            assert caller is not None
            caller.cancel()
        await h.reader.stop()

    h.owner.read.side_effect = read
    h.reader.start()
    advertise(h)
    try:
        await asyncio.wait_for(entered.wait(), 1)
        stops.append(asyncio.create_task(stop()))
        await asyncio.wait_for(cleaning.wait(), 1)
        if mode in ('repeated', 'private'):
            stops[0].cancel()
            await loop_checkpoint()
            stops[0].cancel()
        elif mode == 'concurrent':
            stops.append(asyncio.create_task(h.reader.stop()))
            await loop_checkpoint()
            stops[0].cancel()
        await loop_checkpoint()
        held = not h.tasks[0].done() and all(not task.done() for task in stops)
        cancel_count = h.tasks[0].cancelling()
    finally:
        release.set()
        results = await asyncio.gather(*stops, return_exceptions=True)
        await drain(h)
        await h.reader.stop()
        await loop_checkpoint()
        loop.set_exception_handler(previous_handler)

    assert held, 'stop caller/concurrent cancellation interrupted held cooperative cleanup'
    assert cancel_count == 1, 'owned read must receive exactly one stop cancellation'
    assert cleaned.is_set(), 'caller cancellation aborted cooperative read cleanup'
    expected_cancel = mode != 'normal'
    assert isinstance(results[0], asyncio.CancelledError) is expected_cancel
    if mode == 'concurrent':
        assert results[1] is None  # Each waiter retains its own cancellation priority.
    assert not errors
    assert 'PRIVATE_CANARY' not in caplog.text
    h.owner.read.assert_awaited_once()
    assert all(task.done() for task in h.tasks)
    assert h.reader._task is None
    h.register.return_value.assert_called_once()
    h.timer.return_value.assert_called_once()
    h.state.now += 100
    advertise(h)
    h.timer.call_args.args[1](None)
    assert len(h.tasks) == 1


@pytest.mark.asyncio
async def test_scheduler_backend_cancellation_closes_owned_coroutine(harness, caplog):
    h = harness
    captured, propagated, callback_tasks = [], [], []
    h.reader.start()

    def create(*args, **kwargs):
        captured.append(args[1])
        raise asyncio.CancelledError('PRIVATE_CANARY')

    def callback():
        callback_tasks.append(asyncio.current_task())
        try:
            advertise(h)
            advertise(h)
        except asyncio.CancelledError:
            propagated.append(True)

    h.entry.async_create_background_task.side_effect = create
    asyncio.get_running_loop().call_soon(callback)
    await loop_checkpoint()
    closed = captured[0].cr_frame is None
    try:
        assert closed, 'scheduler cancellation leaked its supplied coroutine'
        assert not propagated
        assert callback_tasks == [None]  # Synchronous backend fault, not caller cancellation.
        assert h.entry.async_create_background_task.call_count == 1
        assert caplog.text.count('Unable to schedule automatic step read') == 1
        assert 'PRIVATE_CANARY' not in caplog.text
        h.owner.read.assert_not_called()
    finally:
        for coroutine in captured:
            coroutine.close()
        await h.reader.stop()
