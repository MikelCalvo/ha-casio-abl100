"""Real HA flow/entity tests, no device."""
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from custom_components.casio_abl100.config_flow import CasioConfigFlow
from custom_components.casio_abl100.sensor import StepsSensor
from custom_components.casio_abl100 import WatchSession
from custom_components.casio_abl100.protocol import StepRecord


@pytest.mark.asyncio
async def test_discovered_address_selection_works_without_manual_entry():
    flow = CasioConfigFlow()
    flow.hass = SimpleNamespace()
    flow._async_current_entries = lambda: []
    device = SimpleNamespace(address='AA:BB:CC:DD:EE:FF', name='CASIO ABL-100WE\0')
    with patch('custom_components.casio_abl100.config_flow.bluetooth.async_discovered_service_info', return_value=[device]):
        form = await flow.async_step_user()
        assert 'discovered_address' in str(form['data_schema'].schema)
        with patch.object(flow, 'async_set_unique_id', return_value=None):
            result = await flow.async_step_user({'discovered_address': device.address})
    assert result['data'] == {'address': device.address}


def test_yesterday_count_not_labeled_today():
    manager = WatchSession(SimpleNamespace(), 'AA:BB:CC:DD:EE:FF')
    manager.record = StepRecord(date(2026, 8, 1), 45)
    sensor = StepsSensor(manager)
    with patch('custom_components.casio_abl100.sensor.dt_util.now', return_value=datetime(2026, 8, 2)):
        assert sensor.native_value is None
        assert sensor.available is False
        assert sensor.extra_state_attributes['record_date'] == '2026-08-01'
