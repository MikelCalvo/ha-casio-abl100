"""Casio ABL-100WE read-only, explicitly initiated BLE session."""
import asyncio
import logging
import sys
from datetime import date
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.const import Platform
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from .protocol import ProtocolError
from .transport import ReadError, disconnect_client, read_record, read_alarms as read_alarm_snapshot

DOMAIN = 'casio_abl100'
PLATFORMS = (Platform.SENSOR, Platform.BUTTON)
MODEL = 'ABL-100WE'
_LOGGER = logging.getLogger(__name__)


class _AlarmReadFailed(HomeAssistantError):
    """Safe fallback text when HA translation context is unavailable."""

    def __str__(self):
        try:
            return super().__str__()
        except HomeAssistantError:
            # A standalone reader has no HA translation context; do not
            # replace the stable public error with a context-lookup failure.
            return 'Unable to read a complete alarm snapshot'


class WatchSession:
    """Entry-owned state and tasks; no global client or per-watch shared futures."""

    client_class = BleakClientWithServiceCache

    def __init__(self, hass, address):
        self.hass = hass
        self.address = address
        self.record = None
        self.last_sync = None
        self.fresh = False
        self.alarm_snapshot = None
        self.alarm_last_sync = None
        self.alarms_fresh = False
        self.lock = asyncio.Lock()
        self.tasks = set()
        self.listeners = set()
        self.alarm_delivery_handles = set()
        self.closed = False

    def subscribe(self, callback):
        self.listeners.add(callback)
        return lambda: self.listeners.discard(callback)

    def _notify(self):
        for listener in tuple(self.listeners):
            listener()

    def _schedule_alarm_delivery(self):
        """Queue observer work outside the read task; never change its result."""
        if self.closed:
            return
        handle = None

        def deliver():
            self.alarm_delivery_handles.discard(handle)
            if self.closed:
                return
            for listener in tuple(self.listeners):
                if self.closed:
                    break
                if listener not in self.listeners:
                    continue
                try:
                    listener()
                except (Exception, asyncio.CancelledError):
                    # Do not log an observer's message or traceback: both may
                    # contain private watch values or BLE addresses.
                    _LOGGER.warning('Alarm observer callback failed')

        try:
            loop = getattr(self.hass, 'loop', None) or asyncio.get_running_loop()
            handle = loop.call_soon(deliver)
        except (Exception, asyncio.CancelledError):
            # Scheduling is best-effort, including backend-only CancelledError.
            # Never log scheduler details or insert a handle that was not queued.
            _LOGGER.warning('Alarm observer scheduling failed')
            return
        self.alarm_delivery_handles.add(handle)

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

    async def read_alarms(self):
        """Manually read a complete alarm snapshot, independent of step freshness."""
        task = asyncio.current_task()
        if self.closed:
            if task is not None and task.cancelling():
                raise asyncio.CancelledError
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key='closed')
        # Registration precedes every await, including the shared-lock wait,
        # so entry shutdown also reaches queued manual reads.
        self.tasks.add(task)
        unavailable = None  # Only this particular validation error may escape.
        try:
            if task is not None and task.cancelling():
                raise asyncio.CancelledError
            async with self.lock:
                if self.closed or (task is not None and task.cancelling()):
                    raise asyncio.CancelledError
                device = bluetooth.async_ble_device_from_address(
                    self.hass, self.address, connectable=True
                )
                name = (device.name or '').rstrip('\x00 \t\r\n') if device is not None else ''
                if (device is None or device.address.upper() != self.address
                        or name not in ('ABL-100', 'ABL-100WE', 'CASIO ABL-100', 'CASIO ABL-100WE')):
                    unavailable = HomeAssistantError(
                        translation_domain=DOMAIN, translation_key='unavailable'
                    )
                    raise unavailable

                # The retry connector may construct a client and then fail before
                # returning it; only that partial client is ours to disconnect.
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
                    if client in owned:
                        owned.remove(client)
                    # The frozen transport owns the returned client's cleanup.
                    snapshot = await read_alarm_snapshot(client, timeout=12)
                finally:
                    # Preserve the original connect/read outcome over partial
                    # cleanup faults; without an original failure, cleanup must
                    # still fail the transaction instead of publishing a value.
                    original_error = sys.exc_info()[1]
                    cleanup_error = None
                    for partial_client in owned:
                        try:
                            await disconnect_client(partial_client)
                        except BaseException as error:
                            cleanup_error = error
                    if original_error is None and cleanup_error is not None:
                        raise cleanup_error
                if self.closed or (task is not None and task.cancelling()):
                    raise asyncio.CancelledError
                synced_at = dt_util.utcnow()
                if self.closed or (task is not None and task.cancelling()):
                    raise asyncio.CancelledError
                self.alarm_snapshot = snapshot
                self.alarm_last_sync = synced_at
                self.alarms_fresh = True
        except BaseException as error:
            self.alarms_fresh = False
            self._schedule_alarm_delivery()
            # shutdown or a caller cancellation outranks backend and cleanup
            # errors, even when a backend converts CancelledError to RuntimeError.
            if self.closed or (task is not None and task.cancelling()):
                raise asyncio.CancelledError from None
            if error is unavailable:
                raise
            # Backend fault details stay only in the causal chain, never in
            # public args/placeholders or logs.
            raise _AlarmReadFailed(
                translation_domain=DOMAIN, translation_key='alarm_read_failed'
            ) from error
        finally:
            self.tasks.discard(task)
        self._schedule_alarm_delivery()

    async def shutdown(self):
        self.closed = True
        for handle in self.alarm_delivery_handles:
            handle.cancel()
        self.alarm_delivery_handles.clear()
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
