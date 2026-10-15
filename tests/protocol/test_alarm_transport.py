"""Synthetic alarm transactions against real Bleak metadata, never hardware."""
import asyncio
import logging

import pytest
from bleak.backends.service import (
    BleakGATTCharacteristic, BleakGATTDescriptor, BleakGATTService,
    BleakGATTServiceCollection,
)

from custom_components.casio_abl100.transport import ReadError
from custom_components.casio_abl100.alarms import AlarmSnapshot

SERVICE = '26eb000d-b012-49a8-b1f8-394fb2032b0f'
QUERY = '26eb002c-b012-49a8-b1f8-394fb2032b0f'
PRIMARY = '26eb002d-b012-49a8-b1f8-394fb2032b0f'
CCCD = '00002902-0000-1000-8000-00805f9b34fb'
FIRST = bytes.fromhex('15 c0 00 17 3b')
REMAINING = bytes.fromhex('16 40 07 00 00 01 02 09 1e 40 03 0c 00 80 04 10 3b')


def metadata(*, service=SERVICE, query=True, query_properties=('write-without-response',),
             primary=True, primary_properties=('notify',), descriptor=True, descriptor_uuid=CCCD):
    services = BleakGATTServiceCollection()
    srv = BleakGATTService(None, 104, service)
    services.add_service(srv)
    if query:
        services.add_characteristic(BleakGATTCharacteristic(
            None, 106, QUERY, list(query_properties), lambda: 20, srv))
    if primary:
        char = BleakGATTCharacteristic(None, 108, PRIMARY, list(primary_properties), lambda: 20, srv)
        services.add_characteristic(char)
        if descriptor:
            services.add_descriptor(BleakGATTDescriptor(None, 110, descriptor_uuid, char))
    return services


class Client:
    def __init__(self, first=FIRST, remaining=REMAINING, services=None):
        self.services = metadata() if services is None else services
        self.first, self.remaining = first, remaining
        self.callback = None
        self.events = []
        self.disconnected = False
        self.subscribe_gate = None
        self.query_gate = None
        self.disconnect_gate = None
        self.fail_write = None
        self.fail_disconnect = None
        self.on_disconnect = None

    def notify(self, frame):
        assert self.callback is not None
        self.callback(self.services.get_characteristic(PRIMARY), bytearray(frame))

    async def start_notify(self, characteristic, callback):
        self.events.append(('subscribe', characteristic))
        assert characteristic is self.services.get_characteristic(PRIMARY)
        self.callback = callback
        if self.subscribe_gate is not None:
            await self.subscribe_gate.wait()

    async def write_gatt_char(self, characteristic, data, response=False):
        self.events.append(('write', characteristic, bytes(data), response))
        assert characteristic is self.services.get_characteristic(QUERY)
        assert response is False
        if bytes(data) == b'\x15' and self.first is not None:
            self.notify(self.first)  # reply before the write await returns
        if bytes(data) == b'\x16' and self.remaining is not None:
            self.notify(self.remaining)
        if self.fail_write == bytes(data):
            raise RuntimeError('SECRET_WRITE_FAILURE')
        if self.query_gate is not None:
            await self.query_gate.wait()

    async def disconnect(self):
        self.events.append(('disconnect',))
        if self.on_disconnect:
            self.on_disconnect()
        if self.disconnect_gate is not None:
            await self.disconnect_gate.wait()
        if self.fail_disconnect:
            raise RuntimeError('SECRET_DISCONNECT_FAILURE')
        self.disconnected = True


def writes(client):
    return [(event[2], event[3]) for event in client.events if event[0] == 'write']


