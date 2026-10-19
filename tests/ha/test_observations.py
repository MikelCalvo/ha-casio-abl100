"""Synthetic semantic codec and independently owned writer regressions."""
import asyncio
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from importlib import import_module, util
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from custom_components.casio_abl100 import WatchSession
from custom_components.casio_abl100.alarms import Alarm, AlarmSnapshot
from custom_components.casio_abl100.protocol import DailySteps, StepRecord
from custom_components.casio_abl100.settings import (
    AutoTimeSettings, BasicSettings, TimerConfiguration, ZoneConfiguration, ZoneSettings,
)

ENTRY = 'syntheticentry'
ADDRESS = 'AA:BB:CC:DD:EE:FF'
STAMP = datetime(2026, 10, 18, 12, 34, 56, 123456, timezone.utc)
GROUPS = ('alarms', 'steps', 'zones', 'basic_settings', 'auto_time', 'timer')


def api():
    name = 'custom_components.casio_abl100.observations'
    assert util.find_spec(name) is not None, 'Missing durable observations API'
    return import_module(name)


def samples():
    day = date(2026, 10, 18)
    return {
        'alarms': AlarmSnapshot(tuple(Alarm(i, 7+i, 35, i == 1, 197, 99) for i in range(1, 6)), True),
        'steps': StepRecord(day, 0, tuple(DailySteps(day-timedelta(days=i), None if i == 2 else 0) for i in range(1, 8))),
        'zones': ZoneSettings(255, None, None, ZoneConfiguration(0, -1920, 3825, 255), ZoneConfiguration(1, 1905, 0, 0)),
        'basic_settings': BasicSettings('24h', True, 'long', True),
        'auto_time': AutoTimeSettings(None, 17),
        'timer': TimerConfiguration(0, 0, 0, 0),
    }


def payload():
    m = api()
    return {'schema': 1, 'binding': m.address_binding(ENTRY, ADDRESS),
            'groups': {g: m.encode_group(g, v, STAMP + timedelta(microseconds=i)) for i, (g, v) in enumerate(samples().items())}}


def test_six_group_semantic_roundtrip_keeps_original_timestamps():
    m = api()
    p = payload()
    restored = m.decode_payload(p, ENTRY, ADDRESS.lower())
    assert set(restored) == set(GROUPS)
    for g, (value, stamp) in restored.items():
        assert m.encode_group(g, value, stamp) == p['groups'][g]
        assert stamp.utcoffset() == timedelta(0)
    assert restored['steps'][0].steps == 0
    assert restored['steps'][0].daily_history[1].steps is None
    assert restored['auto_time'][0].enabled is None
    assert restored['timer'][0].encoded_duration_seconds == 0
    for g, forbidden in [('alarms', 'flags'), ('zones', 'primary_dst_flags'), ('basic_settings', 'power_saving_enabled')]:
        assert not hasattr(restored[g][0], forbidden)
    assert not hasattr(restored['alarms'][0].alarms[0], 'aux')
    assert m.storage_key(ENTRY) == 'casio_abl100.observations.' + ENTRY
    assert m.address_binding(ENTRY, ADDRESS) != m.address_binding('other', ADDRESS)


@pytest.mark.parametrize('group,mutate', [
    ('steps', lambda x: x['value'].__setitem__('steps', True)),
    ('steps', lambda x: x['value']['daily_history'].pop()),
    ('steps', lambda x: x['value']['daily_history'][0].__setitem__('record_date', '2026-10-10')),
    ('steps', lambda x: x['value']['daily_history'][0].__setitem__('steps', False)),
    ('steps', lambda x: x['value'].__setitem__('record_date', '1999-10-18')),
    ('steps', lambda x: x['value'].__setitem__('record_date', '2026-W42-7')),
    ('alarms', lambda x: x['value']['alarms'].pop()),
    ('alarms', lambda x: x['value']['alarms'][0].__setitem__('slot', 2)),
    ('alarms', lambda x: x['value']['alarms'][0].__setitem__('flags', 7)),
    ('alarms', lambda x: x['value'].__setitem__('hourly_signal', 1)),
    ('zones', lambda x: x['value']['home'].__setitem__('standard_utc_offset_minutes', 16)),
    ('zones', lambda x: x['value'].__setitem__('primary_dst_auto', False)),
    ('basic_settings', lambda x: x['value'].__setitem__('power_saving_enabled', True)),
    ('basic_settings', lambda x: x['value'].__setitem__('time_format', 'PRIVATE')),
    ('auto_time', lambda x: x['value'].__setitem__('enabled', 0)),
    ('auto_time', lambda x: x['value'].__setitem__('adjustment_minute', 60)),
    ('timer', lambda x: x['value'].__setitem__('encoded_duration_seconds', 1)),
    ('timer', lambda x: x['value'].__setitem__('hours', True)),
    ('timer', lambda x: x.__setitem__('observed_at', '2026-10-18T12:34:56')),
    ('timer', lambda x: x.__setitem__('observed_at', '2026-10-18T12:34:56+01:00')),
    ('timer', lambda x: x.__setitem__('extra', 'PRIVATE')),
])
def test_rejects_corrupt_group_without_losing_other_groups(group, mutate):
    m = api()
    p = payload()
    mutate(p['groups'][group])
    restored = m.decode_payload(p, ENTRY, ADDRESS)
    assert set(restored) == set(GROUPS) - {group}
    for g, (v, t) in restored.items():
        assert m.encode_group(g, v, t) == p['groups'][g]


