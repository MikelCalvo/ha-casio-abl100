# Pilot validation

## Current installed boundary — daily-use R3, 2026-10-21

Reviewed component revision **`e9a385558034e9e401a4a39d3665b9f824825c08`**
is installed: **37 component files and 37 entity identities**, preserving all
36 earlier identities, semantic observations, original timestamps, local Store
and safety journal. The modern stock dashboard was installed and inspected in
real desktop and 390-pixel mobile views. A disconnected Send displayed the
connection-required error; it did not send a watch-setting change. Source
SPEC/QUALITY, recoverable installation and physical behavior are separate gates.
A newer cleanup/window-capture candidate is not installed merely by publication.

### Coordinated read and idle samples — 2026-10-21

Two separate manually pre-armed connections followed immediately by an explicit
Refresh completed all six groups: alarms, steps, zones, basic settings,
automatic-time configuration and encoded timer. Each group published its own
new observation timestamp; valid unknown automatic-time semantics remained
unknown, not disabled. These were real integration-owned reads, not replayed
fixtures or restored observations. They do not visually validate every setting.

- In the first useful sample, another Refresh approximately 60 seconds after
  the completed read lost the native link at `get15_wait`, status **8**, before
  accepting the second alarm group.
- In the idle control, no second read was sent. The link was lost **64.517
  seconds** after the successful read, before the manual 120-second HA idle
  limit. Therefore a second read is not necessary for this observed failure;
  its timing alone does not establish a radio or watch-side cause.
- Last-good values and timestamps across 27 checked observation/editor entities
  remained preserved after each loss. Cleanup succeeded, the original proxy
  slot was free, and the selected watch/ESPHome entries remained loaded.
- The operator confirmed connection during the reads but **did not observe the
  idle control's terminal screen**. Their report that automatic disconnections
  usually show ERR is not a confirmed ERR for that sample.
- An automatic retained attempt failed before any group was read. An earlier
  parent browser experiment also failed to dispatch its intended read because
  it inspected a stale frontend Hass object; it is excluded as a read trial.
  The corrected parent sequencer was verified separately before the useful
  samples. Neither that helper correction nor the data reads repair link loss.

No setting SET, pairing, reset or firmware change was made in these read
samples. All three automatic modes were off at final postflight; no new physical
window was armed. Persistent listening, 30-second automatic refreshes and
naturally occurring automatic windows still require their own acceptance.

### Historical alarm-5 roundtrip — 2026-10-20

The following results belong to the previously installed
**`badfe909c74f63a2b2d81c69b3b872df596e1b1c`** component, with 34 component
files and 36 identities. They remain valid historical evidence, not new R3
write trials.

| Verification layer | Confirmed result | Limit |
| --- | --- | --- |
| Native requested change/readback | Disabled alarm 5 `00:00` → `00:01`, then `00:01` → `00:00`; both verified | One time-change/restoration pair on the tested setup, not general reliability |
| Native preservation | Alarm 5 remained disabled; other alarms and SIG preserved | No enable or SIG toggle was requested or physically validated |
| Operator display comparison | `12:01`, then `12:00`, on the watch's 12-hour alarm display | Alarm value comparison, not a current running-clock read |
| Explicit Close and fresh post-use checks | Clean closure, terminal owners, released proxy; installed source/identity preservation and continuing node health authenticated | Point-in-time postflight, not future stability or an ERR fix |
| Watch terminal screens | **ERR on both write trials** | Cause unresolved despite verified values |
| Operator timing report | Approximately 20 seconds | An estimate, not measured service/connection/closure duration |

The implemented enable/SIG writer paths and uncertainty journal/reconciliation
have source/synthetic evidence, **not physical acceptance**. Uncertain-write
recovery was not exercised on the watch. Both automatic modes remain **off**;
automatic dispatch and unattended use are unvalidated. Other settings writers
remain withheld, current primary/secondary clock reads and time synchronization
are unimplemented, and everyday reliability remains open. See
[alarm draft semantics](native-alarm-editing.md), [interactive controls](native-interactive-session.md)
and [usage in Spanish](alarm-editing.es.md).

## Historical checkpoints

The sections below retain their **then-current** revision, inventory and test
boundaries. “Not installed”, “unimplemented” and “review pending” refer to their
named historical artifacts, not the current installed boundary above. Counts
from overlapping synthetic selections must not be added as a total or treated
as physical-device samples. Rejected intermediate candidates stay rejected.

