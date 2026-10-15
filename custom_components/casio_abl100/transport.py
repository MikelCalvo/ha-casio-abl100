"""Per-connection read-only DRSP/Convoy framing."""
import asyncio
import logging
import sys
from time import monotonic
from uuid import UUID
from .protocol import parse_steps

_LOGGER = logging.getLogger(__name__)
FEATURES_SERVICE = '26eb000d-b012-49a8-b1f8-394fb2032b0f'
ALL_FEATURES = '26eb002d-b012-49a8-b1f8-394fb2032b0f'
CCCD = UUID('00002902-0000-1000-8000-00805f9b34fb')

DRSP = '26eb0023-b012-49a8-b1f8-394fb2032b0f'
CONVOY = '26eb0024-b012-49a8-b1f8-394fb2032b0f'
START = b'\x00\x11\x00\x00\x00'


class ReadError(Exception):
    """Incomplete or malformed BLE transaction."""


async def disconnect_client(client, *, timeout=1):
    """Bound cooperative disconnect; an uncooperative backend needs its own abort."""
    await asyncio.wait_for(client.disconnect(), timeout)


def _primary_characteristic(client):
    """Resolve only the extra subscription against discovered Bleak GATT metadata."""
    services = client.services
    service = services.get_service(FEATURES_SERVICE) if services is not None else None
    if service is None:
        raise ReadError('ALL_FEATURES service missing')
    characteristic = next((char for char in service.characteristics
                           if char.uuid.lower() == ALL_FEATURES), None)
    if characteristic is None:
        raise ReadError('ALL_FEATURES characteristic missing from service')
    if 'notify' not in characteristic.properties:
        raise ReadError('ALL_FEATURES does not support notify')
    descriptor = next((desc for desc in characteristic.descriptors
                       if UUID(desc.uuid) == CCCD), None)
    if descriptor is None:
        raise ReadError('ALL_FEATURES CCCD missing')
    _LOGGER.debug('ALL_FEATURES metadata uuid=%s handle=%s properties=%s cccd_handle=%s',
                  ALL_FEATURES, characteristic.handle, 'notify', descriptor.handle)
    return characteristic


async def _stage(label, operation, timeout):
    """Log only a fixed stage, monotonic duration and exception class."""
    started = monotonic()
    try:
        result = await asyncio.wait_for(operation, timeout)
    except BaseException as error:
        _LOGGER.debug('%s failed elapsed=%.3fs error_class=%s',
                      label, monotonic() - started, type(error).__name__)
        raise
    _LOGGER.debug('%s completed elapsed=%.3fs', label, monotonic() - started)
    return result


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
        started = monotonic()
        try:
            primary = _primary_characteristic(client)
        except Exception as error:
            _LOGGER.debug('resolve ALL_FEATURES failed elapsed=%.3fs error_class=%s',
                          monotonic() - started, type(error).__name__)
            raise
        _LOGGER.debug('resolve ALL_FEATURES completed elapsed=%.3fs', monotonic() - started)
        # This channel is required for order only: never interpret its payload.
        def discard_primary(_characteristic, _value):
            pass

        await _stage('start_notify ALL_FEATURES',
                     client.start_notify(primary, discard_primary), timeout)
        await _stage('start_notify DRSP', client.start_notify(DRSP, on_header), timeout)
        await _stage('start_notify Convoy', client.start_notify(CONVOY, on_fragment), timeout)
        await _stage('write START', client.write_gatt_char(DRSP, START, response=True), timeout)
        raw = await _stage('await record', complete, timeout)
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
            await _stage('disconnect', client.disconnect(), timeout)
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
