"""Unified adapter allowlist and lazy metadata; no network or HA stubs."""
from types import SimpleNamespace
import pytest

from custom_components.casio_abl100.native_sync_adapter import NativeSyncAdapter
from custom_components.casio_abl100.transport import START
from tests.protocol.test_native_step_adapter import API, metadata as step_metadata
from tests.protocol.test_native_alarm_adapter import metadata as alarm_metadata


def metadata():
    raw = step_metadata()
    raw.services[0].characteristics.append(alarm_metadata().services[0].characteristics[1])
    return raw


class SyncAPI(API):
    async def bluetooth_gatt_write(self, address, handle, data, response, timeout):
        self.calls.append(('query', address, handle, bytes(data), response, timeout))


@pytest.mark.asyncio
async def test_fixed_order_metadata_handles_primary_once_no_inner_disconnect():
    api = SyncAPI()
    adapter = NativeSyncAdapter(api, 1, metadata(), lambda: True)
    await adapter.start_primary()
    await adapter.query_primary(b'\x15')
    await adapter.query_primary(b'\x16')
    await adapter.start_step_notifications()
    await adapter.start_steps()
    for query in (b'\x1d\0', b'\x1e\0', b'\x1e\x01', b'\x13', b'\x11', b'\x18'):
        await adapter.query_primary(query)
    assert [x[1] for x in api.calls if x[0] == 'notify'] == [61, 71, 81]
    queries = [x for x in api.calls if x[0] == 'query']
    assert queries[2] == ('query', 1, 71, START, True, 10)
    assert all(x[2] == 52 and x[4] is False for x in queries[:2] + queries[3:])
    assert not any(x[0] == 'disconnect' for x in api.calls)
    adapter.remove_notifications()
    assert not api.callbacks


@pytest.mark.asyncio
async def test_missing_step_metadata_is_lazy_and_not_an_alarm_prerequisite():
    api = SyncAPI()
    raw = alarm_metadata()
    adapter = NativeSyncAdapter(api, 1, raw, lambda: True)
    await adapter.start_primary()
    await adapter.query_primary(b'\x15')
    await adapter.query_primary(b'\x16')
    before = list(api.calls)
    with pytest.raises(ValueError):
        await adapter.start_step_notifications()
    assert api.calls == before
    adapter.remove_notifications()


@pytest.mark.asyncio
@pytest.mark.parametrize('data', [bytes([i]) for i in range(256) if i != 0x15] + [b'', START, b'\x1d\0', b'\x22\x10'])
async def test_no_arbitrary_query_or_out_of_order_write_reaches_native_api(data):
    api = SyncAPI()
    adapter = NativeSyncAdapter(api, 1, metadata(), lambda: True)
    await adapter.start_primary()
    before = list(api.calls)
    with pytest.raises(ValueError):
        await adapter.query_primary(data)
    assert api.calls == before
    adapter.remove_notifications()


@pytest.mark.asyncio
async def test_unready_or_closed_adapter_submits_nothing():
    api = SyncAPI()
    adapter = NativeSyncAdapter(api, 1, metadata(), lambda: True)
    with pytest.raises(ValueError):
        await adapter.query_primary(b'\x15')
    adapter.remove_notifications()
    with pytest.raises(RuntimeError):
        await adapter.start_primary()
    assert not api.calls


@pytest.mark.asyncio
@pytest.mark.parametrize('collision', ['query', 'service'])
async def test_step_handles_must_not_alias_existing_query_or_service(collision):
    api = SyncAPI()
    raw = metadata()
    if collision == 'query':
        raw.services[0].characteristics[-1].handle = 71
    else:
        raw.services[0].handle = 52
    adapter = NativeSyncAdapter(api, 1, raw, lambda: True)
    await adapter.start_primary()
    await adapter.query_primary(b'\x15')
    await adapter.query_primary(b'\x16')
    before = list(api.calls)
    try:
        with pytest.raises(ValueError):
            await adapter.start_step_notifications()
        assert api.calls == before
    finally:
        adapter.remove_notifications()
