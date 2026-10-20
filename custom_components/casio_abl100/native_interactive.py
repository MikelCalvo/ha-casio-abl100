"""Bounded, entry-owned interactive reads on one original native generation.

The wire has no transaction IDs: an identical delayed reply delivered after a
repeated identical query starts cannot be authenticated as belonging to it.
No quiet delay, retry, keepalive or reconnect is used to disguise that limit.
"""
import asyncio
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.components import bluetooth
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .native_alarms import select_proxy
from .native_api import NativeAPI
from .native_sync import NativeSyncRead, GROUPS, DESTINATIONS
from .sync_transport import (read_alarm_group, read_step_group, read_zone_group,
                             read_basic_group, read_auto_time_group, read_timer_group)

IDLE_SECONDS = 120
SESSION_SECONDS = 300
REFRESH_SECONDS = 60
CHECK_SECONDS = 1
PERIODIC_SECONDS = 30
READERS = (read_alarm_group, read_step_group, read_zone_group,
           read_basic_group, read_auto_time_group, read_timer_group)


def _failed():
    # A fixed safe public error, independent of backend exception provenance.
    return HomeAssistantError('Interactive watch session unavailable or failed')


@dataclass(frozen=True)
class _AlarmApplyRequest:
    intent: object
    revision: int
    anchor: object


@dataclass(frozen=True)
class _NativeFault:
    """First observed native loss, never resource or operation authority."""

    stage: str
    status: int | None


@dataclass(frozen=True)
class _Outcome:
    """Request-owned terminal data; never backend exceptions or latest UI state."""

    result: str
    close_reason: str | None = None
    cleanup_result: str = 'not_attempted'


def _resolve(complete, outcome):
    if complete is not None and not complete.done():
        complete.set_result(outcome)


