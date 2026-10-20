"""Synthetic alarm/SIG SET contract only; no transport, HA, or watch IO."""

from dataclasses import FrozenInstanceError, replace
from importlib import import_module, util
from itertools import product

import pytest

from custom_components.casio_abl100.alarms import parse_alarms
from custom_components.casio_abl100.protocol import ProtocolError


BEFORE15 = bytes.fromhex('15 40 07 06 0A')
BEFORE16 = bytes.fromhex('16 00 00 07 14 00 07 08 1E 40 00 09 28 00 07 0A 32')
SET15 = bytes.fromhex('15 C0 40 06 0A')
SET16 = bytes.fromhex('16 00 40 07 14 40 40 15 2D 40 40 09 28 00 40 0A 32')
AFTER15 = bytes.fromhex('15 C0 07 06 0A')
AFTER16 = bytes.fromhex('16 00 00 07 14 40 07 15 2D 40 00 09 28 00 07 0A 32')


def baseline():
    return parse_alarms(BEFORE15, BEFORE16)


def changed(snapshot, index, **fields):
    alarms = list(snapshot.alarms)
    alarms[index - 1] = replace(alarms[index - 1], **fields)
    return replace(snapshot, alarms=tuple(alarms))


def vector_intent(module):
    return module.AlarmWriteIntent((module.AlarmEdit(3, 21, 45, True),), True)


def invalid_snapshot(case):
    before = baseline()
    if case == 'snapshot_type':
        return None
    if case == 'list':
        return replace(before, alarms=list(before.alarms))
    if case == 'short':
        return replace(before, alarms=before.alarms[:-1])
    if case == 'long':
        return replace(before, alarms=before.alarms + (before.alarms[-1],))
    if case == 'reordered':
        return replace(before, alarms=tuple(reversed(before.alarms)))
    if case == 'duplicate':
        return replace(before, alarms=before.alarms[:-1] + (before.alarms[0],))
    if case == 'record_type':
        return replace(before, alarms=before.alarms[:-1] + (None,))
    if case == 'signal_type':
        return replace(before, hourly_signal=0)
    if case == 'signal_conflict':
        return replace(before, hourly_signal=True)
    field, value = {
        'slot_bool': ('slot', True), 'slot_float': ('slot', 1.0),
        'hour_bool': ('hour', True), 'hour_float': ('hour', 6.0),
        'hour_negative': ('hour', -1), 'hour_high': ('hour', 24),
        'minute_bool': ('minute', False), 'minute_text': ('minute', 'private-canary'),
        'minute_negative': ('minute', -1), 'minute_high': ('minute', 60),
        'enabled_type': ('enabled', 1), 'enabled_conflict': ('enabled', False),
        'flags_bool': ('flags', True), 'flags_float': ('flags', 64.0),
        'flags_negative': ('flags', -1), 'flags_high': ('flags', 256),
        'aux_bool': ('aux', False), 'aux_text': ('aux', 'private-canary'),
        'aux_negative': ('aux', -1), 'aux_high': ('aux', 256),
    }[case]
    return changed(before, 1, **{field: value})


MALFORMED = (
    'snapshot_type', 'list', 'short', 'long', 'reordered', 'duplicate',
    'record_type', 'signal_type', 'signal_conflict', 'slot_bool', 'slot_float',
    'hour_bool', 'hour_float', 'hour_negative', 'hour_high', 'minute_bool',
    'minute_text', 'minute_negative', 'minute_high', 'enabled_type',
    'enabled_conflict', 'flags_bool', 'flags_float', 'flags_negative',
    'flags_high', 'aux_bool', 'aux_text', 'aux_negative', 'aux_high',
)


def api():
    """A missing API fails explicitly, never as an import/setup error."""
    spec = util.find_spec('custom_components.casio_abl100.alarm_write')
    assert spec is not None, 'pure alarm write codec is not implemented'
    module = import_module(spec.name)
    for name in ('AlarmEdit', 'AlarmWriteIntent', 'expected_alarm_readback',
                 'build_alarm_set_pair', 'compare_alarm_write_readback'):
        assert callable(getattr(module, name, None)), 'pure alarm write API is incomplete'
    return module


def test_pure_alarm_write_api_exists():
    api()


