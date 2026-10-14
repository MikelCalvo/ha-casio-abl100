"""Safe diagnostics omit identifying and raw BLE data."""
from datetime import datetime, date, timezone
from types import SimpleNamespace
import pytest
from custom_components.casio_abl100.diagnostics import async_get_config_entry_diagnostics
from custom_components.casio_abl100 import WatchSession


@pytest.mark.asyncio
async def test_diagnostics_are_bounded_and_do_not_include_mac_or_payload():
    manager = WatchSession(SimpleNamespace(), 'AA:BB:CC:DD:EE:FF')
    manager.record = SimpleNamespace(steps=1485, record_date=date(2026, 8, 1), raw=b'PRIVATE')
    manager.last_sync = datetime(2026, 8, 1, tzinfo=timezone.utc)
    result = await async_get_config_entry_diagnostics(None, SimpleNamespace(runtime_data=manager, data={'address': manager.address}))
    assert set(result) == {'model', 'has_successful_read', 'last_sync', 'record_date', 'fresh'}
    assert 'AA:BB' not in repr(result) and 'PRIVATE' not in repr(result)
    assert result['has_successful_read'] is True
