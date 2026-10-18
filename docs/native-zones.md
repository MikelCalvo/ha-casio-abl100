# Native time-zone configuration reads

`Read time zones` is one manually initiated, native-ESPHome-only read group. It
borrows Home Assistant's existing proxy connection generation and the watch
session's mutex; it does not construct another BLE/API client or enable a new
automatic operation. Non-native sources fail without a fallback connection.
Existing alarm and step fallback behavior is unchanged.

## What is observed

Four default-enabled entities are added to the existing device (15 total,
including the preserved eleven):

| Key / unique-ID suffix | English label | Spanish label |
| --- | --- | --- |
| `read_time_zones` | Read time zones | Leer zonas horarias |
| `home_standard_utc_offset` | Home standard UTC offset | Desfase UTC estándar principal |
| `secondary_standard_utc_offset` | Secondary standard UTC offset | Desfase UTC estándar secundario |
| `last_time_zone_read` | Last time zone read | Última lectura de zonas horarias |

Offsets are integer **minutes of configured standard UTC offset**, without a
duration/timestamp device class or statistics state class. Each offset exposes
only configured DST increment, rule code and independent `fresh`; the home
sensor additionally exposes primary DST ON/AUTO interpretations. Unknown upper
flag bits leave both interpretations unknown (`None`). Raw flags are not entity
attributes or diagnostics. There is no invented secondary active/automatic DST.
The timestamp is aware UTC, recording completion of a successful full read,
not clock synchronization or a watch-clock sample.

Before the first full read, sensors are unavailable. Failed refreshes preserve
last-good observations and their timestamp but mark only zones stale. Steps,
alarms and midnight/date rejection do not refresh or invalidate zones. Reload
starts unknown, without a device read; stable identities and customizations
survive reload and native address reconfiguration.

HA supplies the device prefix. On a fresh English installation the new default
IDs are `button.casio_abl_100we_read_time_zones`,
`sensor.casio_abl_100we_home_standard_utc_offset`,
`sensor.casio_abl_100we_secondary_standard_utc_offset` and
`sensor.casio_abl_100we_last_time_zone_read`. Real HA Spanish defaults are
`button.casio_abl_100we_leer_zonas_horarias`,
`sensor.casio_abl_100we_desfase_utc_estandar_principal`,
`sensor.casio_abl_100we_desfase_utc_estandar_secundario` and
`sensor.casio_abl_100we_ultima_lectura_de_zonas_horarias`.

## Fixed transaction and ownership

Resolve unique feature-service/GET/notify/CCCD metadata and nonboolean integer
handles before IO. Subscribe to notify `26eb002d-b012-49a8-b1f8-394fb2032b0f`,
enable its CCCD with `01 00`, then write only the following fixed sequence to
GET `26eb002c-b012-49a8-b1f8-394fb2032b0f`, with response=False:

1. `1D00`: exactly 15 bytes, opcode `1D`.
2. `1E00`: exactly 7 bytes, opcode `1E`, index 0.
3. `1E01`: exactly 7 bytes, opcode `1E`, index 1.

One response is outstanding at a time. An immediate reply during public GET
invocation is accepted; matching-zone replies before invocation, between
queries, duplicate/extra replies, wrong order/index/length, empty and `FF`
responses invalidate the whole group. Unrelated opcodes are ignored. Unknown
payload fields need not be zero. Adjacent callbacks are drained; invalidation
while cleanup is pending prevents publication. Retained callbacks after closure
are inert and all response-future results/errors are consumed.

The wire has **no transaction ID**. Local phase correlation cannot establish
peripheral causality for an indistinguishable stale callback delivered after
public query invocation. It is not a claim about radio submission timing.

The unchanged `NativeRead`/`NativeAPI` own original-generation link/callback
cleanup. The narrow zone adapter reuses accepted finite notify acquisition and
cleanup but rejects inherited alarm GETs and all other writes. It never closes
HA's API connection or touches a replacement generation. Backend-only
cancellation is failure; any outstanding caller cancellation conservatively
wins over backend/cleanup errors. A cleanup-only failure prevents commit; a
primary error retains its identity across both cleanup owners. Observer
execution/scheduling is best-effort after synchronous commit, not part of read
acceptance. Shutdown joins registered queued/active work and cancels pending
observer delivery; refused unload preserves the session.

### Bounds

No retries or new scheduler are added. Existing native advertisement wait is
360 s; connect is 30 s plus its 5 s cleanup, discovery 10 s, installed notify
and descriptor operations each 10 s, writes 10 s, each response 12 s,
disconnect 5 s. Transport stage limits remain 12 s. A conservative composed
after-lock envelope is **516 s**: 360 + 35 + 10 + 22 (joined acquisition and
subscription) + 3×(12+12) (write/response stages) + 12 (transport cleanup) + 5
(owner fallback). This deliberately overcounts healthy stages and excludes
arbitrary mutex queue time, an uncooperative backend, host suspension/death and
external framework overhead. Repeated cancellation does not abandon finite
acquisition or cleanup children.

## Evidence and exclusions

Field provenance and immutable decoder semantics are in
[settings-read-contract.md](settings-read-contract.md), based on GShockAPI 1.7.6
commit `5b1043a888ea3d97331c0c701f2da776ff638d8f` and retained source/third-party
ABL response lengths. The signed quarter-hour offset is byte4; configured DST
increment byte5 is unsigned quarter-hours; byte6 is an uninterpreted rule code.
Slot0/home and slot1/secondary roles are source-inferred, not verified after a
Home/World swap.

Tests exercise real HA services, translations, registries, lifecycle and
installed aioesphomeapi methods with synthetic **external wire only**. These
are local source/runtime checks, **not physical acceptance**. Neither current
running clock has a validated GET. No host-now-plus-offset display, IANA/city,
drift, battery, timer, basic settings, name, time-sync, SET, END, keepalive,
bonding, firmware or write control is added. Diagnostics remains its existing
whitelist. Installation, deployment, remote access and a physical watch trial
are outside this unit.
