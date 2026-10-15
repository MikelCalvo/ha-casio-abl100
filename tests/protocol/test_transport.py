"""In-memory BLE client fake: never touches hardware or a local adapter."""
import asyncio
import logging
import pytest
from bleak.backends.service import (
    BleakGATTServiceCollection, BleakGATTService,
    BleakGATTCharacteristic, BleakGATTDescriptor,
)

from custom_components.casio_abl100.transport import read_record, ReadError, DRSP, CONVOY, START
from custom_components.casio_abl100.protocol import ProtocolError
from tests.protocol.fixtures import SYNTHETIC_RECORD

FEATURES_SERVICE = '26eb000d-b012-49a8-b1f8-394fb2032b0f'
ALL_FEATURES = '26eb002d-b012-49a8-b1f8-394fb2032b0f'


def feature_services(*, service_uuid=FEATURES_SERVICE, properties=('notify',),
                     descriptor_uuid='00002902-0000-1000-8000-00805f9b34fb',
                     include_characteristic=True, include_descriptor=True):
    """Real Bleak metadata with non-fixed handles; no production lookup adapter."""
    services = BleakGATTServiceCollection()
    service = BleakGATTService(None, 70, service_uuid)
    services.add_service(service)
    if include_characteristic:
        characteristic = BleakGATTCharacteristic(
            None, 71, ALL_FEATURES, list(properties), lambda: 20, service,
        )
        services.add_characteristic(characteristic)
        if include_descriptor:
            services.add_descriptor(BleakGATTDescriptor(None, 73, descriptor_uuid, characteristic))
    return services


CAPTURE = SYNTHETIC_RECORD  # Synthetic bytes, never a captured hardware result.


class FakeClient:
    def __init__(self, fragments=None, header=b'\x00\x11\x90\x01\x00'):
        self.callbacks = {}
        self.services = feature_services()
        self.fragments = fragments if fragments is not None else [CAPTURE[:200], CAPTURE[200:]]
        self.header = header
        self.writes = []
        self.disconnected = False
        self.block = None

    async def start_notify(self, uuid, callback):
        self.callbacks[getattr(uuid, 'uuid', uuid)] = callback

    async def write_gatt_char(self, uuid, payload, response=False):
        self.writes.append((uuid, bytes(payload)))
        assert set(self.callbacks) == {ALL_FEATURES, DRSP, CONVOY}  # subscribe first
        if self.header is not None:
            self.callbacks[DRSP](None, bytearray(self.header))
        for fragment in self.fragments:
            self.callbacks[CONVOY](None, bytearray(fragment))
        if self.block:
            await self.block.wait()

    async def disconnect(self):
        self.disconnected = True


@pytest.mark.asyncio
async def test_primary_ack_gates_drsp_convoy_and_only_then_start():
    class AckGated(FakeClient):
        def __init__(self):
            super().__init__()
            self.services = feature_services()
            self.primary_entered = asyncio.Event()
            self.primary_ack = asyncio.Event()
            self.order = []

        async def start_notify(self, uuid, callback):
            name = getattr(uuid, 'uuid', uuid)
            self.order.append(name)
            if name == ALL_FEATURES:
                assert uuid is self.services.get_characteristic(ALL_FEATURES)
                self.primary_entered.set()
                await self.primary_ack.wait()
            await super().start_notify(uuid, callback)

    client = AckGated()
    task = asyncio.create_task(read_record(client))
    try:
        await asyncio.wait_for(client.primary_entered.wait(), 0.2)
        assert client.order == [ALL_FEATURES]
        assert client.writes == []
        client.primary_ack.set()
        result = await asyncio.wait_for(task, 0.2)
        assert result.steps == 1485
        assert client.order == [ALL_FEATURES, DRSP, CONVOY]
        assert client.writes == [(DRSP, START)]
        assert client.disconnected
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('services', [
    None,
    feature_services(service_uuid='26eb000e-b012-49a8-b1f8-394fb2032b0f'),
    feature_services(include_characteristic=False),
    feature_services(properties=('read',)),
    feature_services(include_descriptor=False),
    feature_services(descriptor_uuid='00002901-0000-1000-8000-00805f9b34fb'),
])
async def test_invalid_primary_metadata_stops_before_any_gatt_operation(services, caplog):
    client = FakeClient()
    client.services = services
    with caplog.at_level(logging.DEBUG, logger='custom_components.casio_abl100.transport'):
        with pytest.raises(ReadError, match='ALL_FEATURES'):
            await read_record(client)
    assert 'resolve ALL_FEATURES failed elapsed=' in caplog.text
    assert 'error_class=ReadError' in caplog.text
    assert client.callbacks == {} and client.writes == []
    assert client.disconnected


