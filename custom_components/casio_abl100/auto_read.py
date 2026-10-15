"""Optional HA-native step requests for observed advertisement windows."""
import asyncio
from datetime import timedelta
import logging
import math

from bluetooth_data_tools import monotonic_time_coarse
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

    def __init__(self, hass, entry, session):
        self.hass, self.entry, self.session = hass, entry, session
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
            raise

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
        coroutine = self._read(seen)
        try:
            self._task = self.entry.async_create_background_task(
                self.hass, coroutine, 'Casio automatic step read', eager_start=False,
            )
        except (Exception, asyncio.CancelledError):
            coroutine.close()
            _LOGGER.warning('Unable to schedule automatic step read')

    def _busy(self):
        return self.session.closed or self.session.lock.locked() or bool(self.session.tasks)

    async def _read(self, seen):
        try:
            now = self._now()
            if (now is None or self._faulted or self._stopped or self._suspended
                    or self._busy() or now - seen > MAX_AGE):
                return
            # No yield between the final check and entering the existing session.
            await self.session.read()
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise
            _LOGGER.warning('Automatic step read failed')
        except Exception:
            _LOGGER.warning('Automatic step read failed')
        finally:
            if self._task is asyncio.current_task():
                self._task = None

    @callback
    def suspend(self):
        self._suspended = True

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
        for attribute in ('_cancel_callback', '_cancel_timer'):
            cancel = getattr(self, attribute)
            setattr(self, attribute, None)
            if cancel is not None:
                try:
                    cancel()
                except (Exception, asyncio.CancelledError):
                    _LOGGER.warning('Automatic step observer cleanup failed')
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
