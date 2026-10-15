"""Synthetic daily summaries, not a captured or physically accepted history."""
from dataclasses import FrozenInstanceError
from datetime import date, timedelta

import pytest

from custom_components.casio_abl100.protocol import ProtocolError, StepRecord, parse_steps
from tests.protocol.fixtures import SYNTHETIC_RECORD


TOTALS = (1234, 0, 0xFFFFFFFE, 7654, 42, 99999999, 1)


def history_record(record_date=date(2026, 8, 1), totals=TOTALS):
    """Construct independent uint32 slot inputs; distance is deliberately ignored."""
    raw = bytearray(SYNTHETIC_RECORD)
    for index, value in enumerate((record_date.year - 2000, record_date.month, record_date.day)):
        raw[index] = (value // 10) * 16 + value % 10
    assert len(totals) == 7
    for index, steps in enumerate(totals):
        offset = 318 + index * 8
        raw[offset:offset + 4] = steps.to_bytes(4, 'little')
        raw[offset + 4:offset + 8] = (0xFFFFFFFE).to_bytes(4, 'little')
    return bytes(raw)


def test_seven_daily_slots_keep_zero_unknown_and_original_dates():
    result = parse_steps(history_record())
    assert hasattr(result, 'daily_history'), 'StepRecord must expose a dated daily-history snapshot'
    assert isinstance(result.daily_history, tuple)
    assert [(entry.record_date, entry.steps) for entry in result.daily_history] == [
        (date(2026, 7, 31), 1234),
        (date(2026, 7, 30), 0),
        (date(2026, 7, 29), None),
        (date(2026, 7, 28), 7654),
        (date(2026, 7, 27), 42),
        (date(2026, 7, 26), 99999999),
        (date(2026, 7, 25), 1),
    ]
    assert (result.record_date, result.steps) == (date(2026, 8, 1), 1485)


@pytest.mark.parametrize('record_date', [date(2027, 1, 1), date(2028, 3, 1), date(2026, 3, 1)])
def test_dates_follow_watch_header_across_year_month_and_leap_day(record_date):
    result = parse_steps(history_record(record_date))
    assert hasattr(result, 'daily_history'), 'Dated history is not implemented'
    assert [entry.record_date for entry in result.daily_history] == [
        record_date - timedelta(days=days) for days in range(1, 8)
    ]


@pytest.mark.parametrize('slot', range(7))
@pytest.mark.parametrize('invalid', [0xFFFFFFFE, 0xFFFFFFFF, 100000000])
def test_unknown_historical_slot_never_shifts_or_invalidates_other_days(slot, invalid):
    totals = [10, 20, 30, 40, 50, 60, 70]
    totals[slot] = invalid
    result = parse_steps(history_record(totals=totals))
    assert hasattr(result, 'daily_history'), 'Dated history is not implemented'
    assert [entry.steps for entry in result.daily_history] == [
        None if index == slot else (index + 1) * 10 for index in range(7)
    ]
    assert len({entry.record_date for entry in result.daily_history}) == 7
    assert result.steps == 1485


def test_all_missing_is_seven_unknown_days_not_seven_zeroes():
    result = parse_steps(history_record(totals=(0xFFFFFFFE,) * 7))
    assert hasattr(result, 'daily_history'), 'Dated history is not implemented'
    assert len(result.daily_history) == 7
    assert all(entry.steps is None for entry in result.daily_history)


def test_snapshot_and_daily_values_are_immutable():
    result = parse_steps(history_record())
    assert hasattr(result, 'daily_history'), 'Dated history is not implemented'
    with pytest.raises(FrozenInstanceError):
        result.daily_history = ()
    with pytest.raises(FrozenInstanceError):
        result.daily_history[0].steps = 999


def test_old_two_argument_records_do_not_manufacture_history():
    result = StepRecord(date(2026, 8, 1), 1485)
    assert hasattr(result, 'daily_history'), 'StepRecord needs a backward-compatible empty history'
    assert result.daily_history == ()


@pytest.mark.parametrize('mutation', ['short', 'bad-date', 'bad-current', 'bad-mirror'])
def test_history_never_relaxes_existing_current_record_validation(mutation):
    raw = bytearray(history_record())
    if mutation == 'short':
        raw.pop()
    elif mutation == 'bad-date':
        raw[1] = 0x1A
    elif mutation == 'bad-current':
        raw[374:378] = (0xFFFFFFFE).to_bytes(4, 'little')
    else:
        raw[396] = 0x1A
    with pytest.raises(ProtocolError):
        parse_steps(bytes(raw))