@pytest.mark.asyncio
async def test_primary_failure_blocks_all_later_operations_and_disconnects(caplog):
    class PrimaryFailure(FakeClient):
        async def start_notify(self, uuid, callback):
            if getattr(uuid, 'uuid', uuid) == ALL_FEATURES:
                raise RuntimeError('SECRET_ADDRESS_PRIVATE')
            await super().start_notify(uuid, callback)

    client = PrimaryFailure()
    with caplog.at_level(logging.DEBUG, logger='custom_components.casio_abl100.transport'):
        with pytest.raises(RuntimeError):
            await read_record(client)
    assert client.callbacks == {} and client.writes == [] and client.disconnected
    assert 'SECRET_ADDRESS_PRIVATE' not in caplog.text
    assert 'RuntimeError' in caplog.text and 'ALL_FEATURES' in caplog.text


@pytest.mark.asyncio
async def test_primary_cancellation_disconnects_without_later_operations():
    class PrimaryBlocked(FakeClient):
        def __init__(self):
            super().__init__()
            self.entered = asyncio.Event()

        async def start_notify(self, uuid, callback):
            if getattr(uuid, 'uuid', uuid) == ALL_FEATURES:
                self.entered.set()
                await asyncio.Event().wait()
            await super().start_notify(uuid, callback)

    client = PrimaryBlocked()
    task = asyncio.create_task(read_record(client))
    try:
        await asyncio.wait_for(client.entered.wait(), 0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert client.callbacks == {} and client.writes == [] and client.disconnected
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_drsp_failure_after_primary_blocks_convoy_and_start():
    class DrspFailure(FakeClient):
        async def start_notify(self, uuid, callback):
            if uuid == DRSP:
                raise RuntimeError('DRSP unavailable')
            await super().start_notify(uuid, callback)

    client = DrspFailure()
    with pytest.raises(RuntimeError):
        await read_record(client)
    assert set(client.callbacks) == {ALL_FEATURES}
    assert client.writes == [] and client.disconnected


@pytest.mark.asyncio
async def test_primary_payload_is_discarded_and_diagnostic_never_leaks_it(caplog):
    class PrimaryPayload(FakeClient):
        async def start_notify(self, uuid, callback):
            await super().start_notify(uuid, callback)
            if getattr(uuid, 'uuid', uuid) == ALL_FEATURES:
                callback(uuid, b'SECRET_NOTIFY_CANARY')

    client = PrimaryPayload(header=bytes.fromhex('00 11 90 01 00 42 73'))
    with caplog.at_level(logging.DEBUG, logger='custom_components.casio_abl100.transport'):
        result = await read_record(client)
    assert result.steps == 1485 and client.disconnected
    assert client.writes == [(DRSP, START)]
    assert 'SECRET_NOTIFY_CANARY' not in caplog.text
    assert 'handle=71 properties=' in caplog.text and 'cccd_handle=73' in caplog.text
    assert all(word in caplog.text for word in ('ALL_FEATURES', 'DRSP', 'Convoy', 'START', 'record', 'disconnect'))


@pytest.mark.asyncio
async def test_second_cancellation_during_cleanup_keeps_original_cancellation():
    class InterruptedCleanup(FakeClient):
        def __init__(self):
            super().__init__([], None)
            self.started = asyncio.Event()
            self.cancelled = False

        async def disconnect(self):
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    client = InterruptedCleanup()
    before = asyncio.all_tasks()
    task = asyncio.create_task(read_record(client, timeout=10))
    for _ in range(10):
        if client.writes:
            break
        await asyncio.sleep(0)
    assert client.writes == [(DRSP, START)]
    task.cancel()
    await asyncio.wait_for(client.started.wait(), 0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 0.15)
    assert client.cancelled and asyncio.all_tasks() == before


@pytest.mark.asyncio
@pytest.mark.parametrize('header', [
    bytes.fromhex('00 11 90 01 00'),
    bytes.fromhex('00 11 90 01 00 00 00'),
    bytes.fromhex('00 11 90 01 00 a5 5a'),
])
async def test_immediate_notifications_return_complete_record_without_end_write(header):
    client = FakeClient(header=header)
    result = await read_record(client, timeout=0.05)
    assert result.steps == 1485
    assert client.writes == [(DRSP, START)]
    assert client.disconnected


@pytest.mark.asyncio
@pytest.mark.parametrize('header', [
    bytes.fromhex('00 11 90 01'),             # truncated length
    bytes.fromhex('00 11 90 01 00 00'),       # truncated seven-byte form
    bytes.fromhex('00 11 90 01 00 00 00 00'), # beyond bounded forms
    bytes.fromhex('04 11 90 01 00 00 00'),    # wrong command
    bytes.fromhex('00 12 90 01 00 00 00'),    # wrong category
    bytes.fromhex('00 11 91 01 00 00 00'),    # wrong length
    bytes.fromhex('00 11 90 00 01 00 00'),    # high byte contributes to length
])
async def test_malformed_announcements_never_return_record(header):
    client = FakeClient(header=header)
    with pytest.raises(ReadError):
        await read_record(client, timeout=0.02)
    assert client.writes == [(DRSP, START)]
    assert client.disconnected

@pytest.mark.asyncio
async def test_duplicate_seven_byte_announcement_never_returns_record():
    class DuplicateHeader(FakeClient):
        async def write_gatt_char(self, uuid, payload, response=False):
            self.writes.append((uuid, bytes(payload)))
            assert set(self.callbacks) == {ALL_FEATURES, DRSP, CONVOY}
            self.callbacks[DRSP](None, bytearray(self.header))
            self.callbacks[DRSP](None, bytearray(self.header))
            self.callbacks[CONVOY](None, bytearray(CAPTURE))

    client = DuplicateHeader(header=bytes.fromhex('00 11 90 01 00 00 00'))
    with pytest.raises(ReadError):
        await read_record(client, timeout=0.02)
    assert client.writes == [(DRSP, START)]
    assert client.disconnected

@pytest.mark.asyncio
async def test_duplicate_seven_byte_announcement_after_complete_is_rejected():
    class LateHeader(FakeClient):
        async def write_gatt_char(self, uuid, payload, response=False):
            await super().write_gatt_char(uuid, payload, response)
            self.callbacks[DRSP](None, bytearray(self.header))

    client = LateHeader(header=bytes.fromhex('00 11 90 01 00 00 00'))
    with pytest.raises(ReadError):
        await read_record(client, timeout=0.02)
    assert client.writes == [(DRSP, START)]
    assert client.disconnected

@pytest.mark.asyncio
@pytest.mark.parametrize('fragments', [[CAPTURE + b'\0'], [CAPTURE, b'\0']])
async def test_seven_byte_announcement_rejects_overflow_and_extra_fragment(fragments):
    client = FakeClient(fragments, bytes.fromhex('00 11 90 01 00 00 00'))
    with pytest.raises(ReadError):
        await read_record(client, timeout=0.02)
    assert client.writes == [(DRSP, START)]
    assert client.disconnected

@pytest.mark.asyncio
@pytest.mark.parametrize('header,fragments', [
    (None, [CAPTURE]),
    (b'\x00\x11\x90\x01\x00', [CAPTURE[:-1]]),
    (bytes.fromhex('00 11 90 01 00 00 00'), [CAPTURE[:-1]]),
    (b'\x00\x11\x90\x01\x00', [CAPTURE + b'\0']),
    (b'\x00\x11\x91\x01\x00', [CAPTURE]),
    # Duplicating the trailing half yields an invalid date header. Raw Convoy
    # has no sequence numbers, so this is not general duplicate detection.
    (b'\x00\x11\x90\x01\x00', [CAPTURE[200:], CAPTURE[200:]]),
])
async def test_bad_or_incomplete_transaction_cannot_return_data(header, fragments):
    client = FakeClient(fragments, header)
    with pytest.raises((ReadError, ProtocolError)):
        await read_record(client, timeout=0.02)
    assert client.writes == [(DRSP, START)]
    assert client.disconnected


@pytest.mark.asyncio
async def test_cancellation_disconnects_without_more_writes():
    client = FakeClient([], None)
    task = asyncio.create_task(read_record(client, timeout=10))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.disconnected
    assert client.writes == [(DRSP, START)]


@pytest.mark.asyncio
async def test_repeated_identical_fragments_are_not_deduplicated():
    client = FakeClient([b'\0' * 200, b'\0' * 200])
    # Identical content still completes framing; parser then rejects invalid date.
    with pytest.raises(ProtocolError):
        await read_record(client, timeout=0.03)
    assert client.disconnected


@pytest.mark.asyncio
async def test_extra_fragment_after_complete_before_write_returns_is_rejected():
    client = FakeClient([CAPTURE, b'\0'])
    with pytest.raises(ReadError):
        await read_record(client, timeout=0.03)
    assert client.disconnected


@pytest.mark.asyncio
async def test_blocked_write_is_bounded_and_disconnects():
    client = FakeClient([], None)
    client.block = asyncio.Event()
    with pytest.raises(ReadError):
        await read_record(client, timeout=0.02)
    assert client.disconnected


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['success', 'error', 'cancel'])
async def test_stalled_disconnect_is_bounded_without_leaving_tasks(mode):
    class StalledDisconnect(FakeClient):
        def __init__(self):
            super().__init__([] if mode == 'cancel' else None,
                             None if mode == 'cancel' else b'\x00\x11\x90\x01\x00')
            self.disconnect_started = asyncio.Event()
            self.write_started = asyncio.Event()
            self.disconnect_cancelled = False

        async def write_gatt_char(self, uuid, payload, response=False):
            await super().write_gatt_char(uuid, payload, response)
            self.write_started.set()

        async def disconnect(self):
            self.disconnect_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.disconnect_cancelled = True
                raise

    client = StalledDisconnect()
    if mode == 'error':
        client.header = b'\x00\x11\x91\x01\x00'
    before = asyncio.all_tasks()
    task = asyncio.create_task(read_record(client, timeout=0.02))
    if mode == 'cancel':
        await asyncio.wait_for(client.write_started.wait(), 0.2)
        # Cancel while the transaction is awaiting a missing announcement.
        task.cancel()
    if mode == 'success':
        with pytest.raises(ReadError, match='disconnect'):
            await asyncio.wait_for(task, 0.15)
    else:
        with pytest.raises(asyncio.CancelledError if mode == 'cancel' else ReadError) as exc:
            await asyncio.wait_for(task, 0.15)
        if mode == 'error':
            assert str(exc.value) == 'invalid length announcement'
    assert client.disconnect_started.is_set() and client.disconnect_cancelled
    assert client.writes == [(DRSP, START)]
    assert asyncio.all_tasks() == before


@pytest.mark.asyncio
async def test_notification_subscription_timeout_disconnects():
    class StuckNotify(FakeClient):
        async def start_notify(self, uuid, callback):
            await asyncio.Event().wait()
    client = StuckNotify()
    with pytest.raises(ReadError):
        await read_record(client, timeout=0.02)
    assert client.writes == []
    assert client.disconnected