def test_intent_and_edit_are_frozen_with_optional_defaults():
    module = api()
    edit = module.AlarmEdit(2)
    intent = module.AlarmWriteIntent()
    assert (edit.slot, edit.hour, edit.minute, edit.enabled) == (2, None, None, None)
    assert (intent.alarms, intent.hourly_signal) == ((), None)
    with pytest.raises(FrozenInstanceError):
        edit.hour = 9
    with pytest.raises(FrozenInstanceError):
        intent.hourly_signal = True


def test_synthetic_vector_has_distinct_expected_get_and_canonical_set():
    module = api()
    before = baseline()
    intent = vector_intent(module)
    expected = module.expected_alarm_readback(before, intent)
    assert expected == parse_alarms(AFTER15, AFTER16)
    pair = module.build_alarm_set_pair(before, intent)
    assert pair == (SET15, SET16)
    assert type(pair) is tuple and tuple(map(type, pair)) == (bytes, bytes)
    assert tuple(map(len, pair)) == (5, 17)
    assert expected.alarms[2].hour == 21 and expected.alarms[2].minute == 45
    assert before == parse_alarms(BEFORE15, BEFORE16)
    assert module.compare_alarm_write_readback(before, intent, expected) == 'verified'


@pytest.mark.parametrize('slot', range(1, 6))
@pytest.mark.parametrize('field,value', [
    ('hour', 21), ('minute', 45), ('enabled', True), ('enabled', False),
])
def test_each_partial_field_preserves_all_other_slots_and_get_aux(slot, field, value):
    module = api()
    before = baseline()
    edit = module.AlarmEdit(slot, **{field: value})
    intent = module.AlarmWriteIntent((edit,))
    original = before.alarms[slot - 1]
    fields = {field: value}
    if field == 'enabled':
        fields['flags'] = original.flags | 0x40 if value else original.flags & ~0x40
    expected = changed(before, slot, **fields)
    assert module.expected_alarm_readback(before, intent) == expected
    pair = module.build_alarm_set_pair(before, intent)
    if expected == before:
        assert pair is None
    else:
        assert pair is not None
        records = [[a.flags, 0x40, a.hour, a.minute] for a in expected.alarms]
        assert pair == (bytes([0x15, *records[0]]),
                        bytes([0x16, *(b for row in records[1:] for b in row)]))
    assert before == baseline()


@pytest.mark.parametrize('slot', range(1, 6))
@pytest.mark.parametrize('hour,minute', [(0, 0), (23, 59)])
def test_numeric_time_boundaries_in_all_slots(slot, hour, minute):
    module = api()
    intent = module.AlarmWriteIntent((module.AlarmEdit(slot, hour, minute),))
    pair = module.build_alarm_set_pair(baseline(), intent)
    packet = pair[0] if slot == 1 else pair[1]
    start = 1 if slot == 1 else 1 + (slot - 2) * 4
    assert packet[start + 2:start + 4] == bytes([hour, minute])


@pytest.mark.parametrize('enable,signal', list(product((False, True), repeat=2)))
def test_al1_enable_and_sig_are_independent(enable, signal):
    module = api()
    before = changed(baseline(), 1, flags=(0x40 if enable else 0) | (0x80 if signal else 0),
                     enabled=enable)
    before = replace(before, hourly_signal=signal)
    sig_intent = module.AlarmWriteIntent(hourly_signal=not signal)
    expected = module.expected_alarm_readback(before, sig_intent)
    assert expected.hourly_signal is not signal
    assert expected.alarms[0] == replace(before.alarms[0], flags=before.alarms[0].flags ^ 0x80)
    assert expected.alarms[1:] == before.alarms[1:]
    pair = module.build_alarm_set_pair(before, sig_intent)
    assert pair[0] == bytes([0x15, expected.alarms[0].flags, 0x40, 6, 10])
    assert pair[1] == bytes.fromhex('16 00 40 07 14 00 40 08 1E 40 40 09 28 00 40 0A 32')
    enable_intent = module.AlarmWriteIntent((module.AlarmEdit(1, enabled=not enable),))
    enabled_expected = module.expected_alarm_readback(before, enable_intent)
    assert enabled_expected.hourly_signal is signal
    assert enabled_expected.alarms[0].flags == before.alarms[0].flags ^ 0x40
    assert enabled_expected.alarms[1:] == before.alarms[1:]


