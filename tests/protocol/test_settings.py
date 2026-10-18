"""Source-derived SYNTHETIC buffers only, never captures or physical acceptance.

Field oracles: GShockAPI 1.7.6 (5b1043a888ea3d97331c0c701f2da776ff638d8f)
SettingsIO, TimeAdjustmentIO, TimerIO; retained SmartSync 42.9.11 simple
DST writer/zone helper/name parser bodies. See docs/settings-read-contract.md.
Historical ABL traffic establishes lengths, not the synthetic values below.
"""

from dataclasses import FrozenInstanceError, asdict, fields, is_dataclass
from importlib import import_module, util
from itertools import product

import pytest

from custom_components.casio_abl100.protocol import ProtocolError

MODULE = 'custom_components.casio_abl100.settings'
APIS = ('parse_zone_settings', 'parse_basic_settings', 'parse_auto_time_settings',
        'parse_timer_configuration', 'parse_watch_name')
# Unknown fields deliberately contain nonzero/FF bytes. All values are invented
# fixtures whose expected decoded meaning comes from source, not a device.
DST = bytes.fromhex('1d a5 ff 03 92 81 70 6f 5e 4d 3c 2b 1a 09 f8')
HOME = bytes.fromhex('1e 00 a5 ff f2 04 2a')  # -14 quarters = -210 min
SECONDARY = bytes.fromhex('1e 01 ff 5a 17 02 fe')  # +23 quarters = +345 min
BASIC = bytes.fromhex('13 ed fe ff aa bb cc dd ee ff 98 87')
AUTO = bytes.fromhex('11 ff fe fd fc fb fa f9 f8 f7 f6 f5 80 25 ee')
TIMER = bytes.fromhex('18 01 25 3b ff ee dd cc bb aa 99 88 77 66 55')
NAME = b'\x23' + b'TEST WATCH'.ljust(19, b'\0')
FRAMES = {'dst': DST, 'home': HOME, 'secondary': SECONDARY, 'basic': BASIC,
          'auto': AUTO, 'timer': TIMER, 'name': NAME}
ZONE_EXPECTED = {
    'primary_dst_flags': 3, 'primary_dst_on': True, 'primary_dst_auto': True,
    'home': {'index': 0, 'standard_utc_offset_minutes': -210,
             'configured_dst_increment_minutes': 60, 'dst_rule_code': 42},
    'secondary': {'index': 1, 'standard_utc_offset_minutes': 345,
                  'configured_dst_increment_minutes': 30, 'dst_rule_code': 254},
}
EXPECTED = {
    'dst': ZONE_EXPECTED, 'home': ZONE_EXPECTED, 'secondary': ZONE_EXPECTED,
    'basic': {'time_format': '24h', 'button_tone_enabled': True,
              'light_duration_mode': 'short', 'power_saving_enabled': True},
    'auto': {'enabled': False, 'adjustment_minute': 37},
    'timer': {'hours': 1, 'minutes': 37, 'seconds': 59,
              'encoded_duration_seconds': 5879},
    'name': 'TEST WATCH',
}


def api(name):
    spec = util.find_spec(MODULE)
    assert spec is not None, 'pure settings decoder module is not implemented'
    function = getattr(import_module(MODULE), name, None)
    assert callable(function), f'pure decoder API {name} is not implemented'
    return function


def decode(which, raw):
    if which in ('dst', 'home', 'secondary'):
        frames = {'dst': DST, 'home': HOME, 'secondary': SECONDARY}
        frames[which] = raw
        return api('parse_zone_settings')(**frames)
    names = {'basic': 'parse_basic_settings', 'auto': 'parse_auto_time_settings',
             'timer': 'parse_timer_configuration', 'name': 'parse_watch_name'}
    return api(names[which])(raw)


def plain(result):
    return asdict(result) if is_dataclass(result) else result


def changed(raw, index, value):
    return raw[:index] + bytes([value]) + raw[index + 1:]


@pytest.mark.parametrize('name', APIS)
def test_pure_decoder_api_exists(name):
    assert callable(api(name))


@pytest.mark.parametrize('which', FRAMES)
def test_exact_synthetic_source_vectors_decode(which):
    assert plain(decode(which, FRAMES[which])) == EXPECTED[which]


@pytest.mark.parametrize('which', FRAMES)
@pytest.mark.parametrize('size', (0, 1, 3, 4, 5, 6, 7, 8, 11, 12, 13, 14, 15, 16, 17, 19, 20, 21))
def test_only_exact_historical_abl_lengths_are_accepted(which, size):
    raw = FRAMES[which]
    if size == len(raw):
        assert plain(decode(which, raw)) == EXPECTED[which]
    else:
        with pytest.raises(ProtocolError):
            decode(which, (raw + b'\xa5' * size)[:size])


@pytest.mark.parametrize('which', FRAMES)
@pytest.mark.parametrize('opcode', (0, 0x09, 0x16, 0xff))
def test_wrong_opcode_or_ff_is_rejected_at_correct_length(which, opcode):
    with pytest.raises(ProtocolError):
        decode(which, changed(FRAMES[which], 0, opcode))


