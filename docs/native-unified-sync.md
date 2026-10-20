# Native unified read (installed experimental pilot)

`button.press` on **Read watch data** (`read_all`, Spanish **Leer datos del reloj**)
starts one explicitly initiated native ESPHome transaction. Setup does not contact
the watch. Enable the watch's Bluetooth connection window only when coordinating
a manual read. `auto_read_steps` retains its step-only meaning. Reviewed component
revision `badfe909c74f63a2b2d81c69b3b872df596e1b1c` is installed and includes the
separate default-off `auto_read_all` consumer described below. Both automatic
modes remain off; automatic dispatch is not physically accepted. The new
source increment is not a deployment.

## New automatic retained source (uninstalled, physically unverified)

The separate default-off `auto_connect_retained` option is mutually exclusive with `auto_read_steps` and `auto_read_all`; all three must be off before address reconfiguration. The two older options keep their one-shot meanings. Retained mode reuses the all-mode live RAW admission and exact original native generation, but connects the interactive owner, reads the six groups once, then keeps that same root alive without HA idle/absolute-session expiry. It does not repeatedly read, auto-apply drafts, send keepalive traffic or create another client. Explicit Refresh/Apply remain finite; native loss, Close, unload, observer stop and HA STOP join original cleanup.

One claim is consumed even on busy/failure. After actual terminal cleanup a new 30-second quiet boundary is required, extended by subsequent observed packets, so the closing burst cannot immediately reconnect. Source runtime tests fake only external wire/durations/filesystem; they are not physical reliability or ERR evidence. Details: [interactive owner](native-interactive-session.md#automatic-retained-connection-source-tested-uninstalled), [Spanish guide](automatic-retained-session.es.md). The installed revision above is unchanged by this source work.

## One owner, fixed order

The entry registers the request before awaiting the existing device mutex. It
selects the existing native ESPHome proxy and waits for a fresh, exact-address
advertisement. Non-native sources are refused, without a Bleak fallback or new
API client. The unchanged `NativeRead` owns one connection/discovery generation
and one outer disconnect. The new subclass borrows this link for exactly:

1. Alarms/SIG: GET `15`, then GET `16`.
2. Steps/history: subscribe DRSP and Convoy, send `0011000000` on DRSP.
3. Zone/DST configuration: `1D00`, `1E00`, `1E01`.
4. Basic settings: GET `13`.
5. Automatic-time configuration: GET `11`.
6. Encoded timer configuration: GET `18`.

ALL_FEATURES notification acquisition and its CCCD enable happen once; steps
reuse this channel. DRSP/Convoy metadata is resolved only after alarms complete,
so missing step metadata cannot block a validated alarm observation. Discovery
supplies every handle. No one-shot reader is called and no inner disconnect is
hidden. No GET22/10, GET23, initialization, END, SET, time synchronization,
pairing, reset, reconnect, retry or firmware change is introduced.

For this unified invocation, the first disconnect or error after an affirmative
BLE connection permanently ends operational authority. A later connected callback
without another CONNECT cannot restore it or its removed notifications. No later
requests or group commits are allowed; earlier committed groups remain intact.
This BLE lifetime is separate from the API generation: bounded cleanup is still
permitted on the original valid generation, never on a replacement. An initial
unconnected callback before establishment is not itself terminal.

## Independent observations, not atomic whole-watch success

Each group validates all its frames with the existing immutable parser, drains
adjacent callbacks, closes its local receiving state, then checks owner,
generation, link and outstanding caller cancellation. Value, aware UTC timestamp
and freshness commit synchronously before the next group. Deferred observers
cannot undo a commit if an observer or its scheduler fails.

A later error stops remaining groups, but keeps earlier committed observations.
Failed/unattempted groups retain last-good values and timestamps with freshness
false. A cleanup-only failure likewise keeps all valid observations but prevents
overall success. Caller cancellation/unload wins over backend and cleanup errors;
owned finite cleanup is joined, including repeated cancellation. Queue waiters
own neither active readiness nor freshness. The current component caches the six
validated semantic groups privately and restores them after reload with original
timestamps and `fresh: false`, retaining entity registry identities. It does not
restore a session, owner, task or automatic activation. See the
[interactive observation limits](native-interactive-session.md#truthful-status).

The button exposes only bounded status attributes:

- `phase`: idle, waiting, reading, closed; `wait_expires_at`: aware timestamp/null.
- `result`: never, success, partial, failed, cancelled.
- `group_results`: exactly alarms, steps, zones, basic_settings, auto_time, timer;
  each not_attempted, reading, success or failed.
- `cleanup_result`: not_attempted, success or failed. No disconnect submission
  after invalidated generation is represented as a successful cleanup.

A partial/failed read raises the fixed translated `sync_read_failed` error while
completed groups remain visible. Backend text is not copied into public status,
exception arguments, translation placeholders or component logs. Exception
causal chains remain useful internally. The wire has no transaction IDs: phase
checks reject matching unsolicited-before-invocation and duplicate/adjacent
frames, but cannot prove peripheral causality of an identical late response.
Unrelated notifications do not refresh another group. Real installed API methods
retain address/handle filtering.

## Read entities and current inventory

The original unified-read increment added four entities to fifteen, making
nineteen. The current installed component has 34 component files and 36 entity
identities: those nineteen, four interactive controls/status entities and thirteen
alarm/SIG draft controls. Existing alarm/step/zone identities and individual
actions are preserved. The four unified-read additions are:

- `read_all`: Read watch data / Leer datos del reloj.
- `time_format`: Time format / Formato horario, `12h` or `24h`;
  `button_tone_enabled`, `light_duration_mode` (short/long), `fresh`, `last_read`.
  No public power-saving capability assertion is made.
- `automatic_time_adjustment`: Automatic time adjustment / Ajuste automático de
  hora, enabled/disabled translated enum; `adjustment_minute`, `fresh`,
  `last_read`. In the current component, a valid observation with unknown enable
  is HA `unknown`, with freshness, its original timestamp and observed adjustment
  minute (possibly null); never-read remains unavailable. Unknown is never disabled
  or an invented minute. Reading this configuration does not set or synchronize
  time. This source behavior does not retroactively validate the historical
  unavailable observation below.
- `timer_encoded_duration`: Timer encoded duration / Duración codificada del
  temporizador, encoded arithmetic seconds (unit `s`); `encoded_hours`,
  `encoded_minutes`, `encoded_seconds`, `fresh`, `last_read`. This is not a running
  countdown. Encoded zero stays zero; no zero-to-60-minute interpretation exists.

Zone configuration and record dates are not current running-clock entities.

## Cooperative timing envelope

No timeout was increased. With the existing backend's cooperative bounds, a
conservative serial envelope is 360 s advertisement admission + 30 s connect
(and up to 5 s connect-failure cleanup) + 10 s discovery + six 10 s subscription/
CCCD stages + nine 10 s query-write stages + nine 12 s response waits + 5 s outer
disconnect = **668 s**, plus event-loop scheduling. Query writes without response
usually complete synchronously but are counted conservatively. The step group's
three notifications carry one 400-byte transfer, not three full-record waits.
Each notification acquisition is shielded and joined using the accepted finite
backend helper. This is a cooperative bound, not a hard deadline against an
uncooperative backend, OS pause or host failure; it is not a new watch timeout.
Do not wrap the service in a shorter controller timeout and infer a device fault.

## Validation limits

Tests traverse real HA setup, registry, `button.press`, session, installed
`APIClient` bodies, framing and parsers. Only external native connection messages
are synthetic. The decisive fake feeds request-provided append/completion
predicates, including wrong address/handle and service-DONE replies; two entries
share one wire. Separate offline suites exercise borrowed-group framing.

## Historical physical joint read — 2026-10-18

Component revision `e3690ff299d907b2867d19c65287fc9e19cce392` passed independent
SPEC and QUALITY review, was installed reversibly, and completed one coordinated
joint physical request on 2026-10-18. HA published alarms/SIG, 1,807 current-date
steps with history, zone configuration, basic settings and encoded timer data.
The operator affirmed the read and later reported 1,854 steps; that later count
was not another synchronization. Session and cleanup succeeded; the four manual
owners returned idle, the proxy was free and complete postflight passed.

The external operational result is a **narrow partial acceptance**: five groups
were observable and fresh, while automatic-time adjustment remained unavailable
and **unverifiable** because HA suppressed its extra metadata. Full-data and
full-trial acceptance remained false. This does not prove a new unknown flag,
minute, freshness or persistence for that setting. Group enums alone do not
provide the missing evidence. Newly added settings were not individually
compared on the watch display.

The watch ended in **ERR despite those published observations**. Its cause,
everyday reliability and the earlier connection/CCCD failures remain unresolved;
this transaction is not presented as their fix. The historical failures are not
erased by a later useful sample. See [validation](validation.md) for the exact
installed boundary, values, unknown history and retained historical gates.

## Optional automatic unified consumer (installed, physical acceptance pending)

`auto_read_all` defaults false, independently of legacy step-only `auto_read_steps`.
Both are strict booleans and mutually exclusive; malformed/conflicting stored
options fail setup before forwarding platforms. Options errors do not mutate or
reload an entry. Valid saves use the existing single selected-entry reload listener.
Either enabled mode blocks Bluetooth address reconfiguration, even at submission.
This automatic consumer adds no entities, migrations or identity changes. The
complete current installed component has 34 files and 36 entity identities on
the same device, including later interactive and alarm-editing additions. Both
options false means no automatic observer/task.

All mode attaches one local RAW handler to the selected HA-owned native ESPHome
API generation and one passive generation-maintenance timer. It sends no new
advertisement subscription/unsubscription, scanner request or API-client request.
Only a live exact-target packet carrying a recognized normalized model name and
actual address type 0/1 qualifies; a cached name/type cannot replace those fields.
The existing advertisement-data parser decodes the live payload. This conservative
heuristic does not recognize CNCT or guarantee a request for every physical gesture.

The first qualifying packet **delivered while bound/armed** creates a private,
single-use admission tied to the session, proxy entry and runtime/client/API
connection/proxy/disconnect-callback collection. Under the existing mutex and again
at consumption it checks owner, LOADED state, exact generation, cancellation and
finite nonfuture age at most five seconds. It uses that packet's address type,
without a second RAW wait. Invalid/reused tickets reject, never fall back to manual
waiting. Zero-argument manual calls still wait for a subsequent RAW packet.

A qualifying burst is consumed before busy, suspension, task occupancy or scheduling
checks. No automatic lock queue or retry follows success, partial data, failure or
busy refusal. A host-ingress timestamp gap of at least 30 seconds permits a later
burst even for identical payloads suppressed by ordinary HA callbacks. This time
is local host receive time, not an RF timestamp. Public metadata only suppresses
setup/resume windows; neither metadata nor the maintenance timer authorizes a read.

Maintenance detaches the old handler before reattaching on the same selected proxy
source's new usable generation, preserves the burst claim, and revokes old queued
tickets. It never migrates sources or reads. No guarantee covers packets before
setup or during unavailability/rebinding. Stop/unload removes only owned original
handles and joins cooperative cleanup under repeated cancellation; refused unload
retains observed claims. Native transport, query order, group commits, link latch,
timeouts and cleanup remain unchanged.

The historical manual physical sample above belongs only to `e3690ff`, not to
automatic dispatch in the installed component. Automatic physical acceptance and
reliability remain pending;
ERR remains unresolved. Both automatic modes stay off in the pilot. No physical
test or automatic work is armed by this documentation.