def test_combined_out_of_order_edits_keep_slot_order_and_independent_signal():
    module = api()
    before = baseline()
    edits = tuple(module.AlarmEdit(slot, 24 - slot, 60 - slot, slot % 2 == 1)
                  for slot in (5, 1, 3, 2, 4))
    intent = module.AlarmWriteIntent(edits, True)
    expected = module.expected_alarm_readback(before, intent)
    assert tuple((a.slot, a.hour, a.minute, a.enabled, a.flags, a.aux)
                 for a in expected.alarms) == (
        (1, 23, 59, True, 0xC0, 7), (2, 22, 58, False, 0, 0),
        (3, 21, 57, True, 0x40, 7), (4, 20, 56, False, 0, 0),
        (5, 19, 55, True, 0x40, 7),
    )
    assert module.build_alarm_set_pair(before, intent) == (
        bytes.fromhex('15 C0 40 17 3B'),
        bytes.fromhex('16 00 40 16 3A 40 40 15 39 00 40 14 38 40 40 13 37'),
    )


@pytest.mark.parametrize('kind', ['empty', 'empty_edit', 'explicit_equal', 'all_equal'])
def test_effective_noop_returns_no_frames_but_keeps_expected_get(kind):
    module = api()
    before = baseline()
    intents = {
        'empty': module.AlarmWriteIntent(),
        'empty_edit': module.AlarmWriteIntent((module.AlarmEdit(5),)),
        'explicit_equal': module.AlarmWriteIntent((module.AlarmEdit(1, 6, 10, True),), False),
        'all_equal': module.AlarmWriteIntent(tuple(
            module.AlarmEdit(a.slot, a.hour, a.minute, a.enabled) for a in before.alarms), False),
    }
    intent = intents[kind]
    assert module.expected_alarm_readback(before, intent) == before
    assert module.build_alarm_set_pair(before, intent) is None
    assert module.compare_alarm_write_readback(before, intent, before) == 'verified'


def invoke(module, operation, before, intent):
    if operation == 'compare_alarm_write_readback':
        return getattr(module, operation)(before, intent, baseline())
    return getattr(module, operation)(before, intent)


OPERATIONS = ('expected_alarm_readback', 'build_alarm_set_pair', 'compare_alarm_write_readback')


@pytest.mark.parametrize('operation', OPERATIONS)
@pytest.mark.parametrize('case', MALFORMED)
def test_all_public_functions_reject_malformed_baseline_even_for_noop(operation, case):
    module = api()
    with pytest.raises(ProtocolError, match='^invalid alarm snapshot$'):
        invoke(module, operation, invalid_snapshot(case), module.AlarmWriteIntent())


@pytest.mark.parametrize('operation', OPERATIONS)
@pytest.mark.parametrize('slot', range(1, 6))
@pytest.mark.parametrize('field,value', [('flags', 1), ('flags', 0x80), ('aux', 0x40), ('aux', 0xFF)])
def test_whole_baseline_rejects_unsupported_flags_or_aux_before_any_pair(operation, slot, field, value):
    module = api()
    before = baseline()
    fields = {field: value}
    if field == 'flags':
        fields['enabled'] = bool(value & 0x40)
    before = changed(before, slot, **fields)
    if slot == 1 and field == 'flags':
        before = replace(before, hourly_signal=bool(value & 0x80))
    intent = module.AlarmWriteIntent((module.AlarmEdit(1, minute=11),))
    if slot == 1 and field == 'flags' and value == 0x80:
        # AL1 bit 0x80 is supported and independent of its disabled alarm.
        invoke(module, operation, before, intent)
    else:
        with pytest.raises(ProtocolError, match='^unsupported alarm baseline$'):
            invoke(module, operation, before, intent)
        with pytest.raises(ProtocolError, match='^unsupported alarm baseline$'):
            invoke(module, operation, before, module.AlarmWriteIntent())


BAD_INTENTS = (
    'intent_type', 'edits_list', 'edits_none', 'edit_type', 'duplicate',
    'signal_int', 'signal_text', 'slot_bool', 'slot_float', 'slot_low', 'slot_high',
    'hour_bool', 'hour_float', 'hour_text', 'hour_low', 'hour_high',
    'minute_bool', 'minute_float', 'minute_low', 'minute_high',
    'enabled_int', 'enabled_text',
)


