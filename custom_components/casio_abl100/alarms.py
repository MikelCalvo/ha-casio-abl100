"""Read-only alarm snapshot decoder for the Casio ABL-100WE.

Protocol facts: izivkov/GShockAPI Kotlin 1.7.6
(5b1043a888ea3d97331c0c701f2da776ff638d8f, Alarms.kt and AlarmsIO.kt),
corroborated by Python gshock_api ae817310 and module 3565's five alarm slots.
The 0x40 enable and AL1 0x80 hourly-signal bits are source interpretations;
activated bits have not been physically confirmed on the user's ABL-100WE.
Only synthetic inputs are tested here, not watch captures. No SET encoder.
"""

from dataclasses import dataclass

from .protocol import ProtocolError


@dataclass(frozen=True)
class Alarm:
    """One ordered watch slot; flags and auxiliary byte remain uninterpreted."""

    slot: int
    hour: int
    minute: int
    enabled: bool
    flags: int
    aux: int


@dataclass(frozen=True)
class AlarmSnapshot:
    """Complete five-slot read with a separate AL1 hourly-signal bit."""

    alarms: tuple[Alarm, ...]
    hourly_signal: bool


def parse_alarms(first: bytes, remaining: bytes) -> AlarmSnapshot:
    """Decode exact GET 0x15 (AL1) and 0x16 (AL2–AL5) notifications.

    Wire records are [flags, aux, hour, minute], with numeric (not BCD)
    times. Reject incomplete, misplaced, or invalid data instead of
    presenting absent alarms as disabled. Accept GET aux values such as
    0x00 and 0x07; SET's 0x40 constant is not a GET constraint.
    """
    first, remaining = bytes(first), bytes(remaining)
    if len(first) != 5 or len(remaining) != 17:
        raise ProtocolError('alarm response length')
    if first[0] != 0x15 or remaining[0] != 0x16:
        raise ProtocolError('alarm response opcode')

    raw = first[1:] + remaining[1:]
    alarms = []
    for index in range(5):
        flags, aux, hour, minute = raw[index * 4:index * 4 + 4]
        if hour > 23 or minute > 59:
            raise ProtocolError('invalid alarm time')
        alarms.append(Alarm(index + 1, hour, minute, bool(flags & 0x40), flags, aux))
    return AlarmSnapshot(tuple(alarms), bool(first[1] & 0x80))
