"""Borrowed-link framing for the fixed six-group read; never owns cleanup."""
import asyncio
from homeassistant.util import dt as dt_util

from .alarms import parse_alarms
from .protocol import parse_steps
from .settings import (parse_zone_settings, parse_basic_settings,
                       parse_auto_time_settings, parse_timer_configuration)
from .transport import ReadError


def _release(futures):
    for future in futures:
        if not future.done():
            future.cancel()
        elif not future.cancelled():
            future.exception()


async def _primary(adapter, queries, lengths, parser, *, acquire=False):
    """Only the fixed callers below select framing; no arbitrary wire dispatch."""
    loop = asyncio.get_running_loop()
    futures = [loop.create_future() for _ in queries]
    expected = None
    invalid = False
    opcodes = {query[0] for query in queries}

    def reject():
        nonlocal invalid
        invalid = True
        for future in futures:
            if not future.done():
                future.set_exception(ReadError('Invalid unified response'))

    def receive(value):
        raw = bytes(value)
        if not raw or raw[0] == 0xff:
            reject()
            return
        if raw[0] not in opcodes:
            return
        if (expected is None or futures[expected].done()
                or len(raw) != lengths[expected]
                or raw[0] != queries[expected][0]
                or (raw[0] == 0x1e and raw[1] != queries[expected][1])):
            reject()
        else:
            futures[expected].set_result(raw)

    async def invoke(index):
        nonlocal expected
        adapter.guard()
        if invalid:
            raise ReadError('Invalid unified phase')
        expected = index
        await adapter.query_primary(queries[index])

    adapter.receiver = receive
    try:
        if acquire:
            await adapter.start_primary()
        frames = []
        for index, future in enumerate(futures):
            await asyncio.wait_for(invoke(index), 10)
            frames.append(await asyncio.wait_for(future, 12))
            await asyncio.sleep(0)
            if invalid:
                raise ReadError('Invalid unified framing')
            expected = None
        return parser(*frames)
    finally:
        # Quiesce callbacks before the owner checks/commits a validated group.
        adapter.receiver = None
        _release(futures)


async def read_alarm_group(adapter):
    return await _primary(adapter, (b'\x15', b'\x16'), (5, 17), parse_alarms, acquire=True)


async def read_step_group(adapter):
    loop = asyncio.get_running_loop()
    complete = loop.create_future()
    data = bytearray()
    header_seen = False
    armed = False
    invalid = False

    def reject():
        nonlocal invalid
        invalid = True
        if not complete.done():
            complete.set_exception(ReadError('Invalid unified step framing'))

    def header(value):
        nonlocal header_seen
        raw = bytes(value)
        if (not armed or header_seen or len(raw) not in (5, 7)
                or raw[:2] != b'\0\x11'
                or int.from_bytes(raw[2:5], 'little') != 400):
            reject()
            return
        header_seen = True
        if len(data) == 400 and not complete.done():
            complete.set_result(bytes(data))

    def fragment(value):
        if not armed or complete.done() or not value or len(data) + len(value) > 400:
            reject()
            return
        data.extend(value)
        if header_seen and len(data) == 400:
            complete.set_result(bytes(data))

    async def invoke():
        nonlocal armed
        adapter.guard()
        if invalid:
            raise ReadError('Invalid unified step phase')
        armed = True
        await adapter.start_steps()

    adapter.step_receivers = (header, fragment)
    try:
        await adapter.start_step_notifications()
        await asyncio.wait_for(invoke(), 10)
        raw = await asyncio.wait_for(complete, 12)
        await asyncio.sleep(0)
        if invalid:
            raise ReadError('Invalid unified step framing')
        record = parse_steps(raw)
        if record.record_date != dt_util.now().date():
            raise ReadError('Unified step record not today')
        return record
    finally:
        adapter.step_receivers = None
        _release((complete,))


async def read_zone_group(adapter):
    return await _primary(adapter, (b'\x1d\0', b'\x1e\0', b'\x1e\x01'), (15, 7, 7), parse_zone_settings)


async def read_basic_group(adapter):
    return await _primary(adapter, (b'\x13',), (12,), parse_basic_settings)


async def read_auto_time_group(adapter):
    return await _primary(adapter, (b'\x11',), (15,), parse_auto_time_settings)


async def read_timer_group(adapter):
    return await _primary(adapter, (b'\x18',), (15,), parse_timer_configuration)
