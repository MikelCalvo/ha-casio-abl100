"""Real HA manager/entry lifecycle with synthetic BLE, never radio hardware."""
import asyncio
from datetime import datetime, timedelta
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from types import SimpleNamespace
from unittest.mock import patch

from bleak.backends.device import BLEDevice
from bluetooth_data_tools import monotonic_time_coarse
from habluetooth import get_manager
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
import pytest

from custom_components.casio_abl100 import DOMAIN
from custom_components.casio_abl100.auto_read import AutoStepReader
from tests.protocol.test_transport import FakeClient
from custom_components.casio_abl100.transport import DRSP, START

ADDRESS = 'AA:BB:CC:DD:EE:FF'


def advertisement(timestamp):
    return bluetooth.BluetoothServiceInfoBleak(
        name='CASIO ABL-100WE\0', address=ADDRESS, rssi=-50,
        manufacturer_data={}, service_data={}, service_uuids=[],
        source='00:00:00:00:00:01',
        device=BLEDevice(ADDRESS, 'CASIO ABL-100WE\0', {'path': '/synthetic/local'}),
        advertisement=None, connectable=True, time=timestamp, tx_power=None,
    )


@pytest.mark.asyncio
async def test_actual_manager_dedupes_payload_but_metadata_can_open_next_window(
    hass: HomeAssistant, enable_custom_integrations, enable_bluetooth,
):
    """The timer consumes public producer timestamps, not invented callbacks."""
    entry = MockConfigEntry(domain=DOMAIN, title='Casio ABL-100WE',
                            data={'address': ADDRESS}, unique_id=ADDRESS,
                            options={'auto_read_steps': True})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    session = entry.runtime_data
    observer_packets = []
    cancel_observer = bluetooth.async_register_callback(
        hass, lambda service_info, change: observer_packets.append(service_info.time),
        {'address': ADDRESS, 'connectable': True}, bluetooth.BluetoothScanningMode.PASSIVE,
        replay=bluetooth.BluetoothCallbackReplay.DISABLED,
    )
    now = monotonic_time_coarse()
    clock = SimpleNamespace(now=now)
    clients = [FakeClient(), FakeClient()]
    reader = getattr(session, 'auto_reader', None)
    assert reader is not None, 'Enabled entry must own its native reader'
    with patch('custom_components.casio_abl100.auto_read.monotonic_time_coarse',
               side_effect=lambda: clock.now), patch('custom_components.casio_abl100.establish_connection',
               side_effect=clients) as connect, patch('custom_components.casio_abl100.dt_util.now',
               return_value=datetime(2026, 8, 1)):
        reader.start()
        manager = get_manager()
        manager.scanner_adv_received(advertisement(clock.now))
        await asyncio.wait_for(reader._task, 2)
        await hass.async_block_till_done()
        assert connect.call_count == 1 and session.record.steps == 1485
        assert len(observer_packets) == 1
        for delta in (10, 20):
            clock.now = now + delta
            manager.scanner_adv_received(advertisement(clock.now))
            async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
        assert len(observer_packets) == 1  # Identical packets do not invoke ordinary callbacks.
        assert bluetooth.async_last_service_info(hass, ADDRESS, True).time == now + 20
        assert reader._task is None and connect.call_count == 1
        clock.now = now + 60
        manager.scanner_adv_received(advertisement(clock.now))
        assert len(observer_packets) == 1
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
        assert reader._task is not None
        await asyncio.wait_for(reader._task, 2)
        assert connect.call_count == 2
        assert session.alarm_snapshot is None and session.alarm_last_sync is None
        assert len(session.record.daily_history) == 7
        assert all(client.writes == [(DRSP, START)] for client in clients)
        assert all(client.disconnected for client in clients)
        await reader.stop()
    cancel_observer()
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_option_enabled_entry_owns_reader_and_unload_removes_observation(
    hass: HomeAssistant, enable_custom_integrations, enable_bluetooth,
):
    entry = MockConfigEntry(domain=DOMAIN, title='Casio ABL-100WE',
                            data={'address': ADDRESS}, unique_id=ADDRESS,
                            options={'auto_read_steps': True})
    entry.add_to_hass(hass)
    now = monotonic_time_coarse()
    client = FakeClient()
    with patch('custom_components.casio_abl100.establish_connection', return_value=client) as connect, patch(
        'custom_components.casio_abl100.dt_util.now', return_value=datetime(2026, 8, 1)
    ), patch('custom_components.casio_abl100.auto_read.monotonic_time_coarse', return_value=now):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        connect.assert_not_called()
        assert getattr(entry.runtime_data, 'auto_reader', None) is not None, 'Enabled entry must own its native reader'
        reader = entry.runtime_data.auto_reader
        get_manager().scanner_adv_received(advertisement(now))
        assert reader._task is not None
        await asyncio.wait_for(reader._task, 2)
        await hass.async_block_till_done()
        assert connect.call_count == 1 and entry.runtime_data.record.steps == 1485
        ids = [state.entity_id for state in hass.states.async_all()
               if 'casio_abl_100we' in state.entity_id]
        assert len(ids) == 11
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert reader._stopped and reader._cancel_callback is None and reader._cancel_timer is None
        get_manager().scanner_adv_received(advertisement(now + 60))
        reader._sample(None)
        assert connect.call_count == 1


