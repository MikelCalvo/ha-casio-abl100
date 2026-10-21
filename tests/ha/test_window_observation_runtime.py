"""Passive capture through real HA and compiled RAW ingress; no physical IO."""
from datetime import datetime, timezone
import json
from unittest.mock import patch

import pytest
from aioesphomeapi.api_pb2 import BluetoothLERawAdvertisementsResponse
from homeassistant.data_entry_flow import FlowResultType

from custom_components.casio_abl100.diagnostics import async_get_config_entry_diagnostics
from tests.ha.test_native_alarm_runtime import native, TARGET
from tests.ha.test_unified_sync_runtime import unified
from tests.ha.test_auto_unified_admission_runtime import producer, ProducerWire
from tests.ha.test_auto_unified_runtime import keys


async def observe(p):
    form = await p.hass.config_entries.options.async_init(p.watch.entry_id)
    result = await p.hass.config_entries.options.async_configure(
        form['flow_id'], {'observe_windows': True})
    assert result['type'] is FlowResultType.CREATE_ENTRY
    await p.hass.async_block_till_done()
    p.session = p.watch.runtime_data
    reader = p.session.auto_reader
    assert reader is not None and reader.mode == 'observe', 'Explicit passive observer missing'
    return reader


@pytest.mark.parametrize('enabled', [True, False])
async def test_listening_switch_clears_observe_for_start_and_explicit_stop(producer, enabled):
    from tests.ha.test_native_alarm_editing_runtime import service
    p = producer
    reader = await observe(p)
    p.wire.raw()
    ids = keys(p)
    await service(p, 'watch_connection', 'turn_on' if enabled else 'turn_off')
    await p.hass.async_block_till_done()
    p.session = p.watch.runtime_data
    assert p.watch.options['observe_windows'] is False
    assert p.watch.options['auto_connect_retained'] is enabled
    if enabled:
        assert p.session.auto_reader.mode == 'retained'
    else:
        assert p.session.auto_reader is None
    assert reader._stopped and reader.window_observation()['coverage_since_utc'] is None
    assert await summary(p) is None and keys(p) == ids
    assert not p.wire.calls


async def summary(p):
    return (await async_get_config_entry_diagnostics(p.hass, p.watch))['window_observation']


async def test_explicit_observe_captures_raw_without_any_native_io(producer):
    p = producer
    ids = keys(p)
    assert p.session.auto_reader is None and len(p.wire.handlers) == 1
    assert await summary(p) is None
    reader = await observe(p)
    before = await summary(p)
    assert before['enabled'] and before['bound'] and not before['suspended']
    assert before['raw_record_count'] == before['burst_count'] == 0
    with patch('custom_components.casio_abl100.native_sync._RawSyncAdmission') as ticket, patch.object(
        reader, '_claim') as claim, patch.object(type(p.watch), 'async_create_background_task') as task:
        p.wire.raw()
        await p.hass.async_block_till_done()
        assert ticket.call_count == claim.call_count == task.call_count == 0
    captured = await summary(p)
    assert captured['burst_count'] == captured['raw_record_count'] == 1
    assert captured['recent_bursts'][0]['raw_record_count'] == 1
    assert captured['cause'] == captured['recent_bursts'][0]['cause'] == 'unknown'
    assert captured['coverage_gap_count'] == 0
    assert set(captured) == {'enabled', 'bound', 'suspended', 'coverage_since_utc',
                             'observation_started_utc', 'burst_count', 'raw_record_count',
                             'coverage_gap_count', 'recent_bursts', 'cause', 'clock_fault'}
    burst = captured['recent_bursts'][0]
    assert set(burst) == {'first_seen_utc', 'last_seen_utc', 'raw_record_count', 'cause'}
    assert datetime.fromisoformat(burst['first_seen_utc']).tzinfo == timezone.utc
    assert datetime.fromisoformat(burst['last_seen_utc']).tzinfo == timezone.utc
    assert before['coverage_since_utc'] <= burst['first_seen_utc'] <= burst['last_seen_utc']
    for key in ('coverage_since_utc', 'observation_started_utc'):
        assert datetime.fromisoformat(captured[key]).tzinfo == timezone.utc
    assert keys(p) == ids and len(ids) == 37
    assert not p.wire.calls and not p.session.tasks and reader._task is None
    assert reader._last_seen is None and reader._claimed is False
    assert reader._pending_admission is reader._retained_admission is None
    assert not p.api._notify_callbacks and p.session.interactive_session is None
    p.bleak.assert_not_called()


