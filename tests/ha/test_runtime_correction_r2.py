"""Held retirement cancellation through real HA; only device IO is synthetic."""
import asyncio
from datetime import datetime
import json
import threading
from unittest.mock import patch

from tests.ha.test_native_alarm_runtime import native
from tests.ha.test_unified_sync_runtime import unified
from tests.ha.test_native_alarm_editing_runtime import editing, service, spin_until
from tests.ha.test_native_interactive_ui_runtime import press, connect_ui


async def test_cancel_retire_then_get_cannot_unretire_verified_revision(editing):
    from custom_components.casio_abl100 import alarm_draft_storage as module
    p = editing
    await connect_ui(p)
    await service(p, 'alarm_1_time_draft', 'set_value', '01:02')
    original = module._write_metadata
    entered, release = threading.Event(), threading.Event()

    def hold_retire(path, record):
        if record.get('status') == 'retired':
            entered.set()
            if not release.wait(3):
                raise TimeoutError('release missing')
        return original(path, record)

    with patch.object(module, '_write_metadata', hold_retire):
        task = asyncio.create_task(press(p, 'apply_alarm_draft'))
        try:
            await spin_until(entered.is_set)
            task.cancel()
            await asyncio.sleep(.01)
            assert not task.done()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    store = p.session.draft_storage
    disk = json.loads(store.retirement.path.read_text())
    print('AFTER_CANCEL', p.session.alarm_draft.outcome.result,
          p.session.alarm_draft.pending, p.session.alarm_draft.needs_reconciliation,
          disk, store.fence)
    assert p.session.alarm_draft.outcome.result == 'verified'
    assert disk == store.fence and disk['status'] == 'retired'
    retired_bytes = store.retirement.path.read_bytes()
    journal_bytes = p.session.alarm_safety.path.read_bytes()
    journal = json.loads(journal_bytes)
    assert journal['result'] == 'verified' and not journal['blocked'] and journal['submitted']
    await connect_ui(p)
    with patch('custom_components.casio_abl100.dt_util.now', return_value=datetime(2026, 10, 18)):
        await press(p, 'read_interactive')
    assert not p.session.alarm_draft.pending, 'GET exposed an already retired revision as an ordinary live edit'
    await p.session.close_interactive()
    await p.hass.config_entries.async_reload(p.watch.entry_id)
    p.session = p.watch.runtime_data
    print('AFTER_GET_RELOAD', p.session.alarm_draft.pending,
          p.session.alarm_draft.needs_reconciliation, p.session.draft_storage.fence)
    assert len(p.wire.sets) == 2
    assert not p.session.alarm_draft.pending, 'GET overwrote durable retirement and resurrected already verified revision'
    assert p.session.alarm_draft.outcome.result == 'verified'
    assert not p.session.alarm_draft.needs_reconciliation
    assert p.session.draft_storage.retirement.path.read_bytes() == retired_bytes
    assert p.session.alarm_safety.path.read_bytes() == journal_bytes
    before = list(p.wire.calls)
    await service(p, 'alarm_2_time_draft', 'set_value', '03:04')
    revision = p.session.alarm_draft.revision
    await p.hass.config_entries.async_reload(p.watch.entry_id)
    p.session = p.watch.runtime_data
    assert set(p.session.alarm_draft.times) == {2}
    assert p.session.alarm_draft.revision == revision > disk['revision']
    assert p.wire.calls == before
    assert p.session.alarm_safety.path.read_bytes() == journal_bytes


async def test_get_preserves_newer_revision_after_cancelled_retirement(editing):
    """Characterize revision guard: newer local edits are not retired with Apply."""
    from custom_components.casio_abl100 import alarm_draft_storage as module
    p = editing
    await connect_ui(p)
    await service(p, 'alarm_1_time_draft', 'set_value', '01:02')
    original = module._write_metadata
    entered, release = threading.Event(), threading.Event()

    def hold_retire(path, record):
        if record.get('status') == 'retired':
            entered.set()
            if not release.wait(3):
                raise TimeoutError('release missing')
        return original(path, record)

    with patch.object(module, '_write_metadata', hold_retire):
        task = asyncio.create_task(press(p, 'apply_alarm_draft'))
        try:
            await spin_until(entered.is_set)
            task.cancel()
            await asyncio.sleep(.01)
            assert not task.done()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    journal_bytes = p.session.alarm_safety.path.read_bytes()
    await service(p, 'alarm_2_time_draft', 'set_value', '03:04')
    revision = p.session.alarm_draft.revision
    assert revision > p.session.draft_storage.fence['revision']
    await connect_ui(p)
    with patch('custom_components.casio_abl100.dt_util.now', return_value=datetime(2026, 10, 18)):
        await press(p, 'read_interactive')
    assert p.session.alarm_draft.pending and p.session.alarm_draft.revision == revision
    assert not p.session.alarm_draft.needs_reconciliation
    await p.session.close_interactive()
    await p.hass.config_entries.async_reload(p.watch.entry_id)
    p.session = p.watch.runtime_data
    assert set(p.session.alarm_draft.times) == {1, 2}
    assert p.session.alarm_draft.revision == revision
    assert p.session.alarm_draft.outcome.result == 'verified'
    assert len(p.wire.sets) == 2
    assert p.session.draft_storage.fence['status'] == 'retired'
    assert p.session.alarm_safety.path.read_bytes() == journal_bytes


def test_storage_exact_inverse_restores_pre_r1_bytes():
    from tests.ha.test_auto_unified_preservation import streamlined_source, digest
    assert digest(streamlined_source('alarm_draft_storage.py').encode()) == (
        '79e713854eb6712447d586a91fc8679e8764099d76d7b36fdda13c0df7fc8618')