@pytest.mark.parametrize('which', FRAMES)
def test_short_ff_error_is_not_a_successful_observation(which):
    with pytest.raises(ProtocolError):
        decode(which, b'\xff\x01\xa5')


@pytest.mark.parametrize('which', FRAMES)
@pytest.mark.parametrize('bad', (None, False, True, 0, 15, 'private-name-canary',
                               [], [0x13] * 12, (), {}, 1.5))
def test_non_buffer_python_types_are_rejected_with_safe_protocol_error(which, bad):
    with pytest.raises(ProtocolError) as caught:
        decode(which, bad)
    assert 'private-name-canary' not in str(caught.value)


@pytest.mark.parametrize('which', FRAMES)
@pytest.mark.parametrize('wrapper', (bytes, bytearray, memoryview))
def test_buffers_are_copied_without_retaining_mutable_input(which, wrapper):
    backing = bytearray(FRAMES[which])
    raw = wrapper(backing)
    result = decode(which, raw)
    assert plain(result) == EXPECTED[which]
    backing[:] = b'\xff' * len(backing)
    if isinstance(raw, bytearray):
        raw[:] = b'\xff' * len(raw)
    assert plain(result) == EXPECTED[which]


@pytest.mark.parametrize('which', FRAMES)
def test_released_memoryview_is_a_safe_protocol_error(which):
    raw = memoryview(FRAMES[which])
    raw.release()
    with pytest.raises(ProtocolError):
        decode(which, raw)


@pytest.mark.parametrize('which', ('home', 'secondary'))
@pytest.mark.parametrize('index', (0, 1, 2, 0xff))
def test_zone_record_index_must_match_its_argument(which, index):
    raw = changed(FRAMES[which], 1, index)
    if index == (0 if which == 'home' else 1):
        assert plain(decode(which, raw)) == ZONE_EXPECTED
    else:
        with pytest.raises(ProtocolError):
            decode(which, raw)


def test_swapped_zone_records_are_rejected():
    with pytest.raises(ProtocolError):
        api('parse_zone_settings')(DST, SECONDARY, HOME)


@pytest.mark.parametrize('wire,minutes', ((0, 0), (1, 15), (23, 345), (127, 1905),
                                          (128, -1920), (218, -570), (242, -210), (255, -15)))
def test_standard_offset_is_signed_quarter_hours(wire, minutes):
    result = api('parse_zone_settings')(DST, changed(HOME, 4, wire), changed(SECONDARY, 4, wire))
    expected = dict(ZONE_EXPECTED, home=dict(ZONE_EXPECTED['home'], standard_utc_offset_minutes=minutes),
                    secondary=dict(ZONE_EXPECTED['secondary'], standard_utc_offset_minutes=minutes))
    assert plain(result) == expected


@pytest.mark.parametrize('increment,minutes,rule', ((0, 0, 0), (1, 15, 1), (2, 30, 2),
                                                   (4, 60, 42), (128, 1920, 254), (255, 3825, 255)))
def test_configured_dst_increment_is_unsigned_and_rule_is_preserved(increment, minutes, rule):
    home = changed(changed(HOME, 5, increment), 6, rule)
    secondary = changed(changed(SECONDARY, 5, increment), 6, rule)
    result = api('parse_zone_settings')(DST, home, secondary)
    assert plain(result) == dict(
        ZONE_EXPECTED,
        home=dict(ZONE_EXPECTED['home'], configured_dst_increment_minutes=minutes, dst_rule_code=rule),
        secondary=dict(ZONE_EXPECTED['secondary'], configured_dst_increment_minutes=minutes, dst_rule_code=rule))


@pytest.mark.parametrize('flags,on,auto', ((0, False, False), (1, True, False),
                                         (2, False, True), (3, True, True),
                                         (4, None, None), (5, None, None),
                                         (0x80, None, None), (0x83, None, None), (0xff, None, None)))
def test_primary_dst_known_bits_and_unknown_upper_bits(flags, on, auto):
    result = decode('dst', changed(DST, 3, flags))
    assert plain(result) == dict(ZONE_EXPECTED, primary_dst_flags=flags,
                                primary_dst_on=on, primary_dst_auto=auto)


def test_zone_unknown_bytes_are_not_forced_to_zero_or_treated_as_selectors():
    dst = bytes([0x1d, 0xff, 0xff, 3] + [0xff] * 11)
    home = bytes.fromhex('1e 00 ff ff f2 04 2a')
    secondary = bytes.fromhex('1e 01 ff ff 17 02 fe')
    assert plain(api('parse_zone_settings')(dst, home, secondary)) == ZONE_EXPECTED


@pytest.mark.parametrize('format_bit,tone_off,power_off,long_bit', product((False, True), repeat=4))
def test_basic_flags_have_independent_source_polarity(format_bit, tone_off, power_off, long_bit):
    # Unlisted byte1 bits and byte2 bits must not change understood fields.
    byte1 = 0xec | int(format_bit) | (int(tone_off) << 1) | (int(power_off) << 4)
    raw = changed(changed(BASIC, 1, byte1), 2, 0xfe | int(long_bit))
    assert plain(decode('basic', raw)) == {
        'time_format': '24h' if format_bit else '12h',
        'button_tone_enabled': not tone_off,
        'light_duration_mode': 'long' if long_bit else 'short',
        'power_saving_enabled': not power_off,
    }