async def test_direct_observer_zero_authority_batch_filtering_and_copy(producer):
    from custom_components.casio_abl100.auto_read import AutoStepReader
    p = producer
    reader = p.session.auto_reader = AutoStepReader(p.hass, p.watch, p.session, mode='observe')
    reader.start()
    name = b'ABL-100WE'
    data = bytes([len(name) + 1, 9]) + name
    message = BluetoothLERawAdvertisementsResponse()
    for address, kind, payload in [
        (TARGET + 1, 1, data), (TARGET, 2, data), (TARGET, 255, data),
        (TARGET, 1, b''), (TARGET, 1, b'\x02\x01\x06'),
        (TARGET, 1, b'\xff\x09x'), (TARGET, 1, b'\x04\x09BAD'),
        (TARGET, 0, data), (TARGET, 1, data), (TARGET, 1, data),
    ]:
        message.advertisements.add(address=address, address_type=kind, data=payload)
    with patch('custom_components.casio_abl100.native_sync._RawSyncAdmission') as ticket, patch.object(
        reader, '_claim') as claim:
        p.wire.emit(message)
        await p.hass.async_block_till_done()
        assert ticket.call_count == claim.call_count == 0
    assert not p.wire.calls and reader._task is None
    value = await summary(p)
    assert value['raw_record_count'] == 3 and value['burst_count'] == 1
    assert value['recent_bursts'][0]['raw_record_count'] == 3
    value['recent_bursts'][0]['raw_record_count'] = 999
    value['recent_bursts'].clear()
    assert (await summary(p))['recent_bursts'][0]['raw_record_count'] == 3
    reader.private_canary = 'PRIVATE_WINDOW_CANARY'
    p.session.private_canary = 'PRIVATE_WINDOW_CANARY'
    text = json.dumps(await async_get_config_entry_diagnostics(p.hass, p.watch))
    assert all(token not in text for token in ('PRIVATE', p.session.address, 'ABL-100WE'.lower(),
                                               'rssi', 'payload', 'address', '_clock'))


async def test_grouping_exact_gap_bounded_history_and_saturating_counts(producer):
    p = producer
    reader = await observe(p)
    now = reader._clock + 1
    with patch('custom_components.casio_abl100.auto_read.monotonic_time_coarse') as clock:
        for delta in (0, 0, 29.999, 59.999):
            clock.return_value = now + delta
            p.wire.raw()
        value = await summary(p)
        assert value['burst_count'] == 2 and value['raw_record_count'] == 4
        assert [b['raw_record_count'] for b in value['recent_bursts']] == [3, 1]
        for i in range(20):
            clock.return_value = now + 90 + 30 * i
            p.wire.raw()
        value = await summary(p)
        assert value['burst_count'] == 22 and len(value['recent_bursts']) == 16
        assert value['raw_record_count'] == 24
        # Saturation boundary without billions of synthetic packets.
        reader._window_raw_count = reader._window_burst_count = reader._window_gap_count = 2**31 - 1
        reader._window_bursts[-1]['raw_record_count'] = 2**31 - 1
        p.wire.raw()
        reader.suspend()
        reader.resume()
        clock.return_value += 30
        p.wire.raw()
        value = await summary(p)
        assert value['raw_record_count'] == value['burst_count'] == value['coverage_gap_count'] == 2**31 - 1
        assert len(value['recent_bursts']) == 16
        assert all(0 <= b['raw_record_count'] <= 2**31 - 1 for b in value['recent_bursts'])
    assert not p.wire.calls


@pytest.mark.parametrize('bad', ['nan', 'inf', 'backward', 'boolean', 'huge'])
async def test_clock_fault_ends_coverage_without_counting_or_rebind(producer, bad):
    p = producer
    reader = await observe(p)
    p.wire.raw()
    before = await summary(p)
    invalid = {'nan': float('nan'), 'inf': float('inf'), 'backward': reader._clock - 1,
               'boolean': True, 'huge': 10**1000}[bad]
    with patch('custom_components.casio_abl100.auto_read.monotonic_time_coarse', return_value=invalid):
        p.wire.raw()
    reader._maintain_raw(None)
    p.wire.raw()
    after = await summary(p)
    assert after['raw_record_count'] == before['raw_record_count']
    assert after['coverage_since_utc'] is None and after['clock_fault']
    assert after['coverage_gap_count'] == 1
    assert not p.wire.calls and reader._task is None


