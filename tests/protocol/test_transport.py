"""In-memory BLE client fake: never touches hardware or a local adapter."""
import asyncio
from pathlib import Path
import pytest

from custom_components.casio_abl100.transport import read_record, ReadError, DRSP, CONVOY, START
from custom_components.casio_abl100.protocol import ProtocolError

CAPTURE = bytes.fromhex(''.join(x for x in Path(__file__).with_name('abl_real_capture.txt').read_text().splitlines() if not x.startswith('#')))


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
async def test_immediate_notifications_return_complete_record_without_end_write():
    client = FakeClient()
    result = await read_record(client, timeout=0.05)
    assert result.steps == 1485
    assert client.writes == [(DRSP, START)]
    assert client.disconnected


@pytest.mark.asyncio
@pytest.mark.parametrize('header,fragments', [
    (None, [CAPTURE]),
    (b'\x00\x11\x90\x01\x00', [CAPTURE[:-1]]),
    (b'\x00\x11\x90\x01\x00', [CAPTURE + b'\0']),
    (b'\x00\x11\x91\x01\x00', [CAPTURE]),
    (b'\x00\x11\x90\x01\x00', [CAPTURE[:200], CAPTURE[:200]]),
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
async def test_notification_subscription_timeout_disconnects():
    class StuckNotify(FakeClient):
        async def start_notify(self, uuid, callback):
            await asyncio.Event().wait()
    client = StuckNotify()
    with pytest.raises(ReadError):
        await read_record(client, timeout=0.02)
    assert client.writes == []
    assert client.disconnected
