"""Explicit successful-cycle authority and confirmed subscription reuse."""
import pytest
from custom_components.casio_abl100.native_sync_adapter import NativeSyncAdapter
from tests.protocol.test_native_sync_adapter import SyncAPI, metadata


async def cycle(adapter):
    await adapter.start_primary()
    await adapter.query_primary(b'\x15')
    await adapter.query_primary(b'\x16')
    await adapter.start_step_notifications()
    await adapter.start_steps()
    for query in (b'\x1d\0', b'\x1e\0', b'\x1e\x01', b'\x13', b'\x11', b'\x18'):
        await adapter.query_primary(query)


async def test_complete_cycle_requires_explicit_success_and_reuses_confirmed_handles():
    api = SyncAPI()
    adapter = NativeSyncAdapter(api, 1, metadata(), lambda: True)
    assert callable(getattr(adapter, 'finish_cycle', None)), 'Missing explicit cycle success contract'
    try:
        with pytest.raises(ValueError):
            adapter.rearm_cycle()
        with pytest.raises(ValueError):
            adapter.finish_cycle()
        await cycle(adapter)
        with pytest.raises(ValueError):
            adapter.rearm_cycle()
        adapter.finish_cycle()
        adapter.rearm_cycle()
        with pytest.raises(ValueError):
            adapter.rearm_cycle()
        await cycle(adapter)
        adapter.finish_cycle()
        assert [x[1] for x in api.calls if x[0] == 'notify'] == [61, 71, 81]
        assert len([x for x in api.calls if x[0] == 'cccd']) == 3
    finally:
        adapter.remove_notifications()
    assert not api.callbacks


@pytest.mark.parametrize('blocker', ['receiver', 'step_receivers', 'closing', 'lost'])
async def test_finish_or_rearm_refuses_active_receivers_or_lost_link(blocker):
    alive = True
    api = SyncAPI()
    a = NativeSyncAdapter(api, 1, metadata(), lambda: alive)
    assert callable(getattr(a, 'finish_cycle', None)), 'Missing cycle guards'
    try:
        await cycle(a)
        if blocker == 'lost':
            alive = False
        else:
            setattr(a, blocker, True)
        with pytest.raises((ValueError, RuntimeError)):
            a.finish_cycle()
    finally:
        a.remove_notifications()


async def test_repeated_cycle_entry_is_not_subscription_reuse():
    api = SyncAPI()
    a = NativeSyncAdapter(api, 1, metadata(), lambda: True)
    try:
        await a.start_primary()
        before = list(api.calls)
        with pytest.raises(ValueError):
            await a.start_primary()
        assert api.calls == before
    finally:
        a.remove_notifications()