async def test_no_cached_replay_reload_resets_epoch_and_disable_removes_capture(producer):
    from tests.ha.test_auto_runtime import advertisement
    from bluetooth_data_tools import monotonic_time_coarse
    from habluetooth import get_manager
    p = producer
    ids = keys(p)
    get_manager().scanner_adv_received(advertisement(monotonic_time_coarse() + 100))
    p.wire.raw()
    reader = await observe(p)
    assert (await summary(p))['raw_record_count'] == 0
    assert not reader._faulted and reader._last_seen is None
    p.wire.raw()
    assert (await summary(p))['raw_record_count'] == 1
    old = await summary(p)
    await p.hass.config_entries.async_reload(p.watch.entry_id)
    p.session = p.watch.runtime_data
    fresh = await summary(p)
    assert fresh['raw_record_count'] == fresh['burst_count'] == fresh['coverage_gap_count'] == 0
    assert fresh['observation_started_utc'] >= old['observation_started_utc']
    assert reader._stopped and reader.window_observation()['coverage_since_utc'] is None
    form = await p.hass.config_entries.options.async_init(p.watch.entry_id)
    await p.hass.config_entries.options.async_configure(form['flow_id'], {'observe_windows': False})
    await p.hass.async_block_till_done()
    p.session = p.watch.runtime_data
    assert p.session.auto_reader is None and await summary(p) is None
    p.wire.raw()
    assert not p.wire.calls and len(p.wire.handlers) == 1 and keys(p) == ids


async def test_generation_loss_same_source_rebind_fences_old_callbacks(producer):
    p = producer
    reader = await observe(p)
    p.wire.raw()
    raw = next(cb for cb, _ in p.wire.handlers if cb != p.scanner.async_on_raw_advertisements)
    disconnected = next(iter(p.runtime.disconnect_callbacks))
    replacement = ProducerWire()
    disconnected()
    assert (await summary(p))['coverage_gap_count'] == 1
    for _ in range(3):
        p.runtime.available = False
        reader._maintain_raw(None)
    assert (await summary(p))['coverage_gap_count'] == 1
    assert (await summary(p))['coverage_since_utc'] is None
    p.runtime.available = True
    p.api.wire = replacement
    try:
        reader._maintain_raw(None)
        value = await summary(p)
        assert value['bound'] and value['coverage_since_utc'] is not None
        p.wire.raw()
        stale = BluetoothLERawAdvertisementsResponse()
        stale.advertisements.add(address=TARGET, address_type=1, data=b'\x0a\x09ABL-100WE')
        raw(stale)
        disconnected()
        assert (await summary(p))['raw_record_count'] == 1
        replacement.raw()
        assert (await summary(p))['raw_record_count'] == 2
        assert (await summary(p))['burst_count'] == 2  # Not bridged across the outage.
        assert len(p.wire.handlers) == len(replacement.handlers) == 1
        # Source identity cannot silently migrate to a different proxy.
        source = p.proxy.mac_address
        p.proxy.mac_address = '00:00:00:00:00:00'
        reader._maintain_raw(None)
        assert (await summary(p))['coverage_since_utc'] is None
        assert (await summary(p))['coverage_gap_count'] == 2
        p.proxy.mac_address = source
    finally:
        await reader.stop()
        p.api.wire = p.wire
    assert not replacement.handlers and len(p.wire.handlers) == 1
    assert not p.wire.calls and not replacement.calls


@pytest.mark.parametrize('observed_by', ['raw', 'diagnostics'])
async def test_raw_source_invalidity_revokes_binding_before_same_object_recovers(producer, observed_by):
    p = producer
    reader = await observe(p)
    p.wire.raw()
    p.runtime.available = False
    if observed_by == 'raw':
        p.wire.raw()
    else:
        assert (await summary(p))['coverage_since_utc'] is None
    p.runtime.available = True
    p.wire.raw()
    assert (await summary(p))['raw_record_count'] == 1
    assert (await summary(p))['coverage_since_utc'] is None
    assert len(p.wire.handlers) == 1
    reader._maintain_raw(None)
    p.wire.raw()
    assert (await summary(p))['raw_record_count'] == 2
    assert (await summary(p))['burst_count'] == 2
    assert (await summary(p))['coverage_gap_count'] == 1 and not p.wire.calls