class NativeInteractiveSession:
    """One root, mutex reservation and terminal outcome per explicit connect."""

    def __init__(self, session, *, admission=None, retain_until_disconnect=False):
        self.session = session
        self.admission = admission
        self.retain_until_disconnect = retain_until_disconnect
        self.phase = 'starting'
        self.link_state = 'unknown'
        self.wait_expires_at = None
        self.idle_expires_at = None
        self.session_expires_at = None
        self.operation_result = 'never'
        self.group_results = dict.fromkeys(GROUPS, 'not_attempted')
        self.cleanup_result = 'not_attempted'
        self.close_reason = None
        self.cleanup_in_progress = False
        self.reader = None
        self.root = None
        self.maintenance_handle = None
        self.connect_caller = asyncio.current_task()
        self.operation_caller = None
        self.operation_complete = None
        self.apply_request = None
        loop = asyncio.get_running_loop()
        self.ready = loop.create_future()
        self.closed = loop.create_future()
        self.wake = asyncio.Event()
        self.failure = None
        self.native_fault = None
        self._native_stage = 'unknown'
        self._cancel_sent = False
        self._finished = False
        self._idle_deadline = None
        self._session_deadline = None
        self._refresh_deadline = None
        self._cycles = 0
        self._periodic_deadline = None

    @property
    def active(self):
        return not self._finished

    @property
    def usable(self):
        return (self.active and self.phase == 'connected' and self.close_reason is None
                and self.reader is not None and self.reader.valid() and self.reader.link)

    def publish(self):
        self.session._schedule_alarm_delivery()

    def start(self):
        coroutine = self._run()
        try:
            self.root = self.session.entry.async_create_background_task(
                self.session.hass, coroutine, 'Casio interactive session', eager_start=False,
            )
        except BaseException:
            coroutine.close()
            self._finish()
            raise
        # No yield between reservation, creation and registration. The done
        # callback also accounts for owner cancellation before coroutine entry.
        self.session.tasks.add(self.root)
        self.root.add_done_callback(self._root_done)
        self.publish()

    def _root_done(self, task):
        if not task.cancelled():
            task.exception()
        self.session.tasks.discard(task)
        if not self._finished:
            self.request_close('owner_cancelled')
            self._finish()

    def request_close(self, reason):
        if self._finished:
            return
        if self.close_reason is None:
            self.close_reason = reason
        self.phase = 'closing'
        self.wait_expires_at = self.idle_expires_at = None
        self.wake.set()
        execution = self.reader.execution if self.reader is not None else None
        if (not self.cleanup_in_progress and not self._cancel_sent
                and execution is not None and not execution.done()):
            self._cancel_sent = True
            execution.cancel()
        self.publish()

    def authority(self):
        if not self.active or self.close_reason is not None:
            return False
        if self.root is not None and self.root.cancelling():
            self.request_close('owner_cancelled')
            return False
        caller = self.operation_caller or self.connect_caller
        if caller is not None and caller.cancelling():
            self.request_close('caller_cancelled')
            return False
        now = asyncio.get_running_loop().time()
        for deadline, reason in ((self._session_deadline, 'session_expired'),
                                 (self._refresh_deadline, 'refresh_expired'),
                                 (self._idle_deadline, 'idle_expired')):
            if deadline is not None and now >= deadline:
                self.request_close(reason)
                return False
        return True

    def _tick(self):
        self.maintenance_handle = None
        if self._finished:
            return
        if self.reader is not None and not self.reader.generation_valid():
            self.link_state = 'unknown'
            self.request_close('generation_lost')
        self.authority()
        if self.close_reason is None:
            self.maintenance_handle = asyncio.get_running_loop().call_later(CHECK_SECONDS, self._tick)

    def _idle(self):
        self._native_stage = 'unknown'
        self.phase = 'connected'
        self._refresh_deadline = None
        if self.retain_until_disconnect and self._cycles:
            self._periodic_deadline = asyncio.get_running_loop().time() + PERIODIC_SECONDS
        if not self.retain_until_disconnect:
            self._idle_deadline = asyncio.get_running_loop().time() + IDLE_SECONDS
            self.idle_expires_at = dt_util.utcnow() + timedelta(seconds=IDLE_SECONDS)
        self.publish()

    async def _run(self):
        try:
            # A competing legacy operation may have won scheduling. Never queue.
            if self.session.closed or self.session.lock.locked() or not self.authority():
                raise _failed()
            async with self.session.lock:
                if not self.authority():
                    raise _failed()
                device = bluetooth.async_ble_device_from_address(
                    self.session.hass, self.session.address, connectable=True,
                )
                name = (device.name or '').rstrip('\x00 \t\r\n') if device is not None else ''
                if device is not None and (device.address.upper() != self.session.address or name not in (
                        'ABL-100', 'ABL-100WE', 'CASIO ABL-100', 'CASIO ABL-100WE')):
                    raise _failed()
                entry = select_proxy(self.session.hass, device) if self.session.entry is not None else None
                if self.admission is not None:
                    self.admission.guard(self.session)
                    if entry is not self.admission.entry:
                        raise _failed()
                    entry = self.admission.entry
                if entry is None:
                    raise _failed()
                self.reader = _InteractiveRead(self.session, entry, self)
                self.reader._admission = self.admission
                self.maintenance_handle = asyncio.get_running_loop().call_later(CHECK_SECONDS, self._tick)
                await self.reader.read()
        except BaseException as error:
            self.failure = error
            if self.close_reason is None:
                if self.reader is not None and not self.reader.generation_valid():
                    reason = 'generation_lost'
                elif self.root.cancelling():
                    reason = 'owner_cancelled'
                else:
                    reason = ('apply_failed' if self.apply_request is not None else
                              'read_failed' if self.operation_caller is not None else 'connect_failed')
                self.request_close(reason)
        finally:
            if self.reader is not None and not self.reader.generation_valid():
                self.link_state = 'unknown'
                if self.cleanup_result in ('not_attempted', 'success'):
                    self.cleanup_result = 'generation_lost'
            self.session.tasks.discard(asyncio.current_task())
            self._finish()

    def _finish(self):
        if self._finished:
            return
        self._finished = True
        observer = self.session.auto_reader
        if self.retain_until_disconnect and observer is not None:
            observer.retained_terminal(self)
        self.phase = 'closed'
        self.connect_caller = None
        self.operation_caller = None
        self.wait_expires_at = self.idle_expires_at = self.session_expires_at = None
        if self.maintenance_handle is not None:
            self.maintenance_handle.cancel()
            self.maintenance_handle = None
        if self.apply_request is not None:
            from .alarm_editing import AlarmApplyOutcome
            from .native_alarm_edit import finish_apply
            draft = self.session.alarm_draft
            outcome = AlarmApplyOutcome('unknown' if draft.write_submitted else 'failed_before_write',
                                        draft.write_submitted)
            finish_apply(self.session, self.apply_request, outcome)
            self.operation_result = outcome.result
            self.apply_request = None
            _resolve(self.operation_complete, outcome)
        if self.operation_result == 'reading':
            self.operation_result = ('cancelled' if self.close_reason == 'caller_cancelled' else
                                    'partial' if 'success' in self.group_results.values() else 'failed')
        # Resolve pending work only after teardown. Completed handoffs retain
        # their own outcome even if close or another refresh advanced the UI.
        _resolve(self.ready, _Outcome('failed', self.close_reason, self.cleanup_result))
        _resolve(self.operation_complete,
                 _Outcome(self.operation_result, self.close_reason, self.cleanup_result))
        _resolve(self.closed, _Outcome(
            'failed' if self.cleanup_result in ('failed', 'generation_lost') else 'success',
            self.close_reason, self.cleanup_result,
        ))
        self.publish()

    async def _join(self):
        root = self.root
        cancelled = False
        while root is not None and not root.done():
            try:
                await asyncio.wait({root})
            except asyncio.CancelledError:
                cancelled = True
        if cancelled or asyncio.current_task().cancelling():
            raise asyncio.CancelledError

    async def _wait_caller(self, complete):
        root = self.root
        try:
            await asyncio.wait({complete, root}, return_when=asyncio.FIRST_COMPLETED)
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError
        except asyncio.CancelledError:
            # Completion detaches authority synchronously before notifying
            # waiters. Cancelling that previous caller must not close its
            # successor. An admitted, still-pending caller must close/join.
            if not complete.done():
                self.request_close('caller_cancelled')
                try:
                    await self._join()
                finally:
                    raise asyncio.CancelledError from None
            raise asyncio.CancelledError from None
        return complete.result()

    async def connected(self):
        complete = self.ready
        outcome = await self._wait_caller(complete)
        if outcome.result != 'success':
            raise _failed() from None

    async def refresh(self):
        caller = asyncio.current_task()
        if caller.cancelling():
            raise asyncio.CancelledError
        complete = self._admit_refresh(caller)
        outcome = await self._wait_caller(complete)
        if outcome.result != 'success':
            raise _failed() from None

    def _admit_refresh(self, caller):
        """Single synchronous admission shared by manual and periodic reads."""
        if not self.usable or self.operation_caller is not None:
            raise _failed()
        complete = asyncio.get_running_loop().create_future()
        self.operation_caller = caller
        self.operation_complete = complete
        self._native_stage = 'unknown'
        self.operation_result = 'reading'
        self.group_results = dict.fromkeys(GROUPS, 'not_attempted')
        self._idle_deadline = None
        self.idle_expires_at = None
        self._refresh_deadline = asyncio.get_running_loop().time() + REFRESH_SECONDS
        self.phase = 'reading'
        for _value, _timestamp, freshness in DESTINATIONS:
            setattr(self.session, freshness, False)
        self.publish()
        self.wake.set()
        return complete

    async def apply_alarms(self, intent, revision, anchor):
        from .alarm_editing import operable
        caller = asyncio.current_task()
        if caller.cancelling():
            raise asyncio.CancelledError
        if (not operable(self.session) or self.session.interactive_session is not self
                or self.session.alarm_draft.needs_reconciliation
                or not self.usable or self.operation_caller is not None):
            raise _failed()
        complete = asyncio.get_running_loop().create_future()
        self.operation_caller = caller
        self.operation_complete = complete
        self.apply_request = _AlarmApplyRequest(intent, revision, anchor)
        self._native_stage = 'unknown'
        self.operation_result = 'applying'
        self._idle_deadline = None
        self.idle_expires_at = None
        self._refresh_deadline = asyncio.get_running_loop().time() + REFRESH_SECONDS
        self.phase = 'applying'
        self.publish()
        self.wake.set()
        return await self._wait_caller(complete)

    async def close(self, reason='explicit_close'):
        complete = self.closed
        self.request_close(reason)
        await self._join()
        if complete.result().result != 'success':
            raise _failed() from None