@pytest.mark.asyncio
async def test_full_alarm_snapshot_uses_only_two_ordered_gets_and_disconnect():
    from custom_components.casio_abl100.transport import read_alarms
    client = Client()
    snapshot = await read_alarms(client, timeout=0.1)
    assert isinstance(snapshot, AlarmSnapshot)
    assert [(a.slot, a.hour, a.minute, a.enabled, a.flags, a.aux) for a in snapshot.alarms] == [
        (1, 23, 59, True, 0xc0, 0), (2, 0, 0, True, 0x40, 7),
        (3, 9, 30, False, 1, 2), (4, 12, 0, True, 0x40, 3),
        (5, 16, 59, False, 0x80, 4),
    ]
    assert snapshot.hourly_signal is True
    assert writes(client) == [(b'\x15', False), (b'\x16', False)]
    assert [e[0] for e in client.events] == ['subscribe', 'write', 'write', 'disconnect']
    assert client.disconnected


@pytest.mark.asyncio
async def test_primary_subscription_ack_and_first_reply_gate_second_query():
    from custom_components.casio_abl100.transport import read_alarms
    client = Client(first=None)
    client.subscribe_gate = asyncio.Event()
    task = asyncio.create_task(read_alarms(client, timeout=0.2))
    try:
        await asyncio.sleep(0)
        assert [e[0] for e in client.events] == ['subscribe']
        client.subscribe_gate.set()
        for _ in range(10):
            if writes(client):
                break
            await asyncio.sleep(0)
        assert writes(client) == [(b'\x15', False)]
        client.notify(FIRST)
        result = await asyncio.wait_for(task, 0.2)
        assert len(result.alarms) == 5 and client.disconnected
        assert writes(client) == [(b'\x15', False), (b'\x16', False)]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('services', [
    None, metadata(service='26eb000e-b012-49a8-b1f8-394fb2032b0f'),
    metadata(query=False), metadata(query_properties=('write',)),
    metadata(primary=False), metadata(primary_properties=('read',)),
    metadata(descriptor=False), metadata(descriptor_uuid='00002901-0000-1000-8000-00805f9b34fb'),
])
async def test_bad_metadata_disconnects_before_any_gatt_operation(services):
    from custom_components.casio_abl100.transport import read_alarms
    client = Client()
    client.services = services
    with pytest.raises(ReadError):
        await read_alarms(client, timeout=0.1)
    assert client.events == [('disconnect',)] and client.disconnected


@pytest.mark.asyncio
@pytest.mark.parametrize('early_frame', [REMAINING, b'\xff\x01', b''])
async def test_early_unrequested_error_or_empty_notification_fails(early_frame):
    from custom_components.casio_abl100.transport import read_alarms
    class Early(Client):
        async def write_gatt_char(self, characteristic, data, response=False):
            if bytes(data) == b'\x15':
                self.notify(early_frame)
            await super().write_gatt_char(characteristic, data, response)
    client = Early()
    with pytest.raises(ReadError):
        await read_alarms(client, timeout=0.03)
    assert writes(client) == [(b'\x15', False)] and client.disconnected


@pytest.mark.asyncio
@pytest.mark.parametrize('first', [FIRST[:-1], FIRST + b'\x00',
                                        bytes.fromhex('15 c0 00 18 00'),
                                        bytes.fromhex('15 c0 00 17 3c')])
async def test_invalid_first_rejected_before_querying_second(first):
    from custom_components.casio_abl100.transport import read_alarms
    client = Client(first=first)
    with pytest.raises(ReadError):
        await read_alarms(client, timeout=0.03)
    assert writes(client) == [(b'\x15', False)] and client.disconnected


@pytest.mark.asyncio
@pytest.mark.parametrize('remaining', [REMAINING[:-1], REMAINING + b'\x00',
                                                REMAINING[:7] + b'\x18' + REMAINING[8:],
                                                REMAINING[:-1] + b'\x3c'])
async def test_invalid_second_never_yields_partial_snapshot(remaining):
    from custom_components.casio_abl100.transport import read_alarms
    client = Client(remaining=remaining)
    with pytest.raises(ReadError):
        await read_alarms(client, timeout=0.03)
    assert writes(client) == [(b'\x15', False), (b'\x16', False)] and client.disconnected


