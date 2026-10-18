"""Fixed group read; phase correlation is local, never proof of causality."""
import asyncio
import sys

from .native_api import joined
from .settings import parse_zone_settings
from .transport import ReadError, _primary_characteristic, _alarm_query_characteristic, _stage


async def read_zone_settings(client, *, timeout=12):
    """Return one immutable observation only after complete framing and cleanup.

    No transaction IDs exist. A stale callback after public GET invocation is
    indistinguishable from a fresh reply. Never claim radio/peripheral causality.
    """
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    responses = tuple(loop.create_future() for _ in range(3))
    queries = (b'\x1d\x00', b'\x1e\x00', b'\x1e\x01')
    expected = None
    invalid = False
    closed = False

    def reject():
        nonlocal invalid
        invalid = True
        for response in responses:
            if not response.done():
                response.set_exception(ReadError('Invalid zone response'))

    def receive(_characteristic, value):
        if closed or invalid:
            return
        raw = bytes(value)
        if not raw or raw[0] == 0xff:
            reject()
            return
        if raw[0] not in (0x1d, 0x1e):
            return
        if (expected is None or responses[expected].done()
                or len(raw) != (15 if expected == 0 else 7)
                or raw[0] != (0x1d if expected == 0 else 0x1e)
                or (expected != 0 and raw[1] != expected - 1)):
            reject()
            return
        responses[expected].set_result(raw)

    async def invoke(index):
        nonlocal expected
        if invalid:
            raise ReadError('Invalid zone transaction')
        # This runs in _stage's scheduled operation. No await separates arming
        # from adapter entry; its independent allowlist/owner guard precedes IO.
        expected = index
        await client.write_gatt_char(query, queries[index], response=False)

    try:
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        primary = _primary_characteristic(client)
        query = _alarm_query_characteristic(client)
        await _stage('start_notify zones', client.start_notify(primary, receive), timeout)
        frames = []
        for index, response in enumerate(responses):
            if invalid:
                raise ReadError('Invalid zone transaction')
            await _stage('write zone GET', invoke(index), timeout)
            frames.append(await _stage('await zone response', response, timeout))
            await asyncio.sleep(0)
            if invalid:
                raise ReadError('Invalid zone transaction')
            expected = None
        snapshot = parse_zone_settings(*frames)
    except TimeoutError as error:
        raise ReadError('Zone read timed out') from error
    finally:
        primary_error = sys.exc_info()[1]
        cleanup_error = None
        expected = None
        try:
            await joined(_stage('disconnect zones', client.disconnect(), timeout))
            await asyncio.sleep(0)
        except BaseException as error:
            cleanup_error = error
        finally:
            closed = True
            for response in responses:
                if not response.done():
                    response.cancel()
                elif not response.cancelled():
                    response.exception()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        if isinstance(primary_error, asyncio.CancelledError):
            raise ReadError('Zone read cancelled by backend') from primary_error
        if primary_error is None:
            if invalid:
                raise ReadError('Invalid zone transaction')
            if isinstance(cleanup_error, (asyncio.CancelledError, TimeoutError)):
                raise ReadError('Zone cleanup failed') from cleanup_error
            if cleanup_error is not None:
                raise cleanup_error
    return snapshot
