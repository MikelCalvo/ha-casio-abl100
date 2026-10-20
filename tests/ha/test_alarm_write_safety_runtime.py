"""Local acknowledged safety IO and actual HA/native entry boundaries (no live IO)."""
import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from tests.ha.test_native_alarm_editing_runtime import (
    native, editing, service, press, connect_ui, spin_until, state,
)
from tests.ha.test_unified_sync_runtime import drive, assert_clean


def safety_module():
    from custom_components.casio_abl100 import alarm_write_safety
    return alarm_write_safety


async def test_acknowledged_record_precedes_first_set_and_cold_load(editing):
    p = editing
    await connect_ui(p)
    await service(p,'alarm_2_time_draft','set_value','01:02')
    safety = p.session.alarm_safety
    seen = []
    def before(stage, request):
        if stage == 'set15':
            seen.append(json.loads(safety.path.read_text()))
    # Only observe the external boundary; assert outside callbacks caught by owner.
    p.wire.before = before
    await press(p,'apply_alarm_draft')
    assert len(seen) == 1 and seen[0]['blocked'] is True
    assert seen[0]['result'] == 'unknown'
    assert not safety.blocked and len(p.wire.sets) == 2
    await p.session.close_interactive()
    # New journal object/new runtime; no same-process safety cache allowed.
    cold = safety_module().AlarmWriteSafety(p.hass, p.watch)
    await cold.async_load()
    assert not cold.blocked
    assert not any(k in seen[0] for k in ('intent','draft','frames','revision','address'))
    assert_clean(p)


@pytest.mark.parametrize('fault',['write_failure','cancel','close','generation','deadline'])
async def test_no_set_if_prewrite_save_fails_or_authority_revoked(editing, fault):
    p = editing
    c = await connect_ui(p)
    await service(p,'alarm_2_time_draft','set_value','01:02')
    safety = p.session.alarm_safety
    module = safety_module()
    entered, release = threading.Event(), threading.Event()
    original = module._atomic_write
    def write(path, record):
        entered.set()
        if fault == 'write_failure':
            raise OSError('SYNTHETIC_LOCAL_FAILURE')
        release.wait(10)
        original(path,record)
    with patch.object(module,'_atomic_write',write):
        task = asyncio.create_task(p.session.apply_alarm_draft())
        await spin_until(entered.is_set)
        closer = None
        try:
            assert not p.wire.sets
            if fault == 'cancel':
                task.cancel()
            elif fault == 'close':
                closer = asyncio.create_task(p.session.close_interactive())
            elif fault == 'generation':
                p.api.wire = type(p.wire)()
            elif fault == 'deadline':
                c._refresh_deadline = 0
            await asyncio.sleep(0)
        finally:
            release.set()
            results = await asyncio.gather(task,return_exceptions=True)
            if closer:
                await asyncio.gather(closer,return_exceptions=True)
        assert isinstance(results[0],(HomeAssistantError,asyncio.CancelledError))
    assert not p.wire.sets and safety.blocked
    assert not c.root or c.root.done()


async def test_unknown_cold_setup_and_explicit_getonly_preserves_history(editing):
    p = editing
    await connect_ui(p)
    await service(p,'alarm_2_time_draft','set_value','01:02')
    p.wire.failure_stage = 'set16'
    with pytest.raises(HomeAssistantError):
        await press(p,'apply_alarm_draft')
    saved = p.session.alarm_safety.path.read_bytes()
    assert await p.hass.config_entries.async_unload(p.watch.entry_id)
    # Runtime reference destroyed before a genuine setup; bytes are sole authority.
    p.session = None
    assert await p.hass.config_entries.async_setup(p.watch.entry_id)
    p.session = p.watch.runtime_data
    assert p.session.alarm_draft.pending  # Unknown Apply retains local values, never execution authority.
    assert p.session.alarm_draft.outcome.result == 'unknown'
    assert p.session.alarm_safety.path.read_bytes() == saved
    assert p.session.alarm_draft.needs_reconciliation
    await service(p,'alarm_3_time_draft','set_value','04:05')
    await connect_ui(p)
    with pytest.raises(HomeAssistantError):
        await p.session.apply_alarm_draft()
    p.wire.failure_stage = None
    with patch('custom_components.casio_abl100.dt_util.now',return_value=__import__('datetime').datetime(2026,10,18)):
        await drive(asyncio.create_task(p.session.read_interactive()),p)
    assert not p.session.alarm_safety.blocked
    assert p.session.alarm_draft.outcome.result == 'unknown'
    assert len(p.wire.sets) == 1
    await p.session.close_interactive()
    cold = safety_module().AlarmWriteSafety(p.hass,p.watch)
    await cold.async_load()
    assert not cold.blocked and cold.result == 'unknown'
    assert_clean(p)