@pytest.mark.asyncio
@pytest.mark.parametrize('frame', [FIRST, REMAINING])
async def test_duplicate_notifications_including_queued_adjacent_callback_fail(frame):
    from custom_components.casio_abl100.transport import read_alarms
    class Duplicate(Client):
        async def write_gatt_char(self, characteristic, data, response=False):
            await super().write_gatt_char(characteristic, data, response)
            if bytes(data) == frame[:1]:
                asyncio.get_running_loop().call_soon(self.notify, frame)
    client = Duplicate()
    with pytest.raises(ReadError):
        await read_alarms(client, timeout=0.03)
    assert client.disconnected
    assert writes(client) == ([(b'\x15', False)] if frame is FIRST else
                              [(b'\x15', False), (b'\x16', False)])


@pytest.mark.asyncio
async def test_unrelated_primary_notification_is_ignored_not_parsed():
    from custom_components.casio_abl100.transport import read_alarms
    class Noise(Client):
        async def write_gatt_char(self, characteristic, data, response=False):
            self.notify(b'\x10\x15\xff\xff')
            self.notify(b'\x22\x16\x00')
            await super().write_gatt_char(characteristic, data, response)
    client = Noise()
    assert len((await read_alarms(client, timeout=0.1)).alarms) == 5
    assert writes(client) == [(b'\x15', False), (b'\x16', False)]


@pytest.mark.asyncio
@pytest.mark.parametrize('opcode', [b'\x15', b'\x16'])
async def test_write_failure_even_after_reply_is_failure(opcode):
    from custom_components.casio_abl100.transport import read_alarms
    client = Client()
    client.fail_write = opcode
    with pytest.raises(RuntimeError, match='SECRET_WRITE_FAILURE'):
        await read_alarms(client, timeout=0.1)
    assert client.disconnected
    assert writes(client) == ([(b'\x15', False)] if opcode == b'\x15' else
                              [(b'\x15', False), (b'\x16', False)])


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['subscribe', 'write15', 'write16', 'reply15', 'reply16'])
async def test_each_stalled_stage_is_bounded_and_disconnects(stage):
    from custom_components.casio_abl100.transport import read_alarms
    client = Client(first=None if stage == 'reply15' else FIRST,
                    remaining=None if stage == 'reply16' else REMAINING)
    if stage == 'subscribe':
        client.subscribe_gate = asyncio.Event()
    if stage in ('write15', 'write16'):
        class Stalled(Client):
            async def write_gatt_char(self, characteristic, data, response=False):
                await super().write_gatt_char(characteristic, data, response)
                if bytes(data) == (b'\x15' if stage == 'write15' else b'\x16'):
                    await asyncio.Event().wait()
        client = Stalled()
    with pytest.raises(ReadError, match='timed out'):
        await read_alarms(client, timeout=0.02)
    assert client.events[-1] == ('disconnect',) and client.disconnected


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['subscribe', 'write15', 'reply15', 'write16', 'reply16', 'disconnect'])
async def test_cancel_at_each_stage_disconnects_and_leaves_no_tasks(stage):
    from custom_components.casio_abl100.transport import read_alarms
    entered = asyncio.Event()
    class Paused(Client):
        async def start_notify(self, characteristic, callback):
            if stage == 'subscribe':
                entered.set()
                await asyncio.Event().wait()
            await super().start_notify(characteristic, callback)

        async def write_gatt_char(self, characteristic, data, response=False):
            await super().write_gatt_char(characteristic, data, response)
            if stage == 'write' + ('15' if bytes(data) == b'\x15' else '16'):
                entered.set()
                await asyncio.Event().wait()
            if stage == 'reply' + ('15' if bytes(data) == b'\x15' else '16'):
                entered.set()

        async def disconnect(self):
            if stage == 'disconnect':
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.disconnected = True
                    raise
            await super().disconnect()
    client = Paused(first=None if stage == 'reply15' else FIRST,
                    remaining=None if stage == 'reply16' else REMAINING)
    before = asyncio.all_tasks()
    task = asyncio.create_task(read_alarms(client, timeout=0.2))
    try:
        await asyncio.wait_for(entered.wait(), 0.1)
        if stage.startswith('reply'):
            await asyncio.sleep(0)  # enter the pending response future
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 0.15)
        assert client.disconnected or client.events[-1] == ('disconnect',)
        assert asyncio.all_tasks() == before
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('original', ['success', 'badframe', 'cancel'])
async def test_disconnect_timeout_does_not_mask_original_and_leaves_no_tasks(original):
    from custom_components.casio_abl100.transport import read_alarms
    class Stuck(Client):
        async def disconnect(self):
            self.events.append(('disconnect',))
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.disconnected = True
                raise
    client = Stuck(first=None if original == 'cancel' else (b'\x15' if original == 'badframe' else FIRST))
    before = asyncio.all_tasks()
    task = asyncio.create_task(read_alarms(client, timeout=0.02))
    if original == 'cancel':
        for _ in range(10):
            if writes(client):
                break
            await asyncio.sleep(0)
        task.cancel()
    expected = asyncio.CancelledError if original == 'cancel' else ReadError
    with pytest.raises(expected) as exc:
        await asyncio.wait_for(task, 0.15)
    if original == 'success':
        assert 'disconnect' in str(exc.value)
    elif original == 'badframe':
        assert 'disconnect' not in str(exc.value)
    assert client.disconnected and asyncio.all_tasks() == before