@pytest.mark.parametrize('outcome', ['false', 'exception'])
async def test_failed_unload_resumes_passive_coverage_without_cached_capture(producer, outcome):
    from custom_components.casio_abl100 import async_unload_entry
    p = producer
    reader = await observe(p)
    p.wire.raw()
    async def fail(*args):
        assert reader._suspended
        p.wire.raw()
        assert (await summary(p))['raw_record_count'] == 1
        assert (await summary(p))['coverage_since_utc'] is None
        if outcome == 'exception':
            raise RuntimeError('synthetic unload failure')
        return False
    with patch.object(p.hass.config_entries, 'async_unload_platforms', side_effect=fail):
        if outcome == 'exception':
            with pytest.raises(RuntimeError):
                await async_unload_entry(p.hass, p.watch)
        else:
            assert await async_unload_entry(p.hass, p.watch) is False
    value = await summary(p)
    assert not value['suspended'] and value['coverage_since_utc'] is not None
    assert value['coverage_gap_count'] == 1
    p.wire.raw()
    assert (await summary(p))['burst_count'] == 2
    assert (await summary(p))['raw_record_count'] == 2 and not p.wire.calls


@pytest.mark.parametrize('terminal', ['unload', 'stop', 'explicit_stop'])
async def test_terminal_detaches_only_own_binding_and_prevents_late_capture(producer, terminal):
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP
    p = producer
    reader = await observe(p)
    p.wire.raw()
    raw = next(cb for cb, _ in p.wire.handlers if cb != p.scanner.async_on_raw_advertisements)
    if terminal == 'unload':
        assert await p.hass.config_entries.async_unload(p.watch.entry_id)
    elif terminal == 'stop':
        p.hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
        await p.hass.async_block_till_done()
    else:
        await reader.stop()
        await reader.stop()
    value = reader.window_observation()
    assert not value['enabled'] and not value['bound'] and value['coverage_since_utc'] is None
    assert value['coverage_gap_count'] == 1
    msg = BluetoothLERawAdvertisementsResponse()
    msg.advertisements.add(address=TARGET, address_type=1, data=b'\x0a\x09ABL-100WE')
    raw(msg)
    p.wire.emit(msg)
    assert reader.window_observation()['raw_record_count'] == 1
    assert len(p.wire.handlers) == 1 and not p.runtime.disconnect_callbacks
    assert not p.wire.calls and not p.session.tasks and reader._task is None


@pytest.mark.parametrize('outcome', ['false', 'exception'])
async def test_invalid_resume_cannot_capture_without_new_coverage(producer, outcome):
    from custom_components.casio_abl100 import async_unload_entry
    p = producer
    reader = await observe(p)
    p.wire.raw()
    assert (await summary(p))['raw_record_count'] == 1

    async def failed_unload(*args):
        assert reader._suspended
        # Source goes unavailable while unload is suspended and remains so
        # through resume. No timer/RAW/diagnostics invalidation is delivered.
        p.runtime.available = False
        if outcome == 'exception':
            raise RuntimeError('reviewer synthetic unload failure')
        return False

    with patch.object(p.hass.config_entries, 'async_unload_platforms', side_effect=failed_unload):
        if outcome == 'exception':
            with pytest.raises(RuntimeError):
                await async_unload_entry(p.hass, p.watch)
        else:
            assert await async_unload_entry(p.hass, p.watch) is False
    assert not reader._suspended and reader._window_coverage is None
    assert reader._window_gap_count == 1
    # The same source object recovers before the next one-second timer.
    p.runtime.available = True
    p.wire.raw()
    value = await summary(p)
    assert not p.wire.calls and reader._task is None
    assert value['raw_record_count'] == 1, (
        'RAW counted after invalid resume without rebind/coverage', value)
    assert value['coverage_since_utc'] is None
    reader._maintain_raw(None)
    p.wire.raw()
    value = await summary(p)
    assert value['coverage_since_utc'] is not None
    assert value['raw_record_count'] == value['burst_count'] == 2
    assert value['coverage_gap_count'] == 1 and not p.wire.calls
    assert len(p.wire.handlers) == 2  # Existing scanner plus rebound observer.