@pytest.mark.parametrize('flag,enabled', ((0, True), (0x80, False), (1, None),
                                         (0x7f, None), (0x81, None), (0xff, None)))
@pytest.mark.parametrize('minute,expected', ((0, 0), (30, 30), (37, 37), (59, 59),
                                            (60, None), (0x80, None), (0xff, None)))
def test_auto_time_exact_flag_and_minute_or_unknown_without_defaults(flag, enabled, minute, expected):
    raw = changed(changed(AUTO, 12, flag), 13, minute)
    assert plain(decode('auto', raw)) == {'enabled': enabled, 'adjustment_minute': expected}


def test_auto_time_unknown_bytes_do_not_acquire_clock_semantics():
    raw = bytes([0x11] + [0xff] * 11 + [0, 59, 0xff])
    assert plain(decode('auto', raw)) == {'enabled': True, 'adjustment_minute': 59}


@pytest.mark.parametrize('raw,expected', (
    (bytes.fromhex('18 00 00 00') + b'\0' * 11,
     {'hours': 0, 'minutes': 0, 'seconds': 0, 'encoded_duration_seconds': 0}),
    (bytes.fromhex('18 12 34 56') + b'\xff' * 11,
     {'hours': 18, 'minutes': 52, 'seconds': 86, 'encoded_duration_seconds': 68006}),
    (bytes.fromhex('18 ff ff ff') + b'\xff' * 11,
     {'hours': 255, 'minutes': 255, 'seconds': 255, 'encoded_duration_seconds': 933555}),
))
def test_timer_keeps_binary_components_without_normalization_or_range_claim(raw, expected):
    assert plain(decode('timer', raw)) == expected


@pytest.mark.parametrize('payload,expected', (
    (b'TEST WATCH'.ljust(19, b' '), 'TEST WATCH'),
    (b'TEST WATCH'.ljust(19, b'\0'), 'TEST WATCH'),
    (b'TEST WATCH\0 \0'.ljust(19, b' '), 'TEST WATCH'),
    (b'X' * 19, 'X' * 19),
    (b' TEST WATCH'.ljust(19, b' '), ' TEST WATCH'),
))
def test_name_is_strict_ascii_with_only_right_padding_removed(payload, expected):
    assert decode('name', b'\x23' + payload) == expected


@pytest.mark.parametrize('payload', (b'', b' ', b'\0', b'\0 ', b'X\0Y', b'X\0 Y',
                                    b'NAME\n', b'\tNAME', b'NAME\r', b'NAME\x7f',
                                    b'NAME\x80', b'NAME\xff'))
def test_empty_control_non_ascii_and_internal_nul_names_are_rejected(payload):
    with pytest.raises(ProtocolError):
        decode('name', b'\x23' + payload.ljust(19, b'\0'))


@pytest.mark.parametrize('payload', (b'private-canary\xff', b'private-canary\x01',
                                    b'private\0canary'))
def test_invalid_private_name_is_not_in_exception_or_logs(payload, caplog, capsys):
    raw = b'\x23' + payload.ljust(19, b'\0')
    with pytest.raises(ProtocolError) as caught:
        decode('name', raw)
    assert caught.value.args == ('watch name content',)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert 'private' not in str(caught.value)
    assert caplog.text == ''
    captured = capsys.readouterr()
    assert captured.out == captured.err == ''


@pytest.mark.parametrize('model,expected_fields', (
    ('ZoneConfiguration', ('index', 'standard_utc_offset_minutes',
                           'configured_dst_increment_minutes', 'dst_rule_code')),
    ('ZoneSettings', ('primary_dst_flags', 'primary_dst_on', 'primary_dst_auto', 'home', 'secondary')),
    ('BasicSettings', ('time_format', 'button_tone_enabled', 'light_duration_mode', 'power_saving_enabled')),
    ('AutoTimeSettings', ('enabled', 'adjustment_minute')),
    ('TimerConfiguration', ('hours', 'minutes', 'seconds', 'encoded_duration_seconds')),
))
def test_only_bounded_frozen_dataclass_fields_are_exposed(model, expected_fields):
    api('parse_zone_settings')
    cls = getattr(import_module(MODULE), model, None)
    assert is_dataclass(cls), f'{model} is not a dataclass'
    assert tuple(field.name for field in fields(cls)) == expected_fields
    assert cls.__dataclass_params__.frozen is True


@pytest.mark.parametrize('which', ('dst', 'basic', 'auto', 'timer'))
def test_decoded_models_are_frozen(which):
    result = decode(which, FRAMES[which])
    assert is_dataclass(result), 'decoder did not return a dataclass model'
    for field in fields(result):
        with pytest.raises(FrozenInstanceError):
            setattr(result, field.name, None)
    if which == 'dst':
        for zone in (result.home, result.secondary):
            for field in fields(zone):
                with pytest.raises(FrozenInstanceError):
                    setattr(zone, field.name, None)