@pytest.mark.asyncio
async def test_invalidation_during_disconnect_prevents_success_and_late_callbacks_are_ignored():
    from custom_components.casio_abl100.transport import read_alarms
    client = Client()
    client.on_disconnect = lambda: client.notify(FIRST)
    with pytest.raises(ReadError):
        await read_alarms(client, timeout=0.1)
    client.notify(b'')  # callback after closure cannot revive the old transaction
    assert client.disconnected


@pytest.mark.asyncio
async def test_two_interleaved_clients_have_independent_phase_and_result():
    from custom_components.casio_abl100.transport import read_alarms
    left, right = Client(first=None), Client()
    first = asyncio.create_task(read_alarms(left, timeout=0.2))
    try:
        for _ in range(10):
            if writes(left):
                break
            await asyncio.sleep(0)
        second = await read_alarms(right, timeout=0.1)
        assert writes(left) == [(b'\x15', False)]
        left.notify(FIRST)
        assert await first == second
        assert left.disconnected and right.disconnected
    finally:
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)


@pytest.mark.asyncio
async def test_error_future_is_consumed_and_diagnostics_have_no_private_bytes(caplog):
    from custom_components.casio_abl100.transport import read_alarms
    client = Client()
    client.fail_write = b'\x15'
    seen = []
    loop = asyncio.get_running_loop()
    old_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: seen.append(context))
    try:
        with caplog.at_level(logging.DEBUG, logger='custom_components.casio_abl100.transport'):
            with pytest.raises(RuntimeError):
                await read_alarms(client, timeout=0.1)
        del client
        import gc
        gc.collect()
        await asyncio.sleep(0)
        assert not seen
        assert 'SECRET_WRITE_FAILURE' not in caplog.text
        assert 'c0 00 17 3b' not in caplog.text
    finally:
        loop.set_exception_handler(old_handler)


@pytest.mark.asyncio
async def test_alarm_reply_during_subscribe_ack_fails_before_first_query():
    from custom_components.casio_abl100.transport import read_alarms
    class EarlySubscribe(Client):
        async def start_notify(self, characteristic, callback):
            await super().start_notify(characteristic, callback)
            self.notify(REMAINING)
    client = EarlySubscribe()
    with pytest.raises(ReadError):
        await read_alarms(client, timeout=0.1)
    assert writes(client) == [] and client.disconnected


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['subscribe', 'metadata', 'disconnect'])
async def test_original_error_preserved_over_disconnect_failure(failure):
    from custom_components.casio_abl100.transport import read_alarms
    class Broken(Client):
        async def start_notify(self, characteristic, callback):
            if failure == 'subscribe':
                raise RuntimeError('SECRET_SUBSCRIBE_ERROR')
            await super().start_notify(characteristic, callback)
    client = Broken()
    if failure == 'metadata':
        client.services = None
    client.fail_disconnect = True
    if failure == 'disconnect':
        with pytest.raises(RuntimeError, match='SECRET_DISCONNECT_FAILURE'):
            await read_alarms(client, timeout=0.1)
    elif failure == 'metadata':
        with pytest.raises(ReadError, match='service'):
            await read_alarms(client, timeout=0.1)
    else:
        with pytest.raises(RuntimeError, match='SECRET_SUBSCRIBE_ERROR'):
            await read_alarms(client, timeout=0.1)
    assert client.events[-1] == ('disconnect',)