class _InteractiveAPI(NativeAPI):
    def __init__(self, reader):
        super().__init__(reader.connection, reader.generation_valid, reader.valid)
        self.reader = reader

    async def bluetooth_device_connect(self, address, on_state, *args, **kwargs):
        def state(connected, mtu, error):
            on_state(self.reader.observe_link(connected, error), mtu, error)
        return await super().bluetooth_device_connect(address, state, *args, **kwargs)

    async def bluetooth_gatt_start_notify(self, address, handle, *args, **kwargs):
        c, adapter = self.reader.controller, self.reader.adapter
        primary = adapter is not None and handle == adapter.primary.handle
        if primary:
            c._native_stage = 'primary_notify'
        result = await super().bluetooth_gatt_start_notify(address, handle, *args, **kwargs)
        if primary:
            c._native_stage = 'other'
        return result

    async def bluetooth_gatt_write_descriptor(self, address, handle, *args, **kwargs):
        c, adapter = self.reader.controller, self.reader.adapter
        primary = adapter is not None and handle == adapter.cccd
        if primary:
            c._native_stage = 'primary_cccd'
        result = await super().bluetooth_gatt_write_descriptor(address, handle, *args, **kwargs)
        if primary:
            c._native_stage = 'other'
        return result

    async def bluetooth_gatt_write(self, address, handle, data, *args, **kwargs):
        c, adapter = self.reader.controller, self.reader.adapter
        query = ('get15' if data == b'\x15' else 'get16' if data == b'\x16' else None)
        alarm = adapter is not None and handle == adapter.query.handle
        setting = (c.apply_request is not None and adapter is not None
                   and handle == adapter.primary.handle and len(data) in (5, 17)
                   and data[0] in (0x15, 0x16))
        if setting:
            # Conservative possible submission at native method invocation,
            # before awaiting ATT, never inferred from completion or readback.
            from .alarm_editing import operable
            self.reader.guard()
            adapter.guard()
            if not operable(self.reader.session):
                raise _failed()
            draft = self.reader.session.alarm_draft
            draft.write_submitted = True
            draft.phase = 'set15' if data[0] == 0x15 else 'set16'
            self.reader.session.alarms_fresh = False
            c._native_stage = draft.phase + '_submit'
            c.publish()
        if alarm and query is not None:
            c._native_stage = query + '_submit'
        result = await super().bluetooth_gatt_write(address, handle, data, *args, **kwargs)
        if setting:
            c._native_stage = ('set15' if data[0] == 0x15 else 'set16') + '_wait'
        if alarm and query is not None:
            # Only local write-without-response completion, never a watch ACK.
            c._native_stage = query + '_wait'
        return result

    async def bluetooth_device_disconnect(self, *args, **kwargs):
        controller = self.reader.controller
        # Set before the finalizer's expected false callback. Never cancel the
        # finalizer or overwrite the primary reason because of our own release.
        controller.cleanup_in_progress = True
        if controller.close_reason is None:
            self.reader._latch_failure()
        controller.phase = 'closing'
        controller.publish()
        try:
            result = await super().bluetooth_device_disconnect(*args, **kwargs)
        except BaseException:
            controller.cleanup_result = 'failed'
            controller.link_state = 'unknown'
            raise
        controller.cleanup_result = 'success'
        controller.link_state = 'disconnected'
        return result


