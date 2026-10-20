"""Manual alarm sessions: synthetic BLE, real alarm parser/transport, no watch I/O."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
import json

import pytest
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
import bleak_retry_connector as retry
from homeassistant.exceptions import HomeAssistantError

from custom_components.casio_abl100 import WatchSession, async_setup_entry, async_unload_entry
from custom_components.casio_abl100.alarms import AlarmSnapshot
from custom_components.casio_abl100.protocol import ProtocolError
from custom_components.casio_abl100.transport import ReadError
from tests.protocol.test_alarm_transport import Client, FIRST, REMAINING, writes
from tests.protocol.test_transport import FakeClient

ADDR = 'AA:BB:CC:DD:EE:FF'
DEVICE = SimpleNamespace(address=ADDR, name='CASIO ABL-100WE\0')


@pytest.fixture
def manager():
    with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address', return_value=DEVICE) as lookup:
        value = WatchSession(SimpleNamespace(), ADDR)
        yield value, lookup


def assert_error(error, key='alarm_read_failed'):
    assert error.value.translation_key == key
    assert error.value.args == (key,)
    assert error.value.translation_domain == 'casio_abl100'


@pytest.mark.asyncio
async def test_unknown_before_manual_read_and_no_setup_poll():
    assert hasattr(WatchSession, 'read_alarms'), 'manual alarm API is absent'
    calls = []
    async def forward(*args):
        calls.append(args)
    from unittest.mock import AsyncMock, Mock
    cancel_update = Mock()
    add_update_listener = Mock(return_value=cancel_update)
    on_unload = Mock()
    cancel_stop = Mock()
    hass = SimpleNamespace(bus=SimpleNamespace(async_listen_once=Mock(return_value=cancel_stop)), config_entries=SimpleNamespace(
        async_forward_entry_setups=forward, async_unload_platforms=AsyncMock(return_value=True),
    ))
    entry = SimpleNamespace(entry_id='syntheticalarmentry', data={'address': ADDR}, unique_id=ADDR, options={},
                            add_update_listener=add_update_listener, async_on_unload=on_unload)
    observations = SimpleNamespace(async_load_into=AsyncMock(), async_close=AsyncMock())
    safety = SimpleNamespace(async_load=AsyncMock(), async_close=AsyncMock(),
                             blocked=False, result=None, submitted=False)
    from custom_components.casio_abl100.alarm_draft_storage import AlarmDraftStorage
    storage = SimpleNamespace(async_load_into=AsyncMock(), async_close=AsyncMock())
    with patch('custom_components.casio_abl100.alarm_draft_storage.AlarmDraftStorage', return_value=storage), patch('custom_components.casio_abl100.alarm_write_safety.AlarmWriteSafety', return_value=safety), patch('custom_components.casio_abl100.EntryObservations', return_value=observations), patch('custom_components.casio_abl100.establish_connection') as connect:
        await async_setup_entry(hass, entry)
        assert entry.runtime_data.alarm_snapshot is None
        assert entry.runtime_data.alarm_last_sync is None
        assert entry.runtime_data.alarms_fresh is False
        connect.assert_not_called()
    assert len(calls) == 1
    add_update_listener.assert_called_once()
    assert on_unload.call_count == 2
    assert on_unload.call_args_list[0].args == (cancel_stop,)
    assert on_unload.call_args_list[1].args == (cancel_update,)
    hass.bus.async_listen_once.assert_called_once()
    observations.async_load_into.assert_awaited_once_with(entry.runtime_data)
    hass.config_entries.async_unload_platforms.assert_not_called()


@pytest.mark.asyncio
async def test_real_alarm_transport_publishes_complete_snapshot_after_disconnect(manager):
    owner, lookup = manager
    client = Client()
    observed = []
    owner.record = object()
    owner.last_sync = object()
    previous_record, previous_steps_stamp = owner.record, owner.last_sync
    owner.fresh = True
    owner.subscribe(lambda: observed.append((owner.alarm_snapshot, owner.alarms_fresh, client.disconnected)))
    with patch('custom_components.casio_abl100.establish_connection', return_value=client) as connect:
        await owner.read_alarms()
    lookup.assert_called_once_with(owner.hass, ADDR, connectable=True)
    assert connect.call_args.args[1:] == (DEVICE, 'Casio ABL-100WE')
    assert issubclass(connect.call_args.args[0], owner.client_class)
    assert connect.call_args.kwargs == {'pair': False, 'max_attempts': 2, 'timeout': 10}
    assert isinstance(owner.alarm_snapshot, AlarmSnapshot)
    assert [(a.slot, a.hour, a.minute, a.enabled, a.flags, a.aux) for a in owner.alarm_snapshot.alarms] == [
        (1, 23, 59, True, 192, 0), (2, 0, 0, True, 64, 7),
        (3, 9, 30, False, 1, 2), (4, 12, 0, True, 64, 3), (5, 16, 59, False, 128, 4),
    ]
    assert owner.alarm_snapshot.hourly_signal is True
    assert owner.alarm_last_sync.tzinfo is not None
    await asyncio.sleep(0)
    assert observed == [(owner.alarm_snapshot, True, True)]
    assert writes(client) == [(b'\x15', False), (b'\x16', False)]
    assert owner.record is previous_record and owner.fresh and owner.last_sync is previous_steps_stamp
    assert not owner.tasks and not owner.lock.locked()


@pytest.mark.asyncio
async def test_failed_refresh_retains_previous_snapshot_and_timestamp(manager):
    owner, _ = manager
    good, bad = Client(), Client(remaining=REMAINING[:-1])
    seen = []
    owner.subscribe(lambda: seen.append((owner.alarm_snapshot, owner.alarm_last_sync, owner.alarms_fresh)))
    with patch('custom_components.casio_abl100.establish_connection', side_effect=[good, bad]):
        await owner.read_alarms()
        await asyncio.sleep(0)
        snapshot, stamp = owner.alarm_snapshot, owner.alarm_last_sync
        with pytest.raises(HomeAssistantError) as error:
            await owner.read_alarms()
    await asyncio.sleep(0)
    assert_error(error)
    assert owner.alarm_snapshot is snapshot and owner.alarm_last_sync is stamp
    assert seen == [(snapshot, stamp, True), (snapshot, stamp, False)]
    assert good.disconnected and bad.disconnected
    assert owner.record is None and owner.last_sync is None and owner.fresh is False


@pytest.mark.asyncio
async def test_step_success_and_failure_do_not_change_alarm_freshness(manager):
    owner, _ = manager
    with patch('custom_components.casio_abl100.establish_connection', return_value=Client()):
        await owner.read_alarms()
    snapshot = owner.alarm_snapshot
    stamp = owner.alarm_last_sync
    with patch('custom_components.casio_abl100.establish_connection', return_value=FakeClient()), patch('custom_components.casio_abl100.dt_util.now', return_value=datetime(2026, 8, 1)):
        await owner.read()
    assert owner.alarm_snapshot is snapshot and owner.alarm_last_sync is stamp
    assert owner.alarms_fresh and owner.record.steps == 1485 and owner.fresh
    with patch('custom_components.casio_abl100.establish_connection', side_effect=BleakError('PRIVATE')):
        with pytest.raises(HomeAssistantError):
            await owner.read()
    assert owner.alarm_snapshot is snapshot and owner.alarm_last_sync is stamp
    assert owner.alarms_fresh and owner.record.steps == 1485 and not owner.fresh


@pytest.mark.asyncio
async def test_step_cancellation_does_not_change_alarm_state(manager):
    owner, _ = manager
    with patch('custom_components.casio_abl100.establish_connection', return_value=Client()):
        await owner.read_alarms()
    snapshot, stamp = owner.alarm_snapshot, owner.alarm_last_sync
    entered = asyncio.Event()
    async def connect(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    with patch('custom_components.casio_abl100.establish_connection', side_effect=connect):
        task = asyncio.create_task(owner.read())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert owner.alarm_snapshot is snapshot and owner.alarm_last_sync is stamp
    assert owner.alarms_fresh and not owner.tasks and not owner.lock.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize('device', [None, SimpleNamespace(address=ADDR, name='OtherWatch'),
                                     SimpleNamespace(address='11:22:33:44:55:66', name='ABL-100')])
async def test_unverified_device_does_not_connect_or_replace_alarm_state(device):
    owner = WatchSession(SimpleNamespace(), ADDR)
    previous_alarm, previous_stamp = object(), object()
    owner.alarm_snapshot, owner.alarm_last_sync, owner.alarms_fresh = previous_alarm, previous_stamp, True
    seen = []
    owner.subscribe(lambda: seen.append((owner.alarm_snapshot, owner.alarm_last_sync, owner.alarms_fresh)))
    with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address', return_value=device), patch('custom_components.casio_abl100.establish_connection') as connect:
        with pytest.raises(HomeAssistantError) as error:
            await owner.read_alarms()
    assert_error(error, 'unavailable')
    connect.assert_not_called()
    await asyncio.sleep(0)
    assert owner.alarm_snapshot is previous_alarm and owner.alarm_last_sync is previous_stamp
    assert seen == [(previous_alarm, previous_stamp, False)] and not owner.tasks


@pytest.mark.asyncio
async def test_closed_entry_never_connects_or_notifies():
    owner = WatchSession(SimpleNamespace(), ADDR)
    owner.closed = True
    seen = []
    owner.subscribe(lambda: seen.append(True))
    with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address') as lookup:
        with pytest.raises(HomeAssistantError) as error:
            await owner.read_alarms()
    assert_error(error, 'closed')
    lookup.assert_not_called()
    assert not seen and not owner.tasks


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [ReadError('PRIVATE_READ'), ProtocolError('PRIVATE_PROTOCOL'),
                                      TimeoutError('PRIVATE_TIMEOUT'), OSError('PRIVATE_OS'),
                                      RuntimeError('PRIVATE_RUNTIME'), BleakError('PRIVATE_BLEAK'),
                                      asyncio.CancelledError()])
async def test_backend_transport_failures_are_sanitized_even_for_spurious_cancel(manager, failure, caplog):
    owner, _ = manager
    previous_record, previous_steps_stamp = object(), object()
    previous_alarm, previous_alarm_stamp = object(), object()
    owner.record, owner.last_sync, owner.fresh = previous_record, previous_steps_stamp, True
    owner.alarm_snapshot, owner.alarm_last_sync, owner.alarms_fresh = previous_alarm, previous_alarm_stamp, True
    seen = []
    owner.subscribe(lambda: seen.append(owner.alarms_fresh))
    class FaultingClient(Client):
        async def start_notify(self, characteristic, callback):
            raise failure
    client = FaultingClient()
    with patch('custom_components.casio_abl100.establish_connection', return_value=client):
        with pytest.raises(HomeAssistantError) as error:
            await owner.read_alarms()
    assert client.disconnected and client.events == [('disconnect',)]
    assert_error(error)
    await asyncio.sleep(0)
    assert seen == [False] and owner.record is previous_record and owner.fresh
    assert owner.last_sync is previous_steps_stamp
    assert owner.alarm_snapshot is previous_alarm and owner.alarm_last_sync is previous_alarm_stamp
    assert 'PRIVATE' not in caplog.text and 'PRIVATE' not in repr(error.value.args)
    assert not owner.tasks and not owner.lock.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [RuntimeError('PRIVATE_CONNECT'), TimeoutError('PRIVATE_TIMEOUT'),
                                      BleakError('PRIVATE_BLEAK'), asyncio.CancelledError()])
async def test_connector_failures_sanitized_and_do_not_touch_steps(manager, failure):
    owner, _ = manager
    owner.record, owner.last_sync, owner.fresh = object(), object(), True
    previous_record, previous_stamp = owner.record, owner.last_sync
    seen = []
    owner.subscribe(lambda: seen.append(owner.alarms_fresh))
    with patch('custom_components.casio_abl100.establish_connection', side_effect=failure):
        with pytest.raises(HomeAssistantError) as error:
            await owner.read_alarms()
    assert_error(error)
    await asyncio.sleep(0)
    assert seen == [False] and owner.record is previous_record and owner.fresh
    assert owner.last_sync is previous_stamp
    assert owner.alarm_snapshot is None and owner.alarm_last_sync is None
    assert not owner.tasks and not owner.lock.locked()


@pytest.mark.asyncio
async def test_success_is_withheld_until_transport_cleanup_completes(manager):
    owner, _ = manager
    client = Client()
    client.disconnect_gate = asyncio.Event()
    entered = asyncio.Event()
    client.on_disconnect = entered.set
    seen = []
    owner.subscribe(lambda: seen.append(owner.alarm_snapshot))
    with patch('custom_components.casio_abl100.establish_connection', return_value=client):
        task = asyncio.create_task(owner.read_alarms())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            assert owner.alarm_snapshot is None and owner.alarm_last_sync is None and seen == []
            client.disconnect_gate.set()
            await asyncio.wait_for(task, 1)
            await asyncio.sleep(0)
        finally:
            client.disconnect_gate.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert len(seen) == 1 and seen[0] is owner.alarm_snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize('first_kind', ['alarm', 'steps'])
async def test_steps_and_alarms_share_one_lock_in_either_order(manager, first_kind):
    owner, _ = manager
    entered, release = asyncio.Event(), asyncio.Event()
    connects = []
    async def connect(*args, **kwargs):
        connects.append(True)
        if len(connects) == 1:
            entered.set()
            await release.wait()
        return Client() if len(connects) == (1 if first_kind == 'alarm' else 2) else FakeClient()
    first = owner.read_alarms if first_kind == 'alarm' else owner.read
    second = owner.read if first_kind == 'alarm' else owner.read_alarms
    with patch('custom_components.casio_abl100.establish_connection', side_effect=connect), patch('custom_components.casio_abl100.dt_util.now', return_value=datetime(2026, 8, 1)):
        a = asyncio.create_task(first())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            b = asyncio.create_task(second())
            await asyncio.sleep(0)
            assert len(connects) == 1 and len(owner.tasks) == 2
            release.set()
            await asyncio.wait_for(asyncio.gather(a, b), 1)
        finally:
            release.set()
            for task in (a, b):
                task.cancel()
            await asyncio.gather(a, b, return_exceptions=True)
    assert len(connects) == 2 and owner.record.steps == 1485 and len(owner.alarm_snapshot.alarms) == 5
    assert not owner.tasks and not owner.lock.locked()


@pytest.mark.asyncio
async def test_unload_cancels_active_alarm_and_queued_steps_without_late_publication(manager):
    owner, _ = manager
    entered = asyncio.Event()
    client = Client()
    client.subscribe_gate = asyncio.Event()
    async def notify_entered(*args, **kwargs):
        entered.set()
        return client
    calls = []
    owner.subscribe(lambda: calls.append((owner.alarm_snapshot, owner.record)))
    async def unload(*args):
        return True
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_unload_platforms=unload))
    with patch('custom_components.casio_abl100.establish_connection', side_effect=notify_entered) as connect:
        a = asyncio.create_task(owner.read_alarms())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            b = asyncio.create_task(owner.read())
            await asyncio.sleep(0)
            assert len(owner.tasks) == 2 and connect.call_count == 1
            assert await asyncio.wait_for(async_unload_entry(hass, SimpleNamespace(runtime_data=owner)), 2)
            for task in (a, b):
                with pytest.raises(asyncio.CancelledError):
                    await task
        finally:
            client.subscribe_gate.set()
            for task in (a, b):
                task.cancel()
            await asyncio.gather(a, b, return_exceptions=True)
    assert owner.closed and not owner.tasks and not owner.listeners
    assert owner.alarm_snapshot is None and owner.record is None
    assert connect.call_count == 1 and client.disconnected
    assert all(snapshot is None and record is None for snapshot, record in calls)


@pytest.mark.asyncio
async def test_failed_platform_unload_keeps_alarm_entry_operable(manager):
    owner, _ = manager
    async def unload(*args):
        return False
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_unload_platforms=unload))
    seen = []
    owner.subscribe(lambda: seen.append(owner.alarms_fresh))
    assert await async_unload_entry(hass, SimpleNamespace(runtime_data=owner)) is False
    with patch('custom_components.casio_abl100.establish_connection', return_value=Client()):
        await owner.read_alarms()
    await asyncio.sleep(0)
    assert not owner.closed and seen == [True] and len(owner.listeners) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['connect', 'transport', 'disconnect'])
async def test_actual_caller_cancel_at_each_phase_is_never_home_assistant_error(manager, phase):
    owner, _ = manager
    entered = asyncio.Event()
    client = Client()
    async def connect(*args, **kwargs):
        if phase == 'connect':
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise RuntimeError('masked caller cancellation')
        return client
    if phase == 'transport':
        client.subscribe_gate = asyncio.Event()
        original = client.start_notify
        async def subscribe(*args):
            entered.set()
            try:
                await original(*args)
            except asyncio.CancelledError:
                raise RuntimeError('masked caller cancellation')
        client.start_notify = subscribe
    if phase == 'disconnect':
        client.disconnect_gate = asyncio.Event()
        client.on_disconnect = entered.set
    with patch('custom_components.casio_abl100.establish_connection', side_effect=connect):
        task = asyncio.create_task(owner.read_alarms())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        finally:
            if client.subscribe_gate: client.subscribe_gate.set()
            if client.disconnect_gate: client.disconnect_gate.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert owner.alarm_snapshot is None and owner.alarm_last_sync is None and not owner.alarms_fresh
    assert not owner.tasks and not owner.lock.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['error', 'cancel', 'timeout'])
async def test_real_connector_partial_client_is_disconnected_once_and_sanitized(manager, failure):
    owner, _ = manager
    device = BLEDevice(ADDR, 'ABL-100', {})
    created = []
    class PartialClient:
        def __init__(self, target, **kwargs):
            assert target is device and kwargs['pair'] is False
            created.append(self)
            self.disconnect_calls = 0
        async def connect(self, **kwargs):
            if failure == 'error': raise BleakError('PRIVATE_CONNECT')
            if failure == 'cancel': raise asyncio.CancelledError()
            await asyncio.Event().wait()
        async def disconnect(self):
            self.disconnect_calls += 1
    async def no_devices(*args): return []
    async def no_cache(*args): return False
    async def no_backoff(*args): return None
    owner.client_class = PartialClient
    with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address', return_value=device), patch.object(retry, 'get_connected_devices', side_effect=no_devices), patch.object(retry, '_has_valid_services_in_cache', side_effect=no_cache), patch.object(retry, 'wait_for_disconnect', side_effect=no_backoff), patch.object(retry, 'BLEAK_SAFETY_TIMEOUT', 0.02):
        with pytest.raises(HomeAssistantError) as error:
            await asyncio.wait_for(owner.read_alarms(), 1)
    assert_error(error)
    assert len(created) == 1 and created[0].disconnect_calls == 1
    assert not owner.tasks and owner.alarm_snapshot is None


@pytest.mark.asyncio
async def test_preentry_cancel_has_priority_over_connector_backend_error(manager):
    owner, _ = manager
    async def connect(*args, **kwargs):
        raise RuntimeError('PRIVATE_CONNECT')
    async def cancelled_before_entry():
        asyncio.current_task().cancel()
        await owner.read_alarms()
    with patch('custom_components.casio_abl100.establish_connection', side_effect=connect):
        task = asyncio.create_task(cancelled_before_entry())
        with pytest.raises(asyncio.CancelledError):
            await task
    assert owner.alarm_snapshot is None and not owner.tasks and not owner.lock.locked()


@pytest.mark.asyncio
async def test_real_connector_caller_cancel_during_connect_cleans_partial_client(manager):
    owner, _ = manager
    device = BLEDevice(ADDR, 'ABL-100', {})
    entered = asyncio.Event()
    created = []

    class PartialClient:
        def __init__(self, target, **kwargs):
            assert target is device and kwargs['pair'] is False
            created.append(self)
            self.disconnect_calls = 0

        async def connect(self, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise RuntimeError('PRIVATE_CANCEL_MASK')

        async def disconnect(self):
            self.disconnect_calls += 1

    async def no_devices(*args): return []
    async def no_cache(*args): return False
    async def no_backoff(*args): return None
    owner.client_class = PartialClient
    with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address', return_value=device), patch.object(retry, 'get_connected_devices', side_effect=no_devices), patch.object(retry, '_has_valid_services_in_cache', side_effect=no_cache), patch.object(retry, 'wait_for_disconnect', side_effect=no_backoff):
        task = asyncio.create_task(owner.read_alarms())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert len(created) == 1 and created[0].disconnect_calls == 1
    assert not owner.tasks and owner.alarm_snapshot is None and not owner.alarms_fresh


@pytest.mark.asyncio
@pytest.mark.parametrize('original,cleanup', [('error', 'error'), ('cancel', 'error'),
                                                 ('error', 'cancel'), ('none', 'error'),
                                                 ('none', 'cancel')])
async def test_partial_client_cleanup_priority_and_failure(manager, original, cleanup):
    owner, _ = manager
    class PartialClient:
        def __init__(self, *args, **kwargs):
            self.disconnect_calls = 0
        async def disconnect(self):
            self.disconnect_calls += 1
            if cleanup == 'error': raise RuntimeError('PRIVATE_CLEANUP')
            raise asyncio.CancelledError()
    owner.client_class = PartialClient
    created = []
    async def connector(cls, *args, **kwargs):
        client = cls()
        created.append(client)
        if original == 'error': raise RuntimeError('PRIVATE_CONNECT')
        if original == 'cancel': raise asyncio.CancelledError()
        return Client()  # returned client belongs to transport, partial remains owned
    with patch('custom_components.casio_abl100.establish_connection', side_effect=connector):
        with pytest.raises(HomeAssistantError) as error:
            await owner.read_alarms()
    assert_error(error)
    assert len(created) == 1 and created[0].disconnect_calls == 1
    assert owner.alarm_snapshot is None and not owner.tasks


@pytest.mark.asyncio
async def test_caller_cancel_during_partial_cleanup_overrides_original_and_cleanup_fault(manager):
    owner, _ = manager
    entered = asyncio.Event()
    class PartialClient:
        def __init__(self, *args, **kwargs): self.disconnect_calls = 0
        async def disconnect(self):
            self.disconnect_calls += 1
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise RuntimeError('PRIVATE_CLEANUP_MASK')
    owner.client_class = PartialClient
    created = []
    async def connector(cls, *args, **kwargs):
        created.append(cls())
        raise RuntimeError('PRIVATE_CONNECT')
    with patch('custom_components.casio_abl100.establish_connection', side_effect=connector):
        task = asyncio.create_task(owner.read_alarms())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert len(created) == 1 and created[0].disconnect_calls == 1
    assert owner.alarm_snapshot is None and not owner.tasks


@pytest.mark.asyncio
async def test_cancelled_queued_alarm_never_connects_and_does_not_change_steps(manager):
    owner, _ = manager
    entered, release = asyncio.Event(), asyncio.Event()
    owner.record, owner.last_sync, owner.fresh = object(), object(), True
    alarm_seen = []
    owner.subscribe(lambda: alarm_seen.append(owner.alarms_fresh))
    async def connect(*args, **kwargs):
        entered.set()
        await release.wait()
        return Client()
    with patch('custom_components.casio_abl100.establish_connection', side_effect=connect) as connector:
        active = asyncio.create_task(owner.read_alarms())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            queued = asyncio.create_task(owner.read_alarms())
            await asyncio.sleep(0)
            queued.cancel()
            with pytest.raises(asyncio.CancelledError):
                await queued
            assert connector.call_count == 1 and alarm_seen == [False]
            release.set()
            await asyncio.wait_for(active, 1)
        finally:
            release.set()
            active.cancel()
            await asyncio.gather(active, return_exceptions=True)
    assert connector.call_count == 1 and alarm_seen == [False, True]
    assert owner.record is not None and owner.fresh and not owner.tasks


@pytest.mark.asyncio
async def test_preentry_cancel_preserves_prior_alarm_value_despite_valid_read(manager):
    owner, _ = manager
    previous = object()
    stamp = object()
    owner.alarm_snapshot, owner.alarm_last_sync, owner.alarms_fresh = previous, stamp, True
    seen = []
    owner.subscribe(lambda: seen.append(owner.alarms_fresh))
    async def pending():
        asyncio.current_task().cancel()
        await owner.read_alarms()
    with patch('custom_components.casio_abl100.establish_connection', return_value=Client()) as connect:
        task = asyncio.create_task(pending())
        with pytest.raises(asyncio.CancelledError): await task
    connect.assert_not_called()
    await asyncio.sleep(0)
    assert owner.alarm_snapshot is previous and owner.alarm_last_sync is stamp
    assert owner.alarms_fresh is False and seen == [False] and not owner.tasks


@pytest.mark.asyncio
async def test_transport_cleanup_masks_cancel_but_caller_still_wins(manager):
    owner, _ = manager
    entered = asyncio.Event()
    client = Client()
    async def masked_disconnect():
        client.events.append(('disconnect',))
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise RuntimeError('PRIVATE_MASK')
    client.disconnect = masked_disconnect
    with patch('custom_components.casio_abl100.establish_connection', return_value=client):
        task = asyncio.create_task(owner.read_alarms())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert owner.alarm_snapshot is None and not owner.tasks and not owner.lock.locked()


@pytest.mark.asyncio
async def test_real_transport_malformed_reply_and_disconnect_fault_preserve_old_value(manager):
    owner, _ = manager
    previous, stamp = object(), object()
    owner.alarm_snapshot, owner.alarm_last_sync, owner.alarms_fresh = previous, stamp, True
    bad = Client(first=FIRST[:-1])
    bad.fail_disconnect = True
    seen = []
    owner.subscribe(lambda: seen.append(owner.alarms_fresh))
    with patch('custom_components.casio_abl100.establish_connection', return_value=bad):
        with pytest.raises(HomeAssistantError) as error:
            await owner.read_alarms()
    assert_error(error)
    assert owner.alarm_snapshot is previous and owner.alarm_last_sync is stamp
    await asyncio.sleep(0)
    assert seen == [False] and writes(bad) == [(b'\x15', False)]
    assert bad.events[-1] == ('disconnect',) and not owner.tasks


@pytest.mark.asyncio
async def test_spurious_backend_cancel_in_real_transport_cleanup_is_read_failure(manager):
    owner, _ = manager
    client = Client()
    async def cancelled_disconnect():
        client.events.append(('disconnect',))
        raise asyncio.CancelledError()
    client.disconnect = cancelled_disconnect
    with patch('custom_components.casio_abl100.establish_connection', return_value=client):
        with pytest.raises(HomeAssistantError) as error:
            await owner.read_alarms()
    assert_error(error)
    assert writes(client) == [(b'\x15', False), (b'\x16', False)]
    assert owner.alarm_snapshot is None and not owner.tasks


@pytest.mark.asyncio
async def test_cancel_during_unload_does_not_replace_prior_alarm_snapshot(manager):
    owner, _ = manager
    original, stamp = object(), object()
    owner.alarm_snapshot, owner.alarm_last_sync, owner.alarms_fresh = original, stamp, True
    entered = asyncio.Event()
    client = Client()
    client.on_disconnect = entered.set
    client.disconnect_gate = asyncio.Event()
    async def unload(*args): return True
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_unload_platforms=unload))
    with patch('custom_components.casio_abl100.establish_connection', return_value=client):
        reading = asyncio.create_task(owner.read_alarms())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            assert await asyncio.wait_for(async_unload_entry(hass, SimpleNamespace(runtime_data=owner)), 2)
            with pytest.raises(asyncio.CancelledError): await reading
        finally:
            client.disconnect_gate.set()
            reading.cancel()
            await asyncio.gather(reading, return_exceptions=True)
    assert owner.alarm_snapshot is original and owner.alarm_last_sync is stamp
    assert not owner.alarms_fresh and not owner.tasks and not owner.listeners


@pytest.mark.asyncio
async def test_cancel_at_commit_boundary_never_publishes_new_alarm_value(manager):
    owner, _ = manager
    prior, stamp = object(), object()
    owner.alarm_snapshot, owner.alarm_last_sync, owner.alarms_fresh = prior, stamp, True
    seen = []
    owner.subscribe(lambda: seen.append((owner.alarm_snapshot, owner.alarms_fresh)))
    def cancel_at_commit():
        asyncio.current_task().cancel()
        return datetime.now(timezone.utc)
    task = asyncio.create_task(owner.read_alarms())
    with patch('custom_components.casio_abl100.establish_connection', return_value=Client()), patch('custom_components.casio_abl100.dt_util.utcnow', side_effect=cancel_at_commit):
        with pytest.raises(asyncio.CancelledError):
            await task
    assert owner.alarm_snapshot is prior and owner.alarm_last_sync is stamp
    assert seen == [(prior, False)] and not owner.tasks


@pytest.mark.asyncio
async def test_cancelled_returned_client_is_closed_only_by_transport(manager):
    owner, _ = manager
    entered = asyncio.Event()
    class CountingClient(Client):
        def __init__(self):
            super().__init__()
            self.disconnect_calls = 0
        async def start_notify(self, characteristic, callback):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise RuntimeError('PRIVATE_MASKED_CANCEL')
        async def disconnect(self):
            self.disconnect_calls += 1
            await super().disconnect()
    client = CountingClient()
    with patch('custom_components.casio_abl100.establish_connection', return_value=client):
        task = asyncio.create_task(owner.read_alarms())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert client.disconnected and client.disconnect_calls == 1
    assert owner.alarm_snapshot is None and not owner.tasks


@pytest.mark.asyncio
async def test_partial_disconnect_timeout_is_sanitized_and_releases_lock(manager):
    owner, _ = manager
    class StalledPartial:
        def __init__(self, *args, **kwargs):
            self.attempts = 0
            self.cancelled = False
        async def disconnect(self):
            self.attempts += 1
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
    owner.client_class = StalledPartial
    created = []
    async def connector(cls, *args, **kwargs):
        created.append(cls())
        raise RuntimeError('PRIVATE_CONNECT')
    with patch('custom_components.casio_abl100.establish_connection', side_effect=connector):
        with pytest.raises(HomeAssistantError) as error:
            await asyncio.wait_for(owner.read_alarms(), 2)
    assert_error(error)
    assert len(created) == 1 and created[0].attempts == 1 and created[0].cancelled
    assert not owner.lock.locked() and not owner.tasks


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [RuntimeError('PRIVATE_OBSERVER'), asyncio.CancelledError()])
async def test_success_observer_fault_cannot_change_committed_read_or_block_other_observers(manager, caplog, failure):
    owner, _ = manager
    old, stamp = object(), object()
    owner.alarm_snapshot, owner.alarm_last_sync = old, stamp
    seen = []
    def broken():
        seen.append(('broken', owner.alarm_snapshot, owner.alarms_fresh))
        raise failure
    def healthy():
        seen.append(('healthy', owner.alarm_snapshot, owner.alarms_fresh))
    owner.subscribe(broken)
    owner.subscribe(healthy)
    with patch('custom_components.casio_abl100.establish_connection', return_value=Client()):
        await owner.read_alarms()
    assert seen == []  # Delivery is never inline in the reader transaction.
    assert owner.alarm_snapshot is not old and owner.alarm_last_sync is not stamp
    assert owner.alarms_fresh is True
    await asyncio.sleep(0)
    assert len(seen) == 2 and {item[0] for item in seen} == {'broken', 'healthy'}
    assert all(item[1] is owner.alarm_snapshot and item[2] is True for item in seen)
    assert 'PRIVATE_OBSERVER' not in caplog.text
    assert not owner.tasks and not owner.lock.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [RuntimeError('PRIVATE_OBSERVER'), asyncio.CancelledError()])
async def test_failure_observer_fault_does_not_mask_read_failure(manager, failure, caplog):
    owner, _ = manager
    old, stamp = object(), object()
    owner.alarm_snapshot, owner.alarm_last_sync, owner.alarms_fresh = old, stamp, True
    seen = []
    def broken():
        seen.append('broken')
        raise failure
    owner.subscribe(broken)
    owner.subscribe(lambda: seen.append('healthy'))
    with patch('custom_components.casio_abl100.establish_connection', side_effect=BleakError('PRIVATE_BACKEND')):
        with pytest.raises(HomeAssistantError) as error:
            await owner.read_alarms()
    assert_error(error)
    assert owner.alarm_snapshot is old and owner.alarm_last_sync is stamp
    assert owner.alarms_fresh is False and seen == []
    await asyncio.sleep(0)
    assert set(seen) == {'broken', 'healthy'} and len(seen) == 2
    assert 'PRIVATE_' not in caplog.text and not owner.tasks


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [RuntimeError('PRIVATE_OBSERVER'), asyncio.CancelledError()])
async def test_caller_cancel_observer_fault_does_not_mask_cancellation(manager, failure, caplog):
    owner, _ = manager
    old, stamp = object(), object()
    owner.alarm_snapshot, owner.alarm_last_sync, owner.alarms_fresh = old, stamp, True
    entered = asyncio.Event()
    seen = []
    def broken():
        seen.append('broken')
        raise failure
    owner.subscribe(broken)
    owner.subscribe(lambda: seen.append('healthy'))
    async def connect(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    with patch('custom_components.casio_abl100.establish_connection', side_effect=connect):
        task = asyncio.create_task(owner.read_alarms())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert owner.alarm_snapshot is old and owner.alarm_last_sync is stamp
    assert owner.alarms_fresh is False
    await asyncio.sleep(0)
    assert set(seen) == {'broken', 'healthy'} and len(seen) == 2
    assert 'PRIVATE_' not in caplog.text and not owner.tasks


@pytest.mark.asyncio
async def test_delivery_is_not_reader_and_late_cancel_cannot_undo_commit(manager):
    owner, _ = manager
    observed = []
    reader = {}
    def listener():
        task = reader['task']
        observed.append((asyncio.current_task(), task.done(), owner.alarm_snapshot, owner.alarms_fresh))
        task.cancel()  # Explicitly late; cannot retroactively undo an observation.
    owner.subscribe(listener)
    with patch('custom_components.casio_abl100.establish_connection', return_value=Client()):
        reader['task'] = asyncio.create_task(owner.read_alarms())
        await reader['task']
    await asyncio.sleep(0)
    assert len(observed) == 1 and observed[0][0] is not reader['task'] and observed[0][1] is True
    assert observed[0][2] is owner.alarm_snapshot and observed[0][3] is True
    assert owner.alarms_fresh and not owner.tasks and not owner.lock.locked()


@pytest.mark.asyncio
async def test_unsubscribe_and_shutdown_suppress_pending_alarm_delivery(manager):
    owner, _ = manager
    seen = []
    unsubscribe = owner.subscribe(lambda: seen.append('unsubscribed'))
    owner.subscribe(lambda: seen.append('active'))
    with patch('custom_components.casio_abl100.establish_connection', return_value=Client()):
        await owner.read_alarms()
    unsubscribe()
    await asyncio.sleep(0)
    assert seen == ['active']
    with patch('custom_components.casio_abl100.establish_connection', return_value=Client()):
        await owner.read_alarms()
    assert seen == ['active'] and len(owner.alarm_delivery_handles) == 1
    await owner.shutdown()
    await asyncio.sleep(0)
    assert seen == ['active'] and owner.closed and not owner.listeners
    assert not owner.tasks and not owner.alarm_delivery_handles


@pytest.mark.asyncio
async def test_reentrant_unsubscribe_suppresses_future_delivery(manager):
    owner, _ = manager
    seen = []
    def first():
        seen.append('first')
        unsubscribe()
    unsubscribe = owner.subscribe(first)
    owner.subscribe(lambda: seen.append('other'))
    with patch('custom_components.casio_abl100.establish_connection', return_value=Client()):
        await owner.read_alarms()
        await asyncio.sleep(0)
        await owner.read_alarms()
    await asyncio.sleep(0)
    assert seen.count('first') == 1 and seen.count('other') == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('scheduler_fault', [RuntimeError('PRIVATE_SCHEDULER'), asyncio.CancelledError('PRIVATE_SCHEDULER')])
@pytest.mark.parametrize('outcome', ['success', 'read_failure', 'backend_cancel', 'caller_cancel', 'preentry_cancel'])
async def test_enqueue_fault_never_changes_alarm_transaction(manager, caplog, scheduler_fault, outcome):
    owner, _ = manager
    old, stamp = object(), object()
    owner.alarm_snapshot, owner.alarm_last_sync, owner.alarms_fresh = old, stamp, True
    delivered = []
    owner.subscribe(lambda: delivered.append(owner.alarm_snapshot))
    attempts = []

    class BrokenLoop:
        def call_soon(self, callback):
            attempts.append(callback)
            raise scheduler_fault

    owner.hass.loop = BrokenLoop()
    client = Client()
    entered = asyncio.Event()

    async def connector(*args, **kwargs):
        if outcome == 'read_failure':
            raise BleakError('PRIVATE_CONNECT')
        if outcome == 'backend_cancel':
            raise asyncio.CancelledError('PRIVATE_BACKEND')
        if outcome == 'caller_cancel':
            entered.set()
            await asyncio.Event().wait()
        return client

    async def preentry():
        asyncio.current_task().cancel()
        await owner.read_alarms()

    with patch('custom_components.casio_abl100.establish_connection', side_effect=connector) as connect:
        if outcome == 'caller_cancel':
            task = asyncio.create_task(owner.read_alarms())
            try:
                await asyncio.wait_for(entered.wait(), 1)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        elif outcome == 'preentry_cancel':
            task = asyncio.create_task(preentry())
            with pytest.raises(asyncio.CancelledError):
                await task
            connect.assert_not_called()
        elif outcome == 'success':
            await owner.read_alarms()
        else:
            with pytest.raises(HomeAssistantError) as error:
                await owner.read_alarms()
            assert_error(error)
    await asyncio.sleep(0)
    assert (owner.alarm_snapshot is old) == (outcome != 'success')
    assert (owner.alarm_last_sync is stamp) == (outcome != 'success')
    assert owner.alarms_fresh is (outcome == 'success')
    assert len(attempts) == 1 and delivered == []
    assert not owner.alarm_delivery_handles and not owner.tasks and not owner.lock.locked()
    assert 'PRIVATE_' not in caplog.text


@pytest.mark.asyncio
async def test_scheduler_fault_drops_one_delivery_but_future_delivery_and_shutdown_work(manager):
    owner, _ = manager
    loop = asyncio.get_running_loop()
    attempts = []

    class SometimesBrokenLoop:
        def call_soon(self, callback):
            attempts.append(callback)
            if len(attempts) == 1:
                raise RuntimeError('PRIVATE_ONCE')
            return loop.call_soon(callback)

    owner.hass.loop = SometimesBrokenLoop()
    observed = []
    owner.subscribe(lambda: observed.append(owner.alarms_fresh))
    with patch('custom_components.casio_abl100.establish_connection', return_value=Client()):
        await owner.read_alarms()
        assert owner.alarms_fresh and not owner.alarm_delivery_handles
        await owner.read_alarms()
        assert len(owner.alarm_delivery_handles) == 1
    await owner.shutdown()
    await asyncio.sleep(0)
    assert len(attempts) == 2 and not observed and not owner.alarm_delivery_handles


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', [RuntimeError('PRIVATE_LOOP'), asyncio.CancelledError('PRIVATE_LOOP')])
async def test_loop_lookup_fault_cannot_mask_failed_alarm_read(manager, caplog, fault):
    owner, _ = manager

    class FaultingHass:
        @property
        def loop(self):
            raise fault

    owner.hass = FaultingHass()
    with patch('custom_components.casio_abl100.establish_connection', side_effect=BleakError('PRIVATE_CONNECT')):
        with pytest.raises(HomeAssistantError) as error:
            await owner.read_alarms()
    assert_error(error)
    assert not owner.alarm_delivery_handles and not owner.tasks
    assert 'PRIVATE_' not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize('origin', ['lookup', 'connector', 'transport', 'partial_cleanup'])
@pytest.mark.parametrize('lookalike', [False, True])
async def test_foreign_home_assistant_error_never_escapes_alarm_boundary(manager, caplog, origin, lookalike):
    owner, lookup = manager
    old, stamp = object(), object()
    owner.alarm_snapshot, owner.alarm_last_sync, owner.alarms_fresh = old, stamp, True
    private = 'PRIVATE_BLE_ADDRESS_AA_BB'
    kwargs = ({'translation_domain': 'casio_abl100', 'translation_key': 'unavailable',
               'translation_placeholders': {'address': private}} if lookalike else {})
    failure = HomeAssistantError(private, **kwargs)
    delivered = []
    owner.subscribe(lambda: delivered.append(owner.alarms_fresh))
    partials = []

    class PartialClient:
        def __init__(self, *args, **kwargs):
            partials.append(self)
            self.disconnect_calls = 0
        async def disconnect(self):
            self.disconnect_calls += 1
            raise failure

    async def connector(cls, *args, **kwargs):
        if origin == 'connector':
            raise failure
        if origin == 'partial_cleanup':
            cls()
            return Client()
        return Client()

    if origin == 'partial_cleanup':
        owner.client_class = PartialClient
    if origin == 'transport':
        class FaultingClient(Client):
            async def start_notify(self, characteristic, callback):
                raise failure
        connector_result = FaultingClient()
    else:
        connector_result = None
    with patch('custom_components.casio_abl100.establish_connection', side_effect=connector if connector_result is None else None, return_value=connector_result) as connect:
        if origin == 'lookup':
            lookup.side_effect = failure
        with pytest.raises(HomeAssistantError) as error:
            await owner.read_alarms()
    assert_error(error)
    assert private not in repr(error.value.args)
    assert private not in repr(error.value.translation_placeholders)
    assert private not in caplog.text
    assert private not in str(error.value)
    assert owner.alarm_snapshot is old and owner.alarm_last_sync is stamp
    assert owner.alarms_fresh is False and not owner.tasks and not owner.lock.locked()
    await asyncio.sleep(0)
    assert delivered == [False]
    if origin == 'lookup':
        connect.assert_not_called()
    if origin == 'partial_cleanup':
        assert len(partials) == 1 and partials[0].disconnect_calls == 1


@pytest.mark.asyncio
async def test_caller_cancel_during_lookup_overrides_external_ha_error(manager):
    owner, lookup = manager
    previous, stamp = object(), object()
    owner.alarm_snapshot, owner.alarm_last_sync, owner.alarms_fresh = previous, stamp, True

    def cancel_then_fail(*args, **kwargs):
        current = asyncio.current_task()
        assert current is not None
        current.cancel()
        raise HomeAssistantError('PRIVATE_LOOKUP')

    lookup.side_effect = cancel_then_fail
    async def caller():
        await owner.read_alarms()

    task = asyncio.create_task(caller())
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelling() > 0
    assert owner.alarm_snapshot is previous and owner.alarm_last_sync is stamp
    assert not owner.alarms_fresh and not owner.tasks


@pytest.mark.asyncio
async def test_foreign_same_domain_error_is_not_session_owned(manager):
    owner, lookup = manager
    # Matching public fields do not establish origin at the lookup boundary.
    lookup.side_effect = HomeAssistantError(
        'PRIVATE_LOOKUP', translation_domain='casio_abl100', translation_key='unavailable'
    )
    with pytest.raises(HomeAssistantError) as error:
        await owner.read_alarms()
    assert_error(error)
    assert 'PRIVATE_LOOKUP' not in repr(error.value.args)


@pytest.mark.asyncio
@pytest.mark.parametrize('original', ['foreign_error', 'caller_cancel'])
async def test_partial_cleanup_foreign_ha_error_preserves_original_priority(manager, original):
    owner, _ = manager
    entered = asyncio.Event()
    partials = []

    class PartialClient:
        def __init__(self, *args, **kwargs):
            partials.append(self)
        async def disconnect(self):
            entered.set()
            try:
                if original == 'caller_cancel':
                    await asyncio.Event().wait()
            except asyncio.CancelledError:
                pass
            raise HomeAssistantError('PRIVATE_CLEANUP', translation_domain='casio_abl100', translation_key='closed')

    owner.client_class = PartialClient

    async def connector(cls, *args, **kwargs):
        cls()
        raise HomeAssistantError('PRIVATE_ORIGINAL') if original == 'foreign_error' else RuntimeError('PRIVATE_ORIGINAL')

    with patch('custom_components.casio_abl100.establish_connection', side_effect=connector):
        if original == 'caller_cancel':
            task = asyncio.create_task(owner.read_alarms())
            try:
                await asyncio.wait_for(entered.wait(), 1)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 2)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        else:
            with pytest.raises(HomeAssistantError) as error:
                await owner.read_alarms()
            assert_error(error)
            assert isinstance(error.value.__cause__, HomeAssistantError)
            assert error.value.__cause__.args == ('PRIVATE_ORIGINAL',)
    assert len(partials) == 1 and not owner.tasks and owner.alarm_snapshot is None


def test_alarm_exception_translations_are_human_and_private_free():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / 'custom_components/casio_abl100'
    for path in (root / 'strings.json', root / 'translations/en.json', root / 'translations/es.json'):
        message = json.loads(path.read_text())['exceptions']['alarm_read_failed']['message']
        assert len(message) >= 15 and '{' not in message and 'AA:BB' not in message
    assert 'alarm' in json.loads((root / 'translations/en.json').read_text())['exceptions']['alarm_read_failed']['message'].lower()
    assert 'alarma' in json.loads((root / 'translations/es.json').read_text())['exceptions']['alarm_read_failed']['message'].lower()
