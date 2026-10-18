"""Session rejection and task registration without any external device IO."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.casio_abl100 import WatchSession


@pytest.mark.asyncio
@pytest.mark.parametrize('device', [None, SimpleNamespace(address='AA:BB:CC:DD:EE:FF', name='ABL-100WE', details={'backend': 'local'})])
async def test_unsupported_sources_fail_without_constructing_any_client(device):
    session = WatchSession(SimpleNamespace(), 'AA:BB:CC:DD:EE:FF')
    with patch('custom_components.casio_abl100.bluetooth.async_ble_device_from_address', return_value=device), patch('custom_components.casio_abl100.establish_connection') as connect:
        with pytest.raises(HomeAssistantError) as caught:
            await session.read_time_zones()
        assert caught.value.translation_key == 'zone_read_failed'
        assert 'Unable to read' in str(caught.value)
        assert not caught.value.translation_placeholders
        connect.assert_not_called()
        assert not session.tasks and not session.zones_fresh
    await session.shutdown()


@pytest.mark.asyncio
async def test_queued_zone_cancel_does_not_clear_active_readiness_or_freshness():
    session = WatchSession(SimpleNamespace(), 'AA:BB:CC:DD:EE:FF')
    session.zone_phase = 'reading'
    session.zones_fresh = True
    await session.lock.acquire()
    task = asyncio.create_task(session.read_time_zones())
    await asyncio.sleep(0)
    assert task in session.tasks
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert session.zone_phase == 'reading' and session.zones_fresh
    session.lock.release()
    await session.shutdown()
    assert session.zone_phase == 'closed' and session.zone_wait_expires_at is None
