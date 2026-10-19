"""Real HA publication, reload, STOP and private disk observations."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from tests.ha.test_native_alarm_runtime import native, ADDRESS
from tests.ha.test_unified_sync_runtime import unified, drive, press, GROUPS

# Captured before plugin fixtures patch the class. Disk tests route these only
# for this component's observation key; HA registries keep the plugin mock.
_REAL_LOAD = Store._async_load
_REAL_WRITE = Store._async_write_data
_REAL_REMOVE = Store.async_remove


def registry(p):
    return {e.unique_id: (e.entity_id, e.device_id, e.id)
            for e in er.async_get(p.hass).entities.values() if e.config_entry_id == p.watch.entry_id}


def observations(session):
    from custom_components.casio_abl100.native_sync import DESTINATIONS
    return [(getattr(session, v), getattr(session, t), getattr(session, f)) for v, t, f in DESTINATIONS]


async def read(p):
    await drive(asyncio.create_task(press(p)), p)
    await p.hass.async_block_till_done()


async def reload(p):
    assert await p.hass.config_entries.async_reload(p.watch.entry_id)
    await p.hass.async_block_till_done()
    p.session = p.watch.runtime_data


def projection(rows):
    # Compare public semantics, not raw flags deliberately absent after restore.
    a, s, z, b, u, t = [v for v, _stamp, _fresh in rows]
    return [
        (tuple((v.slot, v.hour, v.minute, v.enabled) for v in a.alarms), a.hourly_signal),
        (s.record_date, s.steps, s.daily_history),
        (z.primary_dst_on, z.primary_dst_auto, z.home, z.secondary),
        (b.time_format, b.button_tone_enabled, b.light_duration_mode),
        (u.enabled, u.adjustment_minute),
        (t.hours, t.minutes, t.seconds, t.encoded_duration_seconds),
    ]


async def test_real_button_reload_restores_six_groups_stale_without_io(unified):
    p = unified
    reg = er.async_get(p.hass)
    reg.async_update_entity('sensor.casio_abl_100we_alarm_1', new_entity_id='sensor.my_alarm', name='My alarm')
    ids = registry(p)
    assert len(ids) == 23
    from tests.ha.test_zone_entities import OLD_KEYS, ZONE_KEYS
    assert {ADDRESS + '_' + k for k in OLD_KEYS | ZONE_KEYS} <= set(ids)
    stamps = [datetime(2026, 10, 18, 12, i, 1, 123456, timezone.utc) for i in range(6)]
    from types import SimpleNamespace
    from homeassistant.util import dt as dt_util
    from unittest.mock import Mock
    with patch('custom_components.casio_abl100.native_sync.dt_util', SimpleNamespace(utcnow=Mock(side_effect=stamps), now=dt_util.now)):
        await read(p)
    before = observations(p.session)
    assert [t for _v, t, _f in before] == stamps
    assert all(f for _v, _t, f in before)
    calls = list(p.wire.calls)
    await reload(p)
    after = observations(p.session)
    assert all(v is not None for v, _t, _f in after), 'Reload lost accepted observations'
    assert projection(after) == projection(before)
    assert [t for _v, t, _f in after] == stamps
    assert all(f is False for _v, _t, f in after)
    assert registry(p) == ids
    assert p.hass.states.get('sensor.my_alarm').as_dict()['state'] == '07:35'
    assert p.hass.states.get('sensor.my_alarm').attributes['friendly_name'] == 'My alarm'
    published = {
        'sensor.my_alarm': ('07:35', 'fresh'),
        'sensor.casio_abl_100we_steps_today': ('1485', 'fresh'),
        'sensor.casio_abl_100we_home_standard_utc_offset': ('60', 'fresh'),
        'sensor.casio_abl_100we_time_format': ('24h', 'fresh'),
        'sensor.casio_abl_100we_automatic_time_adjustment': ('enabled', 'fresh'),
        'sensor.casio_abl_100we_timer_encoded_duration': ('3723', 'fresh'),
    }
    for entity, (expected, fresh_key) in published.items():
        state = p.hass.states.get(entity).as_dict()
        assert state['state'] == expected
        assert state['attributes'][fresh_key] is False
    for entity, stamp in zip(('last_alarm_read', 'last_synchronization', 'last_time_zone_read'), stamps[:3], strict=True):
        # HA's timestamp sensor formatter intentionally serializes seconds;
        # the independent session/storage assertions above retain microseconds.
        assert datetime.fromisoformat(p.hass.states.get('sensor.casio_abl_100we_' + entity).state) == stamp.replace(microsecond=0)
    for entity, stamp in zip(('time_format', 'automatic_time_adjustment', 'timer_encoded_duration'), stamps[3:], strict=True):
        assert p.hass.states.get('sensor.casio_abl_100we_' + entity).as_dict()['attributes']['last_read'] == stamp.isoformat()
    assert len(p.session.listeners) == 23 and p.session.auto_reader is None
    assert p.session.interactive_session is None and p.session.sync_result == 'never'
    assert p.session.sync_group_results == dict.fromkeys(GROUPS, 'not_attempted')
    assert p.session.sync_cleanup_result == 'not_attempted' and dict(p.watch.options) == {}
    assert p.wire.calls == calls
    p.bleak.assert_not_called()
    await read(p)
    assert all(f for _v, _t, f in observations(p.session))


@pytest.mark.parametrize('minute', [17, None])
async def test_valid_unknown_auto_time_metadata_is_visible_and_restored(unified, minute):
    p = unified
    entity = 'sensor.casio_abl_100we_automatic_time_adjustment'
    assert p.hass.states.get(entity).as_dict()['state'] == 'unavailable'
    p.wire.frames[b'\x11'] = b'\x11' + bytes(11) + bytes((1, 255 if minute is None else minute, 0))
    await read(p)
    state = p.hass.states.get(entity).as_dict()
    assert state['state'] == 'unknown', 'Valid unknown observation hidden as unavailable'
    stamp = p.session.auto_time_last_sync
    assert {k: state['attributes'][k] for k in ('fresh', 'last_read', 'adjustment_minute')} == {
        'fresh': True, 'last_read': stamp.isoformat(), 'adjustment_minute': minute}
    assert state['attributes']['options'] == ['enabled', 'disabled']
    await reload(p)
    state = p.hass.states.get(entity).as_dict()
    assert state['state'] == 'unknown'
    assert {k: state['attributes'][k] for k in ('fresh', 'last_read', 'adjustment_minute')} == {
        'fresh': False, 'last_read': stamp.isoformat(), 'adjustment_minute': minute}
    for raw, expected in [(0, 'enabled'), (128, 'disabled')]:
        p.wire.frames[b'\x11'] = b'\x11' + bytes(11) + bytes((raw, 0, 0))
        await read(p)
        assert p.hass.states.get(entity).state == expected


@pytest.fixture
async def real_observation_disk(hass_storage):
    mocked = (Store._async_load, Store._async_write_data, Store.async_remove)
    def route(real, fake):
        async def call(store, *args, **kwargs):
            return await (real if store.key.startswith('casio_abl100.observations.') else fake)(store, *args, **kwargs)
        return call
    with patch.object(Store, '_async_load', route(_REAL_LOAD, mocked[0])), patch.object(
        Store, '_async_write_data', route(_REAL_WRITE, mocked[1])
    ), patch.object(Store, 'async_remove', route(_REAL_REMOVE, mocked[2])):
        yield


@pytest.mark.parametrize('cleanup_only', [False, True])
async def test_later_group_failure_persists_only_committed_updates(unified, cleanup_only):
    from homeassistant.exceptions import HomeAssistantError
    p = unified
    await read(p)
    first = observations(p.session)
    p.wire.frames[b'\x15'] = b'\x15\xc0\0\x09\x12'
    if cleanup_only:
        def fault(stage, _request):
            if stage == 'disconnect': raise RuntimeError('PRIVATE_CLEANUP')
        p.wire.before = fault
    else:
        p.wire.failure_stage = 'get13'
    with pytest.raises(HomeAssistantError): await read(p)
    second = observations(p.session)
    committed = 6 if cleanup_only else 3
    assert projection(second)[0] != projection(first)[0]
    for i in range(committed): assert second[i][1] > first[i][1]
    for i in range(committed, 6): assert second[i][:2] == first[i][:2]
    assert p.session.sync_result != 'success'
    calls = list(p.wire.calls)
    await reload(p)
    restored = observations(p.session)
    assert all(v is not None for v, _t, _f in restored), 'Partial read lost accepted groups on reload'
    assert projection(restored) == projection(second)
    assert [t for _v, t, _f in restored] == [t for _v, t, _f in second]
    assert all(f is False for _v, _t, f in restored)
    assert p.session.sync_result == 'never' and p.session.sync_cleanup_result == 'not_attempted'
    assert p.wire.calls == calls


async def test_restored_steps_keep_record_date_history_and_midnight_boundary(unified):
    p = unified
    await read(p)
    before = p.session.record; stamp = p.session.last_sync
    callbacks = []
    from custom_components.casio_abl100.sensor import async_track_time_change as track
    def observe(*args, **kwargs):
        callbacks.append(args[1]); return track(*args, **kwargs)
    with patch('custom_components.casio_abl100.sensor.async_track_time_change', side_effect=observe):
        await reload(p)
    state = p.hass.states.get('sensor.casio_abl_100we_steps_today')
    assert state.state == '1485', 'Same-day restored steps disappeared'
    assert state.attributes['fresh'] is False and p.session.record == before
    next_day = datetime(2026, 10, 19, tzinfo=timezone.utc)
    with patch('custom_components.casio_abl100.sensor.dt_util.now', return_value=next_day):
        assert len(callbacks) == 1
        callbacks[0](next_day)
        assert p.hass.states.get(state.entity_id).state == 'unavailable'
        await reload(p)
        assert p.hass.states.get(state.entity_id).state == 'unavailable'
    assert p.session.record == before and p.session.last_sync == stamp
    assert any(d.steps == 0 for d in before.daily_history)
    assert any(d.steps is None for d in before.daily_history)


async def test_reconfigure_invalidates_old_binding_but_keeps_registry(unified):
    from homeassistant.config_entries import SOURCE_RECONFIGURE
    from homeassistant.data_entry_flow import FlowResultType
    from tests.ha.test_reconfigure_runtime import entry_for, NEW, OTHER
    p = unified
    await read(p)
    ids = registry(p); before = projection(observations(p.session))
    other = entry_for(p.hass, OTHER, OTHER, title='Other')
    assert await p.hass.config_entries.async_setup(other.entry_id)
    await p.hass.async_block_till_done()
    from custom_components.casio_abl100.observations import EntryObservations
    from tests.ha.test_observations import samples, STAMP
    # Second entry receives its own independently validated cache, not A's data.
    other.runtime_data.observations.capture('timer', samples()['timer'], STAMP)
    other_session = other.runtime_data
    other_ids = {e.unique_id: (e.entity_id, e.device_id, e.id) for e in er.async_get(p.hass).entities.values() if e.config_entry_id == other.entry_id}
    form = await p.hass.config_entries.options.async_init(p.watch.entry_id)
    result = await p.hass.config_entries.options.async_configure(form['flow_id'], {'auto_read_steps': False, 'auto_read_all': False})
    assert result['type'] is FlowResultType.CREATE_ENTRY
    await p.hass.async_block_till_done(); p.session = p.watch.runtime_data
    assert projection(observations(p.session)) == before
    assert all(f is False for _v, _t, f in observations(p.session))
    assert other.runtime_data is other_session and p.session.auto_reader is None
    calls = list(p.wire.calls)
    for target in (NEW, ADDRESS):
        with patch('custom_components.casio_abl100.config_flow.bluetooth.async_discovered_service_info', return_value=[]):
            form = await p.hass.config_entries.flow.async_init('casio_abl100', context={'source': SOURCE_RECONFIGURE, 'entry_id': p.watch.entry_id})
            result = await p.hass.config_entries.flow.async_configure(form['flow_id'], {'address': target})
        assert result['reason'] == 'reconfigure_successful'
        await p.hass.async_block_till_done(); p.session = p.watch.runtime_data
        assert all(v is None for v, _t, _f in observations(p.session))
        assert registry(p) == ids and p.session.auto_reader is None
    assert p.wire.calls == calls
    assert other.runtime_data is other_session
    assert {e.unique_id: (e.entity_id, e.device_id, e.id) for e in er.async_get(p.hass).entities.values() if e.config_entry_id == other.entry_id} == other_ids
    assert await p.hass.config_entries.async_reload(other.entry_id)
    assert other.runtime_data.timer_snapshot.encoded_duration_seconds == 0
    assert other.runtime_data.timer_last_sync == STAMP and other.runtime_data.timer_fresh is False
    assert await p.hass.config_entries.async_unload(other.entry_id)


async def test_remove_after_failed_unload_cannot_resurrect_store(unified, real_observation_disk, tmp_path):
    from pathlib import Path
    from custom_components.casio_abl100.observations import ObservationStore, storage_key
    p = unified
    p.hass.config.config_dir = str(tmp_path)
    await read(p)
    original = p.session
    assert original.observations is not None
    path = Path(p.hass.config.path('.storage', storage_key(p.watch.entry_id)))
    assert path.exists()
    from custom_components.casio_abl100 import async_unload_entry
    with patch.object(p.hass.config_entries, 'async_unload_platforms', return_value=False):
        assert not await async_unload_entry(p.hass, p.watch)
    assert not original.closed
    await read(p)  # Integration refusal retains LOADED authority and open cache.
    entered = asyncio.Event(); release = asyncio.Event()
    real_save = ObservationStore.async_save
    async def held(store, data):
        entered.set(); await release.wait(); await real_save(store, data)
    from homeassistant.helpers.entity_platform import async_get_platforms
    platforms = [platform for platform in async_get_platforms(p.hass, 'casio_abl100')
                 if platform.config_entry is p.watch]
    assert len(platforms) == 3
    with patch.object(ObservationStore, 'async_save', held):
        task = asyncio.create_task(press(p)); await drive(task, p)
        for _ in range(100):
            if entered.is_set(): break
            await asyncio.sleep(0)
        assert entered.is_set()
        with patch.object(p.hass.config_entries, 'async_unload_platforms', return_value=False):
            removal = asyncio.create_task(p.hass.config_entries.async_remove(p.watch.entry_id))
            await asyncio.sleep(0)
            assert not removal.done()
            release.set(); await removal
    assert not path.exists()
    # Cancelled/late captures cannot revive an old owner.
    original._queue_observation_save('timer')
    await original.observations.async_close()
    assert not path.exists()
    # Failed platform unload deliberately leaves real test entities to clean up.
    await p.hass.async_block_till_done()
    from custom_components.casio_abl100 import PLATFORMS
    await p.hass.config_entries.async_unload_platforms(p.watch, PLATFORMS)
    # HA's registry removal emptied these platforms before reset; async_reset
    # returns early for empty platforms, leaving their ordinary polling timer.
    for platform in platforms:
        assert not platform.entities
        platform.async_unsub_polling()
    await original.shutdown()
    await p.hass.async_block_till_done()


@pytest.mark.parametrize('kind', ['major', 'minor', 'schema', 'mismatch', 'group', 'json', 'io'])
async def test_setup_load_failures_are_nonfatal_and_never_start_ble(unified, real_observation_disk, tmp_path, kind):
    import json
    from pathlib import Path
    from custom_components.casio_abl100.observations import storage_key
    p = unified
    await read(p)
    await p.hass.config_entries.async_unload(p.watch.entry_id)
    p.hass.config.config_dir = str(tmp_path)
    key = storage_key(p.watch.entry_id)
    path = Path(p.hass.config.path('.storage', key)); path.parent.mkdir(exist_ok=True)
    from tests.ha.test_observations import payload, ENTRY, ADDRESS as TEST_ADDRESS
    from custom_components.casio_abl100.observations import address_binding
    data = payload(); data['binding'] = address_binding(p.watch.entry_id, TEST_ADDRESS)
    envelope = {'version': 1, 'minor_version': 1, 'key': key, 'data': data}
    if kind in ('major', 'minor'): envelope['version' if kind == 'major' else 'minor_version'] = 2
    elif kind == 'schema': data['schema'] = 2
    elif kind == 'mismatch': data['binding'] = '0'*64
    elif kind == 'group': data['groups']['timer']['value']['hours'] = True
    content = b'{ broken JSON' if kind == 'json' else json.dumps(envelope).encode()
    path.write_bytes(content)
    calls = list(p.wire.calls)
    if kind == 'io':
        with patch('custom_components.casio_abl100.observations.ObservationStore.async_load', side_effect=OSError('PRIVATE_IO')):
            assert await p.hass.config_entries.async_setup(p.watch.entry_id)
    else:
        assert await p.hass.config_entries.async_setup(p.watch.entry_id)
    await p.hass.async_block_till_done(); p.session = p.watch.runtime_data
    assert len(registry(p)) == 23 and p.session.auto_reader is None
    assert p.wire.calls == calls
    if kind == 'group':
        assert p.session.auto_time_snapshot.enabled is None and p.session.timer_snapshot is None
        assert p.session.auto_time_fresh is False
    else:
        assert all(v is None for v, _t, _f in observations(p.session))
    if kind in ('major', 'minor', 'schema', 'io'):
        await read(p)
        assert path.read_bytes() == content, 'Unrecognized/unread file overwritten'
    if kind == 'mismatch': assert not path.exists()
    if kind == 'json': assert list(path.parent.glob(key + '.corrupt.*'))


async def test_fresh_ha_after_stop_loads_private_disk_observations(real_observation_disk, tmp_path):
    import json
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from pathlib import Path
    from homeassistant import loader
    from homeassistant.core import CoreState
    from homeassistant.config_entries import ConfigEntryState
    from pytest_homeassistant_custom_component.common import async_test_home_assistant, MockConfigEntry
    from tests.ha.test_native_alarm_runtime import WireAPI, SOURCE
    from tests.ha.test_unified_sync_runtime import SyncWire
    from aioesphomeapi import APIVersion, BluetoothProxyFeature, DeviceInfo
    from bleak_esphome.backend.device import ESPHomeBluetoothDevice
    from custom_components.casio_abl100.observations import storage_key
    entry_id = 'diskrestartentry'
    @asynccontextmanager
    async def boot():
        async with async_test_home_assistant(event_loop=None, load_registries=True, config_dir=str(tmp_path), initial_state=CoreState.running) as hass:
            hass.data.pop(loader.DATA_CUSTOM_COMPONENTS, None)
            wire = SyncWire(); api = WireAPI('synthetic.invalid', 6053, None); api.wire = wire
            proxy = ESPHomeBluetoothDevice('synthetic', SOURCE, ble_connections_free=1, ble_connections_limit=1, available=True)
            runtime = SimpleNamespace(client=api, available=True, bluetooth_device=proxy,
                device_info=DeviceInfo(bluetooth_mac_address=SOURCE, bluetooth_proxy_feature_flags=int(BluetoothProxyFeature.REMOTE_CACHING | BluetoothProxyFeature.ACTIVE_CONNECTIONS | BluetoothProxyFeature.RAW_ADVERTISEMENTS)),
                api_version=APIVersion(1, 9), disconnect_callbacks=set())
            esp = MockConfigEntry(domain='esphome', title='Synthetic proxy', data={}); esp.add_to_hass(hass)
            esp.runtime_data = runtime; esp._async_set_state(hass, ConfigEntryState.LOADED, None)
            watch = MockConfigEntry(domain='casio_abl100', title='Casio ABL-100WE', data={'address': ADDRESS}, unique_id=ADDRESS, entry_id=entry_id)
            watch.add_to_hass(hass)
            device = SimpleNamespace(address=ADDRESS, name='ABL-100WE', details={'source': SOURCE})
            with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address', return_value=device), patch(
                'custom_components.casio_abl100.establish_connection', side_effect=AssertionError('Unexpected BLE')) as bleak, patch(
                'custom_components.casio_abl100.dt_util.now', return_value=datetime(2026, 10, 18, tzinfo=timezone.utc)):
                assert await hass.config_entries.async_setup(entry_id)
                await hass.async_block_till_done()
                p = SimpleNamespace(hass=hass, watch=watch, wire=wire, session=watch.runtime_data, bleak=bleak)
                try: yield p
                finally:
                    if hass.state is not CoreState.stopped: await hass.async_stop(force=True)
                    # Explicit stop (without unload) is the persistence gate.
                    # Remove framework entity timers only afterwards, as the
                    # ordinary plugin fixture would do before its own stop.
                    from custom_components.casio_abl100 import PLATFORMS
                    await hass.config_entries.async_unload_platforms(watch, PLATFORMS)
                    await watch.runtime_data.shutdown()
                    esp._async_set_state(hass, ConfigEntryState.NOT_LOADED, None)
    async with boot() as first:
        await read(first)
        before = observations(first.session)
        # New commit is deliberately offered during STOP, forcing the actual
        # Store stopping branch to defer its disk write to FINAL_WRITE.
        from homeassistant.const import EVENT_HOMEASSISTANT_STOP
        from homeassistant.core import callback
        from custom_components.casio_abl100.observations import ObservationStore
        started = asyncio.Event(); release = asyncio.Event(); save_states = []
        real_save = ObservationStore.async_save
        async def held_save(store, data):
            started.set(); await release.wait()
            save_states.append(store.hass.state)
            await real_save(store, data)
        @callback
        def release_on_stop(_event):
            release.set()
        first.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, release_on_stop)
        with patch.object(ObservationStore, 'async_save', held_save):
            first.session._queue_observation_save('auto_time')
            for _ in range(100):
                if started.is_set(): break
                await asyncio.sleep(0)
            assert started.is_set()
            path = Path(tmp_path, '.storage', storage_key(entry_id))
            path.unlink()  # A prior successful save cannot satisfy this gate.
            await first.hass.async_stop(force=True)  # No config-entry unload first.
        assert save_states == [CoreState.stopping]
        assert first.session.observations._closed
    path = Path(tmp_path, '.storage', storage_key(entry_id))
    assert path.exists(), 'STOP did not write private observations to disk'
    assert path.stat().st_mode & 0o777 == 0o600
    text = path.read_text(); envelope = json.loads(text)
    assert ADDRESS not in text and 'Synthetic' not in text and 'primary_dst_flags' not in text and 'power_saving' not in text
    assert set(envelope['data']['groups']) == set(GROUPS)
    assert [datetime.fromisoformat(envelope['data']['groups'][g]['observed_at']) for g in GROUPS] == [t for _v, t, _f in before]
    async with boot() as second:
        after = observations(second.session)
        assert all(v is not None for v, _t, _f in after)
        assert projection(after) == projection(before)
        assert [t for _v, t, _f in after] == [t for _v, t, _f in before]
        assert all(f is False for _v, _t, f in after)
        assert not second.wire.calls and second.session.auto_reader is None
        assert second.hass.states.get('sensor.casio_abl_100we_steps_today').state == '1485'
        await second.hass.async_stop(force=True)