class _InteractiveRead(NativeSyncRead):
    def __init__(self, session, entry, controller):
        super().__init__(session, entry)
        self.controller = controller
        self.api = _InteractiveAPI(self)

    def valid(self):
        return self.controller.authority() and super().valid()

    def invalidate(self):
        self.lost = True
        self.controller.link_state = 'unknown'
        self.controller.request_close('generation_lost')

    def observe_link(self, connected, error):
        c = self.controller
        confirmed = connected is True and error == 0
        if c.cleanup_in_progress:
            if not confirmed:
                c.link_state = 'disconnected' if error == 0 else 'unknown'
            return False
        if self.link_established and not confirmed:
            if c.close_reason is None and c.native_fault is None:
                status = error if type(error) is int and -2147483648 <= error <= 2147483647 else None
                c.native_fault = _NativeFault(c._native_stage, status)
            self.link_lost = True
            c.link_state = 'disconnected' if error == 0 else 'unknown'
            if self.adapter is not None:
                self.adapter.receiver = self.adapter.step_receivers = None
            c.request_close('link_lost')
        if confirmed and not self.link_lost and c.close_reason is None:
            if not self.link_established and not c.retain_until_disconnect:
                c._session_deadline = asyncio.get_running_loop().time() + SESSION_SECONDS
                c.session_expires_at = dt_util.utcnow() + timedelta(seconds=SESSION_SECONDS)
            self.link_established = True
            c.link_state = 'connected'
        return confirmed and not self.link_lost and c.close_reason is None

    def set_phase(self, phase, expiry=None):
        c = self.controller
        if c.close_reason is not None:
            return
        c.phase = 'connecting' if phase == 'reading' else phase
        c.wait_expires_at = expiry
        c.publish()

    async def read(self):
        # Observe every primary exit before NativeRead enters its unchanged
        # finalizer, including wait/services/metadata failures. The execution's
        # cancellation counter cannot be used as public caller provenance.
        try:
            return await super().read()
        finally:
            if self.controller.close_reason is None:
                self._latch_failure()

    def _latch_failure(self):
        c = self.controller
        # Already unwinding the execution. Revocation must not inject another
        # cancellation into its finalizer or protected acquisition join.
        c._cancel_sent = True
        if not self.generation_valid():
            reason = 'generation_lost'
        elif self.owner.cancelling():
            reason = 'owner_cancelled'
        elif (c.operation_caller or c.connect_caller) is not None and (
                c.operation_caller or c.connect_caller).cancelling():
            reason = 'caller_cancelled'
        else:
            reason = ('apply_failed' if c.apply_request is not None else
                      'read_failed' if c.operation_caller is not None else 'connect_failed')
        c.request_close(reason)

    async def transfer(self, adapter):
        try:
            return await self._interactive_transfer(adapter)
        except BaseException:
            self._latch_failure()
            raise

    async def _interactive_transfer(self, adapter):
        c = self.controller
        self.guard()
        c.connect_caller = None
        c._idle()
        _resolve(c.ready, _Outcome('success'))
        while True:
            if c.retain_until_disconnect and c._periodic_deadline is not None and c.operation_caller is None:
                delay = max(0, c._periodic_deadline - asyncio.get_running_loop().time())
                try:
                    async with asyncio.timeout(delay):
                        await c.wake.wait()
                except TimeoutError:
                    self.guard()
                    if c.usable and c.operation_caller is None:
                        c._admit_refresh(asyncio.current_task())
            else:
                await c.wake.wait()
            c.wake.clear()
            self.guard()
            if c.operation_caller is None:
                continue
            complete = c.operation_complete
            if c.apply_request is not None:
                from .native_alarm_edit import AlarmApplyView, finish_apply
                request = c.apply_request
                outcome = await AlarmApplyView(self, adapter).apply(request)
                self.guard()
                adapter.guard()
                if outcome.result in ('unchanged', 'conflict'):
                    await self.session.alarm_safety.async_complete(
                        outcome.result, False, lambda: (self.guard(), adapter.guard()))
                    self.guard()
                    adapter.guard()
                finish_apply(self.session, request, outcome)
                if outcome.result in ('verified', 'unchanged'):
                    await self.session.save_alarm_draft()
                    self.guard()
                    adapter.guard()
                c.operation_result = outcome.result
                c.apply_request = None
                c.operation_caller = None
                c._idle()
                _resolve(complete, outcome)
                continue
            if c._cycles:
                adapter.rearm_cycle()
            for group, read, destinations in zip(GROUPS, READERS, DESTINATIONS, strict=True):
                self.guard()
                adapter.guard()
                c._native_stage = 'other'
                c.group_results[group] = 'reading'
                c.publish()
                try:
                    snapshot = await read(adapter)
                    self.guard()
                    adapter.guard()
                    observed_at = dt_util.utcnow()
                    self.guard()
                    adapter.guard()
                    value, timestamp, freshness = destinations
                    setattr(self.session, value, snapshot)
                    setattr(self.session, timestamp, observed_at)
                    setattr(self.session, freshness, True)
                    if group == 'alarms' and self.session.alarm_safety is not None:
                        await self.session.alarm_safety.async_reconcile()
                        self.guard()
                        adapter.guard()
                    c.group_results[group] = 'success'
                except BaseException:
                    c.group_results[group] = 'failed'
                    raise
                self.session._queue_observation_save(group)
                c.publish()
            # Explicit success, not merely last query submission, authorizes
            # the next cycle. Each transport has quiesced its group receivers.
            adapter.finish_cycle()
            c._cycles += 1
            c.operation_result = 'success'
            c.operation_caller = None
            c._idle()
            _resolve(complete, _Outcome('success'))


