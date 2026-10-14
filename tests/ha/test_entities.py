"""Real Home Assistant imports, fake BLE device/client; never contact a device."""
import asyncio
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch
import pytest

from homeassistant.exceptions import HomeAssistantError
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
    assert connect.call_args.args[:3] == (manager.client_class, device, 'Casio ABL-100WE')
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
