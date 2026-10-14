"""Failure-mode checks for a manual transaction."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from bleak.exc import BleakError
from homeassistant.exceptions import HomeAssistantError
from custom_components.casio_abl100 import WatchSession


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
async def test_unload_does_not_attempt_to_disconnect_another_session():
    first = WatchSession(SimpleNamespace(), 'AA:BB:CC:DD:EE:FF')
    second = WatchSession(SimpleNamespace(), '11:22:33:44:55:66')
    await first.shutdown()
    assert second.closed is False
    assert second.tasks == set()
