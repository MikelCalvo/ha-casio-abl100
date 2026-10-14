"""ABL-100WE 400-byte daily record subset. Wire facts from MIT gshock_api
(izivkov/gshock_api ae817310, step_counter_io.py); capture from taviso/casiosync
86b76d93, independently decoded; no casiosync code is included.
"""
from dataclasses import dataclass
from datetime import date


class ProtocolError(ValueError):
    """Invalid or incomplete watch record."""


@dataclass(frozen=True)
class StepRecord:
    record_date: date
    steps: int


def parse_steps(payload: bytes) -> StepRecord:
    """Decode a complete, coherent current-day total."""
    if len(payload) != 400:
        raise ProtocolError('record length')

    def bcd(value: int) -> int:
        tens, units = divmod(value, 16)
        if tens > 9 or units > 9:
            raise ProtocolError('invalid BCD')
        return tens * 10 + units

    # First five bytes: year/month/day/hour/minute. The sixth is not
    # necessarily seconds and is deliberately not interpreted.
    year, month, day, hour, minute = map(bcd, payload[:5])
    try:
        record_date = date(2000 + year, month, day)
    except ValueError as error:
        raise ProtocolError('invalid watch date') from error
    if hour > 23 or minute > 59:
        raise ProtocolError('invalid watch time')
    steps = int.from_bytes(payload[374:378], 'little')
    if steps == 0xFFFFFFFE or steps > 99999999:
        raise ProtocolError('missing step count')
    # An all-zero mirror is optional. For a zero step count it is also
    # coherent; any populated mirror must decode and agree.
    mirror = payload[396:400]
    if any(mirror):
        bcd_total = sum(bcd(value) * (100 ** index) for index, value in enumerate(mirror))
        if steps != bcd_total:
            raise ProtocolError('step totals disagree')
    return StepRecord(record_date, steps)
