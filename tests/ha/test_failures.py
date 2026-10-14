"""Failure-mode checks for a manual transaction."""
import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from bleak.exc import BleakError
from homeassistant.exceptions import HomeAssistantError
from custom_components.casio_abl100 import WatchSession, async_unload_entry
from custom_components.casio_abl100.transport import read_record


@pytest.mark.asyncio
async def test_bleak_error_is_sanitized_and_retains_last_read():
    session = WatchSession(SimpleNamespace(), 'AA:BB:CC:DD:EE:FF')
    session.record = SimpleNamespace(steps=12, record_date=datetime(2026, 8, 1).date())
    session.fresh = True
    device = SimpleNamespace(address=session.address, name='CASIO ABL-100WE')
    with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address', return_value=device), patch('custom_components.casio_abl100.establish_connection', side_effect=BleakError('PRIVATE adapter details')):
        with pytest.raises(HomeAssistantError) as error:
            await session.read()
    assert error.value.translation_key == 'read_failed'
    assert error.value.args == ('read_failed',)
    assert session.record.steps == 12
    assert not session.fresh


@pytest.mark.asyncio
async def test_failed_platform_unload_keeps_entry_operable():
    session = WatchSession(SimpleNamespace(), 'AA:BB:CC:DD:EE:FF')
    notifications = []
    session.subscribe(lambda: notifications.append(True))
    async def unload(entry, platforms):
        return False
    entry = SimpleNamespace(runtime_data=session)
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_unload_platforms=unload))
    assert await async_unload_entry(hass, entry) is False
    assert not session.closed and len(session.listeners) == 1
    device = SimpleNamespace(address=session.address, name='ABL-100')
    from tests.protocol.test_transport import FakeClient
    with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address', return_value=device), patch('custom_components.casio_abl100.establish_connection', return_value=FakeClient()), patch('custom_components.casio_abl100.dt_util.now', return_value=datetime(2026, 8, 1)):
        await session.read()
    assert session.record.steps == 1485 and notifications


@pytest.mark.asyncio
async def test_stalled_disconnect_releases_session_lock_for_next_manual_read():
    from tests.protocol.test_transport import FakeClient

    class StalledClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.cancelled = False

        async def disconnect(self):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    session = WatchSession(SimpleNamespace(), 'AA:BB:CC:DD:EE:FF')
    device = SimpleNamespace(address=session.address, name='ABL-100')
    stalled, healthy = StalledClient(), FakeClient()
    async def short_read(client, *, timeout):
        return await read_record(client, timeout=0.02)
    with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address', return_value=device), patch('custom_components.casio_abl100.establish_connection', side_effect=[stalled, healthy]) as connect, patch('custom_components.casio_abl100.dt_util.now', return_value=datetime(2026, 8, 1)), patch('custom_components.casio_abl100.read_record', side_effect=short_read):
        with pytest.raises(HomeAssistantError):
            await asyncio.wait_for(session.read(), 0.15)
        await asyncio.wait_for(session.read(), 0.15)
    assert connect.call_count == 2
    assert stalled.cancelled and healthy.disconnected
    assert session.record.steps == 1485 and not session.lock.locked()


@pytest.mark.asyncio
async def test_unload_does_not_attempt_to_disconnect_another_session():
    first = WatchSession(SimpleNamespace(), 'AA:BB:CC:DD:EE:FF')
    second = WatchSession(SimpleNamespace(), '11:22:33:44:55:66')
    await first.shutdown()
    assert second.closed is False
    assert second.tasks == set()
