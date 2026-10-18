# Primary clock and second-zone reading limits

## Current conclusion

**No validated GET/decoder contract has been established for the ABL-100WE's
running primary or secondary clock.** The latest physical trial read only alarm
settings and SIG. No time/zone query was sent, and no clock entity is implemented.

The available references support a narrower **zone/DST configuration** proposal:

| Request bytes | Source-supported response | Meaning and limitation |
| --- | --- | --- |
| `1D 00` | 15 bytes, opcode `1D` | Main DST configuration; not a current clock |
| `1E 00` | 7 bytes, opcode/index `1E 00` | Primary-slot offset/DST configuration |
| `1E 01` | 7 bytes, opcode/index `1E 01` | Secondary-slot offset/DST configuration |

The seven-byte interpretation inferred from the reference writer is
`opcode, index, unknown, unknown, standard_offset, DST_increment, DST_rule`.
The standard offset is a signed byte in 15-minute units. The increment does not
prove DST is currently active; the rule is not a boolean. Unknown bytes remain
unknown. Offset/rule values do not uniquely identify an IANA timezone, and the
active DST state of the secondary clock is not established.

These requests and lengths are source/historical-traffic evidence, **not physical
GET-only acceptance on this pilot**. Historical app sessions also contained SET
commands, so success of the full app does not prove a read-only subset or justify
copying its initialization sequence.

## Misleading names and unsafe assumptions

- `getHomeTime()` in the Standard protocol returns a **city name**, not HH:MM:SS.
- `09` is implemented as **SET time**, including a date/time payload. Its encoder
  is not evidence that an equivalent GET exists. `39` is a declared constant
  without an established GET consumer/decoder in the examined material.
- `1F00` / `1F01` city queries have historical ABL error responses and no valid
  city response in the reviewed samples. Generic APIs and capability names do
  not establish this model's support. The analogue-protocol `2400` getter is not
  an established ABL running-clock read either.
- SmartSync's examined display path computes a clock using the phone's current
  time plus an offset. A correct-looking app clock therefore does not prove the
  watch's actual time was read. HA time plus an offset must be named **calculated
  HA time**, never time read from the Casio, and cannot measure watch drift.
- Generic initialization, time-setting facades and even AppInfo/scratchpad
  helpers can cause automatic SET writes. They are outside this read-only scope.

## Bounded future direction, not enabled work

If pursued in a later coordinated increment, reuse the existing HA-owned
transport and watch lock. Validate `1D00`, then `1E00`, then `1E01`, with strict
opcode/length/index handling, errors, cancellation and cleanup. Publish only
configuration actually decoded, with its own timestamp and freshness. Do not
construct a second client, send speculative clock queries, synchronize time,
change zones, initialize scratchpad or infer missing values from the host.

Reading both physical clocks remains an open research boundary, not a feature
that can honestly be replaced by a host-derived clock. No such implementation or
hardware trial is started by this note.

## References and provenance limits

- [GShockAPI 1.7.6, pinned source](https://github.com/izivkov/GShockAPI/tree/5b1043a888ea3d97331c0c701f2da776ff638d8f):
  `GShockAPI.kt` getters and `IGShockManager.kt` GET/SET transport.
- [gshock_api, pinned source](https://github.com/izivkov/gshock_api/tree/ae817310dd9551d5e6e53ee61d89af4e8d954767):
  public getters and connection transport.
- [CasioGShockSmartSync](https://github.com/izivkov/CasioGShockSmartSync): retained
  local APK version 42.9.11 and prior sanitized ABL traffic analysis were examined
  offline. Decompiled simple getter/writer/display bodies support the distinctions
  above; they are not a byte-identical reconstruction of the Kotlin tag. Some
  original scratch source trees were no longer available, so the analysis used
  retained source snapshots and explicitly separated their provenance.

No APK, third-party capture, private identifiers or raw device records are
redistributed here. Source analysis does not establish every firmware capability.
