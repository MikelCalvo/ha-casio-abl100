"""Safe diagnostics: deliberately no address, raw data or exception text."""
from homeassistant.util import dt as dt_util


async def async_get_config_entry_diagnostics(hass, entry):
    manager = entry.runtime_data
    return {
        'model': 'ABL-100WE',
        'has_successful_read': manager.last_sync is not None,
        'last_sync': manager.last_sync.isoformat() if manager.last_sync else None,
        'record_date': manager.record.record_date.isoformat() if manager.record else None,
        'fresh': manager.fresh and bool(manager.record) and manager.record.record_date == dt_util.now().date(),
    }