@pytest.mark.parametrize('change', [
    lambda p: p.__setitem__('schema', True), lambda p: p.__setitem__('schema', 2),
    lambda p: p.__setitem__('binding', '0'*64), lambda p: p.__setitem__('extra', 'PRIVATE'),
    lambda p: p.__setitem__('groups', []),
])
def test_schema_and_address_binding_fail_closed(change):
    m = api()
    p = payload(); change(p)
    assert m.decode_payload(p, ENTRY, ADDRESS) == {}
    assert m.decode_payload(payload(), 'other', ADDRESS) == {}
    assert m.decode_payload(payload(), ENTRY, 'AA:BB:CC:DD:EE:00') == {}
    for bad in ('ſA:BB:CC:DD:EE:FF', 'AA:BB:CC:DD:EE:ＦＦ', '', None):
        assert m.decode_payload(payload(), ENTRY, bad) == {}
    p = payload(); p['groups']['unknown'] = {'PRIVATE': 'CANARY'}
    assert set(m.decode_payload(p, ENTRY, ADDRESS)) == set(GROUPS)


class Entry:
    def __init__(self): self.created = []
    def async_create_task(self, hass, coro, name, *, eager_start):
        assert name == 'Casio observation save' and eager_start is False
        task = asyncio.create_task(coro)
        self.created.append(task)
        return task


def cache():
    m = api()
    # Queue tests fake only Store, never the product writer or codec.
    with patch.object(m.ObservationStore, '__init__', return_value=None):
        return m.EntryObservations(SimpleNamespace(data={}, loop=asyncio.get_running_loop()), ENTRY, ADDRESS, Entry())


@pytest.mark.asyncio
async def test_capture_coalesces_and_close_joins_original_writer():
    m = api(); started = asyncio.Event(); release = asyncio.Event(); writes = []
    async def save(store, p):
        writes.append(deepcopy(p)); started.set(); await release.wait()
    with patch.object(m.ObservationStore, 'async_save', save):
        c = cache()
        c.capture('auto_time', AutoTimeSettings(True, 1), STAMP)
        await asyncio.sleep(0)
        assert started.is_set(), 'Capture did not schedule a writer'
        c.capture('auto_time', AutoTimeSettings(False, 2), STAMP)
        c.capture('auto_time', AutoTimeSettings(None, 17), STAMP)
        c.capture('timer', samples()['timer'], STAMP)
        close = asyncio.create_task(c.async_close()); await asyncio.sleep(0)
        close.cancel(); await asyncio.sleep(0)
        assert not close.done() and len(c.entry.created) == 1
        assert not c.entry.created[0].cancelling()
        release.set()
        with pytest.raises(asyncio.CancelledError): await close
        assert len(writes) == 2
        assert writes[-1]['groups']['auto_time']['value'] == {'enabled': None, 'adjustment_minute': 17}
        assert set(writes[-1]['groups']) == {'timer', 'auto_time'}
        c.capture('steps', samples()['steps'], STAMP)
        await c.async_close()
        assert len(writes) == 2 and all(t.done() for t in c.entry.created)


@pytest.mark.asyncio
async def test_save_fault_does_not_escape_or_drop_last_good(caplog):
    m = api(); writes = []; fail = True
    async def save(store, p):
        writes.append(deepcopy(p))
        if fail: raise RuntimeError('PRIVATE_SAVE_CANARY')
    with patch.object(m.ObservationStore, 'async_save', save):
        c = cache(); c.capture('auto_time', samples()['auto_time'], STAMP)
        await asyncio.sleep(0); await asyncio.sleep(0)
        fail = False
        c.capture('auto_time', AutoTimeSettings(False, 60), STAMP)
        c.capture('timer', samples()['timer'], STAMP)
        await c.async_close()
        assert writes[-1]['groups']['auto_time']['value'] == {'enabled': None, 'adjustment_minute': 17}
        assert 'PRIVATE' not in caplog.text
        assert all(t.done() for t in c.entry.created)
    c = cache()
    with patch.object(c.entry, 'async_create_task', side_effect=RuntimeError('PRIVATE_SCHEDULE')):
        c.capture('timer', samples()['timer'], STAMP)
    with patch.object(m.ObservationStore, 'async_save', save): await c.async_close()
    assert 'PRIVATE' not in caplog.text