@pytest.mark.asyncio
async def test_default_entry_has_no_observer_or_automatic_read(
    hass: HomeAssistant, enable_custom_integrations, enable_bluetooth,
):
    entry = MockConfigEntry(domain=DOMAIN, title='Casio ABL-100WE',
                            data={'address': ADDRESS}, unique_id=ADDRESS)
    entry.add_to_hass(hass)
    with patch('custom_components.casio_abl100.establish_connection') as connect:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert getattr(entry.runtime_data, 'auto_reader', None) is None
        get_manager().scanner_adv_received(advertisement(monotonic_time_coarse()))
        await hass.async_block_till_done()
        connect.assert_not_called()
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['false', 'exception'])
async def test_failed_platform_unload_restores_observer_without_resurrecting_burst(
    hass: HomeAssistant, enable_custom_integrations, enable_bluetooth, failure,
):
    from custom_components.casio_abl100 import async_unload_entry
    entry = MockConfigEntry(domain=DOMAIN, title='Casio ABL-100WE',
                            data={'address': ADDRESS}, unique_id=ADDRESS,
                            options={'auto_read_steps': True})
    entry.add_to_hass(hass)
    now = monotonic_time_coarse()
    with patch('custom_components.casio_abl100.establish_connection') as connect:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        session = entry.runtime_data
        reader = getattr(session, 'auto_reader', None)
        assert reader is not None, 'Enabled entry must own its native reader'
        async def refuse(*args):
            assert reader._suspended
            get_manager().scanner_adv_received(advertisement(now))
            if failure == 'exception':
                raise RuntimeError('UNLOAD_FIXTURE_FAILURE')
            return False
        with patch.object(hass.config_entries, 'async_unload_platforms', side_effect=refuse):
            if failure == 'exception':
                with pytest.raises(RuntimeError):
                    await async_unload_entry(hass, entry)
            else:
                assert await async_unload_entry(hass, entry) is False
        assert not reader._suspended and not reader._stopped and not session.closed
        reader._sample(None)
        await hass.async_block_till_done()
        connect.assert_not_called()
        assert session.listeners
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_options_update_reloads_selected_entry_and_preserves_ids(
    hass: HomeAssistant, enable_custom_integrations, enable_bluetooth,
):
    from homeassistant.helpers import entity_registry as er
    entry = MockConfigEntry(domain=DOMAIN, title='Casio ABL-100WE',
                            data={'address': ADDRESS}, unique_id=ADDRESS)
    entry.add_to_hass(hass)
    other_address = '11:22:33:44:55:66'
    other = MockConfigEntry(domain=DOMAIN, title='Other Casio ABL-100WE',
                            data={'address': other_address}, unique_id=other_address,
                            options={'auto_read_steps': True})
    with patch('custom_components.casio_abl100.establish_connection') as connect:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        # Initial domain setup loads all entries already registered with HA.
        other.add_to_hass(hass)
        assert await hass.config_entries.async_setup(other.entry_id)
        await hass.async_block_till_done()
        old_session = entry.runtime_data
        other_session, other_reader = other.runtime_data, other.runtime_data.auto_reader
        registry = er.async_get(hass)
        def entity_ids(owner):
            return {entity.unique_id: entity.entity_id for entity in registry.entities.values()
                    if entity.config_entry_id == owner.entry_id}
        ids, other_ids = entity_ids(entry), entity_ids(other)
        assert len(ids) == len(other_ids) == 11
        with patch.object(hass.config_entries, 'async_reload',
                          wraps=hass.config_entries.async_reload) as reload_entry:
            result = await hass.config_entries.options.async_init(entry.entry_id)
            await hass.config_entries.options.async_configure(result['flow_id'], {'auto_read_steps': True})
            await hass.async_block_till_done()
            reload_entry.assert_called_once_with(entry.entry_id)
            assert entry.runtime_data is not old_session, 'Options must apply via selected-entry reload'
            assert old_session.closed
            assert entry.runtime_data.auto_reader is not None
            assert entity_ids(entry) == ids and entity_ids(other) == other_ids
            assert other.runtime_data is other_session and other_session.auto_reader is other_reader
            assert not other_session.closed and not other_reader._stopped
            connect.assert_not_called()
            active_session = entry.runtime_data
            active = active_session.auto_reader
            reload_entry.reset_mock()
            result = await hass.config_entries.options.async_init(entry.entry_id)
            await hass.config_entries.options.async_configure(result['flow_id'], {'auto_read_steps': False})
            await hass.async_block_till_done()
            reload_entry.assert_called_once_with(entry.entry_id)
            assert active_session.closed and entry.runtime_data is not active_session
            assert active._stopped and entry.runtime_data.auto_reader is None
            assert entity_ids(entry) == ids and entity_ids(other) == other_ids
            assert len(entity_ids(entry)) == len(entity_ids(other)) == 11
            assert other.runtime_data is other_session and other_session.auto_reader is other_reader
            assert not other_session.closed and not other_reader._stopped
        connect.assert_not_called()
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert await hass.config_entries.async_unload(other.entry_id)


