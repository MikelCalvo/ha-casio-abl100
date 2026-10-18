# Bounded settings and zone decoding

## Status and boundary

`custom_components/casio_abl100/settings.py` contains **pure decoders only** for
source-backed ABL-100WE configuration responses. It does not send requests,
connect, initialize, synchronize, change settings, publish entities or schedule
reads. This is a prerequisite for read expansion, not a completed HA feature.
All tests use explicitly synthetic source-derived buffers, never watch captures.

Field meanings come from reference decoder/writer bodies. Exact response lengths
come from historical third-party ABL traffic. Neither establishes new physical
GET-only acceptance on the user's watch. A successful app session can include
interleaved SET commands; it does not authorize copying app initialization.

There is **no established GET/decoder for either running clock**. Zone settings,
configured DST increments and step-record timestamps do not provide a current
HH:MM:SS sample, clock accuracy or drift. No host clock is substituted. See also
[clock-reading-limits.md](clock-reading-limits.md).

## Shared contract

All decoder inputs accept only `bytes`, `bytearray` or `memoryview`, copied to
`bytes` before decoding. Other Python types, unusable buffers, incorrect lengths,
wrong opcodes and `FF` error responses raise the existing `ProtocolError` with a
fixed safe message. Unknown fields need not be zero. No raw input or watch name
is included in exception messages or logs. There is no encoder, executor,
generic-opcode API or current-clock helper.

All returned models are frozen dataclasses (the optional name is a `str`):

| API | Exact response shape | Result |
| --- | --- | --- |
| `parse_zone_settings(dst, home, secondary)` | `1D`, 15 bytes; `1E`, 7 bytes/index 0; `1E`, 7 bytes/index 1 | `ZoneSettings` |
| `parse_basic_settings(raw)` | `13`, 12 bytes | `BasicSettings` |
| `parse_auto_time_settings(raw)` | `11`, 15 bytes | `AutoTimeSettings` |
| `parse_timer_configuration(raw)` | `18`, 15 bytes | `TimerConfiguration` |
| `parse_watch_name(raw)` | `23`, 20 bytes | Optional identity text |

Offsets below include the opcode at byte 0. The decoders expose no transport
operations. Any later transport must validate and own the observations itself.

## Zone configuration, not current clocks

`ZoneConfiguration(index, standard_utc_offset_minutes,
configured_dst_increment_minutes, dst_rule_code)` inverts the seven-byte source
writer:

- Byte 1 must match the requested role: home index 0, secondary index 1. The
  source time-write flow establishes these slot roles; roles after a Home/World
  swap have not been physically confirmed. Historical sanitized traffic retains
  lengths/opcodes, not the response index values.
- Byte 4 is a signed int8 in 15-minute units, including negative and fractional-
  hour standard offsets. No additional geographical/range validation is invented.
- Byte 5 is an unsigned configured DST increment in 15-minute units, **not
  currently active DST**. Byte 6 is an integer rule code, including unknown codes.
- Bytes 2 and 3 remain uninterpreted. Offset and rule do not identify a city or
  unique IANA zone. No effective-offset calculation is provided.

`ZoneSettings(primary_dst_flags, primary_dst_on, primary_dst_auto, home,
secondary)` reads byte 3 of `1D`: low bit `01` means ON and `02` means AUTO;
`03` validly has both. When any upper bit is present, both interpreted states
are `None`; the raw unsigned flags remain in the internal model for later
interpretation, **not blanket public diagnostics**. Other bytes are unknown,
including any response selector and the secondary active-DST state.

## Basic settings

`BasicSettings(time_format, button_tone_enabled, light_duration_mode,
power_saving_enabled)` exposes only:

| Field | Source interpretation |
| --- | --- |
| `time_format` | Byte 1 bit `01`: set=`24h`, clear=`12h` |
| `button_tone_enabled` | Byte 1 bit `02`: clear=enabled, set=muted |
| `light_duration_mode` | Byte 2 bit `01`: clear=`short`, set=`long` |
| `power_saving_enabled` | Byte 1 bit `10`: clear=enabled, set=disabled |

The ABL inherits the client's power-saving capability flag; physical capability
is unverified. Light duration is not measured: the generic decoder's 2s/4s labels
conflict with ABL model metadata of 1.5s/3s. The model therefore exposes only
short/long; a later UI must retain provenance if using ABL-specific durations.

The generic 17-byte settings default is **not** the ABL GET length: all 14 retained
GET13 replies were 12 bytes. Byte 12 must not be read. No autolight, DnD, date
format, language, font, vibration or SIG field is inferred. SIG remains part of
the separate alarm contract.

## Automatic time adjustment configuration

`AutoTimeSettings(enabled, adjustment_minute)` reads byte 12 as a whole byte:
`00` means `True`, `80` means `False`, anything else means `None`. Byte 13 is a
binary minute 0–59 or `None`; the reference client's fallback to 30 is not copied.
Other bytes, including byte 14, remain unknown. This is neither a synchronization
operation nor proof of its schedule execution, last successful sync or accuracy.

