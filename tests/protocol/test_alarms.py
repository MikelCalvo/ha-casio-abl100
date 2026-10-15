"""Synthetic alarm frames only; no watch, capture, transport, or HA access."""

from dataclasses import FrozenInstanceError
from importlib import import_module, util
from itertools import product

import pytest

from custom_components.casio_abl100.protocol import ProtocolError


# A missing module must be a behavioral RED assertion, not an import/setup error.
def test_read_only_alarm_parser_api_exists():
    spec = util.find_spec('custom_components.casio_abl100.alarms')
    assert spec is not None, 'read-only alarm parser is not implemented'
    assert callable(getattr(import_module(spec.name), 'parse_alarms', None))


def parser():
    return import_module('custom_components.casio_abl100.alarms').parse_alarms


def packets(records=None):
    """Construct complete 0x15/0x16 notifications, never captured bytes."""
    if records is None:
        records = [(0, 0, 0, 0), (0, 7, 18, 37), (0, 0, 23, 59),
                   (0, 0, 12, 34), (0, 0, 1, 2)]
    return (bytes([0x15, *records[0]]),
            bytes([0x16, *(byte for record in records[1:] for byte in record)]))


def replace_record(slot, *, flag=None, aux=None, hour=None, minute=None):
    first, remaining = packets()
    records = [list(first[1:])] + [list(remaining[1+i*4:5+i*4]) for i in range(4)]
    for index, value in enumerate((flag, aux, hour, minute)):
        if value is not None:
            records[slot - 1][index] = value
    return packets(records)


def test_five_ordered_slots_decode_numeric_not_bcd_time_and_are_frozen():
    result = parser()(*packets())
    assert isinstance(result.alarms, tuple) and len(result.alarms) == 5
    assert [(alarm.slot, alarm.hour, alarm.minute, alarm.enabled,
             alarm.flags, alarm.aux) for alarm in result.alarms] == [
                 (1, 0, 0, False, 0, 0),
                 (2, 18, 37, False, 0, 7),
                 (3, 23, 59, False, 0, 0),
                 (4, 12, 34, False, 0, 0),
                 (5, 1, 2, False, 0, 0),
             ]
    assert result.hourly_signal is False
    with pytest.raises(FrozenInstanceError):
        result.hourly_signal = True
    with pytest.raises(FrozenInstanceError):
        result.alarms[0].hour = 7


@pytest.mark.parametrize('which,bad', [
    ('first', b''), ('first', b'\x15'), ('first', b'\x15\0\0\0'),
    ('first', b'\x15\0\0\0\0\0'),
    ('remaining', b''), ('remaining', b'\x16'),
    ('remaining', b'\x16' + b'\0' * 15),
    ('remaining', b'\x16' + b'\0' * 17),
])
def test_only_exact_five_and_seventeen_byte_responses_are_accepted(which, bad):
    first, remaining = packets()
    with pytest.raises(ProtocolError):
        parser()(bad if which == 'first' else first,
                 bad if which == 'remaining' else remaining)


@pytest.mark.parametrize('which,opcode', [
    ('first', 0x16), ('first', 0xff), ('first', 0),
    ('remaining', 0x15), ('remaining', 0xff), ('remaining', 0),
])
def test_wrong_opcodes_are_not_decoded_even_with_correct_length(which, opcode):
    first, remaining = packets()
    if which == 'first':
        first = bytes([opcode]) + first[1:]
    else:
        remaining = bytes([opcode]) + remaining[1:]
    with pytest.raises(ProtocolError):
        parser()(first, remaining)


@pytest.mark.parametrize('slot', range(1, 6))
@pytest.mark.parametrize('field,value', [
    ('hour', 24), ('hour', 0xff), ('minute', 60), ('minute', 0xff),
])
def test_invalid_hour_or_minute_is_rejected_in_each_slot(slot, field, value):
    with pytest.raises(ProtocolError):
        parser()(*replace_record(slot, **{field: value}))


@pytest.mark.parametrize('slot', range(1, 6))
@pytest.mark.parametrize('field,value', [
    ('hour', 0), ('hour', 23), ('minute', 0), ('minute', 59),
])
def test_valid_time_boundaries_are_accepted_in_each_slot(slot, field, value):
    result = parser()(*replace_record(slot, **{field: value}))
    assert getattr(result.alarms[slot-1], field) == value


@pytest.mark.parametrize('enabled', list(product((False, True), repeat=5)))
def test_every_enable_bit_combination_is_independent(enabled):
    records = [(0x40 if state else 0, 0, 0, 0) for state in enabled]
    result = parser()(*packets(records))
    assert tuple(alarm.enabled for alarm in result.alarms) == enabled
    assert result.hourly_signal is False


@pytest.mark.parametrize('signal', [False, True])
def test_hourly_signal_comes_only_from_al1_even_when_other_flags_have_bit7(signal):
    records = [(0x80 if signal else 0, 0, 0, 0)] + [(0x80, 0, 0, 0)] * 4
    result = parser()(*packets(records))
    assert result.hourly_signal is signal
    assert tuple(alarm.enabled for alarm in result.alarms) == (False,) * 5
    assert tuple(alarm.flags for alarm in result.alarms) == (records[0][0],) + (0x80,) * 4


def test_uninterpreted_flag_bits_and_aux_are_preserved_without_set_constant():
    records = [(0xbf, 0, 0, 0), (0x81, 7, 1, 2),
               (0x41, 0xff, 3, 4), (0x20, 0x40, 5, 6),
               (0x02, 0x55, 7, 8)]
    result = parser()(*packets(records))
    assert tuple((alarm.flags, alarm.aux) for alarm in result.alarms) == tuple(
        (flag, aux) for flag, aux, _, _ in records)
    assert tuple(alarm.enabled for alarm in result.alarms) == (
        False, False, True, False, False)
    assert result.hourly_signal is True


def test_mutating_input_bytearrays_after_parse_cannot_mutate_snapshot():
    first, remaining = map(bytearray, packets())
    result = parser()(first, remaining)
    first[:] = b'\xff' * len(first)
    remaining[:] = b'\xff' * len(remaining)
    assert result.alarms[0].flags == 0 and result.alarms[0].hour == 0
    assert result.alarms[1].aux == 7 and result.alarms[1].minute == 37
    assert result.hourly_signal is False