def invalid_intent(module, case):
    edit = module.AlarmEdit(1)
    if case == 'intent_type':
        return None
    if case == 'edits_list':
        return module.AlarmWriteIntent([edit])
    if case == 'edits_none':
        return module.AlarmWriteIntent(None)
    if case == 'edit_type':
        return module.AlarmWriteIntent((None,))
    if case == 'duplicate':
        return module.AlarmWriteIntent((edit, module.AlarmEdit(1, minute=11)))
    if case.startswith('signal_'):
        return module.AlarmWriteIntent(hourly_signal=1 if case == 'signal_int' else 'private-canary')
    field, value = {
        'slot_bool': ('slot', True), 'slot_float': ('slot', 1.0),
        'slot_low': ('slot', 0), 'slot_high': ('slot', 6),
        'hour_bool': ('hour', True), 'hour_float': ('hour', 8.0),
        'hour_text': ('hour', 'private-canary'), 'hour_low': ('hour', -1),
        'hour_high': ('hour', 24), 'minute_bool': ('minute', False),
        'minute_float': ('minute', 9.0), 'minute_low': ('minute', -1),
        'minute_high': ('minute', 60), 'enabled_int': ('enabled', 1),
        'enabled_text': ('enabled', 'private-canary'),
    }[case]
    return module.AlarmWriteIntent((module.AlarmEdit(2, minute=22), replace(edit, **{field: value})))


@pytest.mark.parametrize('operation', OPERATIONS)
@pytest.mark.parametrize('case', BAD_INTENTS)
def test_entire_intent_is_validated_before_expected_or_frames(operation, case):
    module = api()
    with pytest.raises(ProtocolError, match='^invalid alarm write intent$'):
        invoke(module, operation, baseline(), invalid_intent(module, case))


@pytest.mark.parametrize('case', MALFORMED)
def test_malformed_observation_raises_instead_of_becoming_a_result(case):
    module = api()
    with pytest.raises(ProtocolError, match='^invalid alarm snapshot$'):
        module.compare_alarm_write_readback(baseline(), vector_intent(module), invalid_snapshot(case))


@pytest.mark.parametrize('slot', range(1, 6))
@pytest.mark.parametrize('field', ['hour', 'minute', 'enabled'])
def test_readback_checks_requested_and_untouched_semantics_for_all_slots(slot, field):
    module = api()
    before = baseline()
    intent = vector_intent(module)
    expected = parse_alarms(AFTER15, AFTER16)
    alarm = expected.alarms[slot - 1]
    value = not alarm.enabled if field == 'enabled' else getattr(alarm, field) + 1
    fields = {field: value}
    if field == 'enabled':
        fields['flags'] = alarm.flags ^ 0x40
    observed = changed(expected, slot, **fields)
    assert module.compare_alarm_write_readback(before, intent, observed) == 'mismatch'


def test_readback_sig_is_checked_even_when_not_requested():
    module = api()
    before = baseline()
    intent = module.AlarmWriteIntent((module.AlarmEdit(3, 21, 45, True),))
    observed = parse_alarms(AFTER15, AFTER16)
    assert module.compare_alarm_write_readback(before, intent, observed) == 'mismatch'
    assert module.compare_alarm_write_readback(before, vector_intent(module), before) == 'mismatch'


@pytest.mark.parametrize('slot', range(1, 6))
@pytest.mark.parametrize('field,value', [('aux', 0), ('aux', 7), ('aux', 0x40),
                                       ('aux', 0xFF), ('flags', 1), ('flags', 0x80)])
def test_structurally_valid_opaque_change_is_preservation_mismatch(slot, field, value):
    module = api()
    before = baseline()
    intent = vector_intent(module)
    expected = parse_alarms(AFTER15, AFTER16)
    original = expected.alarms[slot - 1]
    if field == 'aux':
        observed = changed(expected, slot, aux=value)
        verdict = 'verified' if original.aux == value else 'preservation_mismatch'
    else:
        observed = changed(expected, slot, flags=original.flags | value)
        verdict = 'mismatch' if slot == 1 and value == 0x80 else 'preservation_mismatch'
        if slot == 1 and value == 0x80:
            observed = changed(expected, slot, flags=original.flags ^ value)
            observed = replace(observed, hourly_signal=False)
    assert module.compare_alarm_write_readback(before, intent, observed) == verdict


def test_opaque_mismatch_takes_priority_over_known_semantic_mismatch():
    module = api()
    observed = changed(parse_alarms(AFTER15, AFTER16), 5, hour=11, aux=0xFF, flags=0x80)
    assert module.compare_alarm_write_readback(baseline(), vector_intent(module), observed) == 'preservation_mismatch'
