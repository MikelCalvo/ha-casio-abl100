"""Offline wire-format tests; no Home Assistant imports."""
from datetime import date
import pytest

from custom_components.casio_abl100.protocol import parse_steps, ProtocolError
from tests.protocol.fixtures import SYNTHETIC_RECORD

RAW = SYNTHETIC_RECORD


def test_synthetic_abl_record():
    # Constructed test inputs, not a physical watch result.
    result = parse_steps(RAW)
    assert (result.record_date, result.steps) == (date(2026, 8, 1), 1485)
    assert len(RAW) == 400


@pytest.mark.parametrize('mutation', [lambda b: b[:-1], lambda b: b + b'\0',
    lambda b: b[:1] + b'\x1a' + b[2:],  # invalid month BCD
    lambda b: b[:2] + b'\x32' + b[3:],  # invalid calendar date
    lambda b: b[:374] + b'\x01\0\0\0' + b[378:],  # contradictory total
    lambda b: b[:396] + b'\x1a' + b[397:]])
def test_bad_record_is_rejected(mutation):
    with pytest.raises(ProtocolError):
        parse_steps(mutation(RAW))


def test_absent_bcd_total_keeps_valid_uint32_steps():
    raw = bytearray(RAW)
    raw[396:400] = b'\0' * 4
    assert parse_steps(bytes(raw)).steps == 1485


def test_populated_bcd_must_match_and_invalid_bcd_is_rejected():
    raw = bytearray(RAW)
    raw[396:400] = b'\x84\x14\0\0'  # 1484 != 1485
    with pytest.raises(ProtocolError, match='step totals disagree'):
        parse_steps(bytes(raw))
    raw[396] = 0xfa
    with pytest.raises(ProtocolError, match='invalid BCD'):
        parse_steps(bytes(raw))


def test_year_and_zero_are_not_special_cased():
    raw = bytearray(RAW)
    raw[0] = 0x27
    raw[374:378] = b'\0' * 4
    raw[396:400] = b'\0' * 4
    assert (parse_steps(bytes(raw)).record_date, parse_steps(bytes(raw)).steps) == (date(2027, 8, 1), 0)


def test_sixth_header_byte_is_not_required_to_be_bcd_seconds():
    raw = bytearray(RAW)
    raw[5] = 0xff  # status/reserved byte, not a clock second
    assert parse_steps(bytes(raw)).steps == 1485
