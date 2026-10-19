"""Casio ABL-100WE read-only, explicitly initiated BLE session."""
import asyncio
import logging
import sys
from datetime import date
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.const import Platform, EVENT_HOMEASSISTANT_STOP
from .observations import EntryObservations, _DESTINATIONS
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from .protocol import ProtocolError
from .transport import ReadError, disconnect_client, read_record, read_alarms as read_alarm_snapshot

DOMAIN = 'casio_abl100'
PLATFORMS = (Platform.SENSOR, Platform.BUTTON, Platform.BINARY_SENSOR)
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


class _ZoneReadFailed(HomeAssistantError):
    """Keep a context-free safe string as well as the HA translation key."""

    def __str__(self):
        try:
            return super().__str__()
        except HomeAssistantError:
            return 'Unable to read complete time zone configuration'


class WatchSession:
    """Entry-owned state and tasks; no global client or per-watch shared futures."""

    client_class = BleakClientWithServiceCache

    def __init__(self, hass, address, identity=None, entry=None):
        self.hass = hass
        self.entry = entry
        self.alarm_phase = 'idle'
        self.alarm_wait_expires_at = None
        self.step_phase = 'idle'
        self.step_wait_expires_at = None
        self.zone_phase = 'idle'
        self.zone_wait_expires_at = None
        self.zone_snapshot = None
        self.zone_last_sync = None
        self.zones_fresh = False
        self.address = address
        # Registry identity is immutable; the BLE transport address may change.
        self.identity = identity or address
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
        self.auto_reader = None
        self.interactive_session = None
        self.sync_phase = 'idle'
        self.sync_wait_expires_at = None
        self.sync_result = 'never'
        self.sync_group_results = dict.fromkeys(
            ('alarms', 'steps', 'zones', 'basic_settings', 'auto_time', 'timer'),
            'not_attempted',
        )
        self.sync_cleanup_result = 'not_attempted'
        self.basic_settings_snapshot = None
        self.basic_settings_last_sync = None
        self.basic_settings_fresh = False
        self.auto_time_snapshot = None
        self.auto_time_last_sync = None
        self.auto_time_fresh = False
        self.timer_snapshot = None
        self.timer_last_sync = None
        self.timer_fresh = False
        self.observations = None

    def _queue_observation_save(self, group):
        """Observation failures cannot revoke a validated synchronous commit."""
        if self.observations is None:
            return
        try:
            value, timestamp, _freshness = _DESTINATIONS[group]
            self.observations.capture(group, getattr(self, value), getattr(self, timestamp))
        except (Exception, asyncio.CancelledError):
            _LOGGER.warning('Observation capture failed')

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
        task = asyncio.current_task()
        if self.closed:
            if task is not None and task.cancelling():
                raise asyncio.CancelledError
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key='closed')
        self.tasks.add(task)
        active = False
        native = False
        owned_error = None
        try:
            if task is not None and task.cancelling():
                raise asyncio.CancelledError
            async with self.lock:
                active = True
                if self.closed or (task is not None and task.cancelling()):
                    raise asyncio.CancelledError
                device = bluetooth.async_ble_device_from_address(
                    self.hass, self.address, connectable=True
                )
                name = (device.name or '').rstrip('\x00 \t\r\n') if device is not None else ''
                if device is not None and (device.address.upper() != self.address or name not in ('ABL-100', 'ABL-100WE', 'CASIO ABL-100', 'CASIO ABL-100WE')):
                    owned_error = HomeAssistantError(translation_domain=DOMAIN, translation_key='unavailable')
                    raise owned_error
                native_entry = None
                if self.entry is not None:
                    from .native_alarms import select_proxy
                    native = True
                    native_entry = select_proxy(self.hass, device)
                    native = native_entry is not None
                if native_entry is not None:
                    from .native_steps import NativeStepRead
                    record = await NativeStepRead(self, native_entry).read()
                else:
                    if device is None:
                        owned_error = HomeAssistantError(translation_domain=DOMAIN, translation_key='unavailable')
                        raise owned_error
                    self.set_step_phase('reading', notify=False)
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
                    finally:
                        for partial_client in owned:
                            try:
                                await disconnect_client(partial_client)
                            except (Exception, asyncio.CancelledError):
                                # Preserve the original connector outcome.
                                pass
                if record.record_date != dt_util.now().date():
                    owned_error = HomeAssistantError(translation_domain=DOMAIN, translation_key='stale_date')
                    raise owned_error
                if self.closed or (task is not None and task.cancelling()):
                    raise asyncio.CancelledError
                synced_at = dt_util.utcnow()
                if self.closed or (task is not None and task.cancelling()):
                    raise asyncio.CancelledError
                self.record = record
                self.last_sync = synced_at
                self.fresh = True
                self._queue_observation_save('steps')
                if not native:
                    self._notify()
        except BaseException as error:
            self.fresh = False
            if native:
                self._schedule_alarm_delivery()
            else:
                self._notify()
            if self.closed or (task is not None and task.cancelling()):
                raise asyncio.CancelledError from None
            if error is owned_error or (not native and isinstance(error, asyncio.CancelledError)):
                raise
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key='read_failed') from error
        finally:
            self.tasks.discard(task)
            if active:
                self.set_step_phase('closed' if self.closed else 'idle', notify=False)
        if native:
            self._schedule_alarm_delivery()

    def set_step_phase(self, phase, expiry=None, *, notify=True):
        self.step_phase = phase
        self.step_wait_expires_at = expiry
        if notify:
            self._schedule_alarm_delivery()

    def set_alarm_phase(self, phase, expiry=None, *, notify=True):
        self.alarm_phase = phase
        self.alarm_wait_expires_at = expiry
        if notify:
            self._schedule_alarm_delivery()

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
        active = False
        try:
            if task is not None and task.cancelling():
                raise asyncio.CancelledError
            async with self.lock:
                active = True
                if self.closed or (task is not None and task.cancelling()):
                    raise asyncio.CancelledError
                device = bluetooth.async_ble_device_from_address(
                    self.hass, self.address, connectable=True
                )
                name = (device.name or '').rstrip('\x00 \t\r\n') if device is not None else ''
                if (device is not None and (device.address.upper() != self.address
                        or name not in ('ABL-100', 'ABL-100WE', 'CASIO ABL-100', 'CASIO ABL-100WE'))):
                    unavailable = HomeAssistantError(
                        translation_domain=DOMAIN, translation_key='unavailable'
                    )
                    raise unavailable

                native_entry = None
                if self.entry is not None:
                    from .native_alarms import select_proxy
                    native_entry = select_proxy(self.hass, device)
                if native_entry is not None:
                    from .native_alarms import NativeAlarmRead
                    snapshot = await NativeAlarmRead(self, native_entry).read()
                else:
                    if device is None:
                        unavailable = HomeAssistantError(translation_domain=DOMAIN, translation_key='unavailable')
                        raise unavailable
                    self.set_alarm_phase('reading', notify=False)
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
                self._queue_observation_save('alarms')
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
            if active:
                self.set_alarm_phase('closed' if self.closed else 'idle', notify=False)
        self._schedule_alarm_delivery()

    def set_zone_phase(self, phase, expiry=None, *, notify=True):
        self.zone_phase = phase
        self.zone_wait_expires_at = expiry
        if notify:
            self._schedule_alarm_delivery()

    async def read_time_zones(self):
        """Accept a native configuration observation independently of clocks."""
        task = asyncio.current_task()
        if self.closed:
            if task is not None and task.cancelling():
                raise asyncio.CancelledError
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key='closed')
        self.tasks.add(task)
        active = False
        unavailable = None
        try:
            if task is not None and task.cancelling():
                raise asyncio.CancelledError
            async with self.lock:
                active = True
                if self.closed or (task is not None and task.cancelling()):
                    raise asyncio.CancelledError
                device = bluetooth.async_ble_device_from_address(
                    self.hass, self.address, connectable=True
                )
                name = (device.name or '').rstrip('\x00 \t\r\n') if device is not None else ''
                if (device is not None and (device.address.upper() != self.address
                        or name not in ('ABL-100', 'ABL-100WE', 'CASIO ABL-100', 'CASIO ABL-100WE'))):
                    unavailable = HomeAssistantError(
                        translation_domain=DOMAIN, translation_key='unavailable'
                    )
                    raise unavailable
                from .native_alarms import select_proxy
                from .native_zones import NativeZoneRead
                native_entry = select_proxy(self.hass, device) if self.entry is not None else None
                if native_entry is None:
                    raise ValueError('Native zone source required')
                snapshot = await NativeZoneRead(self, native_entry).read()
                if self.closed or (task is not None and task.cancelling()):
                    raise asyncio.CancelledError
                observed_at = dt_util.utcnow()
                if self.closed or (task is not None and task.cancelling()):
                    raise asyncio.CancelledError
                # Nothing fallible, awaited or observer-controlled inside commit.
                self.zone_snapshot = snapshot
                self.zone_last_sync = observed_at
                self.zones_fresh = True
                self._queue_observation_save('zones')
        except BaseException as error:
            # A cancelled queue waiter owns neither readiness nor freshness.
            if active:
                self.zones_fresh = False
            self._schedule_alarm_delivery()
            if self.closed or (task is not None and task.cancelling()):
                raise asyncio.CancelledError from None
            if error is unavailable:
                raise
            raise _ZoneReadFailed(
                translation_domain=DOMAIN, translation_key='zone_read_failed'
            ) from error
        finally:
            self.tasks.discard(task)
            if active:
                self.set_zone_phase('closed' if self.closed else 'idle', notify=False)
        self._schedule_alarm_delivery()

    async def read_all(self, *, admission=None):
        """Read supported groups on one HA-owned native connection."""
        from .native_sync import read_all
        await read_all(self, admission=admission)

    async def connect_interactive(self):
        """Explicitly retain one native link; no application reads on connect."""
        from .native_interactive import connect_interactive
        await connect_interactive(self)

    async def read_interactive(self):
        """Refresh fixed groups on the admitted retained link, never reconnect."""
        from .native_interactive import _failed
        if asyncio.current_task().cancelling():
            raise asyncio.CancelledError
        if self.interactive_session is None:
            raise _failed()
        await self.interactive_session.refresh()

    async def close_interactive(self):
        """Close bypasses the lifetime mutex and joins original cleanup."""
        if self.interactive_session is not None:
            await self.interactive_session.close()

    async def shutdown(self):
        self.closed = True
        self.sync_phase = 'closed'
        self.sync_wait_expires_at = None
        self.zone_phase = 'closed'
        self.zone_wait_expires_at = None
        self.alarm_phase = 'closed'
        self.alarm_wait_expires_at = None
        self.step_phase = 'closed'
        self.step_wait_expires_at = None
        for handle in self.alarm_delivery_handles:
            handle.cancel()
        self.alarm_delivery_handles.clear()
        try:
            try:
                if self.interactive_session is not None:
                    self.interactive_session.request_close('shutdown')
                    await self.interactive_session._join()
            finally:
                try:
                    if self.auto_reader is not None:
                        await self.auto_reader.stop()
                finally:
                    # Interactive and automatic cleanup already joined. Never
                    # re-cancel their finalizers while releasing manual owners.
                    tasks = set(self.tasks)
                    for task in tasks:
                        task.cancel()
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            self.listeners.clear()
            if self.observations is not None:
                await self.observations.async_close()


