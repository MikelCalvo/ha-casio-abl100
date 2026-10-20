"""Pure source-derived vectors, never device captures or live write approval."""
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

PATH = Path(__file__).parents[2] / 'custom_components/casio_abl100/settings_write.py'


def api():
    assert PATH.is_file(), 'Pure settings preparation API not implemented'
    import importlib
    return importlib.import_module('custom_components.casio_abl100.settings_write')


AUTO = bytes.fromhex('11 01 12 23 34 45 56 67 78 89 9a ab 80 21 de')
ZONE = bytes.fromhex('1e 01 a2 b3 ea 04 05')
DST = bytes.fromhex('1d 00 01 02 43 54 65 76 87 98 a9 ba cb dc ed')


def test_auto_time_preserves_opaque_fields_and_sparse_values():
    a = api()
    plan = a.prepare_auto_time(AUTO, a.AutoTimeIntent(enabled=True))
    assert plan.expected == AUTO[:12] + b'\0' + AUTO[13:]
    assert plan.frame == plan.expected and plan.baseline == AUTO
    assert plan.edited_masks == tuple(0x80 if i == 12 else 0 for i in range(15))
    minute = a.prepare_auto_time(AUTO, a.AutoTimeIntent(adjustment_minute=59))
    assert minute.expected == AUTO[:13] + b'\x3b' + AUTO[14:]
    assert minute.expected[12] == 0x80
    assert a.prepare_auto_time(AUTO, a.AutoTimeIntent(enabled=False)).frame is None


def test_zone_signed_offset_preserves_index_and_opaque_bytes():
    a = api()
    p = a.prepare_zone(ZONE, 1, a.ZoneIntent(standard_offset_minutes=-345))
    assert p.expected == bytes.fromhex('1e 01 a2 b3 e9 04 05')
    assert p.frame == p.expected
    p = a.prepare_zone(ZONE, 1, a.ZoneIntent(configured_dst_increment_minutes=30, rule_code=9))
    assert p.expected == bytes.fromhex('1e 01 a2 b3 ea 02 09')
    assert a.prepare_zone(ZONE, 1, a.ZoneIntent()).frame is None


def test_primary_dst_bits_are_independent_and_tail_preserved():
    a = api()
    p = a.prepare_primary_dst(DST, a.PrimaryDSTIntent(on=True))
    assert p.expected == DST[:3] + b'\x03' + DST[4:]
    p = a.prepare_primary_dst(DST, a.PrimaryDSTIntent(auto=False))
    assert p.expected == DST[:3] + b'\0' + DST[4:]
    assert a.prepare_primary_dst(DST, a.PrimaryDSTIntent(auto=True)).frame is None


@pytest.mark.parametrize('kind', ['auto', 'zone', 'dst'])
def test_comparison_separates_intended_mismatch_and_opaque_difference(kind):
    a = api()
    if kind == 'auto':
        p = a.prepare_auto_time(AUTO, a.AutoTimeIntent(adjustment_minute=40))
        requested, opaque = 13, 5
    elif kind == 'zone':
        p = a.prepare_zone(ZONE, 1, a.ZoneIntent(rule_code=9))
        requested, opaque = 6, 2
    else:
        p = a.prepare_primary_dst(DST, a.PrimaryDSTIntent(on=True))
        requested, opaque = 3, 5
    assert a.compare_settings_readback(p, p.expected) == 'verified'
    data = bytearray(p.expected); data[requested] ^= 1
    assert a.compare_settings_readback(p, data) == 'mismatch'
    data = bytearray(p.expected); data[opaque] ^= 1
    assert a.compare_settings_readback(p, data) == 'preservation_mismatch'
    data[requested] ^= 1
    assert a.compare_settings_readback(p, data) == 'preservation_mismatch'
    for malformed in (b'', b'\xff', p.expected[:-1], b'\xff' + p.expected[1:]):
        with pytest.raises(ValueError):
            a.compare_settings_readback(p, malformed)


@pytest.mark.parametrize('invalid', [True, 1.0, -1, 60, '30', [], 10**1000],
                         ids=['bool', 'float', 'negative', 'overrange', 'text', 'list', 'huge'])
