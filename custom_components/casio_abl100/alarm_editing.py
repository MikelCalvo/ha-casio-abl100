"""Nonpersistent sparse alarm draft, distinct from validated observations."""
from dataclasses import dataclass
from datetime import time

from .alarm_write import AlarmEdit, AlarmWriteIntent


@dataclass(frozen=True)
class AlarmApplyOutcome:
    result: str
    write_submitted: bool
    observed_at: object = None


class AlarmDraft:
    def __init__(self, safety=None):
        self.safety = safety
        self.times = {}
        self.enabled = {}
        self.signal = None
        self.revision = 0
        self.anchor = None
        self.applying = False
        self.apply_revision = None
        self.phase = 'idle'
        self.outcome = (AlarmApplyOutcome(safety.result, safety.submitted)
                        if safety is not None and safety.result is not None else None)
        self.write_submitted = safety.submitted if safety is not None else False
        self.needs_reconciliation = False
        self.verified_at = None
        self.conflicted = False

    @property
    def needs_reconciliation(self):
        return self.safety.blocked if self.safety is not None else self._needs_reconciliation

    @needs_reconciliation.setter
    def needs_reconciliation(self, value):
        self._needs_reconciliation = value
        if value and self.safety is not None:
            self.safety.blocked = True

    @property
    def pending(self):
        return bool(self.times or self.enabled or self.signal is not None)

    @staticmethod
    def semantic(snapshot):
        if snapshot is None:
            return None
        return (tuple((a.slot,a.hour,a.minute,a.enabled,
                       getattr(a,'flags',None),getattr(a,'aux',None)) for a in snapshot.alarms),
                snapshot.hourly_signal)

    def conflicts(self, snapshot):
        if self.anchor is None:
            return False
        actual = self.semantic(snapshot)
        return (self.anchor[1] != actual[1] or any(
            known is not None and known != value
            for before,after in zip(self.anchor[0],actual[0],strict=True)
            for known,value in zip(before,after,strict=True)))

    @staticmethod
    def _slot(slot):
        if type(slot) is not int or not 1 <= slot <= 5:
            raise ValueError('Invalid alarm slot')

    def _stage(self,snapshot):
        if not self.pending:
            self.anchor = self.semantic(snapshot)
        self.revision += 1

    def stage_time(self,slot,value,snapshot):
        self._slot(slot)
        if type(value) is not time or value.second or value.microsecond or value.tzinfo is not None:
            raise ValueError('Minute precision required')
        self._stage(snapshot)
        self.times[slot] = value

    def stage_enabled(self,slot,value,snapshot):
        self._slot(slot)
        if type(value) is not bool:
            raise ValueError('Boolean required')
        self._stage(snapshot)
        self.enabled[slot] = value

    def stage_signal(self,value,snapshot):
        if type(value) is not bool:
            raise ValueError('Boolean required')
        self._stage(snapshot)
        self.signal = value

    def value(self,field,slot,snapshot):
        if field == 'signal':
            return self.signal if self.signal is not None else (snapshot.hourly_signal if snapshot else None)
        overrides = self.times if field == 'time' else self.enabled
        if slot in overrides:
            return overrides[slot]
        if snapshot is None:
            return None
        alarm = snapshot.alarms[slot-1]
        return time(alarm.hour,alarm.minute) if field == 'time' else alarm.enabled

    def freeze(self):
        if not self.pending:
            raise ValueError('Empty alarm draft')
        intent = AlarmWriteIntent(tuple(
            AlarmEdit(slot,
                      self.times[slot].hour if slot in self.times else None,
                      self.times[slot].minute if slot in self.times else None,
                      self.enabled.get(slot))
            for slot in sorted(self.times.keys() | self.enabled.keys())),self.signal)
        return self.revision,intent,self.anchor

    def clear(self,revision=None):
        if revision is not None and revision != self.revision:
            return
        self.times.clear()
        self.enabled.clear()
        self.signal = None
        self.anchor = None
        self.conflicted = False
        self.revision += 1

    def attributes(self,session):
        c = session.interactive_session
        return dict(draft_pending=self.pending,draft_revision=self.revision,
                    apply_revision=self.apply_revision,apply_phase=self.phase,
                    apply_result=self.outcome.result if self.outcome else 'never',
                    write_submitted=self.write_submitted,
                    needs_reconciliation=self.needs_reconciliation,
                    verified_at=self.verified_at.isoformat() if self.verified_at else None,
                    cleanup_result=c.cleanup_result if c else 'not_attempted')


def operable(session):
    from homeassistant.config_entries import ConfigEntryState
    return (not session.closed and session.entry is not None
            and session.entry.state is ConfigEntryState.LOADED
            and session.entry.runtime_data is session)


class DraftEntity:
    """Shared identity, lifecycle and truthful observation metadata."""
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self,session,key,field=None,slot=None):
        from homeassistant.helpers.device_registry import DeviceInfo
        from . import DOMAIN,MODEL
        self.session,self.field,self.slot = session,field,slot
        self._attr_unique_id = f'{session.identity}_{key}'
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN,session.identity)},
            name='Casio ABL-100WE',model=MODEL,manufacturer='Casio')

    @property
    def available(self):
        return operable(self.session) and not self.session.alarm_draft.applying

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.session.subscribe(self.async_write_ha_state))
        self.async_on_remove(self.session.entry.async_on_state_change(self.async_write_ha_state))

    @property
    def capability_attributes(self):
        # HA drops extra attributes for unavailable entities. Operation evidence
        # must remain visible while Apply is busy or blocked after uncertainty.
        return self.extra_state_attributes

    @property
    def extra_state_attributes(self):
        draft = self.session.alarm_draft
        attrs = draft.attributes(self.session)
        if self.field is not None:
            observed = AlarmDraft().value(self.field,self.slot,self.session.alarm_snapshot)
            if type(observed) is time:
                observed = observed.isoformat()
            pending = (draft.signal is not None if self.field == 'signal' else
                       self.slot in (draft.times if self.field == 'time' else draft.enabled))
            attrs.update(pending=pending,observed_value=observed,
                         observed_at=self.session.alarm_last_sync.isoformat() if self.session.alarm_last_sync else None,
                         observed_fresh=self.session.alarms_fresh)
        return attrs