async def async_setup_entry(hass, entry):
    manager = WatchSession(hass, entry.data['address'], identity=entry.unique_id, entry=entry)
    entry.runtime_data = manager
    cancel_update = None
    try:
        modes = tuple(entry.options.get(key, False) for key in ('auto_read_steps', 'auto_read_all'))
        if any(type(value) is not bool for value in modes) or all(modes):
            from homeassistant.exceptions import ConfigEntryError
            raise ConfigEntryError('Invalid automatic read options')
        manager.observations = EntryObservations(hass, entry.entry_id, manager.address, entry)
        await manager.observations.async_load_into(manager)

        async def stop_observations(_event):
            await manager.observations.async_close()

        entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop_observations))
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        if entry.options.get('auto_read_steps') is True or entry.options.get('auto_read_all') is True:
            from .auto_read import AutoStepReader

            manager.auto_reader = AutoStepReader(hass, entry, manager, mode=(
                'all' if entry.options.get('auto_read_all') is True else 'steps'))
            manager.auto_reader.start()
        cancel_update = entry.add_update_listener(_async_options_updated)
        entry.async_on_unload(cancel_update)
    except BaseException:
        if cancel_update is not None:
            cancel_update()
        try:
            await manager.shutdown()
        finally:
            # HA processes entry on_unload callbacks on failed setup, but does
            # not forward platform unloads for us after partial setup.
            await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        raise
    return True


async def async_remove_entry(hass, entry):
    """Join the original writer before unlink, even after refused unload."""
    manager = getattr(entry, 'runtime_data', None)
    observations = manager.observations if isinstance(manager, WatchSession) else None
    if observations is None:
        observations = EntryObservations(hass, entry.entry_id, entry.data['address'], entry)
    await observations.async_remove()


async def _async_options_updated(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    manager = entry.runtime_data
    reader = manager.auto_reader
    if reader is not None:
        reader.suspend()
    try:
        try:
            if manager.interactive_session is not None:
                manager.interactive_session.request_close('unload')
                await manager.interactive_session._join()
        finally:
            unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    except BaseException:
        if reader is not None:
            reader.resume()
        raise
    if not unloaded:
        if reader is not None:
            reader.resume()
        return False
    await manager.shutdown()
    return True