@pytest.mark.asyncio
async def test_remove_joins_write_and_blocks_late_recreation():
    m = api(); started = asyncio.Event(); release = asyncio.Event(); disk = {}
    async def save(store, p): started.set(); await release.wait(); disk.update(p)
    async def remove(store): disk.clear()
    with patch.object(m.ObservationStore, 'async_save', save), patch.object(m.ObservationStore, 'async_remove', remove):
        c = cache(); c.capture('timer', samples()['timer'], STAMP)
        await asyncio.sleep(0)
        assert started.is_set(), 'Capture did not schedule a writer'
        removal = asyncio.create_task(c.async_remove()); await asyncio.sleep(0)
        assert not removal.done()
        release.set(); await removal
        c.capture('timer', samples()['timer'], STAMP)
        await c.async_close()
        assert not disk and all(t.done() for t in c.entry.created)


@pytest.mark.asyncio
async def test_close_attempts_pending_fault_once_without_retry_loop():
    m = api(); writes = []
    async def fail(store, data):
        writes.append(data)
        raise RuntimeError('PRIVATE_PERSISTENT_FAILURE')
    with patch.object(m.ObservationStore, 'async_save', fail):
        c = cache(); c.capture('timer', samples()['timer'], STAMP)
        await asyncio.sleep(0); await asyncio.sleep(0)
        assert len(writes) == 1
        await c.async_close()
        assert len(writes) == 2, 'Close retried a persistent fault more than once'
        await c.async_close()
        assert len(writes) == 2
        assert all(task.done() for task in c.entry.created)


def test_session_capture_failure_is_observation_only(caplog):
    s = WatchSession(None, ADDRESS)
    s.observations = SimpleNamespace(capture=lambda *args: (_ for _ in ()).throw(asyncio.CancelledError('PRIVATE')))
    s.auto_time_snapshot = samples()['auto_time']; s.auto_time_last_sync = STAMP; s.auto_time_fresh = True
    assert callable(getattr(s, '_queue_observation_save', None)), 'Missing safe capture seam'
    s._queue_observation_save('auto_time')
    assert s.auto_time_fresh and s.auto_time_last_sync == STAMP
    assert 'PRIVATE' not in caplog.text


@pytest.mark.asyncio
async def test_store_backend_cancellation_is_contained_and_close_stays_finite(caplog):
    m = api(); calls = []
    async def cancel(store, data):
        calls.append(data)
        raise asyncio.CancelledError('PRIVATE_BACKEND')
    with patch.object(m.ObservationStore, 'async_save', cancel):
        c = cache(); c.capture('auto_time', samples()['auto_time'], STAMP)
        await asyncio.sleep(0); await c.async_close()
        assert len(calls) == 2
        assert not asyncio.current_task().cancelling()
        assert all(t.done() and not t.cancelled() for t in c.entry.created)
    assert 'PRIVATE' not in caplog.text


@pytest.mark.asyncio
async def test_loaded_cache_sanitizes_future_and_invalid_groups_without_save():
    m = api(); writes = []
    async def save(store, data): writes.append(deepcopy(data))
    p = payload(); p['groups']['timer']['value']['hours'] = True
    p['groups']['unknown'] = {'private': 'PRIVATE_CANARY'}
    with patch.object(m.ObservationStore, 'async_load', return_value=p), patch.object(m.ObservationStore, 'async_save', save):
        c = cache(); s = WatchSession(None, ADDRESS)
        await c.async_load_into(s)
        assert s.timer_snapshot is None and s.auto_time_snapshot.enabled is None
        assert not writes and s.auto_time_last_sync == STAMP + timedelta(microseconds=4)
        c.capture('timer', samples()['timer'], STAMP)
        await c.async_close()
        assert set(writes[-1]['groups']) == set(GROUPS)
        assert 'PRIVATE' not in str(writes)
    writes.clear(); p['schema'] = 2
    with patch.object(m.ObservationStore, 'async_load', return_value=p), patch.object(m.ObservationStore, 'async_save', save):
        c = cache(); s = WatchSession(None, ADDRESS)
        await c.async_load_into(s)
        c.capture('timer', samples()['timer'], STAMP)
        await c.async_close()
        assert s.auto_time_snapshot is None and not writes
