"""Sensor date rollover updates the actual state without BLE polling."""
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from custom_components.casio_abl100 import WatchSession
from custom_components.casio_abl100.sensor import StepsSensor


@pytest.mark.asyncio
async def test_midnight_refresh_invalidates_yesterdays_state_without_ble():
    session = WatchSession(SimpleNamespace(), 'AA:BB:CC:DD:EE:FF')
    session.record = SimpleNamespace(steps=1485, record_date=date(2026, 8, 1))
    sensor = StepsSensor(session)
    callbacks = []
    removed = []
    sensor.async_on_remove = lambda unsub: removed.append(unsub)
    sensor.async_write_ha_state = lambda: callbacks.append(sensor.native_value)
    with patch('custom_components.casio_abl100.sensor.async_track_time_change') as track, patch('custom_components.casio_abl100.sensor.dt_util.now', return_value=datetime(2026, 8, 2)):
        await sensor.async_added_to_hass()
        assert track.call_args.kwargs == {'hour': 0, 'minute': 0, 'second': 0}
        track.call_args.args[1](datetime(2026, 8, 2))
    assert callbacks == [None]
    assert len(removed) == 2  # listener + midnight tracker removed on unload
