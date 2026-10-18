"""Fixed source-derived synthetic zone responses, never physical observations."""
import asyncio
import gc
import importlib
import importlib.util
from types import SimpleNamespace

import pytest

from custom_components.casio_abl100.transport import ReadError
from tests.protocol.test_native_alarm_adapter import metadata

QUERIES = (b'\x1d\x00', b'\x1e\x00', b'\x1e\x01')
FRAMES = (bytes.fromhex('1d aa bb 03 cc dd ee ff 12 34 56 78 90 ab cd'),
          bytes.fromhex('1e 00 ab cd ea 04 fe'),
          bytes.fromhex('1e 01 12 34 17 02 7f'))


def reader():
    name = 'custom_components.casio_abl100.zone_transport'
    assert importlib.util.find_spec(name) is not None, 'Missing fixed zone transport'
    return importlib.import_module(name).read_zone_settings


class Client:
    def __init__(self):
        raw = metadata()
        chars = raw.services[0].characteristics
        chars[0].properties = ['notify']
        chars[1].properties = ['write-without-response']
        self.services = SimpleNamespace(get_service=lambda _: raw.services[0])
        self.calls = []
        self.callback = None
        self.mode = None
        self.index = 0
        self.primary_error = None
        self.cleanup_error = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.hold = None
        self.retained = None
        self.unrelated = True

    def emit(self, frame):
        self.callback(None, bytearray(frame))

    async def start_notify(self, char, callback):
        self.calls.append('notify')
        self.callback = self.retained = callback
        if self.mode == 'early':
            self.emit(FRAMES[0])
        if self.hold == 'notify':
            self.entered.set()
            await self.release.wait()

    async def write_gatt_char(self, char, data, response):
        self.calls.append((bytes(data), response))
        i = self.index
        self.index += 1
        if self.hold == f'query{i}':
            self.entered.set()
            await self.release.wait()
        frame = FRAMES[i]
        mode = self.mode
        if isinstance(mode, tuple) and mode[1] == i:
            kind = mode[0]
            if kind == 'missing':
                self.entered.set()
                return
            if kind == 'short':
                frame = frame[:-1]
            elif kind == 'empty':
                frame = b''
            elif kind == 'ff':
                frame = b'\xff'
            elif kind == 'wrong':
                frame = FRAMES[(i + 1) % 3]
        if self.unrelated:
            self.emit(b'\x13\x55')  # Unrelated feature ignored.
        self.emit(frame)
        if isinstance(mode, tuple) and mode[1] == i and mode[0] == 'duplicate':
            asyncio.get_running_loop().call_soon(self.emit, frame)
        if self.primary_error is not None:
            raise self.primary_error

    async def disconnect(self):
        self.calls.append('disconnect')
        if self.mode == 'cleanup_frame':
            self.emit(FRAMES[2])
        if self.hold == 'cleanup':
            self.entered.set()
            await self.release.wait()
        if self.cleanup_error is not None:
            raise self.cleanup_error


@pytest.mark.asyncio
async def test_fixed_group_source_fields_and_cleanup():
    client = Client()
    result = await reader()(client, timeout=.1)
    assert client.calls == ['notify', *((q, False) for q in QUERIES), 'disconnect']
    assert (result.home.standard_utc_offset_minutes, result.secondary.standard_utc_offset_minutes) == (-330, 345)
    assert (result.home.configured_dst_increment_minutes, result.home.dst_rule_code) == (60, 254)
    assert result.primary_dst_on is True and result.primary_dst_auto is True
    client.retained(None, FRAMES[0])  # Closed callbacks inert.


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['short', 'empty', 'ff', 'wrong', 'missing', 'duplicate'])
@pytest.mark.parametrize('index', range(3))
async def test_each_response_rejects_bad_envelope_order_or_duplicate(kind, index):
    client = Client()
    client.mode = (kind, index)
    client.unrelated = False
    with pytest.raises(ReadError):
        await reader()(client, timeout=.01)
    queries = [x[0] for x in client.calls if isinstance(x, tuple)]
    assert queries == list(QUERIES[:index + 1])
    assert client.calls[-1] == 'disconnect'


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['early', 'cleanup_frame'])
async def test_out_of_phase_frames_prevent_atomic_success(mode):
    client = Client()
    client.mode = mode
    client.unrelated = False
    with pytest.raises(ReadError):
        await reader()(client, timeout=.1)
    if mode == 'early':
        assert client.calls == ['notify', 'disconnect']


@pytest.mark.asyncio
@pytest.mark.parametrize('cleanup', ['ordinary', 'backend_cancel'])
async def test_primary_error_identity_and_completed_future_disposal(cleanup):
    errors = []
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, ctx: errors.append(ctx))
    client = Client()
    client.mode = ('short', 0)
    client.unrelated = False
    primary = RuntimeError('PRIVATE_WRITE')
    client.primary_error = primary
    client.cleanup_error = RuntimeError('PRIVATE_CLEANUP') if cleanup == 'ordinary' else asyncio.CancelledError()
    try:
        task = asyncio.create_task(reader()(client, timeout=.1))
        try:
            await task
        except RuntimeError as error:
            assert error is primary
        else:
            pytest.fail('Write failure lost')
        client.primary_error = None
        primary.__traceback__ = None
        del task, primary
        await asyncio.sleep(0)
        gc.collect()
        await asyncio.sleep(0)
        assert not errors
    finally:
        loop.set_exception_handler(previous)


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['notify', 'query0', 'query1', 'query2', 'cleanup'])
async def test_actual_caller_cancel_is_dominant(stage):
    client = Client()
    client.hold = stage
    task = asyncio.create_task(reader()(client, timeout=.2))
    await asyncio.wait_for(client.entered.wait(), 1)
    task.cancel()
    client.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.calls[-1] == 'disconnect'


@pytest.mark.asyncio
async def test_backend_only_cancel_is_failure_and_preentry_cancel_is_abandonment():
    client = Client()
    client.primary_error = asyncio.CancelledError('PRIVATE_BACKEND')
    with pytest.raises(ReadError):
        await reader()(client, timeout=.1)
    async def cancelled():
        asyncio.current_task().cancel()
        await reader()(Client(), timeout=.1)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.create_task(cancelled())
