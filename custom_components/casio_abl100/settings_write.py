"""Pure preserved-byte settings preparation, not a native write capability.

Only clone-based 11/1D/1E transformations from pinned source are represented.
No IO, clock synchronization, cached baseline, runtime import or device support
is implied. See docs/settings-write-boundaries.md for provenance and limits.
"""
from dataclasses import dataclass

from .protocol import ProtocolError


@dataclass(frozen=True)
class AutoTimeIntent:
    enabled: bool | None = None
    adjustment_minute: int | None = None


@dataclass(frozen=True)
class ZoneIntent:
    standard_offset_minutes: int | None = None
    configured_dst_increment_minutes: int | None = None
    rule_code: int | None = None


@dataclass(frozen=True)
class PrimaryDSTIntent:
    on: bool | None = None
    auto: bool | None = None


@dataclass(frozen=True)
class SettingsPlan:
    """Complete expected raw configuration; never freshness or IO authority."""

    baseline: bytes
    expected: bytes
    edited_masks: tuple[int, ...]
    frame: bytes | None


def _frame(raw, opcode, length, index=None):
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise ProtocolError('Invalid settings buffer')
    try:
        data = bytes(raw)
    except (TypeError, ValueError):
        raise ProtocolError('Invalid settings buffer') from None
    if len(data) != length or data[0] != opcode:
        raise ProtocolError('Invalid settings envelope')
    if index is not None and data[1] != index:
        raise ProtocolError('Invalid zone index')
    return data


def _boolean(value):
    if value is not None and type(value) is not bool:
        raise ProtocolError('Invalid settings boolean')


def _integer(value, minimum, maximum, multiple=1):
    if value is not None and (type(value) is not int or not minimum <= value <= maximum
                              or value % multiple):
        raise ProtocolError('Invalid settings integer')


def _plan(baseline, desired, masks):
    expected = bytes(desired)
    return SettingsPlan(baseline, expected, tuple(masks),
                        expected if expected != baseline else None)


def prepare_auto_time(raw, intent: AutoTimeIntent) -> SettingsPlan:
    """Prepare configuration11; never set the running clock or infer defaults."""
    baseline = _frame(raw, 0x11, 15)
    if type(intent) is not AutoTimeIntent:
        raise ProtocolError('Invalid settings intent')
    _boolean(intent.enabled)
    _integer(intent.adjustment_minute, 0, 59)
    if baseline[12] not in (0, 0x80) or baseline[13] > 59:
        raise ProtocolError('Unsupported auto-time baseline')
    desired, masks = bytearray(baseline), [0] * 15
    if intent.enabled is not None:
        desired[12] = 0 if intent.enabled else 0x80
        masks[12] = 0x80
    if intent.adjustment_minute is not None:
        desired[13] = intent.adjustment_minute
        masks[13] = 0xff
    return _plan(baseline, desired, masks)


def prepare_zone(raw, index: int, intent: ZoneIntent) -> SettingsPlan:
    """Transform indexed1E wire fields, not IANA zones or effective DST."""
    if type(index) is not int or index not in (0, 1):
        raise ProtocolError('Invalid zone index')
    baseline = _frame(raw, 0x1e, 7, index)
    if type(intent) is not ZoneIntent:
        raise ProtocolError('Invalid settings intent')
    _integer(intent.standard_offset_minutes, -1920, 1905, 15)
    _integer(intent.configured_dst_increment_minutes, 0, 3825, 15)
    _integer(intent.rule_code, 0, 255)
    desired, masks = bytearray(baseline), [0] * 7
    fields = ((4, intent.standard_offset_minutes, 15),
              (5, intent.configured_dst_increment_minutes, 15),
              (6, intent.rule_code, 1))
    for position, value, divisor in fields:
        if value is not None:
            desired[position] = (value // divisor) & 0xff
            masks[position] = 0xff
    return _plan(baseline, desired, masks)


def prepare_primary_dst(raw, intent: PrimaryDSTIntent) -> SettingsPlan:
    """Change only known primary ON/AUTO bits; preserve all shared/tail bytes."""
    baseline = _frame(raw, 0x1d, 15)
    if type(intent) is not PrimaryDSTIntent:
        raise ProtocolError('Invalid settings intent')
    _boolean(intent.on)
    _boolean(intent.auto)
    if baseline[3] & 0xfc:
        raise ProtocolError('Unsupported primary DST baseline')
    desired, masks = bytearray(baseline), [0] * 15
    for mask, value in ((1, intent.on), (2, intent.auto)):
        if value is not None:
            desired[3] = (desired[3] & ~mask) | (mask if value else 0)
            masks[3] |= mask
    return _plan(baseline, desired, masks)


def compare_settings_readback(plan: SettingsPlan, raw) -> str:
    """Classify complete raw equality, not freshness, causality or acceptance.

    A structurally valid differing value remains an observation, even if it is
    unsupported by the preparer. Opaque differences take precedence over intent
    mismatches. This helper performs no publication or reconciliation itself.
    """
    if type(plan) is not SettingsPlan or type(plan.baseline) is not bytes or not plan.baseline:
        raise ProtocolError('Invalid settings plan')
    opcode = plan.baseline[0]
    if opcode == 0x11:
        length, index, allowed = 15, None, {12: 0x80, 13: 0xff}
    elif opcode == 0x1d:
        length, index, allowed = 15, None, {3: 3}
    elif opcode == 0x1e:
        length = 7
        index = plan.baseline[1] if len(plan.baseline) == length else -1
        allowed = {4: 0xff, 5: 0xff, 6: 0xff}
        if index not in (0, 1):
            raise ProtocolError('Invalid settings plan')
    else:
        raise ProtocolError('Invalid settings plan')
    _frame(plan.baseline, opcode, length, index)
    expected = _frame(plan.expected, opcode, length, index)
    masks = plan.edited_masks
    if (type(masks) is not tuple or len(masks) != length
            or any(type(mask) is not int or not 0 <= mask <= 255
                   or mask & ~allowed.get(i, 0) for i, mask in enumerate(masks))
            or any((before ^ after) & ~mask
                   for before, after, mask in zip(plan.baseline, expected, masks, strict=True))):
        raise ProtocolError('Invalid settings plan')
    observed = _frame(raw, opcode, length, index)
    if any((before ^ after) & ~mask
           for before, after, mask in zip(expected, observed, masks, strict=True)):
        return 'preservation_mismatch'
    return 'verified' if observed == expected else 'mismatch'