@pytest.mark.asyncio
async def test_failed_reply_plus_failure_during_disconnect_preserves_reply_error():
    from custom_components.casio_abl100.transport import read_alarms
    client = Client(first=b'\x15\x00')
    client.fail_disconnect = True
    with pytest.raises(ReadError, match='invalid alarm response'):
        await read_alarms(client, timeout=0.1)
    assert writes(client) == [(b'\x15', False)]


@pytest.mark.asyncio
async def test_duplicate_first_while_waiting_for_second_is_rejected():
    from custom_components.casio_abl100.transport import read_alarms
    class LateFirst(Client):
        async def write_gatt_char(self, characteristic, data, response=False):
            if bytes(data) == b'\x16':
                self.notify(FIRST)
            await super().write_gatt_char(characteristic, data, response)
    client = LateFirst()
    with pytest.raises(ReadError):
        await read_alarms(client, timeout=0.1)
    assert client.disconnected


@pytest.mark.asyncio
async def test_invalid_response_during_failing_disconnect_remains_primary_error():
    from custom_components.casio_abl100.transport import read_alarms
    client = Client()
    client.on_disconnect = lambda: client.notify(REMAINING)
    client.fail_disconnect = True
    with pytest.raises(ReadError, match='invalid alarm transaction'):
        await read_alarms(client, timeout=0.1)


@pytest.mark.asyncio
@pytest.mark.parametrize('opcode,frame,expected_writes', [
    (b'\x15', FIRST, []),
    (b'\x16', REMAINING, [(b'\x15', False)]),
])
async def test_alarm_callback_before_public_get_invocation_invalidates_without_write(
        monkeypatch, opcode, frame, expected_writes):
    """A callback before the write coroutine starts is not its response."""
    import custom_components.casio_abl100.transport as transport
    client = Client()
    original_stage = transport._stage
    delivered = []

    async def deliver_before_write(label, operation, timeout):
        if label == 'write alarm GET' + opcode.hex():
            loop = asyncio.get_running_loop()
            loop.call_soon(lambda: (delivered.append(True), client.notify(frame)))
            await asyncio.sleep(0)  # Dispatch before invoking the GET coroutine.
        return await original_stage(label, operation, timeout)

    monkeypatch.setattr(transport, '_stage', deliver_before_write)
    before = asyncio.all_tasks()
    with pytest.raises(ReadError, match='invalid alarm transaction'):
        await transport.read_alarms(client, timeout=0.1)
    assert delivered == [True]
    assert writes(client) == expected_writes
    assert client.disconnected and asyncio.all_tasks() == before