### Historical local alarm/SIG editing checkpoint — before installation

The local source adds 13 nonpersistent draft controls and explicit Apply/readback
through the retained interactive native owner. Focused real-HA services with a
synthetic external memory wire exercise staging without IO, Connect without SET,
canonical SET15/16, held readback, mismatch/unknown and GET-only reconciliation.
This is not installation or physical write acceptance. Correction R1 preserves
the reconciliation prerequisite across actual reload and cold saved-byte load
using a separate acknowledged local safety journal, without persisting drafts.
Zone acceptance now counts named alarm additions while preserving historical
names; setup-failure fixtures distinguish pre-platform zero from late 36 restored
placeholders and retain the original STOP callback census. Local test receipts
are private; independent complete-unit SPEC and QUALITY remain pending. See
[alarm draft semantics and limits](native-alarm-editing.md). Historical physical
claims below are unchanged.


### Historical installed joint read — 2026-10-18

Installed component revision: **`e3690ff299d907b2867d19c65287fc9e19cce392`**.
This supersedes the earlier alarm-only pilot below. One coordinated native
**Read watch data** request used the existing HA-owned ESPHome API connection
and published the implemented groups in the fixed alarm-first transaction.
The private operational checker accepted **five observable groups**, not all
six: unknown automatic-time configuration remained unavailable with metadata
suppressed by HA, so that group is explicitly **unverifiable**. A successful
integration group enum does not authenticate an unavailable setting's value,
freshness or last-read timestamp.

| Real joint-read observation | Result |
| --- | --- |
| Steps | 1,807, current record date; operator affirmed the read and later reported 1,854 on the watch, not a second HA read |
| Alarms/SIG | Five slots `00:00`, only first enabled; SIG off |
| Zone configuration | Primary standard UTC+1, configured DST +60 minutes, DST on/automatic; secondary standard UTC+9, DST increment zero |
| Basic settings | `12h`, button tone enabled, short light mode |
| Encoded timer | 600 encoded arithmetic seconds, components `00:10:00`; no running-countdown claim |
| Automatic-time adjustment | Unavailable and externally unverifiable; not accepted as disabled or fresh |
| Historical daily slots | Yesterday zero; six earlier slots `null`, not fabricated zeros or independently compared historical totals |
| Watch terminal screen | Operator confirmed **ERR**, despite the received observations |

Group timestamps advanced between `2026-10-18T20:24:27Z` and
`2026-10-18T20:24:30.519366Z`. Session result and cleanup result were success;
all four manual owners returned to idle with explicitly null expiry. The
worker exited zero, the proxy was free, automatic steps stayed off, and fresh
postflight verified source, firmware lineage, selected entries, preserved
identities and repeated temperature/humidity reports. No second watch gesture,
watch-setting write or firmware change was made for this trial. Individual
visual comparison of the newly added settings remains unreported.

The reversible update checked target-interpreter syntax and private backups,
installed exactly **26 component files** and preserved the original **11
identities plus 8 allowed additions**, for 19 on the existing device. One
necessary Core restart was acknowledged and independently correlated with the
new container start, running Core/Supervisor and loaded selected entries. The
historical startup stall did not recur in the observed check; its cause is not
diagnosed. Observations reset on restart and were then populated by the new read.

The exact source had independent SPEC PASS and QUALITY APPROVED, with retained
**1,256 offline / 479 real-HA runtime** passing cases and focused link-lifetime
regressions. External device IO in those suites was synthetic. The separately
reviewed private operational wrapper passed 48 tests, including the real HA
unavailable-entity formatter boundary. These are different, overlapping
verification layers, not additional hardware reads or reliability samples.

At this historical gate, automatic combined dispatch was **not implemented or
enabled**. The legacy `auto_read_steps` option remained step-only and off. The
planned next increment was an explicit opt-in combined consumer with one owner
and no loss of the first qualifying advertisement. Current primary/secondary
time reads were not established; see [clock limits](clock-reading-limits.md).

### Historical installed alarm-only boundary

