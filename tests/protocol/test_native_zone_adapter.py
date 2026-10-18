"""Narrow zone bridge authorization and finite callback ownership."""
import asyncio
import importlib
import importlib.util

import pytest

from tests.protocol.test_native_alarm_adapter import API, metadata
from tests.protocol.test_zone_transport import QUERIES, FRAMES, reader


def adapter_type():
    name = 'custom_components.casio_abl100.native_zone_adapter'
    assert importlib.util.find_spec(name) is not None, 'Missing native zone adapter'
    return importlib.import_module(name).NativeZoneAdapter


class ZoneAPI(API):
    async def bluetooth_gatt_write(self, address, handle, data, response, timeout):
        self.calls.append(('query', address, handle, data, response))
        self.cb(61, bytearray(FRAMES[QUERIES.index(data)]))


@pytest.mark.asyncio
async def test_real_adapter_fixed_transfer_and_cleanup():
    api = ZoneAPI()
    adapter = adapter_type()(api, 1, metadata(), lambda: True)
    result = await reader()(adapter, timeout=.1)
    assert result.home.standard_utc_offset_minutes == -330
    assert api.calls == [('notify', 1, 61), ('cccd', 1, 77, b'\x01\x00'),
                         *(('query', 1, 52, q, False) for q in QUERIES), ('disconnect', 1)]
    assert api.removed and adapter.disconnected


@pytest.mark.asyncio
@pytest.mark.parametrize('data', [b'\x15', b'\x16', b'\x13', b'\x11', b'\x18', b'\x23', b'\x09', b'\x1d', b'\x1e\x02', b'\xff', b''])
async def test_no_other_write_reaches_api(data):
    api = ZoneAPI()
    adapter = adapter_type()(api, 1, metadata(), lambda: True)
    await adapter.start_notify(adapter.primary, lambda *_: None)
    before = list(api.calls)
    with pytest.raises(ValueError):
        await adapter.write_gatt_char(adapter.query, data, False)
    assert api.calls == before
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_subscription_readiness_order_response_and_repetition():
    api = ZoneAPI()
    adapter = adapter_type()(api, 1, metadata(), lambda: True)
    with pytest.raises(ValueError):
        await adapter.write_gatt_char(adapter.query, QUERIES[0], False)
    assert not api.calls
    await adapter.start_notify(adapter.primary, lambda *_: None)
    for char, data, response in [(adapter.primary, QUERIES[0], False), (adapter.query, QUERIES[1], False), (adapter.query, QUERIES[0], True)]:
        before = list(api.calls)
        with pytest.raises(ValueError):
            await adapter.write_gatt_char(char, data, response)
        assert api.calls == before
    await adapter.write_gatt_char(adapter.query, QUERIES[0], False)
    before = list(api.calls)
    with pytest.raises(ValueError):
        await adapter.write_gatt_char(adapter.query, QUERIES[0], False)
    assert api.calls == before
    await adapter.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize('handle', [True, 0, -1, 65536, 52, 61, 77])
async def test_service_handle_validated_before_io(handle):
    api = ZoneAPI()
    raw = metadata()
    raw.services[0].handle = handle
    with pytest.raises(ValueError):
        adapter_type()(api, 1, raw, lambda: True)
    assert not api.calls


@pytest.mark.asyncio
async def test_joined_acquisition_repeated_cancellation_retains_remover():
    api = ZoneAPI()
    entered, release = asyncio.Event(), asyncio.Event()
    original = api.bluetooth_gatt_start_notify
    async def acquire(*args):
        result = await original(*args)
        entered.set()
        await release.wait()
        return result
    api.bluetooth_gatt_start_notify = acquire
    adapter = adapter_type()(api, 1, metadata(), lambda: True)
    task = asyncio.create_task(adapter.start_notify(adapter.primary, lambda *_: None))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await adapter.disconnect()
    assert api.removed and not any(x[0] == 'query' for x in api.calls)

@pytest.mark.asyncio
async def test_entire_single_byte_opcode_space_and_extra_zone_selectors_rejected():
    api = ZoneAPI()
    adapter = adapter_type()(api, 1, metadata(), lambda: True)
    await adapter.start_notify(adapter.primary, lambda *_: None)
    before = list(api.calls)
    for data in [*(bytes([i]) for i in range(256)),
                 *(bytes([opcode, i]) for opcode in (0x1d, 0x1e) for i in range(256)
                   if bytes([opcode, i]) != QUERIES[0])]:
        with pytest.raises(ValueError):
            await adapter.write_gatt_char(adapter.query, data, False)
    assert api.calls == before
    await adapter.disconnect()