## Encoded timer configuration

`TimerConfiguration(hours, minutes, seconds, encoded_duration_seconds)` preserves
binary unsigned bytes 1/2/3 and computes `3600*hours + 60*minutes + seconds`.
There is no BCD conversion, normalization, invented component-range restriction,
active flag or running/remaining countdown assertion. Bytes 4–14 are unknown.
The historical GET18 response is **15 bytes**, not the generic SET size of 7.

All-zero components remain encoded zero. The retained module-3565 manual audit
reports a maximum 60-minute timer and a 00:00 setting meaning 60 minutes, but its
wire mapping has not been physically compared. Thus encoded zero is **not**
published as an immediate timer and is **not** silently normalized to 3600 here.
A later presentation layer must label or withhold ambiguous durations until that
comparison; generic raw values do not establish supported ABL settings ranges.

## Optional watch-name text

The source name parser establishes ASCII text starting at byte 1. This decoder
uses a stricter validation policy rather than the client's arbitrary cleanup:
exactly 19 payload bytes, printable ASCII, only right NUL/space padding removed.
Empty, control/non-ASCII text and internal NUL followed by nonpadding text are
rejected. Leading spaces and ordinary internal spaces are not silently removed.
No constant model-name fallback, serial or firmware interpretation is provided.
Names are private data and must not become public diagnostic attributes.

## Provenance

- [GShockAPI 1.7.6, commit `5b1043a888ea3d97331c0c701f2da776ff638d8f`](https://github.com/izivkov/GShockAPI/tree/5b1043a888ea3d97331c0c701f2da776ff638d8f):
  [SettingsIO.kt](https://github.com/izivkov/GShockAPI/blob/5b1043a888ea3d97331c0c701f2da776ff638d8f/api/src/main/java/org/avmedia/gshockapi/io/SettingsIO.kt#L61-L129)
  (masks/positions),
  [WatchInfo.kt](https://github.com/izivkov/GShockAPI/blob/5b1043a888ea3d97331c0c701f2da776ff638d8f/api/src/main/java/org/avmedia/gshockapi/WatchInfo.kt#L94-L130)
  (defaults and ABL overrides at lines 223–230),
  [TimeAdjustmentIO.kt](https://github.com/izivkov/GShockAPI/blob/5b1043a888ea3d97331c0c701f2da776ff638d8f/api/src/main/java/org/avmedia/gshockapi/io/TimeAdjustmentIO.kt#L35-L68)
  (configuration fields), and
  [TimerIO.kt](https://github.com/izivkov/GShockAPI/blob/5b1043a888ea3d97331c0c701f2da776ff638d8f/api/src/main/java/org/avmedia/gshockapi/io/TimerIO.kt#L50-L77)
  (binary components/arithmetic). These pinned files were read statically, not run.
- Retained SmartSync 42.9.11 APK, SHA256
  `28ec2a6531b896413d978a91713bfdad74c785bda8698dee09ce9ffab116bc1d`:
  simple `DstForWorldCitiesIOFunctional.setDST` (bytes 4/5/6),
  `CasioTimeZoneHelper.CasioTimeZone` (quarter-hour units),
  `DstWatchStateIOFunctional.setDST` (byte 3),
  `TimeIOFunctional.calculateDSTValue` (ON/AUTO masks), and
  `WatchNameIO`/`WatchNameIOFunctional` (GET23/ASCII start).
  These retained decompiled bodies are separate evidence, **not proof the APK
  was compiled from the Kotlin pin**. Defective reconstructed methods were not
  used as execution evidence.
- Static historical ABL analysis: six `1D` responses of 15 bytes, twelve `1E`
  responses of 7 bytes, fourteen `13` responses of 12 bytes, eleven `11` responses
  of 15 bytes, two `18` responses of 15 bytes and two `23` responses of 20 bytes.
  These are opcode/length observations across three third-party captures, not a
  new request/response or physical acceptance trial. Captures, private names and
  device identifiers are not redistributed.

## Verification and later gates

`tests/protocol/test_settings.py` covers source-derived synthetic vectors,
envelope/type/index rejection, unknown bytes/flags/rules, signed and fractional
quarter-hour offsets, independent inverted bits, unknown auto-time values,
GET15 versus SET7 timer lengths, binary versus BCD and zero-timer ambiguity,
mutable-input independence, frozen model fields and private-name error handling.
Missing-API RED and semantic RED against no-op APIs are separate evidence; the
latter assertions preceded decoder implementation. No test is labeled a capture
or physical measurement.

The unchanged offline suite excludes all `*runtime.py` modules. Passing decoder
and offline suites does not establish HA runtime integration or physical support.
Later integration needs independent observations/freshness per feature, safe
failure with retained last-good values, and separately authorized physical
comparison. No deployment, session operations or settings writes are enabled by
this module or document.
