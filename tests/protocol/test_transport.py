"""In-memory BLE client fake: never touches hardware or a local adapter."""
import asyncio
import pytest

from custom_components.casio_abl100.transport import read_record, ReadError, DRSP, CONVOY, START
from custom_components.casio_abl100.protocol import ProtocolError

from tests.protocol.fixtures import SYNTHETIC_RECORD

CAPTURE = SYNTHETIC_RECORD  # Synthetic bytes, never a captured hardware result.


class FakeClient:
    def __init__(self, fragments=None, header=b'\x00\x11\x90\x01\x00'):
        self.callbacks = {}
        self.fragments = fragments if fragments is not None else [CAPTURE[:200], CAPTURE[200:]]
        self.header = header
        self.writes = []
        self.disconnected = False
        self.block = None

    async def start_notify(self, uuid, callback):
        self.callbacks[uuid] = callback

    async def write_gatt_char(self, uuid, payload, response=False):
        self.writes.append((uuid, bytes(payload)))
        assert set(self.callbacks) == {DRSP, CONVOY}  # subscribe first
        if self.header is not None:
            self.callbacks[DRSP](None, bytearray(self.header))
        for fragment in self.fragments:
            self.callbacks[CONVOY](None, bytearray(fragment))
        if self.block:
            await self.block.wait()

    async def disconnect(self):
        self.disconnected = True


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
            assert set(self.callbacks) == {DRSP, CONVOY}
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
