"""Pure, bounded ABL-100WE settings/zone decoders; no device operations.

Field semantics come from pinned GShockAPI 1.7.6 and retained SmartSync simple
writer/decoder bodies. Exact lengths come from historical ABL traffic, not a
new physical trial. See docs/settings-read-contract.md for provenance and
limits. Configuration is not a running-clock sample. No encoder or executor.
"""

from dataclasses import dataclass

from .protocol import ProtocolError


@dataclass(frozen=True)
class ZoneConfiguration:
    """Source-indexed zone configuration, not an effective current offset."""

    index: int
    standard_utc_offset_minutes: int
    configured_dst_increment_minutes: int
    dst_rule_code: int


@dataclass(frozen=True)
class ZoneSettings:
    """Primary DST flags and two configured slots; upper bits stay unknown.

    Raw flags are retained for interpretation, not blanket public diagnostics.
    Slot roles are source-inferred and unverified after Home/World swapping.
    """

    primary_dst_flags: int
    primary_dst_on: bool | None
    primary_dst_auto: bool | None
    home: ZoneConfiguration
    secondary: ZoneConfiguration


@dataclass(frozen=True)
class BasicSettings:
    """Four source-backed fields; ABL power-saving capability is unverified.

    Light duration is a mode, not a physically measured number of seconds.
    """

    time_format: str
    button_tone_enabled: bool
    light_duration_mode: str
    power_saving_enabled: bool


@dataclass(frozen=True)
class AutoTimeSettings:
    """Automatic-adjustment configuration, not evidence of a completed sync."""

    enabled: bool | None
    adjustment_minute: int | None


@dataclass(frozen=True)
class TimerConfiguration:
    """Raw binary components and their encoded arithmetic, not a live timer.

    Zero remains encoded zero. This does not claim an immediate timer or a
    supported ABL range; the later presentation layer must resolve ambiguity.
    """

    hours: int
    minutes: int
    seconds: int
    encoded_duration_seconds: int


def _response(raw: bytes | bytearray | memoryview, length: int, opcode: int) -> bytes:
    """Copy only byte buffers and reject anything outside the exact envelope."""
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise ProtocolError('settings response type')
    try:
        data = bytes(raw)
    except (TypeError, ValueError):
        raise ProtocolError('settings response buffer') from None
    if len(data) != length:
        raise ProtocolError('settings response length')
    if data[0] != opcode:
        raise ProtocolError('settings response opcode')
    return data


def _zone(raw: bytes | bytearray | memoryview, index: int) -> ZoneConfiguration:
    """Invert the source seven-byte zone writer without inventing tail fields."""
    data = _response(raw, 7, 0x1e)
    if data[1] != index:
        raise ProtocolError('zone response index')
    signed_offset = data[4] if data[4] < 128 else data[4] - 256
    return ZoneConfiguration(index, signed_offset * 15, data[5] * 15, data[6])


def parse_zone_settings(
    dst: bytes | bytearray | memoryview,
    home: bytes | bytearray | memoryview,
    secondary: bytes | bytearray | memoryview,
) -> ZoneSettings:
    """Decode exact 1D/1E(index0)/1E(index1) configuration responses.

    Only low DST bits 01=ON and 02=AUTO have source semantics. If upper bits
    exist, preserve flags but withhold both interpreted states. Unknown bytes
    need not be zero. No secondary active DST, IANA identity or running clock.
    """
    data = _response(dst, 15, 0x1d)
    home_zone = _zone(home, 0)
    secondary_zone = _zone(secondary, 1)
    flags = data[3]
    known = flags & 0xfc == 0
    return ZoneSettings(flags, bool(flags & 1) if known else None,
                        bool(flags & 2) if known else None,
                        home_zone, secondary_zone)


def parse_basic_settings(raw: bytes | bytearray | memoryview) -> BasicSettings:
    """Decode the 12-byte ABL 13 reply, not the generic 17-byte SET default.

    Tone and power-saving use inverted enable bits. Power saving is only a
    source-inferred ABL capability. Do not read byte12 or infer SIG/auto-light.
    """
    data = _response(raw, 12, 0x13)
    return BasicSettings('24h' if data[1] & 1 else '12h',
                         not bool(data[1] & 2),
                         'long' if data[2] & 1 else 'short',
                         not bool(data[1] & 0x10))


def parse_auto_time_settings(raw: bytes | bytearray | memoryview) -> AutoTimeSettings:
    """Decode exact 11 configuration, preserving unknown enable/minute values."""
    data = _response(raw, 15, 0x11)
    enabled = {0x00: True, 0x80: False}.get(data[12])
    minute = data[13] if data[13] <= 59 else None
    return AutoTimeSettings(enabled, minute)


def parse_timer_configuration(raw: bytes | bytearray | memoryview) -> TimerConfiguration:
    """Decode GET18's 15 bytes (not SET7) with no BCD or normalization.

    Tail bytes and running state are unknown. In particular, encoded zero
    cannot yet be presented as either a zero-duration or a 60-minute timer.
    """
    data = _response(raw, 15, 0x18)
    hours, minutes, seconds = data[1:4]
    return TimerConfiguration(hours, minutes, seconds,
                              3600 * hours + 60 * minutes + seconds)


def parse_watch_name(raw: bytes | bytearray | memoryview) -> str:
    """Decode optional GET23 text; reject invalid content, never sanitize it.

    Only right NUL/space padding is removed. Errors contain no input/name and
    no decoding exception holding the private buffer. No model-name fallback.
    """
    data = _response(raw, 20, 0x23)
    text = data[1:].rstrip(b'\0 ')
    if not text or any(byte < 0x20 or byte > 0x7e for byte in text):
        raise ProtocolError('watch name content')
    return text.decode('ascii')