@pytest.mark.asyncio
@pytest.mark.parametrize('original,duplicate,cleanup_failure', [
    ('success', False, None),
    ('success', True, None),
    ('badframe', False, None),
    ('badframe', True, 'error'),
    ('success', True, 'error'),
    ('success', True, 'cancel'),
])
async def test_caller_cancellation_during_cleanup_has_priority(
        original, duplicate, cleanup_failure):
    """New caller cancellation outranks invalidation, prior error, and cleanup."""
    from custom_components.casio_abl100.transport import read_alarms
    entered = asyncio.Event()

    class HeldDisconnect(Client):
        async def disconnect(self):
            self.events.append(('disconnect',))
            if duplicate:
                self.notify(FIRST)
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                if cleanup_failure == 'error':
                    raise RuntimeError('backend cleanup failed after caller cancellation')
                raise

    client = HeldDisconnect(first=b'\x15' if original == 'badframe' else FIRST)
    before = asyncio.all_tasks()
    task = asyncio.create_task(read_alarms(client, timeout=0.2))
    try:
        await asyncio.wait_for(entered.wait(), 0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 0.1)
        assert writes(client) == ([(b'\x15', False)] if original == 'badframe'
                                  else [(b'\x15', False), (b'\x16', False)])
        assert asyncio.all_tasks() == before
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('original,duplicate,cleanup_failure,expected', [
    ('success', True, 'cancel', 'invalid alarm transaction'),
    ('badframe', True, 'cancel', 'invalid alarm response'),
    ('badframe', True, 'error', 'invalid alarm response'),
])
async def test_backend_cleanup_failure_without_caller_cancel_preserves_priority(
        original, duplicate, cleanup_failure, expected):
    """A backend CancelledError is not itself a caller task cancellation."""
    from custom_components.casio_abl100.transport import read_alarms

    class BrokenDisconnect(Client):
        async def disconnect(self):
            self.events.append(('disconnect',))
            if duplicate:
                self.notify(FIRST)
            if cleanup_failure == 'cancel':
                raise asyncio.CancelledError()
            raise RuntimeError('backend cleanup failed')

    client = BrokenDisconnect(first=b'\x15' if original == 'badframe' else FIRST)
    before = asyncio.all_tasks()
    with pytest.raises(ReadError, match=expected):
        await read_alarms(client, timeout=0.1)
    assert asyncio.all_tasks() == before


@pytest.mark.asyncio
async def test_spurious_backend_cleanup_cancellation_is_not_caller_cancellation():
    from custom_components.casio_abl100.transport import read_alarms

    class SpuriousCancel(Client):
        async def disconnect(self):
            self.events.append(('disconnect',))
            raise asyncio.CancelledError()

    client = SpuriousCancel()
    before = asyncio.all_tasks()
    with pytest.raises(ReadError, match='alarm disconnect cancelled'):
        await read_alarms(client, timeout=0.1)
    assert writes(client) == [(b'\x15', False), (b'\x16', False)]
    assert asyncio.all_tasks() == before


@pytest.mark.asyncio
async def test_caller_cancel_converted_by_write_backend_remains_cancellation():
    """A backend's ordinary error after swallowing cancel cannot replace caller cancel."""
    from custom_components.casio_abl100.transport import read_alarms
    entered = asyncio.Event()

    class MaskedWrite(Client):
        async def write_gatt_char(self, characteristic, data, response=False):
            self.events.append(('write', characteristic, bytes(data), response))
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise RuntimeError('backend masked caller cancellation')

    client = MaskedWrite()
    before = asyncio.all_tasks()
    task = asyncio.create_task(read_alarms(client, timeout=0.2))
    try:
        await asyncio.wait_for(entered.wait(), 0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 0.1)
        assert writes(client) == [(b'\x15', False)] and client.disconnected
        assert asyncio.all_tasks() == before
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('stage,expected_writes', [
    ('metadata', []), ('subscribe', []), ('write15', [(b'\x15', False)]),
    ('reply15', [(b'\x15', False)]),
    ('write16', [(b'\x15', False), (b'\x16', False)]),
    ('reply16', [(b'\x15', False), (b'\x16', False)]),
    ('disconnect', [(b'\x15', False), (b'\x16', False)]),
])
@pytest.mark.parametrize('cleanup_failure', [None, 'error', 'cancel'])
async def test_backend_only_cancel_at_each_stage_is_read_failure(
        monkeypatch, stage, expected_writes, cleanup_failure):
    """Backend/future cancellation without task.cancel never cancels the caller."""
    import custom_components.casio_abl100.transport as transport

    class CancelledBackend(Client):
        @property
        def services(self):
            if stage == 'metadata':
                raise asyncio.CancelledError()
            return self._services

        @services.setter
        def services(self, value):
            self._services = value

        async def start_notify(self, characteristic, callback):
            if stage == 'subscribe':
                raise asyncio.CancelledError()
            await super().start_notify(characteristic, callback)

        async def write_gatt_char(self, characteristic, data, response=False):
            await super().write_gatt_char(characteristic, data, response)
            if stage == 'write' + data.hex():
                raise asyncio.CancelledError()

        async def disconnect(self):
            self.events.append(('disconnect',))
            if stage == 'disconnect' or cleanup_failure == 'cancel':
                raise asyncio.CancelledError()
            if cleanup_failure == 'error':
                raise RuntimeError('cleanup fault')
            self.disconnected = True

    client = CancelledBackend(first=None if stage == 'reply15' else FIRST,
                              remaining=None if stage == 'reply16' else REMAINING)
    original_stage = transport._stage

    async def cancelled_future(label, operation, timeout):
        if label == 'await alarm GET' + stage[-2:] and stage.startswith('reply'):
            operation.cancel()  # Cancel only the response future, not its caller.
        return await original_stage(label, operation, timeout)

    monkeypatch.setattr(transport, '_stage', cancelled_future)
    before = asyncio.all_tasks()
    current = asyncio.current_task()
    initial_cancels = current.cancelling()
    with pytest.raises(ReadError):
        await transport.read_alarms(client, timeout=0.1)
    assert current.cancelling() == initial_cancels
    assert writes(client) == expected_writes
    assert client.events[-1] == ('disconnect',)
    assert asyncio.all_tasks() == before