@pytest.mark.parametrize('fault',['malformed','unreadable','binding'])
async def test_invalid_record_blocks_apply_but_getonly_can_recover(editing,fault):
    p = editing
    module = safety_module()
    safety = p.session.alarm_safety
    await safety.async_pending()
    if fault == 'malformed':
        await p.hass.async_add_executor_job(safety.path.write_text,'{}')
    elif fault == 'binding':
        record = json.loads(safety.path.read_text())
        record['binding'] = '0'*64
        await p.hass.async_add_executor_job(safety.path.write_text,json.dumps(record))
    if fault == 'unreadable':
        with patch.object(module,'_read_record',side_effect=OSError('SYNTHETIC_READ_FAILURE')):
            assert await p.hass.config_entries.async_reload(p.watch.entry_id)
    else:
        assert await p.hass.config_entries.async_reload(p.watch.entry_id)
    p.session = p.watch.runtime_data
    assert p.session.alarm_safety.blocked
    await connect_ui(p)
    await service(p,'hourly_signal_draft','turn_on')
    with pytest.raises(HomeAssistantError):
        await p.session.apply_alarm_draft()
    with patch('custom_components.casio_abl100.dt_util.now',return_value=__import__('datetime').datetime(2026,10,18)):
        await drive(asyncio.create_task(p.session.read_interactive()),p)
    assert not p.session.alarm_safety.blocked and not p.wire.sets
    await p.session.close_interactive()


async def test_record_two_entries_and_removal_cannot_resurrect(hass,tmp_path):
    module = safety_module()
    hass.config.config_dir = str(tmp_path)
    a = SimpleNamespace(entry_id='entry-a',unique_id='immutable-a',data={'address':'AA'})
    b = SimpleNamespace(entry_id='entry-b',unique_id='immutable-b',data={'address':'BB'})
    first, second = (module.AlarmWriteSafety(hass,e) for e in (a,b))
    await first.async_load()
    await second.async_load()
    await first.async_pending()
    assert first.blocked and not second.blocked and first.path != second.path
    changed = SimpleNamespace(entry_id=a.entry_id,unique_id=a.unique_id,data={'address':'CC'})
    changed_owner = module.AlarmWriteSafety(hass,changed)
    await changed_owner.async_load()
    assert changed_owner.blocked and changed_owner.result == 'unknown'
    assert changed_owner.path == first.path
    await first.async_remove()
    with pytest.raises(RuntimeError):
        await first.async_pending()
    assert not first.path.exists()
    cold = module.AlarmWriteSafety(hass,a)
    await cold.async_load()
    assert not cold.blocked


async def test_clear_failure_keeps_barrier_and_unknown_history(editing):
    p = editing
    await connect_ui(p)
    await service(p,'hourly_signal_draft','turn_on')
    module = safety_module()
    original = module._atomic_write
    def write(path,record):
        if not record['blocked']:
            raise OSError('SYNTHETIC_CLEAR_FAILURE')
        original(path,record)
    with patch.object(module,'_atomic_write',write):
        with pytest.raises(HomeAssistantError):
            await press(p,'apply_alarm_draft')
    assert p.session.alarm_safety.blocked and len(p.wire.sets) == 2
    assert p.session.alarm_draft.outcome.result == 'unknown'
