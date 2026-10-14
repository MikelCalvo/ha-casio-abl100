"""Offline wire-format tests; no Home Assistant imports."""
from datetime import date
from pathlib import Path
import pytest

from custom_components.casio_abl100.protocol import parse_steps, ProtocolError

CAPTURE = Path(__file__).with_name('abl_real_capture.txt')
RAW = bytes.fromhex(''.join(line for line in CAPTURE.read_text().splitlines() if not line.startswith('#')))


def test_real_abl_capture():
    # casiosync 86b76d9 logs/260801071501.txt, sha256 e189b91a293504f8ef3d6f6d467de8d04efe28818c48c3c34d09f3cd8bf6c6b0.
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
