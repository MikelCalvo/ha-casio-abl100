"""Finite alarm Apply view on the retained native lifetime receiver."""
from .alarm_write import build_alarm_set_pair, compare_alarm_write_readback
from .alarm_editing import AlarmApplyOutcome, operable
from .native_alarm_adapter import FEATURES, PRIMARY, _unique, _uuid
from .sync_transport import read_alarm_group
from homeassistant.util import dt as dt_util


class AlarmApplyView:
    """Borrow one adapter; never acquire a client, lock or second subscription."""
    def __init__(self,reader,adapter):
        self.reader,self.adapter = reader,adapter
        self.index = 0
        self.invalid = False
        self._receiver = None
        service = _unique(s for s in adapter.metadata.services if _uuid(s.uuid) == FEATURES)
        char = _unique(c for c in service.characteristics if _uuid(c.uuid) == PRIMARY)
        if not char.properties & 0x08 or char.handle != adapter.primary.handle:
            raise ValueError('Alarm write capability unavailable')

    def guard(self):
        self.reader.guard()
        self.adapter.guard()
        if not operable(self.reader.session) or self.reader.session.interactive_session is not self.reader.controller:
            raise RuntimeError('Alarm entry no longer current')
        if self.invalid:
            raise ValueError('Invalid alarm apply response')

    @property
    def receiver(self):
        return self._receiver

    @receiver.setter
    def receiver(self,callback):
        self._receiver = callback
        # Retain error/unsolicited-frame handling during both SET awaits.
        self.adapter.receiver = self.receive

    def receive(self,data):
        if self._receiver is not None:
            self._receiver(data)
        elif not data or data[0] in (0x15,0x16,0xff):
            self.invalid = True

    async def start_primary(self):
        self.guard()
        await self.adapter._ensure_primary_subscription()

    async def query_primary(self,data):
        self.guard()
        if self.index >= 4 or data != (b'\x15',b'\x16',b'\x15',b'\x16')[self.index]:
            raise ValueError('Unexpected alarm apply query')
        self.index += 1
        await self.adapter.api.bluetooth_gatt_write(self.adapter.address,self.adapter.query.handle,data,False,10)
        self.guard()

    def commit(self,snapshot):
        self.guard()
        observed_at = dt_util.utcnow()
        self.guard()
        session = self.reader.session
        session.alarm_snapshot = snapshot
        session.alarm_last_sync = observed_at
        session.alarms_fresh = True
        session._queue_observation_save('alarms')
        self.reader.controller.publish()
        return observed_at

    async def apply(self,request):
        draft = self.reader.session.alarm_draft
        try:
            draft.phase = 'baseline'
            baseline = await read_alarm_group(self)
            observed_at = self.commit(baseline)
            # Codec validates the entire raw baseline even if the intent is no-op.
            pair = build_alarm_set_pair(baseline,request.intent)
            self.guard()
            if draft.conflicts(baseline):
                draft.conflicted = True
                return AlarmApplyOutcome('conflict',False,observed_at)
            if pair is None:
                return AlarmApplyOutcome('unchanged',False,observed_at)
            # A local acknowledged conservative barrier precedes any possible SET.
            await self.reader.session.alarm_safety.async_pending()
            self.guard()
            # Both canonical packets were built before the first invocation.
            for data in pair:
                self.guard()
                await self.adapter.api.bluetooth_gatt_write(
                    self.adapter.address,self.adapter.primary.handle,data,True,10)
                self.guard()
            draft.phase = 'readback'
            observed = await read_alarm_group(self)
            observed_at = self.commit(observed)
            result = compare_alarm_write_readback(baseline,request.intent,observed)
            await self.reader.session.alarm_safety.async_complete(result, True, self.guard)
            self.guard()
            return AlarmApplyOutcome(result,True,observed_at)
        finally:
            self._receiver = None
            self.adapter.receiver = None


def finish_apply(session,request,outcome):
    draft = session.alarm_draft
    draft.outcome = outcome
    draft.write_submitted = outcome.write_submitted
    if outcome.result == 'unknown':
        draft.needs_reconciliation = True
        session.alarms_fresh = False
    if outcome.result in ('verified','unchanged'):
        draft.verified_at = outcome.observed_at
        draft.clear(request.revision)
        draft.phase = 'complete'
    session._schedule_alarm_delivery()