@pytest.mark.asyncio
async def test_unload_cancels_owned_transfer_before_publishing_and_releases_client(
    hass: HomeAssistant, enable_custom_integrations, enable_bluetooth,
):
    entry = MockConfigEntry(domain=DOMAIN, title='Casio ABL-100WE',
                            data={'address': ADDRESS}, unique_id=ADDRESS,
                            options={'auto_read_steps': True})
    entry.add_to_hass(hass)
    client = FakeClient()
    client.block = asyncio.Event()
    entered = asyncio.Event()
    original_write = client.write_gatt_char
    async def held_write(*args, **kwargs):
        entered.set()
        await original_write(*args, **kwargs)
    client.write_gatt_char = held_write
    with patch('custom_components.casio_abl100.establish_connection', return_value=client) as connect, patch(
        'custom_components.casio_abl100.dt_util.now', return_value=datetime(2026, 8, 1)
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        reader = getattr(entry.runtime_data, 'auto_reader', None)
        assert reader is not None, 'Enabled entry must own its native reader'
        get_manager().scanner_adv_received(advertisement(monotonic_time_coarse()))
        await asyncio.wait_for(entered.wait(), 2)
        session, task = entry.runtime_data, reader._task
        assert session.record is None
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert client.disconnected and task.done()
        assert reader._stopped and session.closed and not session.tasks
        assert session.record is None and session.last_sync is None and not session.fresh
        assert connect.call_count == 1


@pytest.mark.asyncio
async def test_manual_service_occupies_window_without_queued_automatic_duplicate(
    hass: HomeAssistant, enable_custom_integrations, enable_bluetooth,
):
    entry = MockConfigEntry(domain=DOMAIN, title='Casio ABL-100WE',
                            data={'address': ADDRESS}, unique_id=ADDRESS,
                            options={'auto_read_steps': True})
    entry.add_to_hass(hass)
    entered, release = asyncio.Event(), asyncio.Event()
    client = FakeClient()
    clock = SimpleNamespace(now=monotonic_time_coarse())
    async def connector(*args, **kwargs):
        entered.set()
        await release.wait()
        return client
    with patch('custom_components.casio_abl100.establish_connection', side_effect=connector) as connect, patch(
        'custom_components.casio_abl100.dt_util.now', return_value=datetime(2026, 8, 1)
    ), patch('custom_components.casio_abl100.auto_read.monotonic_time_coarse',
             side_effect=lambda: clock.now):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        reader = getattr(entry.runtime_data, 'auto_reader', None)
        assert reader is not None, 'Enabled entry must own its native reader'
        # Seed the public manager cache without launching an automatic task.
        reader.suspend()
        get_manager().scanner_adv_received(advertisement(clock.now))
        reader.resume()
        seed_seen = reader._last_seen
        manual = asyncio.create_task(hass.services.async_call(
            'button', 'press', {'entity_id': 'button.casio_abl_100we_read_steps'}, blocking=True,
        ))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            assert entry.runtime_data.lock.locked() and entry.runtime_data.tasks
            # An identical payload updates manager metadata without a callback.
            clock.now += 60
            get_manager().scanner_adv_received(advertisement(clock.now))
            assert reader._last_seen == seed_seen
            async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
            assert reader._last_seen == clock.now and reader._claimed
            assert reader._task is None and connect.call_count == 1
        finally:
            release.set()
            await asyncio.wait_for(manual, 2)
        await hass.async_block_till_done()
        clock.now += 1
        get_manager().scanner_adv_received(advertisement(clock.now))
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=4))
        await hass.async_block_till_done()
        assert connect.call_count == 1 and reader._task is None
        assert entry.runtime_data.record.steps == 1485 and client.disconnected
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_background_connector_failure_is_sanitized_and_consumes_burst(
    hass: HomeAssistant, enable_custom_integrations, enable_bluetooth, caplog,
):
    entry = MockConfigEntry(domain=DOMAIN, title='Casio ABL-100WE',
                            data={'address': ADDRESS}, unique_id=ADDRESS,
                            options={'auto_read_steps': True})
    entry.add_to_hass(hass)
    clock = SimpleNamespace(now=monotonic_time_coarse())
    client = FakeClient()
    private = 'PRIVATE_RUNTIME_ADDRESS_AA_BB_CC_DD_EE_FF'
    with patch('custom_components.casio_abl100.establish_connection',
               side_effect=[client, RuntimeError(private)]) as connect, patch(
        'custom_components.casio_abl100.dt_util.now', return_value=datetime(2026, 8, 1)
    ), patch('custom_components.casio_abl100.auto_read.monotonic_time_coarse',
             side_effect=lambda: clock.now):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        session = entry.runtime_data
        reader = session.auto_reader
        manager = get_manager()
        manager.scanner_adv_received(advertisement(clock.now))
        await asyncio.wait_for(reader._task, 2)
        await hass.async_block_till_done()
        record, stamp = session.record, session.last_sync
        assert record.steps == 1485 and session.fresh and client.disconnected
        caplog.clear()
        clock.now += 60
        manager.scanner_adv_received(advertisement(clock.now))
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
        owned = reader._task
        assert owned is not None
        await asyncio.wait_for(owned, 2)
        await hass.async_block_till_done()
        assert owned.done() and not owned.cancelled() and owned.exception() is None
        assert session.record is record and session.last_sync is stamp and not session.fresh
        assert session.alarm_snapshot is None and session.alarm_last_sync is None
        assert not session.tasks and not session.lock.locked() and reader._task is None
        failures = [item for item in caplog.records
                    if item.name.startswith('custom_components.casio_abl100')]
        assert [(item.getMessage(), item.exc_info) for item in failures] == [
            ('Automatic step read failed', None),
        ]
        assert private not in caplog.text and 'RuntimeError' not in caplog.text
        for delta in (1, 2):
            clock.now += delta
            manager.scanner_adv_received(advertisement(clock.now))
            async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=4 + delta))
            await hass.async_block_till_done()
            assert reader._task is None and connect.call_count == 2
        assert len(client.writes) == 1 and client.writes == [(DRSP, START)]
        assert await hass.config_entries.async_unload(entry.entry_id)
