# Native alarm pilot: physical HA publication

## Accepted result and revision

On **2026-10-18**, installed component revision
`a897b0078a73deeaafbec0c1265be4848592f343` completed one manual alarm read through
**Home Assistant's already-owned ESPHome API connection**. The integration
published the result through its existing session and entities. No REST state
injection, historical snapshot replay, competing API owner or temporary ESPHome
disable was used for this HA trial. Later documentation commits do not change
that installed code or constitute another hardware test.

| Observation | Fresh HA result |
| --- | --- |
| Alarm 1 | `00:00`, enabled |
| Alarms 2–5 | `00:00`, disabled |
| Hourly signal | Off |
| Last alarm read | `2026-10-18T01:41:26+00:00` |
| Freshness | All five alarm observations and SIG marked `fresh: true` |
| Steps / last step synchronization | Unavailable; not refreshed by this test |

The timestamp is **03:41:26 CEST** on that date. It is HA's read timestamp, not a
clock value read from the watch. Alarm times are daily `HH:MM` settings, not
current-clock observations.

## Execution and cleanup

- Exactly **one** HA `button.press` service request was submitted. The button's
  `waiting` phase and more than 300 seconds of remaining wait budget were checked
  before asking the operator to open one `CNCT` window.
- The unchanged alarm transport used notify/CCCD setup and GET `15`, then GET `16`.
  There were **zero watch-setting writes**. Transport writes for subscription and
  read requests are not settings changes.
- The HTTP request returned and the trial process exited **0**. Success is based
  on the complete fresh entity snapshot, not merely that exit status.
- The button returned to `phase: idle` and the wait window was closed.
- Postflight verified the installed 17 component files, loaded watch/ESPHome
  entries, preservation of all **11 entity identities and customizations**,
  `auto_read_steps=false`, one free proxy slot and repeated temperature/humidity
  reports. No second watch request was used for that health check.
- There is no pending gesture, open test window or scheduled automatic read from
  this trial. No further physical test is implied by publishing these findings.

## Evidence layers: do not combine their claims

1. **Earlier manual steps:** two successful reads on step revision `008f026`
   matched 229 and 276 on the watch on 2026-10-15. This alarm trial does not
   revalidate steps on the current revision.
2. **Earlier alarm pilot:** revision `fefd06b` produced one complete HA snapshot
   compared with the watch, with `ERR` reported. It is a different implementation
   boundary from the shared native route tested here.
3. **Standalone native baseline:** the first complete post-reset diagnostic read
   returned all five alarms at `00:00`, disabled, and SIG off. The operator
   confirmed those settings on the watch and reported `ERR`.
4. **Standalone known-change sample:** the operator manually enabled AL1. The
   same reader detected **only that enable-state change**, with all times, the
   other slots and SIG unchanged. The operator again confirmed `ERR`. The reader
   did not enable the alarm itself. These two standalone reads did not publish
   HA entities and used a diagnostic ownership handoff.
5. **Shared HA route:** the single physical read documented above freshly
   published the changed state using HA's existing owner. The operator confirmed
   the gesture but **did not report the terminal watch screen**. Do not carry the
   previous `ERR` reports forward as an observation of this trial.

A zero-attempt advertisement-window expiry is not another failed connection.
Earlier failed connection/callback trials remain failures at their observed
stages; later successful reads do not explain their cause.

## Tests, review and deployment

The exact component revision passed **449 offline cases** and **143 real-HA
runtime cases**. Runtime coverage uses actual HA entry management, service calls,
registries and publication, with external native IO synthetic. Offline execution
had five existing dependency deprecations. These are complete overlapping
replacement selections, not counts to add to earlier development gates.
Independent SPEC and QUALITY review approved the final artifact.

A separate private installation wrapper passed **12 recovery tests** after fixing
owned staging-file cleanup and resumable interrupted rollback. These operational
scripts, private backups and raw receipts are not included in the integration
repository. Installation changed five of 17 component files with one Core restart.
The restart caller timed out, but changed startup state and successful loading
were independently checked; the restart was not repeated.

## Open findings and next-session boundary

- **Data publication is established for this one shared-HA trial.** General
  reliability, activated SIG, longer-term reconnection and real-transfer
  cancellation/unload remain unverified.
- **`ERR` is unresolved.** A valid snapshot and confirmed cleanup do not establish
  the watch's expected session-end handshake. Do not invent an END/keepalive
  packet or call the error harmless on this evidence.
- **Automatic reads and interactive session lifetime remain separate goals.**
  The installed automatic step option stays off; daily-history decoding and
  unattended reads have no physical acceptance here. A one-shot disconnecting
  alarm reader is not yet an app-like persistent interactive session.
- **Current primary/secondary clocks are not read.** Source-backed zone/DST
  configuration is a narrower possible next increment, described in
  [clock-reading limits](clock-reading-limits.md). No clock/zone queries or
  settings changes were made by this trial.
- Resume physical work only in a newly coordinated window after checking the
  installed state, proxy health and operator availability. Do not replay the
  completed request or repeat a reset.

Raw logs, addresses, credentials, deployment configuration and registry backups
remain private. This document is the sanitized outcome, not a redistributed
capture or a new physical execution.
