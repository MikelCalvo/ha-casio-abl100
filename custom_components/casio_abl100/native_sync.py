"""One fixed unified transaction using the unchanged native generation owner."""
import asyncio
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from homeassistant.components import bluetooth

from .native_alarms import NativeRead, select_proxy
from .native_api import NativeAPI
from .native_sync_adapter import NativeSyncAdapter
from .sync_transport import (read_alarm_group, read_step_group, read_zone_group,
                             read_basic_group, read_auto_time_group, read_timer_group)

GROUPS = ('alarms', 'steps', 'zones', 'basic_settings', 'auto_time', 'timer')
# Explicit destinations preserve existing per-feature state/identity semantics.
DESTINATIONS = (
    ('alarm_snapshot', 'alarm_last_sync', 'alarms_fresh'),
    ('record', 'last_sync', 'fresh'),
    ('zone_snapshot', 'zone_last_sync', 'zones_fresh'),
    ('basic_settings_snapshot', 'basic_settings_last_sync', 'basic_settings_fresh'),
    ('auto_time_snapshot', 'auto_time_last_sync', 'auto_time_fresh'),
    ('timer_snapshot', 'timer_last_sync', 'timer_fresh'),
)


class _SyncReadFailed(HomeAssistantError):
    def __str__(self):
        try:
            return super().__str__()
        except HomeAssistantError:
            return 'Unable to read all watch data; completed groups are retained'


class _SyncAPI(NativeAPI):
    """Observe the outer owner's actual cleanup, including suppressed faults."""
    async def bluetooth_device_disconnect(self, *args, **kwargs):
        try:
            result = await super().bluetooth_device_disconnect(*args, **kwargs)
        except BaseException:
            self.session.sync_cleanup_result = 'failed'
            raise
        self.session.sync_cleanup_result = 'success'
        return result


class NativeSyncRead(NativeRead):
    def __init__(self, session, entry):
        super().__init__(session, entry)
        self.api = _SyncAPI(self.connection, self.generation_valid, self.valid)
        self.api.session = session
        self.execution = None

    async def read(self):
        # Keep the captured public owner for submission/commit authority. A
        # backend disconnect cancels only this joined execution, never invents
        # a Task.cancel request on the public caller.
        self.execution = asyncio.create_task(super().read())
        return await self.execution

    def invalidate(self):
        self.lost = True
        self.set_phase('idle')
        if self.execution is not None:
            self.execution.cancel()

    def set_phase(self, phase, expiry=None):
        self.session.sync_phase = phase
        self.session.sync_wait_expires_at = expiry
        self.session._schedule_alarm_delivery()

    def make_adapter(self, metadata):
        return NativeSyncAdapter(self.api, self.target, metadata,
                                 lambda: self.valid() and self.link, self.generation_valid)

    async def transfer(self, adapter):
        readers = (read_alarm_group, read_step_group, read_zone_group,
                   read_basic_group, read_auto_time_group, read_timer_group)
        for group, reader, destinations in zip(GROUPS, readers, DESTINATIONS, strict=True):
            self.guard()
            adapter.guard()
            self.session.sync_group_results[group] = 'reading'
            self.session._schedule_alarm_delivery()
            try:
                snapshot = await reader(adapter)
                self.guard()
                adapter.guard()
                observed_at = dt_util.utcnow()
                self.guard()
                adapter.guard()
                # Group callbacks have closed. No awaits/observers in commit.
                value, timestamp, freshness = destinations
                setattr(self.session, value, snapshot)
                setattr(self.session, timestamp, observed_at)
                setattr(self.session, freshness, True)
                self.session.sync_group_results[group] = 'success'
            except BaseException:
                self.session.sync_group_results[group] = 'failed'
                raise
            self.session._schedule_alarm_delivery()

    def remove_notifications(self):
        if self.adapter is not None:
            self.adapter.remove_notifications()


async def read_all(session):
    """Register before lock wait; a queue waiter owns no status or freshness."""
    task = asyncio.current_task()
    if session.closed:
        if task.cancelling():
            raise asyncio.CancelledError
        raise _SyncReadFailed(translation_domain='casio_abl100', translation_key='sync_read_failed')
    session.tasks.add(task)
    active = False
    try:
        if task.cancelling():
            raise asyncio.CancelledError
        async with session.lock:
            if session.closed or task.cancelling():
                raise asyncio.CancelledError
            active = True
            session.sync_group_results = dict.fromkeys(GROUPS, 'not_attempted')
            session.sync_cleanup_result = 'not_attempted'
            for _value, _timestamp, freshness in DESTINATIONS:
                setattr(session, freshness, False)
            device = bluetooth.async_ble_device_from_address(session.hass, session.address, connectable=True)
            name = (device.name or '').rstrip('\x00 \t\r\n') if device is not None else ''
            if device is not None and (device.address.upper() != session.address or name not in (
                    'ABL-100', 'ABL-100WE', 'CASIO ABL-100', 'CASIO ABL-100WE')):
                raise ValueError('Unverified watch model')
            entry = select_proxy(session.hass, device) if session.entry is not None else None
            if entry is None:
                raise ValueError('Native unified source required')
            reader = NativeSyncRead(session, entry)
            await reader.read()
            if session.closed or task.cancelling():
                raise asyncio.CancelledError
            if not reader.generation_valid():
                raise RuntimeError('Unified generation lost during cleanup')
            session.sync_result = 'success'
    except BaseException as error:
        if active:
            cancelled = session.closed or task.cancelling()
            session.sync_result = 'cancelled' if cancelled else (
                'partial' if 'success' in session.sync_group_results.values() else 'failed')
            if 'reading' not in session.sync_group_results.values() and not any(
                    state == 'failed' for state in session.sync_group_results.values()):
                if session.sync_group_results['alarms'] == 'not_attempted' and not cancelled:
                    session.sync_group_results['alarms'] = 'failed'
        if session.closed or task.cancelling():
            raise asyncio.CancelledError from None
        raise _SyncReadFailed(translation_domain='casio_abl100', translation_key='sync_read_failed') from error
    finally:
        session.tasks.discard(task)
        if active:
            session.sync_phase = 'closed' if session.closed else 'idle'
            session.sync_wait_expires_at = None
            session._schedule_alarm_delivery()
