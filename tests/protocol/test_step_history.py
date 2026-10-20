"""Observed dated totals are not Recorder samples or fabricated end-of-day data."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import importlib

import pytest

DAY = date(2026, 10, 20)
STAMP = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
MODULE = 'custom_components.casio_abl100.step_history'
PATH = Path(__file__).parents[2] / 'custom_components/casio_abl100/step_history.py'


def api():
    assert PATH.is_file(), 'Dated step archive API missing'
    return importlib.import_module(MODULE)


def window(current=100, previous=50, unknown=2, day=DAY):
    from custom_components.casio_abl100.protocol import DailySteps, StepRecord
    return StepRecord(day, current, tuple(DailySteps(day-timedelta(days=i),
                      None if i == unknown else previous+i) for i in range(1, 8)))


def test_merge_keeps_null_days_and_actual_dates():
    a = api(); result = a.merge_step_history((), window(), STAMP)
    assert len(result) == 8 and tuple(x.record_date for x in result) == tuple(DAY-timedelta(days=i) for i in range(8))
    assert result[0].steps == 100 and result[0].source == 'current'
    assert result[1].steps == 51 and result[1].source == 'watch_history'
    assert result[2].steps is None and result[2].observed_at == STAMP


def test_known_history_is_not_erased_by_later_unknown():
    a = api(); first = a.merge_step_history((), window(unknown=0), STAMP)
    later = a.merge_step_history(first, window(unknown=2), STAMP+timedelta(hours=1))
    assert later[2] == first[2]
    assert later[0].observed_at == STAMP+timedelta(hours=1)


def test_prior_day_total_supersedes_provisional_current_even_lower_count():
    a = api(); first = a.merge_step_history((), window(current=900), STAMP)
    second = a.merge_step_history(first, window(previous=10, day=DAY+timedelta(days=1)), STAMP+timedelta(days=1))
    old = next(x for x in second if x.record_date == DAY)
    assert old.steps == 11 and old.source == 'watch_history'
    # A later stale window/current sample cannot replace a previously observed history total.
    third = a.merge_step_history(second, window(current=9999), STAMP+timedelta(days=2))
    assert next(x for x in third if x.record_date == DAY) == old


def test_same_source_latest_stamp_wins_and_equal_stamp_does_not_invent_order():
    a = api(); first = a.merge_step_history((), window(current=5), STAMP)
    equal = a.merge_step_history(first, window(current=6), STAMP)
    assert equal[0].steps == 5
    old = a.merge_step_history(first, window(current=6), STAMP-timedelta(seconds=1))
    assert old[0].steps == 5
    new = a.merge_step_history(first, window(current=6), STAMP+timedelta(seconds=1))
    assert new[0].steps == 6


def test_missing_known_values_recover_and_archive_is_bounded_by_record_date():
    a = api(); archive = ()
    for i in range(380):
        archive = a.merge_step_history(archive, window(day=DAY+timedelta(days=i)), STAMP+timedelta(days=i))
    assert len(archive) == 366
    assert archive[0].record_date == DAY+timedelta(days=379)
    assert archive[-1].record_date == archive[0].record_date-timedelta(days=365)
    view = a.history_projection(archive)
    assert len(view) == 30 and list(view)[0] == archive[0].record_date.isoformat()
    assert set(next(iter(view.values()))) == {'steps', 'source', 'observed_at'}


def test_known_sample_recovers_unknown_without_inventing_a_newer_stamp():
    a = api()
    missing = a.merge_step_history((), window(unknown=2), STAMP)
    repaired = a.merge_step_history(missing, window(unknown=0), STAMP-timedelta(hours=1))
    assert repaired[2].steps == 52
    assert repaired[2].observed_at == STAMP-timedelta(hours=1)


def test_roundtrip_strict_and_preserves_original_stamps():
    a = api(); archive = a.merge_step_history((), window(), STAMP)
    encoded = a.encode_step_history(archive)
    assert a.decode_step_history(encoded) == archive
    assert encoded[0] == {'record_date': DAY.isoformat(), 'steps': 100,
                          'source': 'current', 'observed_at': STAMP.isoformat()}


@pytest.mark.parametrize('mutation', ['bool_steps', 'negative', 'large', 'bad_date', 'nonutc',
                                     'naive', 'bad_source', 'extra', 'duplicate', 'order', 'type'])
def test_corrupt_archive_rejected_without_partial_acceptance(mutation):
    a = api(); data = a.encode_step_history(a.merge_step_history((), window(), STAMP))
    if mutation == 'bool_steps': data[0]['steps'] = True
    elif mutation == 'negative': data[0]['steps'] = -1
    elif mutation == 'large': data[0]['steps'] = 100000000
    elif mutation == 'bad_date': data[0]['record_date'] = '2026-W43-2'
    elif mutation == 'nonutc': data[0]['observed_at'] = '2026-10-20T12:00:00+01:00'
    elif mutation == 'naive': data[0]['observed_at'] = '2026-10-20T12:00:00'
    elif mutation == 'bad_source': data[0]['source'] = 'PRIVATE'
    elif mutation == 'extra': data[0]['raw'] = 'PRIVATE'
    elif mutation == 'duplicate': data[1] = data[0].copy()
    elif mutation == 'order': data.reverse()
    else: data = {}
    with pytest.raises(ValueError): a.decode_step_history(data)


@pytest.mark.parametrize('invalid', ['naive', 'nonutc', 'short', 'wrong_date', 'bool_steps'])
def test_incoming_window_all_or_nothing_validation(invalid):
    a = api(); stamp = STAMP; record = window()
    from custom_components.casio_abl100.protocol import StepRecord, DailySteps
    if invalid == 'naive': stamp = STAMP.replace(tzinfo=None)
    elif invalid == 'nonutc': stamp = STAMP.astimezone(timezone(timedelta(hours=1)))
    elif invalid == 'short': record = StepRecord(DAY, 1, ())
    elif invalid == 'wrong_date': record = StepRecord(DAY, 1, (DailySteps(DAY, 2),)+record.daily_history[1:])
    else: record = StepRecord(DAY, True, record.daily_history)
    with pytest.raises(ValueError): a.merge_step_history((), record, stamp)
