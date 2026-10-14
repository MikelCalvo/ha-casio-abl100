"""Per-connection read-only DRSP/Convoy framing."""
import asyncio
import sys
from .protocol import parse_steps

DRSP = '26eb0023-b012-49a8-b1f8-394fb2032b0f'
CONVOY = '26eb0024-b012-49a8-b1f8-394fb2032b0f'
START = b'\x00\x11\x00\x00\x00'


class ReadError(Exception):
    """Incomplete or malformed BLE transaction."""


async def disconnect_client(client, *, timeout=1):
    """Bound cooperative disconnect; an uncooperative backend needs its own abort."""
    await asyncio.wait_for(client.disconnect(), timeout)


async def read_record(client, *, timeout=12):
    """Read a record and always close the connection, without an end command."""
    loop = asyncio.get_running_loop()
    complete = loop.create_future()
    header_seen = False
    invalid = False
    data = bytearray()

    def fail(message):
        nonlocal invalid
        invalid = True
        if not complete.done():
            complete.set_exception(ReadError(message))

    def on_header(_characteristic, value):
        nonlocal header_seen
        if complete.done():
            if header_seen:
                fail('duplicate length announcement')
            return
        # The first five bytes encode command, category and little-endian
        # 24-bit record length; the optional two trailing bytes are not length.
        header = bytes(value)
        if (header_seen or len(header) not in (5, 7)
                or header[:2] != b'\x00\x11'
                or int.from_bytes(header[2:5], 'little') != 400):
            fail('invalid length announcement')
            return
        header_seen = True
        if len(data) == 400:
            complete.set_result(bytes(data))

    def on_fragment(_characteristic, value):
        if complete.done():
            fail('extra fragment')
            return
        if len(data) + len(value) > 400:
            fail('oversized record')
            return
        data.extend(value)
        if header_seen and len(data) == 400:
            complete.set_result(bytes(data))

    try:
        await asyncio.wait_for(client.start_notify(DRSP, on_header), timeout)
        await asyncio.wait_for(client.start_notify(CONVOY, on_fragment), timeout)
        await asyncio.wait_for(client.write_gatt_char(DRSP, START, response=True), timeout)
        raw = await asyncio.wait_for(complete, timeout)
        # Yield once to receive queued adjacent notifications before accepting
        # a record. The wire format has no sequence numbers; it cannot reorder.
        await asyncio.sleep(0)
        if invalid or len(data) != 400:
            raise ReadError('invalid record framing')
        return parse_steps(raw)
    except TimeoutError as error:
        raise ReadError('read timed out') from error
    finally:
        if not complete.done():
            complete.cancel()
        failed = sys.exc_info()[0] is not None
        try:
            await disconnect_client(client, timeout=timeout)
        except TimeoutError as error:
            if not failed:
                raise ReadError('disconnect timed out') from error
        except Exception:
            if not failed:
                raise
        except asyncio.CancelledError:
            if not failed:
                raise
        # wait_for joins cooperative cancellation of the disconnect coroutine.
        # A backend that suppresses cancellation cannot be made finite by
        # asyncio.wait_for; that requires a backend-level abort mechanism.
