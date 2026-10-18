"""Borrowed framing: current implementation characterization and adversarial IO."""
import asyncio
from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest
from custom_components.casio_abl100.sync_transport import (
    read_alarm_group, read_step_group, read_zone_group, read_basic_group,
    read_auto_time_group, read_timer_group,
)
from custom_components.casio_abl100.transport import ReadError
from tests.protocol.test_daily_history import history_record

FRAMES = {
    b'\x15': bytes.fromhex('15 40 00 07 23'),
    b'\x16': b'\x16' + bytes.fromhex('00 00 08 09') * 4,
    b'\x1d\0': b'\x1d' + bytes(14),
    b'\x1e\0': b'\x1e' + bytes(6),
    b'\x1e\x01': b'\x1e\x01' + bytes(5),
    b'\x13': b'\x13' + bytes(11),
    b'\x11': b'\x11' + bytes(14),
    b'\x18': b'\x18' + bytes(14),
}
READERS = ((read_alarm_group, b'\x15'), (read_zone_group, b'\x1d\0'),
           (read_basic_group, b'\x13'), (read_auto_time_group, b'\x11'),
           (read_timer_group, b'\x18'))


class Borrowed:
    def __init__(self):
        self.receiver = None
        self.step_receivers = None
        self.mode = None
        self.calls = []
        self.retained = []
        self.write_failure = None

    def guard(self):
        pass

    async def start_primary(self):
        if self.mode == 'early':
            self.receiver(FRAMES[b'\x15'])

    async def query_primary(self, query):
        self.calls.append(query)
        callback = self.receiver
        self.retained.append(callback)
        raw = FRAMES[query]
        if self.mode == 'empty':
            raw = b''
        elif self.mode == 'ff':
            raw = b'\xff'
        elif self.mode == 'short':
            raw = raw[:-1]
        elif self.mode == 'wrong_index' and query[0] == 0x1e:
            raw = raw[:1] + bytes([2]) + raw[2:]
        elif self.mode == 'wrong_phase' and query[0] == 0x15:
            raw = FRAMES[b'\x16']
        callback(raw)
        if self.mode == 'duplicate':
            asyncio.get_running_loop().call_soon(callback, raw)
        if self.write_failure:
            raise self.write_failure

    async def start_step_notifications(self):
        self.retained.extend(self.step_receivers)
        if self.mode == 'early':
            self.step_receivers[0](b'\0\x11\x90\x01\0')

    async def start_steps(self):
        header, fragment = self.step_receivers
        raw = history_record(date(2026, 10, 18))
        length = b'\0\x11\x90\x01\0'
        if self.mode == 'bad_length':
            length = b'\0\x11\x91\x01\0'
        elif self.mode == 'seven':
            length += b'\xff\xff'
        elif self.mode == 'bad_header':
            length = b'\x01\x11\x90\x01\0'
        if self.mode == 'fragment_first':
            fragment(raw[:200])
            header(length)
            fragment(raw[200:])
        else:
            header(length)
            fragment(raw)
        if self.mode == 'extra':
            asyncio.get_running_loop().call_soon(fragment, b'\0')
        elif self.mode == 'duplicate':
            asyncio.get_running_loop().call_soon(header, length)
        if self.write_failure:
            raise self.write_failure


@pytest.mark.asyncio
@pytest.mark.parametrize('reader,query', READERS)
@pytest.mark.parametrize('mode', ['valid', 'empty', 'ff', 'short', 'duplicate'])
async def test_primary_group_frames_drained_and_receiving_quiesced(reader, query, mode):
    adapter = Borrowed()
    adapter.mode = mode
    if mode == 'valid':
        result = await reader(adapter)
        assert result is not None
    else:
        with pytest.raises(ReadError):
            await reader(adapter)
    assert adapter.receiver is None
    assert adapter.calls[0] == query


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['early', 'wrong_phase'])
async def test_alarm_rejects_before_public_invocation_and_wrong_phase(mode):
    adapter = Borrowed()
    adapter.mode = mode
    with pytest.raises(ReadError):
        await read_alarm_group(adapter)
    if mode == 'early':
        assert not adapter.calls


@pytest.mark.asyncio
async def test_zone_wrong_index_stops_before_next_query():
    adapter = Borrowed()
    adapter.mode = 'wrong_index'
    with pytest.raises(ReadError):
        await read_zone_group(adapter)
    assert adapter.calls == [b'\x1d\0', b'\x1e\0']


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['valid', 'seven', 'fragment_first', 'bad_length', 'bad_header', 'extra', 'duplicate', 'early'])
async def test_step_framing_reuses_exact_length_and_parser_without_cleanup(mode):
    adapter = Borrowed()
    adapter.mode = mode
    with patch('custom_components.casio_abl100.sync_transport.dt_util.now', return_value=datetime(2026, 10, 18, tzinfo=timezone.utc)):
        if mode in ('valid', 'seven', 'fragment_first'):
            result = await read_step_group(adapter)
            assert result.steps == 1485
        else:
            with pytest.raises(ReadError):
                await read_step_group(adapter)
    assert adapter.step_receivers is None


@pytest.mark.asyncio
@pytest.mark.parametrize('reader,query', READERS)
async def test_matching_unsolicited_frame_before_scheduled_public_invocation(reader, query):
    adapter = Borrowed()
    # Python 3.14 wait_for may enter the coroutine inline. Intercept only its
    # scheduling boundary to deliver before public query invocation, retaining
    # the real group receiver and query guard (not a fake refusal).
    original = asyncio.wait_for
    delivered = []
    async def wait(operation, timeout):
        if not delivered:
            delivered.append(True)
            adapter.receiver(FRAMES[query])
        return await original(operation, timeout)
    with patch('custom_components.casio_abl100.sync_transport.asyncio.wait_for', wait):
        with pytest.raises(ReadError):
            await reader(adapter)
    assert delivered and not adapter.calls
    assert adapter.receiver is None


@pytest.mark.asyncio
@pytest.mark.parametrize('reader,query', READERS)
async def test_malformed_immediate_reply_does_not_replace_write_failure_or_leak_future(reader, query):
    import gc
    adapter = Borrowed()
    adapter.mode = 'short'
    adapter.write_failure = RuntimeError('PRIVATE_WRITE')
    loop = asyncio.get_running_loop()
    old = loop.get_exception_handler()
    errors = []
    loop.set_exception_handler(lambda _loop, context: errors.append(context))
    try:
        task = asyncio.create_task(reader(adapter))
        with pytest.raises(RuntimeError) as caught:
            await task
        assert caught.value is adapter.write_failure
        adapter.write_failure.__traceback__ = None
        adapter.write_failure = None
        del caught, task
        gc.collect()
        await asyncio.sleep(0)
        assert not errors
    finally:
        loop.set_exception_handler(old)
