"""One fixed unified transaction using the unchanged native generation owner."""
import asyncio
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from homeassistant.components import bluetooth

from bluetooth_data_tools import monotonic_time_coarse

from .auto_read import _finite_time, MAX_AGE
from .native_alarms import NativeRead, select_proxy, _usable, _loaded
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
    """Scope BLE lifetime callbacks without changing the shared native client."""
    def __init__(self, connection, valid, operational, observe_link):
        super().__init__(connection, valid, operational)
        self.observe_link = observe_link

    async def bluetooth_device_connect(self, address, on_state, *args, **kwargs):
        def state(connected, mtu, error):
            on_state(self.observe_link(connected, error), mtu, error)

        return await super().bluetooth_device_connect(address, state, *args, **kwargs)

    async def bluetooth_device_disconnect(self, *args, **kwargs):
        try:
            result = await super().bluetooth_device_disconnect(*args, **kwargs)
        except BaseException:
            self.session.sync_cleanup_result = 'failed'
            raise
        self.session.sync_cleanup_result = 'success'
        return result


class _RawSyncAdmission:
    """Single-use host-ingress authority, bound to one watch and API generation."""
    def __init__(self, session, proxy_entry, *, runtime, client, connection,
                 proxy, disconnect_callbacks, target, address_type, seen):
        self.session, self.entry = session, proxy_entry
        self.runtime, self.client, self.connection = runtime, client, connection
        self.proxy, self.callbacks = proxy, disconnect_callbacks
        self.target, self.address_type, self.seen = target, address_type, seen
        self.consumed = False

    def guard(self, session):
        now = monotonic_time_coarse()
        task = asyncio.current_task()
        if (self.consumed or session is not self.session or session.closed
                or task is None or task.cancelling()
                or not _loaded(session.entry) or session.entry.runtime_data is not session
                or type(self.address_type) is not int or self.address_type not in (0, 1)
                or type(self.target) is not int
                or self.target != int(session.address.replace(':', ''), 16)
                or not _finite_time(now) or not _finite_time(self.seen)
                or not 0 <= now - self.seen <= MAX_AGE
                or not _usable(self.entry)
                or self.entry.runtime_data is not self.runtime
                or self.runtime.client is not self.client
                or self.runtime.bluetooth_device is not self.proxy
                or self.runtime.disconnect_callbacks is not self.callbacks
                or self.client._get_connection() is not self.connection):
            raise ValueError('Invalid automatic read admission')

    def consume(self, reader):
        self.guard(reader.session)
        if (reader.entry is not self.entry or reader.runtime is not self.runtime
                or reader.client is not self.client or reader.connection is not self.connection
                or reader.proxy is not self.proxy or reader.callbacks is not self.callbacks
                or reader.target != self.target):
            raise ValueError('Automatic read generation changed')
        # free/guard checks the captured public owner, not this execution child.
        reader.free()
        reader.guard()
        self.consumed = True
        return self.address_type


class NativeSyncRead(NativeRead):
    def __init__(self, session, entry):
        super().__init__(session, entry)
        self.link_established = False
        self.link_lost = False
        self.api = _SyncAPI(self.connection, self.generation_valid, self.valid,
                            self.observe_link)
        self.api.session = session
        self.execution = None
        self._admission = None

    async def wait(self):
        if self._admission is None:
            return await super().wait()
        return self._admission.consume(self)

    def observe_link(self, connected, error):
        confirmed = connected is True and error == 0
        if self.link_established and not confirmed:
            # BLE loss is terminal, but does not revoke the original API
            # generation's authority to release this invocation's resources.
            self.link_lost = True
            if self.adapter is not None:
                self.adapter.receiver = self.adapter.step_receivers = None
        if confirmed and not self.link_lost:
            self.link_established = True
        return confirmed and not self.link_lost

    def valid(self):
        # Also guards actual native submission after a scheduled acquisition,
        # not just the adapter's earlier check or the final group commit.
        return not self.link_lost and super().valid()

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


async def read_all(session, *, admission=None):
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
            if admission is not None:
                if type(admission) is not _RawSyncAdmission:
                    raise ValueError('Invalid automatic read admission')
                admission.guard(session)
                device = bluetooth.async_ble_device_from_address(session.hass, session.address, connectable=True)
                if select_proxy(session.hass, device) is not admission.entry:
                    raise ValueError('Automatic read source changed')
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
            reader._admission = admission
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