def test_auto_minute_strict_range_even_with_no_other_change(invalid):
    a = api()
    with pytest.raises(ValueError):
        a.prepare_auto_time(AUTO, a.AutoTimeIntent(adjustment_minute=invalid))


@pytest.mark.parametrize('invalid', [0, 1, 'true', [], {}])
def test_boolean_intent_refuses_coercion(invalid):
    a = api()
    with pytest.raises(ValueError):
        a.prepare_auto_time(AUTO, a.AutoTimeIntent(enabled=invalid))
    with pytest.raises(ValueError):
        a.prepare_primary_dst(DST, a.PrimaryDSTIntent(on=invalid))
    with pytest.raises(ValueError):
        a.prepare_primary_dst(DST, a.PrimaryDSTIntent(auto=invalid))


@pytest.mark.parametrize('position,value', [(12, 1), (12, 0xff), (13, 60), (13, 0xff)])
def test_unknown_auto_baseline_blocks_even_noop(position, value):
    a = api(); data = bytearray(AUTO); data[position] = value
    with pytest.raises(ValueError):
        a.prepare_auto_time(data, a.AutoTimeIntent())


@pytest.mark.parametrize('value', [4, 0x80, 0xff])
def test_unknown_primary_flags_block_noop(value):
    a = api(); data = bytearray(DST); data[3] = value
    with pytest.raises(ValueError):
        a.prepare_primary_dst(data, a.PrimaryDSTIntent())


@pytest.mark.parametrize('index', [True, -1, 2, '1', 1.0])
def test_zone_index_is_strict(index):
    a = api()
    with pytest.raises(ValueError):
        a.prepare_zone(ZONE, index, a.ZoneIntent())


@pytest.mark.parametrize('field,value', [
    ('standard_offset_minutes', True), ('standard_offset_minutes', -1935),
    ('standard_offset_minutes', 1920), ('standard_offset_minutes', 1),
    ('standard_offset_minutes', 15.0), ('configured_dst_increment_minutes', -15),
    ('configured_dst_increment_minutes', 3840), ('configured_dst_increment_minutes', 16),
    ('configured_dst_increment_minutes', True), ('rule_code', True),
    ('rule_code', -1), ('rule_code', 256), ('rule_code', '5'),
])
def test_zone_numeric_wire_constraints(field, value):
    a = api()
    with pytest.raises(ValueError):
        a.prepare_zone(ZONE, 1, a.ZoneIntent(**{field: value}))


@pytest.mark.parametrize('index', [0, 1])
@pytest.mark.parametrize('offset', [-1920, -345, 0, 855, 1905])
def test_source_wire_limits_are_not_geographical_claims(index, offset):
    a = api(); raw = bytearray(ZONE); raw[1] = index
    p = a.prepare_zone(raw, index, a.ZoneIntent(standard_offset_minutes=offset,
                                              configured_dst_increment_minutes=3825, rule_code=255))
    assert p.expected[4] == (offset // 15) & 0xff
    assert p.expected[5:7] == b'\xff\xff'


@pytest.mark.parametrize('raw', [None, [], {}, 'PRIVATE', 15, b'', b'\xff', AUTO[:-1], b'\x13' + AUTO[1:]])
def test_auto_rejects_malformed_input_without_echo(raw):
    a = api()
    with pytest.raises(ValueError) as err:
        a.prepare_auto_time(raw, a.AutoTimeIntent())
    assert 'PRIVATE' not in str(err.value)


def test_immutable_copy_and_plans():
    a = api(); raw = bytearray(AUTO)
    p = a.prepare_auto_time(memoryview(raw), a.AutoTimeIntent(enabled=True))
    raw[1] = 0xff
    assert p.baseline == AUTO and p.expected[1] == AUTO[1]
    with pytest.raises(FrozenInstanceError):
        p.expected = b''
    with pytest.raises(ValueError):
        a.prepare_zone(ZONE, 0, a.ZoneIntent())


def test_wrong_intent_class_refused():
    a = api()
    for build, raw in ((a.prepare_auto_time, AUTO), (a.prepare_primary_dst, DST)):
        with pytest.raises(ValueError):
            build(raw, {})
    with pytest.raises(ValueError):
        a.prepare_zone(ZONE, 1, a.AutoTimeIntent())
