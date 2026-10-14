"""Real Home Assistant imports, fake BLE device/client; never contact a device."""
import asyncio
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch
import pytest

from homeassistant.exceptions import HomeAssistantError
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
import bleak_retry_connector as retry
from custom_components.casio_abl100 import WatchSession
from custom_components.casio_abl100.sensor import StepsSensor, LastSyncSensor
from custom_components.casio_abl100.button import ReadStepsButton
from tests.protocol.test_transport import FakeClient

ADDR = 'AA:BB:CC:DD:EE:FF'


@pytest.mark.asyncio
async def test_manual_read_updates_entities_only_after_success():
    device = SimpleNamespace(address=ADDR, name='CASIO ABL-100WE\0')
    client = FakeClient()
    manager = WatchSession(SimpleNamespace(), ADDR)
    steps, sync, button = StepsSensor(manager), LastSyncSensor(manager), ReadStepsButton(manager)
    assert steps.native_value is None and sync.native_value is None
    assert steps.device_info['model'] == 'ABL-100WE'
    assert steps.device_info['manufacturer'] == 'Casio'
    assert button.unique_id.endswith('_read_steps')
    with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address', return_value=device) as lookup, patch('custom_components.casio_abl100.establish_connection', return_value=client) as connect, patch('custom_components.casio_abl100.dt_util.now', return_value=__import__('datetime').datetime(2026, 8, 1)):
        await manager.read()
    lookup.assert_called_once_with(manager.hass, ADDR, connectable=True)
    assert issubclass(connect.call_args.args[0], manager.client_class)
    assert connect.call_args.args[1:] == (device, 'Casio ABL-100WE')
    assert connect.call_args.kwargs['pair'] is False
    with patch('custom_components.casio_abl100.sensor.dt_util.now', return_value=datetime(2026, 8, 1)):
        assert steps.native_value == 1485
    assert steps.extra_state_attributes['record_date'] == '2026-08-01'
    assert sync.native_value is not None
    assert client.disconnected
    assert steps.unique_id != sync.unique_id


@pytest.mark.asyncio
async def test_unreachable_or_wrong_name_keeps_last_good_value():
    manager = WatchSession(SimpleNamespace(), ADDR)
    steps = StepsSensor(manager)
    manager.record = SimpleNamespace(steps=23, record_date=date(2026, 8, 1))
    for device in (None, SimpleNamespace(address=ADDR, name='OtherWatch')):
        with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address', return_value=device), patch('custom_components.casio_abl100.establish_connection') as connect:
            with pytest.raises(HomeAssistantError):
                await manager.read()
            connect.assert_not_called()
        with patch('custom_components.casio_abl100.sensor.dt_util.now', return_value=datetime(2026, 8, 1)):
            assert steps.native_value == 23
    assert not manager.fresh


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['cancel', 'timeout', 'failure'])
async def test_real_retry_connector_cleans_partial_connection(mode):
    device = BLEDevice(ADDR, 'ABL-100', {})
    created = []
    started = asyncio.Event()

    class PartialClient:
        def __init__(self, target, **kwargs):
            assert target is device
            assert kwargs['pair'] is False
            created.append(self)
            self.disconnected = False
            self.disconnect_calls = 0

        async def connect(self, **kwargs):
            started.set()
            if mode == 'failure':
                raise BleakError('failed')
            await asyncio.Event().wait()

        async def disconnect(self):
            self.disconnect_calls += 1
            self.disconnected = True

    async def no_devices(_device):
        return []

    async def no_cache(_device):
        return False

    async def no_backoff(*args):
        return None

    manager = WatchSession(SimpleNamespace(), ADDR)
    manager.client_class = PartialClient
    before = asyncio.all_tasks()
    with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address', return_value=device), patch.object(retry, 'get_connected_devices', side_effect=no_devices), patch.object(retry, '_has_valid_services_in_cache', side_effect=no_cache), patch.object(retry, 'wait_for_disconnect', side_effect=no_backoff), patch.object(retry, 'BLEAK_SAFETY_TIMEOUT', 0.02):
        task = asyncio.create_task(manager.read())
        await asyncio.wait_for(started.wait(), 1)
        if mode == 'cancel':
            await manager.shutdown()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(HomeAssistantError):
                await asyncio.wait_for(task, 1)
    assert len(created) == 1
    assert created[0].disconnected and created[0].disconnect_calls >= 1
    assert asyncio.all_tasks() == before


@pytest.mark.asyncio
async def test_reads_are_serialized_and_unload_cancels_active_read():
    device = SimpleNamespace(address=ADDR, name='ABL-100')
    client = FakeClient([], None)
    entered = asyncio.Event()
    async def connect(*args, **kwargs):
        entered.set()
        return client
    manager = WatchSession(SimpleNamespace(), ADDR)
    with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address', return_value=device), patch('custom_components.casio_abl100.establish_connection', side_effect=connect) as mock_connect:
        first = asyncio.create_task(manager.read())
        await asyncio.wait_for(entered.wait(), 1)
        second = asyncio.create_task(manager.read())
        await asyncio.sleep(0)
        assert mock_connect.call_count == 1
        await manager.shutdown()
        for task in (first, second):
            with pytest.raises(asyncio.CancelledError):
                await task
        assert client.disconnected
        assert mock_connect.call_count == 1