async def connect_interactive(session, *, admission=None, retain_until_disconnect=False):
    caller = asyncio.current_task()
    if caller.cancelling():
        raise asyncio.CancelledError
    previous = session.interactive_session
    if (session.closed or session.entry is None or session.lock.locked() or session.tasks
            or (previous is not None and (previous.active or not previous.root.done()))):
        raise _failed()
    try:
        if type(retain_until_disconnect) is not bool or (admission is None) != (not retain_until_disconnect):
            raise _failed()
        if admission is not None:
            from .native_sync import _RawSyncAdmission
            if type(admission) is not _RawSyncAdmission:
                raise _failed()
            admission.guard(session)
            device = bluetooth.async_ble_device_from_address(
                session.hass, session.address, connectable=True)
            name = (device.name or '').rstrip('\x00 \t\r\n') if device is not None else ''
            if (device is not None and (device.address.upper() != session.address or name not in (
                    'ABL-100', 'ABL-100WE', 'CASIO ABL-100', 'CASIO ABL-100WE'))):
                raise _failed()
            if select_proxy(session.hass, device) is not admission.entry:
                raise _failed()
    except Exception:
        raise _failed() from None
    controller = NativeInteractiveSession(session, admission=admission,
                                          retain_until_disconnect=retain_until_disconnect)
    session.interactive_session = controller
    controller.start()
    await controller.connected()