The historical pilot at component revision `a897b0078a73deeaafbec0c1265be4848592f343`
published a complete alarm/SIG snapshot at `2026-10-18T01:41:26+00:00`, with 11
identities, closed window and a healthy proxy/node. Its final watch screen was
not reported. That exact component passed 449 offline/143 real-HA runtime cases,
with synthetic external IO, and its private deployment wrapper passed 12 recovery
tests. Those earlier counts and unknown screen apply to that trial, not the new
joint read or its confirmed ERR.

### Historical address-reconfiguration gates

The sections below retain their **then-current** revision and test boundaries.
Statements such as “not installed” or “review pending” apply to those historical
gates, not to today’s inventory. They do not supersede the installed boundary
at the start of this document, and overlapping test counts must not be added together.

#### Native address reconfiguration: historical local review candidate

This increment is based on exact local baseline `fd6228bc28586f385ab130a03cc4f7fa32e5099f`. The original worktree was clean and is left unchanged. Its retained baseline rerun passed **444 offline tests (five dependency deprecations) and 42 real-HA runtime tests**, with synthetic BLE. The initial reconfiguration candidate added 28 runtime cases and passed 444 offline / 70 runtime tests, but independent SPEC found one pre-existing user-flow reservation gap: HA's native ignored entries were omitted and could be replaced. That candidate remains blocked and was not deployed. Revision 1 adds 10 runtime cases using native ignored-entry production plus ignored/disabled address-and-identity controls. Causal RED preserved 3 behavioral failures and 7 passing controls; after the minimal reservation fix, all 10 pass. The revised combined selection passes **444 offline and 80 real-HA runtime cases**, exit 0 each. These are replacement-suite counts, not additional physical reads; independent SPEC and QUALITY of the revised artifact remain pending.

Causal RED snapshots retain source/test bytes, command/environment, raw output and actual exits before production changes: divergent transport/identity produced the wrong 11 unique IDs; the missing native reconfigure API was asserted explicitly before implementing the step; subsequent behavioral RED cases showed absent duplicate-address, auto-off, missing-identity, stale-selection and ASCII-normalization guards; the EN/ES UI catalog failed before translations were added. Existing correct rejection/fallback cases and later lifecycle/isolation assertions are characterization, not independent proof of RED. Initial harness errors and secondary teardown failures were retained and rerun correctly, not counted as causal evidence.

Runtime tests exercise actual HA flows, entry mutation and reload, all 11 entity-registry IDs/unique IDs/internal IDs on the same device-registry ID, EN/ES translation loading, two-entry isolation, default-off/explicit-off options, rejected validation without mutation, both other-entry transport and identity conflicts, and conflict/option changes while a form is open. Explicit `button.press` requests use the new address through the unchanged real session, transport and parser against synthetic BLE. Synthetic old/new advertisements plus real HA timer dispatch do not trigger a read while auto remains off. A held manual transaction is cancelled/cleaned before the new entry session becomes usable.

HA 2026.9.4 calls existing update listeners for data changes and warns against separately scheduling another reload. Loaded entries therefore use native `async_update_and_abort` with the existing update listener; unloaded entries use `async_update_reload_and_abort`. Runtime assertions verify exactly one reload in both paths. Only `data.address` is updated; there is no new persisted key, migration, unique-ID rewrite, raw-storage edit or name-based auto-rebind. An entry without a unique ID still loads with address-based compatibility identity, but reconfiguration is refused because preserving its identity would otherwise require a separate migration.

The earlier offline ConfigEntry double now supplies its real `unique_id` field; the revision1 flow doubles accept HA's native `include_ignore` keyword. All original behavioral assertions remain unchanged. Reservation checks explicitly include ignored entries, so native user-flow override behavior cannot replace another reserved identity. The previous temporary interpreter had been pruned. Tests reuse pinned HA/Bluetooth packages already retained in the local wheel cache through isolated `PYTHONPATH` entries; no package install or network fetch was performed. The runtime suite disables IP sockets and uses the HA plugin's synthetic Bluetooth boundary. Five offline dependency warnings match the recovered pristine baseline; no runtime warnings were reported.

