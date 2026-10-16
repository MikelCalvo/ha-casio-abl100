"""Real HA ConfigFlow class exercised without BLE hardware."""
from types import SimpleNamespace
from unittest.mock import patch
import pytest

from custom_components.casio_abl100.config_flow import CasioConfigFlow, normalize_address, watch_name


@pytest.mark.parametrize('bad', ['not a mac', 'AA:BB:CC:DD:EE:GG', 'AA:BB:CC:DD:EE:F', ''])
def test_manual_mac_validation(bad):
    assert normalize_address(bad) is None


def test_normalized_names_only_exact_casio_watch():
    assert watch_name('CASIO ABL-100WE\0   ')
    assert watch_name('CASIO ABL-100')
    assert not watch_name('CASIO G-SHOCK')
    assert not watch_name('CASIO ABL-100WE fake')


@pytest.mark.asyncio
async def test_manual_flow_does_not_connect_and_rejects_duplicate():
    flow = CasioConfigFlow()
    flow.hass = SimpleNamespace()
    flow._async_current_entries = lambda *, include_ignore=False: [SimpleNamespace(unique_id='AA:BB:CC:DD:EE:FF', data={'address':'AA:BB:CC:DD:EE:FF'})]
    with patch.object(flow, 'async_set_unique_id', return_value=None):
        result = await flow.async_step_user({'address': 'aa:bb:cc:dd:ee:ff'})
    assert result['type'] == 'abort' and result['reason'] == 'already_configured'


@pytest.mark.asyncio
async def test_user_flow_can_select_only_matching_discovered_device():
    devices = [SimpleNamespace(address='AA:BB:CC:DD:EE:FF', name='CASIO ABL-100WE\0'), SimpleNamespace(address='11:22:33:44:55:66', name='Unrelated')]
    flow = CasioConfigFlow()
    flow.hass = SimpleNamespace()
    flow._async_current_entries = lambda *, include_ignore=False: []
    with patch('custom_components.casio_abl100.config_flow.bluetooth.async_discovered_service_info', return_value=devices):
        form = await flow.async_step_user()
        assert form['type'] == 'form'
        with patch.object(flow, 'async_set_unique_id', return_value=None):
            result = await flow.async_step_user({'address': 'AA:BB:CC:DD:EE:FF'})
    assert result['type'] == 'create_entry'
    assert result['data'] == {'address': 'AA:BB:CC:DD:EE:FF'}
    assert result['title'] == 'Casio ABL-100WE'
