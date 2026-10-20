"""Daily workflow through real HA services; only device wire is synthetic."""
import asyncio
import json
from unittest.mock import patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from tests.ha.test_native_alarm_runtime import native
from tests.ha.test_auto_unified_admission_runtime import producer
from tests.ha.test_native_alarm_editing_runtime import editing, service, state, spin_until
from tests.ha.test_native_interactive_ui_runtime import entities, press, connect_ui
from tests.ha.test_auto_retained_runtime import enable, initial, connects, queries
from tests.ha.test_unified_sync_runtime import QUERIES, unified


async def test_listen_switch_supported_options_and_stop(producer):
    p = producer
    key = entities(p).get('watch_connection')
    assert key is not None, 'Missing additive persistent listening control'
    assert p.hass.states.get(key.entity_id).state == 'off'
    await service(p, 'watch_connection', 'turn_on')
    await p.hass.async_block_till_done()
    p.session = p.watch.runtime_data
    assert p.watch.options['auto_connect_retained'] is True
    assert p.watch.options['auto_read_steps'] is p.watch.options['auto_read_all'] is False
    c = await initial(p, p.session.auto_reader)
    await service(p, 'watch_connection', 'turn_off')
    await p.hass.async_block_till_done()
    assert c.root.done() and not c.usable
    assert p.watch.runtime_data.auto_reader is None


@pytest.mark.parametrize('terminal', ['close', 'unload', 'stop', 'loss', 'cancel'])
async def test_periodic_original_root_no_post_terminal_io(producer, monkeypatch, terminal):
    from custom_components.casio_abl100 import native_interactive as module
    from aioesphomeapi.api_pb2 import BluetoothDeviceConnectionResponse
    from tests.ha.test_native_alarm_runtime import TARGET
    monkeypatch.setattr(module, 'PERIODIC_SECONDS', .02, raising=False)
    p = producer
    reader = await enable(p)
    c = await initial(p, reader)
    async with asyncio.timeout(2):
        while c._cycles < 3:
            await asyncio.sleep(.001)
    assert queries(p) == list(QUERIES) * c._cycles
    assert len(connects(p)) == 1 and c.usable
    if terminal == 'close': await p.session.close_interactive()
    elif terminal == 'unload': assert await p.hass.config_entries.async_unload(p.watch.entry_id)
    elif terminal == 'stop':
        p.hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
        await p.hass.async_block_till_done()
    elif terminal == 'loss':
        p.wire.emit(BluetoothDeviceConnectionResponse(address=TARGET, connected=False))
    else: c.root.cancel()
    await spin_until(lambda: c.root.done())
    before = list(p.wire.calls)
    await asyncio.sleep(.05)
    assert p.wire.calls == before and not c.usable
    assert all(q in QUERIES for q in queries(p))  # GET only, never SET


@pytest.mark.parametrize('language', ['en', 'es'])
async def test_disconnected_send_is_real_translated_service_failure(editing, language):
    p = editing
    p.hass.config.language = language
    await p.hass.config_entries.async_reload(p.watch.entry_id)
    p.session = p.watch.runtime_data
    await service(p, 'alarm_1_time_draft', 'set_value', '01:02')
    with pytest.raises(HomeAssistantError) as caught:
        await press(p, 'apply_alarm_draft')
    assert caught.value.translation_key == 'alarm_apply_disconnected'
    from homeassistant.helpers import translation
    assert str(caught.value) == 'Connect the watch before sending changes'  # HA English fallback strips punctuation.
    catalog = await translation.async_get_translations(p.hass, language, 'exceptions', {'casio_abl100'})
    assert catalog['component.casio_abl100.exceptions.alarm_apply_disconnected.message'] == (
        'Conecta el reloj antes de enviar cambios.' if language == 'es'
        else 'Connect the watch before sending changes.')
    assert not p.wire.calls


async def test_edit_confirmed_durable_sparse_restore_and_discard(editing):
    p = editing
    await connect_ui(p)
    with patch('custom_components.casio_abl100.dt_util.now', return_value=__import__('datetime').datetime(2026, 10, 18)):
        await press(p, 'read_interactive')
    anchor = p.session.alarm_draft.semantic(p.session.alarm_snapshot)
    await p.session.close_interactive()
    await service(p, 'alarm_3_time_draft', 'set_value', '02:03')
    await service(p, 'hourly_signal_draft', 'turn_on')
    revision = p.session.alarm_draft.revision
    before = list(p.wire.calls)
    await p.hass.config_entries.async_reload(p.watch.entry_id)
    p.session = p.watch.runtime_data
    d = p.session.alarm_draft
    assert d.pending, 'Successful entity edit must survive reload'
    assert d.revision == revision and d.anchor == anchor and set(d.times) == {3}
    assert not d.enabled and d.signal is True and not d.applying
    assert not p.session.alarms_fresh and p.wire.calls == before
    p.session.alarm_safety.blocked = True
    await press(p, 'discard_alarm_draft')
    assert p.session.alarm_safety.blocked
    await p.hass.config_entries.async_reload(p.watch.entry_id)
    p.session = p.watch.runtime_data
    assert not p.session.alarm_draft.pending and p.wire.calls == before


async def test_entity_save_failure_reports_error_without_device_io(editing):
    p = editing
    with patch('custom_components.casio_abl100.alarm_write_safety.os.replace', side_effect=OSError('disk')):
        with pytest.raises(HomeAssistantError) as caught:
            await service(p, 'alarm_1_time_draft', 'set_value', '01:02')
    assert caught.value.translation_key == 'alarm_draft_save_failed'
    assert not p.wire.calls


