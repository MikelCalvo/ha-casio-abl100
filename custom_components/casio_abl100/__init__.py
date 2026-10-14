"""Casio ABL-100WE read-only, explicitly initiated BLE session."""
import asyncio
from datetime import date
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.const import Platform
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from .protocol import ProtocolError
from .transport import ReadError, disconnect_client, read_record

DOMAIN = 'casio_abl100'
PLATFORMS = (Platform.SENSOR, Platform.BUTTON)
MODEL = 'ABL-100WE'


class WatchSession:
    """Entry-owned state and tasks; no global client or per-watch shared futures."""

    client_class = BleakClientWithServiceCache

    def __init__(self, hass, address):
        self.hass = hass
        self.address = address
        self.record = None
        self.last_sync = None
        self.fresh = False
        self.lock = asyncio.Lock()
        self.tasks = set()
        self.listeners = set()
        self.closed = False

    def subscribe(self, callback):
        self.listeners.add(callback)
        return lambda: self.listeners.discard(callback)

    def _notify(self):
        for listener in tuple(self.listeners):
            listener()

    async def read(self):
        if self.closed:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key='closed')
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            async with self.lock:
                if self.closed:
                    raise asyncio.CancelledError
                device = bluetooth.async_ble_device_from_address(
                    self.hass, self.address, connectable=True
                )
                name = (device.name or '').rstrip('\x00 \t\r\n') if device is not None else ''
                if device is None or device.address.upper() != self.address or name not in ('ABL-100', 'ABL-100WE', 'CASIO ABL-100', 'CASIO ABL-100WE'):
                    raise HomeAssistantError(translation_domain=DOMAIN, translation_key='unavailable')
                owned = []
                base_client_class = self.client_class

                class OwnedClient(base_client_class):
                    def __init__(self, *args, **kwargs):
                        super().__init__(*args, **kwargs)
                        owned.append(self)

                try:
                    client = await asyncio.wait_for(establish_connection(
                        OwnedClient, device, 'Casio ABL-100WE',
                        pair=False, max_attempts=2, timeout=10,
                    ), timeout=16)
                    # read_record owns cleanup once the connector returns.
                    if client in owned:
                        owned.remove(client)
                    record = await read_record(client, timeout=12)
                    if record.record_date != dt_util.now().date():
                        raise HomeAssistantError(translation_domain=DOMAIN, translation_key='stale_date')
                except (ReadError, ProtocolError, TimeoutError, OSError, RuntimeError, BleakError) as error:
                    raise HomeAssistantError(translation_domain=DOMAIN, translation_key='read_failed') from error
                finally:
                    for partial_client in owned:
                        try:
                            await disconnect_client(partial_client)
                        except (Exception, asyncio.CancelledError):
                            # The original connect outcome/cancellation takes priority.
                            pass
                self.record = record
                self.last_sync = dt_util.utcnow()
                self.fresh = True
                self._notify()
        except (HomeAssistantError, asyncio.CancelledError):
            self.fresh = False
            self._notify()
            raise
        finally:
            self.tasks.discard(task)

    async def shutdown(self):
        self.closed = True
        tasks = set(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.listeners.clear()


async def async_setup_entry(hass, entry):
    manager = WatchSession(hass, entry.data['address'])
    entry.runtime_data = manager
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass, entry):
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.shutdown()
    return True
