"""Pure ABL-100 alarm/SIG SET framing and expected GET comparison.

Source contract: GShockAPI 5b1043a888ea3d97331c0c701f2da776ff638d8f,
Alarms.kt and AlarmsIO.kt. SET auxiliaries are canonical 0x40; their
physical equivalence to GET auxiliaries is not established here. No IO.
"""

from dataclasses import dataclass, replace

from .alarms import Alarm, AlarmSnapshot
from .protocol import ProtocolError


@dataclass(frozen=True)
class AlarmEdit:
    """Explicit optional changes to one of the five alarm slots."""

    slot: int
    hour: int | None = None
    minute: int | None = None
    enabled: bool | None = None


@dataclass(frozen=True)
class AlarmWriteIntent:
    """An immutable set of slot edits and an independent optional SIG edit."""

    alarms: tuple[AlarmEdit, ...] = ()
    hourly_signal: bool | None = None


def _is_int(value: object, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def _known_flags(slot: int) -> int:
    return 0xC0 if slot == 1 else 0x40


def _validate_snapshot(snapshot: AlarmSnapshot) -> None:
    """Check complete GET structure and consistency, not writer support."""
    if (type(snapshot) is not AlarmSnapshot or type(snapshot.alarms) is not tuple
            or len(snapshot.alarms) != 5 or type(snapshot.hourly_signal) is not bool):
        raise ProtocolError('invalid alarm snapshot')
    for slot, alarm in enumerate(snapshot.alarms, 1):
        if (type(alarm) is not Alarm or not _is_int(alarm.slot, slot, slot)
                or not _is_int(alarm.hour, 0, 23) or not _is_int(alarm.minute, 0, 59)
                or type(alarm.enabled) is not bool
                or not _is_int(alarm.flags, 0, 255) or not _is_int(alarm.aux, 0, 255)):
            raise ProtocolError('invalid alarm snapshot')
        if alarm.enabled != bool(alarm.flags & 0x40):
            raise ProtocolError('invalid alarm snapshot')
    if snapshot.hourly_signal != bool(snapshot.alarms[0].flags & 0x80):
        raise ProtocolError('invalid alarm snapshot')


def _validate_baseline(baseline: AlarmSnapshot) -> None:
    _validate_snapshot(baseline)
    for alarm in baseline.alarms:
        if alarm.flags & ~_known_flags(alarm.slot) or alarm.aux not in (0, 7):
            raise ProtocolError('unsupported alarm baseline')


def _validate_intent(intent: AlarmWriteIntent) -> None:
    if (type(intent) is not AlarmWriteIntent or type(intent.alarms) is not tuple
            or (intent.hourly_signal is not None and type(intent.hourly_signal) is not bool)):
        raise ProtocolError('invalid alarm write intent')
    seen = set()
    for edit in intent.alarms:
        if (type(edit) is not AlarmEdit or not _is_int(edit.slot, 1, 5)
                or (edit.hour is not None and not _is_int(edit.hour, 0, 23))
                or (edit.minute is not None and not _is_int(edit.minute, 0, 59))
                or (edit.enabled is not None and type(edit.enabled) is not bool)):
            raise ProtocolError('invalid alarm write intent')
        if edit.slot in seen:
            raise ProtocolError('invalid alarm write intent')
        seen.add(edit.slot)


def expected_alarm_readback(baseline: AlarmSnapshot, intent: AlarmWriteIntent) -> AlarmSnapshot:
    """Return an expected GET value, never an observation or freshness claim.

    Preserve GET auxiliary bytes, unrelated fields and independent SIG. The
    caller must separately establish entry, generation, freshness and consent.
    """
    _validate_baseline(baseline)
    _validate_intent(intent)
    alarms = list(baseline.alarms)
    for edit in intent.alarms:
        old = alarms[edit.slot - 1]
        enabled = old.enabled if edit.enabled is None else edit.enabled
        alarms[edit.slot - 1] = replace(
            old,
            hour=old.hour if edit.hour is None else edit.hour,
            minute=old.minute if edit.minute is None else edit.minute,
            enabled=enabled,
            flags=(old.flags & ~0x40) | (0x40 if enabled else 0),
        )
    signal = baseline.hourly_signal if intent.hourly_signal is None else intent.hourly_signal
    alarms[0] = replace(alarms[0], flags=(alarms[0].flags & ~0x80) | (0x80 if signal else 0))
    return AlarmSnapshot(tuple(alarms), signal)


def build_alarm_set_pair(baseline: AlarmSnapshot, intent: AlarmWriteIntent) -> tuple[bytes, bytes] | None:
    """Validate everything before framing both SETs; return None for a no-op.

    Auxiliary 0x40 is a canonical SET representation, not expected GET data.
    Every effective change requires both SET15 and SET16, in that order.
    """
    expected = expected_alarm_readback(baseline, intent)
    if expected == baseline:
        return None
    records = tuple(bytes((a.flags, 0x40, a.hour, a.minute)) for a in expected.alarms)
    return b'\x15' + records[0], b'\x16' + b''.join(records[1:])


def compare_alarm_write_readback(baseline: AlarmSnapshot, intent: AlarmWriteIntent,
                                observed: AlarmSnapshot) -> str:
    """Compare all five GET records and SIG; equality is not causal proof.

    Structurally valid, differing raw byte values remain observations, not
    malformed data. Auxiliary/unsupported-bit differences take precedence over
    known-semantic mismatches. No ACK, transport or physical support claim.
    """
    expected = expected_alarm_readback(baseline, intent)
    _validate_snapshot(observed)
    for before, after in zip(baseline.alarms, observed.alarms):
        if (before.aux != after.aux
                or (before.flags ^ after.flags) & ~_known_flags(before.slot)):
            return 'preservation_mismatch'
    return 'verified' if expected == observed else 'mismatch'
