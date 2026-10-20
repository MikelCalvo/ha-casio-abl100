"""Bounded observed dated totals, not Recorder samples or end-of-day inference."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .protocol import DailySteps, StepRecord

RETENTION_DAYS = 366
VISIBLE_DAYS = 30


@dataclass(frozen=True)
class DayTotal:
    record_date: date
    steps: int | None
    observed_at: datetime
    source: str


def _day(value):
    if type(value) is not date or not 2000 <= value.year <= 2099:
        raise ValueError('Invalid archive date')


def _stamp(value):
    if (type(value) is not datetime or value.tzinfo is None
            or value.utcoffset() != timedelta(0)):
        raise ValueError('Invalid archive timestamp')


def _steps(value):
    if value is not None and (type(value) is not int or not 0 <= value <= 99999999):
        raise ValueError('Invalid archived steps')


def _validate(history):
    if type(history) is not tuple or len(history) > RETENTION_DAYS:
        raise ValueError('Invalid step archive')
    previous = None
    for item in history:
        if type(item) is not DayTotal or type(item.source) is not str or item.source not in ('current', 'watch_history'):
            raise ValueError('Invalid archive row')
        _day(item.record_date)
        _stamp(item.observed_at)
        _steps(item.steps)
        if previous is not None and item.record_date >= previous:
            raise ValueError('Invalid archive order')
        previous = item.record_date


def merge_step_history(existing, record: StepRecord, observed_at: datetime) -> tuple[DayTotal, ...]:
    """Merge an entire validated eight-day observation with honest provenance."""
    _validate(existing)
    _stamp(observed_at)
    if type(record) is not StepRecord or type(record.daily_history) is not tuple or len(record.daily_history) != 7:
        raise ValueError('Invalid step window')
    _day(record.record_date)
    _steps(record.steps)
    if record.steps is None:
        raise ValueError('Invalid current steps')
    incoming = [DayTotal(record.record_date, record.steps, observed_at, 'current')]
    for i, item in enumerate(record.daily_history, 1):
        if type(item) is not DailySteps or item.record_date != record.record_date - timedelta(days=i):
            raise ValueError('Invalid step window')
        _day(item.record_date)
        _steps(item.steps)
        incoming.append(DayTotal(item.record_date, item.steps, observed_at, 'watch_history'))
    merged = {item.record_date: item for item in existing}
    rank = {'current': 0, 'watch_history': 1}
    for item in incoming:
        old = merged.get(item.record_date)
        if old is not None:
            if old.steps is not None and item.steps is None:
                continue
            if rank[item.source] < rank[old.source]:
                continue
            recovering = old.steps is None and item.steps is not None
            if (not recovering and rank[item.source] == rank[old.source]
                    and item.observed_at <= old.observed_at):
                continue
        merged[item.record_date] = item
    return tuple(merged[day] for day in sorted(merged, reverse=True)[:RETENTION_DAYS])


def encode_step_history(history) -> list[dict]:
    _validate(history)
    return [{'record_date': row.record_date.isoformat(), 'steps': row.steps,
             'observed_at': row.observed_at.isoformat(), 'source': row.source} for row in history]


def decode_step_history(raw) -> tuple[DayTotal, ...]:
    if type(raw) is not list or len(raw) > RETENTION_DAYS:
        raise ValueError('Invalid step archive')
    result = []
    for row in raw:
        if type(row) is not dict or set(row) != {'record_date', 'steps', 'observed_at', 'source'}:
            raise ValueError('Invalid archive row')
        day, stamp = row['record_date'], row['observed_at']
        if type(day) is not str or len(day) != 10 or type(stamp) is not str or len(stamp) > 40:
            raise ValueError('Invalid archive representation')
        try:
            parsed_day = date.fromisoformat(day)
            parsed_stamp = datetime.fromisoformat(stamp)
        except ValueError:
            raise ValueError('Invalid archive representation') from None
        if parsed_day.isoformat() != day:
            raise ValueError('Invalid archive representation')
        result.append(DayTotal(parsed_day, row['steps'], parsed_stamp, row['source']))
    result = tuple(result)
    _validate(result)
    return result


def history_projection(history) -> dict:
    """Bound attribute size without fabricating absent calendar days."""
    return {row['record_date']: {key: value for key, value in row.items() if key != 'record_date'}
            for row in encode_step_history(history)[:VISIBLE_DAYS]}
