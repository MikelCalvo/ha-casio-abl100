"""Optional HA-native step requests for observed advertisement windows."""
import asyncio
from datetime import timedelta
import logging
import math

from bluetooth_data_tools import monotonic_time_coarse, parse_advertisement_data_bytes
from aioesphomeapi.api_pb2 import BluetoothLERawAdvertisementsResponse
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ConfigEntryError

from .native_alarms import select_proxy, _usable
from homeassistant.components import bluetooth
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval

_LOGGER = logging.getLogger(__name__)
QUIET_GAP = 30.0
MAX_AGE = 5.0
_NAMES = ('ABL-100', 'ABL-100WE', 'CASIO ABL-100', 'CASIO ABL-100WE')


def _finite_time(value):
    """Validate producer/local numbers without overflowing arbitrary integers."""
    try:
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        return False


class AutoStepReader:
    """One request per observed burst, not proof of a particular watch gesture."""

    def __init__(self, hass, entry, session, *, mode='steps'):
        self.hass, self.entry, self.session = hass, entry, session
        self.mode = mode
        self._clock = None
        self._last_seen = None
        self._claimed = False
        self._suspended = False
        self._stopped = False
        self._faulted = False
        self._started = False
        self._ready = False
        self._task = None
        self._stop_cancelled_task = None
        self._cancel_callback = None
        self._cancel_timer = None
        self._cancel_disconnect = None
        self._raw_binding = None
        self._proxy_entry = None
        self._source = None
        self._pending_admission = None
        self._retained_admission = None

    def retained_terminal(self, controller):
        """Anchor rearm to joined native cleanup, never the initial read return."""
        if self.mode != 'retained' or controller.admission is not self._retained_admission:
            return
        self._retained_admission = None
        now = self._now()
        if now is not None:
            self._last_seen = max(now, self._last_seen or now)
        self._claimed = True

    def _now(self):
        now = monotonic_time_coarse()
        if not _finite_time(now) or (self._clock is not None and now < self._clock):
            self._faulted = True
            return None
        self._clock = now
        return now

    @callback
    def start(self):
        """Observe passively without cached replay or a connection at setup."""
        if self._started or self._stopped:
            return
        self._started = True
        now = self._now()
        cached = bluetooth.async_last_service_info(self.hass, self.session.address, connectable=True)
        if cached is not None:
            self._observe(cached, allow_request=False)
        if now is None or self._faulted:
            return
        try:
            if self.mode in ('all', 'retained'):
                device = bluetooth.async_ble_device_from_address(self.hass, self.session.address, connectable=True)
                name = (device.name or '').rstrip('\x00 \t\r\n') if device is not None else ''
                if device is not None and (device.address.upper() != self.session.address or name not in _NAMES):
                    raise ValueError('Unverified watch model')
                self._proxy_entry = select_proxy(self.hass, device)
                if self._proxy_entry is None:
                    raise ValueError('Native automatic source required')
                self._source = self._proxy_entry.runtime_data.bluetooth_device.mac_address
                self._bind_raw(self._proxy_entry)
                self._cancel_timer = async_track_time_interval(
                    self.hass, self._maintain_raw, timedelta(seconds=1),
                )
                self._ready = True
                return
            self._cancel_callback = bluetooth.async_register_callback(
                self.hass, self._advertisement,
                {'address': self.session.address, 'connectable': True},
                bluetooth.BluetoothScanningMode.PASSIVE,
                replay=bluetooth.BluetoothCallbackReplay.DISABLED,
            )
            self._cancel_timer = async_track_time_interval(
                self.hass, self._sample, timedelta(seconds=1),
            )
            self._ready = True
        except (Exception, asyncio.CancelledError):
            self._stopped = True
            if self._cancel_callback is not None:
                cancel, self._cancel_callback = self._cancel_callback, None
                try:
                    cancel()
                except (Exception, asyncio.CancelledError):
                    _LOGGER.warning('Automatic step observer cleanup failed')
            if self.mode in ('all', 'retained'):
                self._detach_raw()
                if self._cancel_timer is not None:
                    cancel, self._cancel_timer = self._cancel_timer, None
                    cancel()
                raise ConfigEntryError('Unable to observe native automatic source') from None
            raise

    def _detach_raw(self):
        self._raw_binding = None
        if self._pending_admission is not None:
            self._pending_admission.consumed = True
            self._pending_admission = None
        for attribute in ('_cancel_callback', '_cancel_disconnect'):
            cancel = getattr(self, attribute)
            setattr(self, attribute, None)
            if cancel is not None:
                try:
                    cancel()
                except (Exception, asyncio.CancelledError):
                    _LOGGER.warning('Automatic watch observer cleanup failed')

    def _bind_raw(self, proxy_entry):
        if not _usable(proxy_entry):
            raise ValueError('Native automatic source unavailable')
        runtime = proxy_entry.runtime_data
        proxy = runtime.bluetooth_device
        if proxy.mac_address != self._source:
            raise ValueError('Native automatic source changed')
        client = runtime.client
        connection = client._get_connection()
        callbacks = runtime.disconnect_callbacks
        binding = self._raw_binding = (runtime, client, connection, proxy, callbacks)
        def raw(message):
            # A producer can retain a delivery snapshot after handler removal.
            if self._raw_binding is binding:
                self._raw_advertisement(message)
        try:
            self._cancel_callback = connection.add_message_callback(
                raw, (BluetoothLERawAdvertisementsResponse,),
            )
            def disconnected():
                if self._raw_binding is binding:
                    self._detach_raw()
            callbacks.add(disconnected)
            self._cancel_disconnect = lambda: callbacks.discard(disconnected)
        except BaseException:
            self._detach_raw()
            raise

    def _raw_valid(self):
        try:
            runtime, client, connection, proxy, callbacks = self._raw_binding
            return (_usable(self._proxy_entry)
                    and self._proxy_entry.runtime_data is runtime
                    and runtime.client is client and runtime.bluetooth_device is proxy
                    and runtime.disconnect_callbacks is callbacks
                    and proxy.mac_address == self._source
                    and client._get_connection() is connection)
        except Exception:
            return False

    @callback
    def _maintain_raw(self, _now):
        if self._stopped or self._faulted or self._raw_valid():
            return
        self._detach_raw()
        try:
            self._bind_raw(self._proxy_entry)
        except (Exception, asyncio.CancelledError):
            # Passive maintenance cannot request a read or change selected source.
            self._detach_raw()

    @callback
    def _raw_advertisement(self, message):
        if self._stopped or self._faulted or not self._raw_valid():
            return
        for adv in message.advertisements:
            if adv.address != int(self.session.address.replace(':', ''), 16) or adv.address_type not in (0, 1):
                continue
            name = parse_advertisement_data_bytes(bytes(adv.data))[0]
            if (name or '').rstrip('\x00 \t\r\n') not in _NAMES:
                continue
            seen = self._now()
            if seen is None:
                return
            from .native_sync import _RawSyncAdmission
            runtime, client, connection, proxy, callbacks = self._raw_binding
            admission = _RawSyncAdmission(
                self.session, self._proxy_entry, runtime=runtime, client=client,
                connection=connection, proxy=proxy, disconnect_callbacks=callbacks,
                target=adv.address, address_type=adv.address_type, seen=seen,
            )
            self._claim(seen, seen, admission=admission)
            return

    @callback
    def _advertisement(self, info, change):
        if change is bluetooth.BluetoothChange.ADVERTISEMENT:
            self._observe(info)

    @callback
    def _sample(self, _now):
        if self._stopped or self._faulted:
            return
        cached = bluetooth.async_last_service_info(self.hass, self.session.address, connectable=True)
        if cached is not None:
            self._observe(cached)

    def _observe(self, info, *, allow_request=True):
        if self._stopped or self._faulted:
            return
        if (info.address != self.session.address or not info.connectable
                or (info.name or '').rstrip('\x00 \t\r\n') not in _NAMES):
            return
        now = self._now()
        seen = info.time
        if now is None or not _finite_time(seen) or seen > now:
            self._faulted = True
            return
        if self.mode in ('all', 'retained') and self._last_seen is not None and seen <= self._last_seen:
            # Scanner and local RAW ingress stamp independently. Older public
            # metadata can suppress a window, never revoke newer RAW observation.
            return
        self._claim(seen, now, allow_request=allow_request and self.mode == 'steps')

    def _claim(self, seen, now, *, allow_request=True, admission=None):
        previous = self._last_seen
        if previous is not None:
            if seen < previous:
                self._faulted = True
                return
            if seen == previous:
                return
        # Read metadata even when HA suppressed the equal-payload callback.
        self._last_seen = seen
        if previous is None or seen - previous >= QUIET_GAP:
            self._claimed = False
        if self._claimed:
            return
        self._claimed = True  # Consume failures, busy skips and stale bursts too.
        if (not self._ready or not allow_request or self._suspended or now - seen > MAX_AGE
                or self._busy() or self._task is not None):
            return
        self._pending_admission = admission
        coroutine = self._read(seen, admission=admission)
        try:
            self._task = self.entry.async_create_background_task(
                self.hass, coroutine, 'Casio automatic step read', eager_start=False,
            )
        except (Exception, asyncio.CancelledError):
            coroutine.close()
            if admission is not None:
                admission.consumed = True
            self._pending_admission = None
            _LOGGER.warning('Unable to schedule automatic step read')

    def _busy(self):
        return self.session.closed or self.session.lock.locked() or bool(self.session.tasks)

    async def _read(self, seen, *, admission=None):
        try:
            now = self._now()
            if (now is None or self._faulted or self._stopped or self._suspended
                    or self.entry.state is not ConfigEntryState.LOADED
                    or self.entry.runtime_data is not self.session
                    or asyncio.current_task().cancelling()
                    or self._busy() or not 0 <= now - seen <= MAX_AGE):
                return
            # No yield between the final check and entering the existing session.
            if self.mode in ('all', 'retained'):
                if not self._raw_valid():
                    return
                admission.guard(self.session)
                if self.mode == 'retained':
                    self._retained_admission = admission
                    try:
                        await self.session.connect_interactive(
                            admission=admission, retain_until_disconnect=True)
                        await self.session.read_interactive()
                    except BaseException:
                        # Connect's successful handoff detaches its caller. If
                        # the initial read never enters, this observer still
                        # owns revocation and must join that original root.
                        controller = self.session.interactive_session
                        if controller is not None and controller.admission is admission:
                            controller.request_close('automatic_read_abandoned')
                            await controller._join()
                        raise
                else:
                    await self.session.read_all(admission=admission)
            else:
                await self.session.read()
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise
            _LOGGER.warning('Automatic step read failed')
        except Exception:
            _LOGGER.warning('Automatic step read failed')
        finally:
            if self._pending_admission is admission:
                self._pending_admission = None
            if self._task is asyncio.current_task():
                self._task = None

    @callback
    def suspend(self):
        self._suspended = True
        if self._pending_admission is not None:
            self._pending_admission.consumed = True
            self._pending_admission = None

    @callback
    def resume(self):
        # A failed unload must not reinterpret the current window as a new one.
        cached = bluetooth.async_last_service_info(self.hass, self.session.address, connectable=True)
        if cached is not None:
            self._observe(cached, allow_request=False)
        self._suspended = False

    async def stop(self):
        """Remove observers; wait for cooperative cleanup even if this caller cancels.

        An uncooperative session backend can still prevent completion; stopping
        does not impose a timeout or forcibly abandon the owned reader.
        """
        self._stopped = True
        if self.mode in ('all', 'retained'):
            self._detach_raw()
        for attribute in ('_cancel_callback', '_cancel_timer', '_cancel_disconnect'):
            cancel = getattr(self, attribute)
            setattr(self, attribute, None)
            if cancel is not None:
                try:
                    cancel()
                except (Exception, asyncio.CancelledError):
                    _LOGGER.warning('Automatic step observer cleanup failed')
        controller = self.session.interactive_session if self.mode == 'retained' else None
        if controller is not None and controller.admission is self._retained_admission:
            controller.request_close('observer_stopped')
        else:
            controller = None
        task = self._task
        if task is not None:
            # Claim once before yielding: concurrent stops must not re-cancel
            # a reader already awaiting asynchronous session cleanup.
            if self._stop_cancelled_task is not task:
                self._stop_cancelled_task = task
                task.cancel()
            cancelled = None
            while not task.done():
                try:
                    # wait() never forwards caller cancellation to the child,
                    # and removes its callback when cancelled (no shield orphan).
                    await asyncio.wait({task})
                except asyncio.CancelledError as err:
                    cancelled = err
            if not task.cancelled():
                task.exception()  # Retrieve any failure before propagating ours.
            if self._task is task:
                self._task = None
            if self._stop_cancelled_task is task:
                self._stop_cancelled_task = None
            if cancelled is not None:
                raise cancelled
        if controller is not None:
            await controller._join()