@pytest.mark.parametrize('noop', [False, True])
async def test_successful_apply_clears_durable_draft(editing, noop):
    p = editing
    await connect_ui(p)
    await service(p, 'alarm_1_time_draft', 'set_value', '06:17' if noop else '01:02')
    await press(p, 'apply_alarm_draft')
    await p.session.close_interactive()
    await p.hass.config_entries.async_reload(p.watch.entry_id)
    p.session = p.watch.runtime_data
    assert not p.session.alarm_draft.pending


@pytest.mark.parametrize('kind', ['foreign', 'malformed'])
async def test_invalid_draft_storage_restores_nothing(editing, kind):
    p = editing
    await service(p, 'alarm_1_time_draft', 'set_value', '01:02')
    store = getattr(p.session, 'draft_storage', None)
    assert store is not None, 'Independent strict draft storage required'
    record = json.loads(store.path.read_text())
    if kind == 'foreign': record['binding'] = '0' * 64
    else: record['revision'] = True
    store.path.write_text(json.dumps(record))
    await p.hass.config_entries.async_reload(p.watch.entry_id)
    p.session = p.watch.runtime_data
    assert not p.session.alarm_draft.pending and not p.wire.calls


async def test_saved_metadata_and_admission_causes(editing):
    p = editing
    await service(p, 'alarm_1_time_draft', 'set_value', '01:02')
    attrs = state(p, 'apply_alarm_draft')['attributes']
    assert attrs.get('draft_saved') is True
    assert attrs.get('draft_restored') is False and attrs.get('draft_conflicted') is False
    await p.hass.config_entries.async_reload(p.watch.entry_id)
    p.session = p.watch.runtime_data
    assert state(p, 'apply_alarm_draft')['attributes']['draft_restored'] is True
    c = await connect_ui(p)
    for attr, value, key in [('phase', 'reading', 'busy'), ('phase', 'applying', 'busy')]:
        old = getattr(c, attr)
        setattr(c, attr, value)
        with pytest.raises(HomeAssistantError) as caught:
            await press(p, 'apply_alarm_draft')
        assert caught.value.translation_key == 'alarm_apply_' + key
        setattr(c, attr, old)
    for attr, key in [('needs_reconciliation', 'reconciliation'), ('conflicted', 'conflict')]:
        setattr(p.session.alarm_draft, attr, True)
        with pytest.raises(HomeAssistantError) as caught:
            await press(p, 'apply_alarm_draft')
        assert caught.value.translation_key == 'alarm_apply_' + key
        setattr(p.session.alarm_draft, attr, False)
        if attr == 'needs_reconciliation': p.session.alarm_safety.blocked = False
    await press(p, 'discard_alarm_draft')
    with pytest.raises(HomeAssistantError) as caught:
        await press(p, 'apply_alarm_draft')
    assert caught.value.translation_key == 'alarm_apply_empty'
    await p.session.close_interactive()


async def test_verified_apply_caller_cancellation_cannot_resurrect(editing):
    from custom_components.casio_abl100 import native_interactive as module
    p = editing
    await connect_ui(p)
    await service(p, 'alarm_1_time_draft', 'set_value', '01:02')
    original = module._resolve
    def cancel_after_commit(complete, outcome):
        if outcome.result == 'verified': task.cancel()
        original(complete, outcome)
    with patch.object(module, '_resolve', cancel_after_commit):
        task = asyncio.create_task(press(p, 'apply_alarm_draft'))
        await asyncio.gather(task, return_exceptions=True)
    await p.session.close_interactive()
    await p.hass.config_entries.async_reload(p.watch.entry_id)
    p.session = p.watch.runtime_data
    assert not p.session.alarm_draft.pending


async def test_concurrent_entity_saves_keep_latest_revision(editing):
    import threading
    from custom_components.casio_abl100 import alarm_draft_storage as module
    p = editing
    entered, release = threading.Event(), threading.Event()
    original = module._atomic_write
    def hold(path, record):
        if record['revision'] == 1:
            entered.set()
            release.wait(3)
        original(path, record)
    with patch.object(module, '_atomic_write', hold):
        first = asyncio.create_task(service(p, 'alarm_1_time_draft', 'set_value', '01:02'))
        await spin_until(entered.is_set)
        second = asyncio.create_task(service(p, 'alarm_2_time_draft', 'set_value', '03:04'))
        await spin_until(lambda: p.session.alarm_draft.revision == 2)
        release.set()
        await asyncio.gather(first, second)
    await p.hass.config_entries.async_reload(p.watch.entry_id)
    p.session = p.watch.runtime_data
    assert set(p.session.alarm_draft.times) == {1, 2} and p.session.alarm_draft.revision == 2
    assert not p.wire.calls


async def test_restored_anchor_checks_fresh_get_before_any_set(editing):
    from tests.ha.test_native_alarm_runtime import TARGET
    p = editing
    await connect_ui(p)
    with patch('custom_components.casio_abl100.dt_util.now', return_value=__import__('datetime').datetime(2026, 10, 18)):
        await press(p, 'read_interactive')
    await p.session.close_interactive()
    await service(p, 'alarm_2_time_draft', 'set_value', '01:02')
    await p.hass.config_entries.async_reload(p.watch.entry_id)
    p.session = p.watch.runtime_data
    p.wire.memory(TARGET)[b'\x15'] = bytes.fromhex('15 40 07 05 11')
    await connect_ui(p)
    with pytest.raises(HomeAssistantError) as caught:
        await press(p, 'apply_alarm_draft')
    assert caught.value.translation_key == 'alarm_apply_conflict' and not p.wire.sets
    assert p.session.alarm_draft.conflicted and p.session.alarm_draft.pending
    await p.session.close_interactive()