@pytest.mark.asyncio
async def test_backend_child_self_cancel_does_not_cancel_parent():
    from custom_components.casio_abl100.transport import read_alarms

    class ChildSelfCancel(Client):
        async def start_notify(self, characteristic, callback):
            async def child():
                asyncio.current_task().cancel()
                await asyncio.sleep(0)
            await asyncio.create_task(child())

    client = ChildSelfCancel()
    parent = asyncio.current_task()
    before = asyncio.all_tasks()
    cancels = parent.cancelling()
    with pytest.raises(ReadError):
        await read_alarms(client, timeout=0.1)
    assert parent.cancelling() == cancels
    assert client.events == [('disconnect',)] and asyncio.all_tasks() == before


@pytest.mark.asyncio
async def test_real_caller_cancel_during_cleanup_overrides_backend_only_cancel():
    """A later real cancellation overrides the original backend-only failure."""
    from custom_components.casio_abl100.transport import read_alarms
    entered = asyncio.Event()

    class BackendAndCaller(Client):
        async def start_notify(self, characteristic, callback):
            raise asyncio.CancelledError()

        async def disconnect(self):
            self.events.append(('disconnect',))
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise RuntimeError('backend masked real cancellation')

    client = BackendAndCaller()
    before = asyncio.all_tasks()
    task = asyncio.create_task(read_alarms(client, timeout=0.2))
    try:
        await asyncio.wait_for(entered.wait(), 0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 0.1)
        assert asyncio.all_tasks() == before
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_post_invocation_same_opcode_callback_cannot_prove_freshness():
    """Characterization: no public transaction token separates these histories."""
    from custom_components.casio_abl100.transport import read_alarms

    class Ambiguous(Client):
        async def write_gatt_char(self, characteristic, data, response=False):
            if bytes(data) != b'\x16':
                await super().write_gatt_char(characteristic, data, response)
                return
            self.events.append(('entered16',))
            self.notify(REMAINING)  # Indistinguishable old frame, after invocation.
            await asyncio.sleep(0)  # Synthetic backend pre-send suspension.
            self.events.append(('write', characteristic, bytes(data), response))

    client = Ambiguous()
    assert len((await read_alarms(client, timeout=0.1)).alarms) == 5
    assert [e[0] for e in client.events] == [
        'subscribe', 'write', 'entered16', 'write', 'disconnect',
    ]
