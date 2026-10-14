"""Synthetic records for offline tests; no watch capture is redistributed.

This deliberately minimal record exercises the documented header and daily-total
fields only. It is not evidence of hardware compatibility or a complete lifelog.
"""

# Independent test inputs: 2026-08-01 07:15, reserved/status byte 0xA5,
# a little-endian daily total of 1485, and its packed-BCD mirror.
_record = bytearray(400)
_record[:6] = bytes.fromhex('26 08 01 07 15 a5')
_record[374:378] = bytes.fromhex('cd 05 00 00')
_record[396:400] = bytes.fromhex('85 14 00 00')
SYNTHETIC_RECORD = bytes(_record)
del _record