No live HA, ESPHome, ADB, SSH, watch or deployment operation was performed. Independent review, deployment authorization and physical acceptance remain separate gates. See [English procedure](../README.md#native-bluetooth-address-reconfiguration) and [procedimiento en español](reconfigure.es.md).

### Historical acceptance boundary retained from the base

This document retains historical implementation/review gates below. Since those gates, alarm revision `fefd06b10375bea34e84ec060f34acc2c9cb86c5` was installed with 13 component-file hashes verified. One fresh complete read of AL1–AL5 and SIG on 2026-10-15 was confirmed by the operator: enabled and disabled alarms, and disabled SIG, matched the watch. The watch still showed `ERR`; the detailed capture had already ended before that successful read, so its cause is unknown. A SIG-on test, separate-session alarm repeatability and general reliability remain open. No watch setting/time or pairing change was made.

The **daily-history increment** changes only the parser and existing step entity's snapshot attributes. It has not been installed or physically verified. At the historical gate recorded here, reviewer dispatch failed before independent SPEC/QUALITY execution. Its recorded local gates passed **386 offline cases** (the same five dependency deprecations as the pristine baseline) and **13 real-HA runtime cases**, all with synthetic BLE. The pristine alarm baseline was rerun separately: **351 offline and 11 runtime cases passed**. These counts overlap and must not be added as independent coverage.

The new focused tests first failed at the missing API, then at substantive empty-history and absent-attribute assertions before decoding/wiring. The full first regression run exposed one obsolete SimpleNamespace fixture lacking the new StepRecord field; it was replaced with the actual backward-compatible StepRecord type, without weakening the existing date/unavailability assertion. The saved failing run is retained privately. Two additional runtime cases exercise English/Spanish actual `button.press`, exact unchanged START-only command, disconnect, seven dated totals with a missing slot and a real zero, failure retention, timestamp independence and reload identity. They characterize the integrated test-first increment; they are not a physical read or an independent review.

Seven previous-day summaries plus a separate current total are source-described protocol capacity, not a manufacturer retention guarantee. `daily_history` is a bounded snapshot attribute, not persistent history or retrospective Recorder/statistics import. The transport, session logic, existing IDs and diagnostics whitelist are unchanged. That history-only gate did not include automatic synchronization. The optional reader described below adds step-window requests, not settings editing or an `ERR` fix.

### Optional native step-window reader: historical local boundary

The combined history/reader candidate is **local, uninstalled and not physically validated**. The installed pilot remains alarm revision `fefd06b10375bea34e84ec060f34acc2c9cb86c5`, with manual step and alarm actions. There has been no new physical test: the accepted manual step results remain 229 and 276, and the accepted AL1–AL5/SIG snapshot does not resolve the watch's `ERR`.

The daily-history increment `986976b`, step commit guards/options at `83f9af3`, and standalone observer at `eed5783` have independent local SPEC and QUALITY approval within their respective scopes. These do **not** by themselves establish final entry-lifecycle wiring, combined-bundle acceptance, unattended operation or hardware compatibility.

The composed code revision `9d8d245` passes **444 offline tests and 42 real-HA runtime tests**, with synthetic BLE and no radio hardware; a separate parent execution reproduced both results with process exit 0. The offline run reports five existing dependency deprecations. Runtime coverage includes actual manager callback deduplication and metadata-timer dispatch, entry options/reload isolation, manual-read contention, failure privacy, setup rollback and cancellation/unload cleanup. A narrowly updated ConfigEntry test double preserves the original unknown-alarm/no-read assertions while supplying HA's options/listener lifecycle APIs. Final independent reviews of the complete bundle remain pending. These counts overlap earlier layer selections and must not be summed with them; historical counts elsewhere apply only to their named revisions/layers.

#### Historical intended behavior and evidence limits

- At this historical gate, the standard integration **Configure** options exposed English/Spanish `auto_read_steps`, default **false**. A change reloaded only the selected entry, preserving the then-existing 11 entity identities and both manual buttons. Reload then reset observations; the current local durable-observation candidate instead restores the six validated semantic groups stale with their original timestamps, without reading or restoring automatic/session authority. Its real-HA tests cover reload and two fresh HA instances with a real private file through STOP/FINAL_WRITE; this is offline evidence, not installation or physical validation. Valid unknown auto-time now exposes HA `unknown` plus observation metadata; that does not retroactively verify the historical unavailable trial.
- An entry-owned public HA Bluetooth **PASSIVE** callback matches only the exact configured address with connectable availability. A one-second timer reads public Bluetooth metadata because HA deduplicates unchanged-payload callbacks even as packet timestamps advance. This is not watch GATT polling or a second ESPHome scanner.
- A cached target burst at setup/reload is consumed without a request; the first fresh observation can qualify if no cached target exists. Timestamp advances less than 30 seconds apart form one observed burst; a gap of at least 30 seconds permits another. Only one request per burst is allowed, including consumed busy/failure cases.
- A read opportunity must be at most five seconds old, rechecked at scheduled-task entry. Busy/stale opportunities and failures have no queued automatic retry. The existing connector may still make its bounded internal connection attempts (maximum two) within the one request.
- Disabling/unloading removes the callback and timer and cooperatively cancels/joins the owned read. This is not a forced-release guarantee for an uncooperative backend.
- Automatic requests use the unchanged step-only DRSP START transaction and include the existing seven dated daily-history slots on a valid read. There is no Recorder backfill, alarm/time/settings/AppInfo query, END request, pairing or firmware change. Alarm reads remain manual.

This detects **observed timestamp bursts, not physical `CNCT` gestures**. Phone, automatic time-connection and FIND windows can qualify; scanning outages and missed samples make boundaries ambiguous. No empirical result establishes that automatic time windows permit a completed step transfer. No unattended reliability or watch-side `ERR` resolution is claimed.

For a future coordinated acceptance, enable the option through Configure, allow setup/reload suppression to settle, then open a fresh `CNCT` window near a connectable proxy without pressing the HA step button. With the option off, use the existing manual button during `CNCT`. Preserve failures rather than repeatedly retrying to obtain a success. Both procedures require separately coordinated physical acceptance; they are not completed tests of this feature.

### Historical physical step results on then-installed revision `008f026`

On **2026-10-15**, the installed ALL_FEATURES-first implementation at commit [`008f026d6177a8a29592a872987b560dd9b723db`](https://github.com/MikelCalvo/ha-casio-abl100/commit/008f026d6177a8a29592a872987b560dd9b723db) completed one explicitly initiated physical read through Home Assistant and an ESPHome Bluetooth proxy. Home Assistant published **229 steps**, dated **2026-10-15**, and the operator confirmed **229 on the watch display**.

A second separately initiated `CNCT` session on the same date published **276 steps**, matching the operator's reported display count. The successful read timestamp advanced to **2026-10-15T00:41:02Z**, with the same record date and `fresh: true`. No integration or firmware change was made between these two successful sessions.

This establishes two end-to-end manual reads and one successful reconnection with an updated count on the tested setup. It does **not** establish long-term reliability, continuous synchronization, other models, clock/alarm support, or button-triggered automations. It also does not prove that the additional subscription was the sole cause of success: an earlier minimal-reader session had already passed notification setup before failing at a later parser boundary.

#### Historical source and deployment identity — `008f026`

- Tested integration version: `0.1.0`, experimental, without a stable release.
- Both successful physical reads used component files from `008f026d6177a8a29592a872987b560dd9b723db`. The newer local alarm-reader code at `3ae255ae8ea27505ee2adcae4c17622ef86cdeda` has not been installed. Its synthetic test results below must not be attributed to the installed revision or treated as another hardware test.
- The installed artifact's 11 component-file hashes were checked against the reviewed source before installation and again after each physical read. Target-interpreter syntax and the HA configuration check passed; one Core restart loaded the variant. No sensor firmware or watch pairing/settings were changed for this update.
- The public repository preserves development history with private test logs, generated bytecode and an excluded third-party capture removed. Its commit IDs differ from the original private history.
- The public test fixture is newly constructed synthetic data, not a historical watch capture. Its **1485-step example is not a physical result** and must not be confused with the measured 229 and 276 steps.

#### Historical installed-step validation layers

| Layer | Result | Boundary |
| --- | --- | --- |
| Offline protocol and HA-boundary tests | 73 passed; five upstream dependency deprecation warnings | Synthetic BLE clients, not hardware |
| Real Home Assistant 2026.9.4 runtime tests | 7 passed | Actual setup, registries, unload and job-dispatched date rollover; synthetic BLE |
| Independent source review | SPEC PASS and QUALITY APPROVED for `008f026` | Local review, not a general hardware-compatibility certification |
| Target configuration and loading | Syntax and HA configuration checks passed; entry loaded after one Core restart | No automatic watch read |
| Device/entity identity after update | One existing Casio device and three entities; no duplicates | English frontend rendering remains unverified |
| Physical manual reads | 229 steps, then 276, matching the watch display | Two separate sessions; not long-term reliability |
| Reconnection | Second session used cached services and published the changed count | One successful reconnect, not a reliability percentage |
| Disconnect after successful reads | Completed; proxy slot free after both | Not unload/cancellation during a real active transfer |
| Temperature/humidity node | Remained operational; no node reboot for the read | Not a long-term stability test |
| Scoped diagnostic logging | Temporarily enabled for the read and restored afterward | Raw logs remain private |

The original public tree passed 47 offline and 7 real-HA runtime tests with synthetic BLE. The five/seven-byte header correction then passed 62 offline and 7 runtime tests. Those are historical gates for earlier revisions, not extra successful hardware reads.

#### First successful physical step session — 2026-10-15

1. The operator opened ordinary `CNCT` mode near the room proxy, with the phone's Bluetooth disabled. One actor initiated one HA **Read steps** service request.
2. The proxy connected without a cached service database, completed service discovery and reported MTU 512. That reported size is not evidence of a fault: the complete read succeeded in this session.
3. ALL_FEATURES was resolved by UUID inside the discovered Casio features service, with notify and CCCD metadata validated. Its subscription completed in approximately **0.274 s**.
4. DRSP and Convoy subscriptions completed in approximately **0.137 s** and **0.254 s**. Every subscription was awaited before the application request.
5. The only application write was `00 11 00 00 00` to DRSP. Its write completed in approximately **0.252 s**; the record future then completed in approximately **0.202 s**.
6. The unchanged parser accepted the supported DRSP announcement, exactly 400 Convoy bytes, the current record date and coherent step fields. No raw header or record was retained in the diagnostic output, so their exact bytes are not claimed as measured here.
7. Disconnect completed in approximately **0.173 s**, and HA published 229 steps with `fresh: true`. Last successful synchronization was **2026-10-15T00:26:58Z**.
8. The operator independently confirmed 229 steps on the watch. The proxy slot was free afterward and temporary transport DEBUG logging was removed.

No end/clear request, scratchpad initialization, time/DST/city, alarm, pairing or connection-parameter change was sent by the integration.

#### Second successful physical step session — 2026-10-15

- The operator reported **276 displayed steps** and opened a fresh `CNCT` window. One HA **Read steps** service request was made, without another manual retry.
- The proxy used cached services and reported MTU 23. That cached-path report does **not** establish the effective negotiated on-air MTU; it must not be treated as a controlled MTU comparison with the first session.
- ALL_FEATURES, DRSP and Convoy subscriptions completed in approximately **0.392 s**, **0.233 s** and **0.266 s**, respectively. START completed in **0.139 s**, the record future in **0.310 s**, and disconnect in **0.062 s**.
- HA updated the count from **229 to 276**, with `record_date: 2026-10-15`, `fresh: true`, and synchronization at **2026-10-15T00:41:02Z**. This was a new validated read, not retention of the previous value. From the HA request to the published state took approximately **14.1 s**, including connection setup.
- The proxy slot was free afterward. The sensor node remained available, with subsequent humidity and uptime reports. The component hashes still matched `008f026`, and scoped transport logging was restored. No watch settings, integration code or firmware were changed.

### Historical protocol changes and earlier failures

#### Announcement length

The earliest reader required exactly five DRSP announcement bytes. Upstream `izivkov/gshock_api` ABL examples at `ae817310` also show `00 11 90 01 00 00 00`, a seven-byte form. Android GShockAPI `1.7.6` (`5b1043a`, `StepCounterIO.onDrspReceived`) reads the command, category and 24-bit little-endian length from the first five bytes when at least five are available.

The reader now accepts only the known five- and seven-byte forms, with command `00`, category `11` and length 400 in bytes 2–4. The final two bytes are not part of the length; their purpose is not established. Duplicate announcements within the acceptance window, missing data, overflow and extra fragments are rejected. The raw unsequenced Convoy format does not permit general content-based deduplication or reorder recovery.

#### Subscription order

Android GShockAPI `1.7.6` and Python `gshock_api` subscribe to ALL_FEATURES before the lifelog channels in the examined ABL sessions. The implemented variant resolves `26eb002d-b012-49a8-b1f8-394fb2032b0f` within Casio features service `26eb000d-b012-49a8-b1f8-394fb2032b0f`, validates notify/CCCD metadata, discards that channel's payload and awaits its subscription before DRSP and Convoy. No general Android initialization or settings-writing helper was copied.

Optional integration DEBUG diagnostics contain fixed stage names, monotonic durations, error classes and primary characteristic/CCCD metadata. They do not include raw payloads, the watch address or exception text. Privacy of unrelated library logs is a separate boundary.

#### Historical physical failures

Earlier revisions did not produce valid steps:

- The first attempt connected, but timed out after 12 seconds while awaiting the first DRSP CCCD descriptor-write response. It never sent the lifelog start request. The operator's 29 displayed steps were **not** a value read by HA.
- Another attempt failed during link establishment, before notification setup.
- One session completed both lifelog subscriptions and START, but the old parser rejected its announcement. The actual announcement bytes were not logged.
- With the five/seven-byte parser correction installed, a fresh-discovery session failed on the DRSP CCCD with explicit GATT error 133 and a link timeout. HA received the descriptor error before START. This did not establish pairing, MTU or stale handles as the cause.

These failures remain part of the evidence. Two successful ALL_FEATURES-first sessions do not retroactively diagnose their exact causes, and no reliability percentage is claimed.

### Historical alarm-reader candidate gate (before installation)

The code candidate is `3ae255ae8ea27505ee2adcae4c17622ef86cdeda`. It retains the accepted step reader and adds an explicitly initiated **read-only** alarm snapshot. Subsequent documentation-only commits do not change those component files. No physical alarm query, settings write, installation or restart was performed to validate this candidate.

| Layer | Candidate result | Boundary |
| --- | --- | --- |
| Offline protocol/HA-boundary suites | 351 passed; five pre-existing dependency deprecation warnings | Synthetic alarm and step frames/clients |
| Home Assistant 2026.9.4 runtime | 11 passed | Real HA registries, services, translations, lifecycle and date scheduling; synthetic BLE |
| Independent reviews | Parser, transport, session and entities passed local SPEC then QUALITY | Exact reviewed source identities, not hardware certification |
| Real-HA setup and reload | One device, 11 stable entity identities; no automatic BLE read | Isolated test instance, not the deployed pilot |
| Real-HA manual alarm action | `button.press` → session → transport → parser publishes five times/flags, SIG and independent UTC read timestamp | Lookup, connector and BLE client are synthetic |
| Failure handling | Malformed reply or connector failure retains last observation and marks alarm freshness false | Controlled synthetic failures |
| Lifecycle and independence | Shared step/alarm lock; partial-connect cleanup, cancellation, unload; alarm state survives midnight while step validity follows date | Cooperative backend simulations and actual HA jobs |
| English/Spanish names | Friendly names, default IDs and exception catalogs checked in real HA | Not an interactive browser screenshot test |
| Diagnostics/privacy | Existing five-key diagnostics whitelist; no alarm times, flags, auxiliary bytes, address or error text | Own logging/diagnostics only, not all third-party library logs |
| Physical alarm read / activated flags | **Not performed / unconfirmed** | Requires separately coordinated watch comparison |

#### Historical alarm protocol evidence and scope

The [module 3565 manual](https://support.casio.com/global/en/wat/manual/3565_en/UQIDSYhietkkjq.html) documents five daily alarms AL1–AL5 and a separate hourly signal SIG. Pinned [Kotlin GShockAPI 1.7.6](https://github.com/izivkov/GShockAPI/tree/5b1043a888ea3d97331c0c701f2da776ff638d8f) (`Alarms.kt`, `AlarmsIO.kt`, `IGShockManager.kt`) and [Python gshock_api](https://github.com/izivkov/gshock_api/tree/ae817310dd9551d5e6e53ee61d89af4e8d954767) provide the request/record interpretation. Historical third-party ABL traffic contains five- and seventeen-byte alarm responses with slots disabled; it is not a capture from the pilot watch and is not redistributed here. It does not validate activated bits.

The candidate resolves the feature service and required properties from discovered Bleak metadata, awaits notification setup on `002d`, then writes only single-byte GET `15` and GET `16` to read-request `002c`, without GATT response. It requires one valid `15 + 4 bytes` before requesting `16 + 4 × 4 bytes`; each entry is `[flags, auxiliary, hour, minute]`, with numeric hour 0–23 and minute 0–59. The auxiliary byte may be zero or other values; a SET encoder's `0x40` constant is not imposed on reads. Enable mask `0x40` and SIG from **AL1 only** mask `0x80` are source interpretations pending physical comparison. No upstream encoder/facade is called, and no alarm settings are written.

Each call owns its phase/futures, rejects locally early, malformed, duplicate or error replies and disconnects before returning a complete snapshot. Missing/failed reads never fabricate disabled alarms. The session serializes steps and alarms but keeps their observations, freshness and successful timestamps separate. A positive public task-cancellation count is conservatively treated as caller cancellation, including an already-present count; it cannot distinguish all previously handled cancellation history. Backend-only cancellation at count zero becomes a sanitized read error. Timeouts require cooperative cancellation rather than guaranteeing abort of an uncooperative backend.

#### Historical observable limits and review provenance

Alarm notification frames contain no transaction ID. A response phase is armed only as the public query-write operation begins, rejecting callbacks delivered before that point. A queued old same-opcode callback delivered after invocation can be indistinguishable from an immediate genuine response. An adversarial probe for the stronger pre-radio-send guarantee remains failing and is retained privately; that guarantee is **not claimed or part of the local phase contract**. Neither write completion nor the `fresh` attribute proves radio-level causality.

A complete observation is committed synchronously after successful transport cleanup and final cancellation/closed checks. Observer notification is separate deferred delivery. Callback or scheduling failure cannot turn a committed read into a false BLE failure; pending delivery is cancelled on unload. Observers see current entry state when delivered, not a captured event for every read. The design does not promise retroactive rollback after an observer has already seen a value; older probes assuming inline delivery/rollback were retained with that disposition rather than reported as passing.

The entity implementer's original RED output files were not retained. Its missing-module result proves absence only. A later independent control used archived baseline source with the new real-HA setup assertion and failed at **3 entities versus 11**; that is an independent regression-strength check, not a reconstruction of the original execution. Current full-suite runs and independent reviews are tied to the candidate above. Synthetic fixture examples, private probes and hardware results remain separate evidence layers.

## Validation environment versions

Home Assistant `2026.9.4`, ESPHome `2026.9.1`, Bleak `3.0.2`, bleak-retry-connector `4.7.0`, bleak-esphome `4.0.0`, aioesphomeapi `46.2.0`, habluetooth `6.26.11`.

## Remaining acceptance — current

- Disabled alarm-5 time editing and restoration are physically confirmed, with
  native preservation comparison and clean explicit Close. Enable/SIG toggles,
  uncertain-write recovery, other alarm/time combinations, longer-term reconnects
  and real active-transfer cancellation/unload remain unvalidated. Keep failures;
  repeated retries until success are not reliability evidence.
- **ERR remains unresolved**: it appeared in the historical joint read and on
  both alarm-write terminal screens. A native success or released proxy is not
  evidence of normal watch-side session completion.
- Bounded interactive controls and all three optional automatic modes are
  implemented in the installed R3 component. Two manual full reads succeeded;
  the automatic retained attempt failed before useful reads. Automatic fresh-window
  acceptance, 30-second periodic reads and unattended reliability remain open.
  All three automatic modes were off at final postflight.
- The newer source adds an exclusive default-OFF passive `observe_windows`
  option on the existing native RAW owner. It creates no read admission or
  connection and keeps bounded volatile diagnostic summaries only. Its source
  verification is separate from installation and actual four-period capture;
  no such physical capture is claimed here. See [passive windows](passive-windows.es.md).
- The dated history snapshot was not independently compared for all seven days.
  Other read-only settings from the historical joint trial were not individually
  visually compared; its unavailable auto-time observation remains unverifiable.
  Later source handling of valid unknown values does not retroactively accept it.
- Current primary/secondary running clocks lack a validated GET/decoder contract.
  Zone/DST configuration is not a clock sample. Other settings writers, time
  synchronization and watch-button events remain withheld/unimplemented; only
  the alarm/SIG draft writer exists. No new physical work is armed by this update.

The reported success of Android CasioGShockSmartSync phone actions is an
independent session reference, not proof of this integration's unimplemented
features or an ERR diagnosis. Raw logs, device addresses, credentials, registry
backups and third-party captures remain private and excluded from this repository.
