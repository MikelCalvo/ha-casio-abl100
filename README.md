# [WIP] Casio ABL-100WE for Home Assistant

**Experimental pilot, updated 2026-10-20.** Reviewed component revision `badfe909c74f63a2b2d81c69b3b872df596e1b1c` is installed: **34 component files, 36 entity identities**, preserving the original 23 identities on the same watch device. The installed boundary remains that component; newer source is not a newer deployment. This source increment adds default-off automatic retained connection, tested with synthetic external IO but **uninstalled and physically unverified**.

A coordinated native write changed **disabled alarm 5 from `00:00` to `00:01`, then restored `00:00`**. Native readback verified both results; the operator confirmed **`12:01`, then `12:00`** on the watch's 12-hour display. Alarm 5 stayed disabled, and native comparison preserved the other alarms and SIG. This is a confirmed time roundtrip, **not physical validation of enable/SIG toggling or general write reliability**.

The watch showed **ERR on both terminal screens**. Its cause remains unresolved even though the intended values were read back and visually confirmed. The operator's approximately 20-second duration is an estimate, not a measured timing result. Both automatic-read modes remain **off**.

No Casio account or cloud service is required. This is not an official Casio or Home Assistant integration, is not in the HACS default catalog and has no stable release.

## Current status

- **Read watch data / Leer datos del reloj** requests all implemented source-backed read groups over one HA-owned native ESPHome connection, alarm-first. Individual read buttons remain available outside an interactive reservation.
- Manual **Connect watch → Refresh connected watch → Close watch connection** controls retain their bounded link. New opt-in `auto_connect_retained` connects on a qualifying live native RAW opportunity, reads once, then retains that original link without HA idle/absolute-session expiry (not a guarantee of physical permanence). A separate **Interactive connection** entity reports confirmed, lost or uncertain native link state, not the watch's screen. See [interactive session controls](docs/native-interactive-session.md).
- Thirteen additive alarm/SIG controls stage local drafts: five times, five enable flags, SIG, Apply and Discard. Only explicit **Apply alarm draft** can dispatch settings writes, after a fresh complete baseline and before new readback. An acknowledged local safety journal retains uncertain-outcome reconciliation across reload/restart without restoring drafts. See [alarm draft controls and limits](docs/native-alarm-editing.md) and the [practical Spanish guide](docs/alarm-editing.es.md).
- The physical alarm-5 change and restoration completed with clean explicit Close. Fresh post-use checks authenticated installed source, retained identities, released ownership/proxy and continuing node health. These are point-in-time observations, not everyday reliability or an ERR fix.
- Enable/SIG writes and uncertain-write recovery have synthetic coverage only; neither was physically exercised. Other settings writers remain withheld. Current clock reads, time synchronization and watch-button events remain unimplemented.
- Optional step-only and combined automatic readers exist in the installed source, but both are off in the pilot. Manual success does not validate unattended dispatch. Source tests with synthetic external IO, independent source review, installation, native readback and visual comparison are separate verification layers; this source increment does not approve new device actions.

See [current and historical validation](docs/validation.md), [unified transaction details](docs/native-unified-sync.md), [zone semantics](docs/native-zones.md) and [read-only settings contracts](docs/settings-read-contract.md). A successful data operation, native cleanup and the watch's terminal screen are separate observations.

### Historical joint read — 2026-10-18

The earlier installed revision `e3690ff299d907b2867d19c65287fc9e19cce392` completed one combined read with five externally verifiable groups; automatic-time adjustment was unavailable and **unverifiable**, not disabled or fully accepted. The watch ended in **ERR** despite the data. The following values and inventory describe that trial, not current live state:

- HA published **1,807 steps**. The operator affirmed the read and subsequently reported **1,854 steps** on the watch; that later number was not another HA synchronization.
- Five alarms at `00:00`, only the first enabled, and SIG off were received. Basic settings reported 12-hour format, button tone on and short light mode. Encoded timer components were `00:10:00`. The added settings were not individually visually compared.
- Primary zone configuration reported standard UTC+1, configured DST +60 minutes and DST on/automatic; secondary configuration reported UTC+9 with no DST increment. These are configuration fields, **not current running-clock readings or inferred city names**.
- The step snapshot included yesterday's zero and six unknown (`null`) historical totals. Unknown is not zero; receipt did not validate every historical value against the watch.
- That installation preserved **11 identities plus 8 additions**, for **19**. Source equality, the necessary Core restart, selected entries, a free proxy slot and continuing temperature/humidity reports were checked. No watch settings or firmware were changed **in that read trial**. Its source tests/reviews do not count as hardware samples; the detailed historical boundary is retained in [validation](docs/validation.md).

## Read-only entities

Names omit the device prefix, which Home Assistant adds once. Entity IDs depend on UI language; unique-ID suffixes remain stable.

| Observation or action | English name | Spanish name | Meaning |
| --- | --- | --- | --- |
| Daily steps | Steps today | Pasos de hoy | Last valid current-date total, with seven dated history slots |
| Step timestamp | Last synchronization | Última sincronización | Last successful step read, not a heartbeat |
| Manual step query | Read steps | Leer pasos | One explicitly requested step session |
| Five alarm slots | Alarm 1–5 | Alarma 1–5 | Observed `HH:MM`, with independent `enabled` and `fresh` attributes |
| Hourly signal | Hourly time signal | Señal horaria | SIG setting, not an alarm-ringing event |
| Alarm timestamp | Last alarm read | Última lectura de alarmas | Last complete alarm read |
| Manual alarm query | Read alarms | Leer alarmas | All five slots and SIG |
| Primary standard offset | Home standard UTC offset | Desfase UTC estándar principal | Standard offset in minutes, with separate DST configuration |
| Secondary standard offset | Secondary standard UTC offset | Desfase UTC estándar secundario | Standard offset in minutes, with separate DST configuration |
| Zone timestamp | Last time zone read | Última lectura de zonas horarias | Last complete zone-configuration read |
| Manual zone query | Read time zones | Leer zonas horarias | Configuration, not current clock values |
| Combined query | Read watch data | Leer datos del reloj | All supported groups in one owned native session |
| Basic settings | Time format | Formato horario | `12h`/`24h`, tone and short/long light attributes |
| Auto-time configuration | Automatic time adjustment | Ajuste automático de hora | Enabled/disabled when known; `unknown` with observation metadata when valid but unknown, unavailable when never read |
| Encoded timer | Timer encoded duration | Duración codificada del temporizador | Encoded seconds and hour/minute/second components, not a running countdown |

Each group has independent observation time and freshness. Before a successful read it is unavailable, not zero or off. A failed refresh retains last-good values/time and marks the affected group not fresh. A later group failure in a combined read does not erase earlier completed groups; whole-session and cleanup outcomes are separate. A valid auto-time observation with unknown enable is HA `unknown`, with `fresh`, original `last_read` and observed `adjustment_minute` (possibly null); it is never replaced by disabled or a default minute.

The six semantic last-good groups are cached in a private local HA Store file per entry and transport-address binding, and restored after reload/Core restart with **original timestamps and `fresh: false`**. Only validated commits are saved, not raw bytes, names, addresses, connection/session status or automatic options. This is not encrypted storage, Recorder history or guaranteed durability across disk failure, SIGKILL or power loss before writing finishes. Changing transport address invalidates the old cache; removing the entry removes its cache. All 36 registry identities remain unchanged by cache restoration. Steps keep their original record date/history: yesterday is never presented as today's steps, and alarm times do not expire at midnight.

## Manual combined read

1. Keep the watch near a connectable ESPHome Bluetooth proxy and prevent the phone or other clients from connecting automatically. Do not unpair or reset it.
2. Press **Read watch data / Leer datos del reloj** once in HA.
3. Wait until that button reports `phase: waiting` and a future UTC `wait_expires_at`.
4. Open the watch's `CNCT` window by holding **C/MODE, lower left, for at least three seconds**. Do not also press an individual HA read button.
5. Inspect each group's values/freshness and the combined button's `result`, `group_results` and `cleanup_result`. Watch-side ERR alone does not decide whether data arrived.

The native reader waits at most **360 seconds** for a fresh exact-target RAW advertisement on the captured proxy generation, then connects using its actual advertised address type. It does not replay a cached packet or retry a failed unified operation. The shared lock serializes manual requests; use one initiating actor rather than queueing them.

The combined route borrows the loaded ESPHome entry's API connection. It creates no second API client or advertisement subscription and does not disable ESPHome. Cold-start selection accepts only one unambiguous usable native proxy; ambiguous, missing or occupied sources are refused. There is no Bleak fallback for the combined action. Individual actions retain their compatibility routes.

The fixed sequence is alarms/SIG (`15`, `16`), steps/history (DRSP `0011000000`), zones (`1D00`, `1E00`, `1E01`), basic settings (`13`), auto-time configuration (`11`), then encoded timer (`18`). One primary notification acquisition is shared across groups; DRSP/Convoy are acquired for steps. There is one outer disconnect. No setting SET, clock synchronization, AppInfo/scratchpad initialization, END, pairing or reset is introduced.

Timeouts and cleanup are cooperative, not forced termination of an unresponsive backend. The conservative envelope is in [the transaction documentation](docs/native-unified-sync.md#cooperative-timing-envelope); a controller must not impose a shorter deadline and call its timeout a watch failure. For individual native alarms, see [Spanish instructions](docs/native-alarms.es.md) and [historical findings](docs/native-alarm-validation.md).

## Daily-history snapshot

The source-described 400-byte ABL-100WE block contains **seven previous-day summary slots plus today's separate total**, not seven days of full hourly detail. This is reverse-engineered capacity, not an official retention guarantee. Dates are anchored to the validated record date, yesterday through seven days ago.

`daily_history` maps ISO dates to totals or `null`. A legitimate zero remains zero; missing sentinel `0xFFFFFFFE` or an out-of-range count becomes unknown for that slot without shifting later dates. Failed reads retain last-good data with `fresh: false`. The last valid semantic snapshot is cached across reload/restart without changing its dates; this is not automatic backfill or historical Recorder/statistics import. The joint trial received the snapshot but did not compare all seven historical totals on the device.

## Experimental installation

Back up the integration and configuration and use only a recoverable installation. Local reviews and one useful joint physical sample do not certify other installations or everyday reliability. Leave automatic reads off until separately tested.

1. Copy `custom_components/casio_abl100` into Home Assistant's `config/custom_components/` directory.
2. Restart Home Assistant Core.
3. Add **Casio ABL-100WE** under **Settings → Devices & services → Add integration**, or retain the existing entry when updating.
4. Select a discovered watch or enter its exact Bluetooth address. No watch is preconfigured.
5. Use a **connectable** ESPHome Bluetooth proxy. Advertisement forwarding alone is insufficient.

Existing identities are retained; new read groups add entities. Reloading restores validated last-good observations stale, without initiating a read. Do not delete/re-add the watch just to update code.

## Optional automatic window readers

Under **Configure**, `auto_read_steps`, `auto_read_all` and `auto_connect_retained` are strict English/Spanish booleans, **all off by default**. Select at most one. `auto_read_steps` still means **steps only**, including history on a valid record. `auto_read_all` requests the same fixed six-group transaction as the combined manual button. Saving reloads only the selected entry, retaining its 36 entity identities and device (23 pre-editing identities plus 13 draft controls). Conflicting or malformed stored options fail closed; there is no silent migration.

In steps mode, the entry-owned observer uses public HA Bluetooth metadata: a PASSIVE exact-address connectable callback and a one-second cached-metadata timer, because unchanged payloads may suppress callbacks while timestamps advance. It is not watch GATT polling or a second ESPHome scanner. Cached setup bursts are consumed without reading. Fresh opportunities are at most five seconds old; timestamp gaps of at least 30 seconds can separate bursts. Busy/stale/failed opportunities are consumed without a queued automatic retry.

In all or retained mode, one local RAW callback observes HA's already-owned native ESPHome API connection. The **first qualifying RAW packet delivered while bound and armed** supplies the exact address and live public/random address type; it does not wait for another packet or trust cached device type. That packet must itself carry a recognized ABL-100 name. Name-absent packets are not triggers even if HA remembers the name. There is no second scanner, client or advertisement subscription. One passive one-second timer only detaches/rebinds a changed generation on the same selected proxy; it never requests a read. Packets before setup or during source unavailability/rebinding are not guaranteed.

All automatic modes consume a qualifying burst before busy/suspended/scheduling checks: no lock queue or automatic retry within that burst, including after partial/failure. The 30-second quiet-gap and five-second admission limits use host-ingress monotonic time, not RF-origin time. Public metadata can suppress a setup/resume burst but cannot authorize an all-mode read.

This is a **timestamp heuristic, not a physical CNCT identifier**. Phone, FIND or automatic time-connection windows can also qualify. There is no evidence of exactly one request per physical gesture or useful data transfer during automatic time windows. Disable/unload removes observers and joins cooperative cleanup. Physical automatic acceptance and unattended reliability remain open.

Retained mode runs one initial full read through the interactive owner, then leaves it alive for explicit Refresh or Apply. It never auto-applies drafts, retries, sends keepalives or uses another client. Native loss, explicit Close, unload and HA STOP revoke authority and join original cleanup. Finite Refresh/Apply deadlines and uncertain-write safety remain. After terminal cleanup, the next automatic opportunity needs at least 30 seconds of quiet from the terminal/latest observed RAW watermark; busy or post-terminal packets can extend it. See the [Spanish retained-session guide](docs/automatic-retained-session.es.md).

With all three options off, use an explicit manual button as above. With exactly one enabled, a fresh qualifying opportunity may request that mode without the HA button. Automatic readers have local synthetic-IO validation, but physical automatic acceptance is still open. Do not attribute the historical manual combined read or alarm-5 writes to automatic dispatch, or enable any automatic mode merely because those manual operations worked.

## Native Bluetooth-address reconfiguration

If the **same physical watch** changes its Bluetooth address after a pairing reset, use the existing entry's **⋮ → Reconfigure** action. Do not remove/re-add it or edit HA storage. Turn all automatic modes off first; reconfiguration is refused if any is enabled or malformed, including a submission-time race.

Explicitly select or type the new address after confirming the physical device. The selected entry's transport address changes while its entry/device/entity identities, title and options remain. Other entries' current addresses and retained identities remain reserved. A changed transport address invalidates the old observation cache before entities load; returning to the old address without a new read does not resurrect it. Reconfiguration alone is not a successful read, new pairing or ERR fix. See [Spanish instructions](docs/reconfigure.es.md).

## Protocol and safety limits

- Current primary and secondary clock reads are **not implemented or validated**. Zone/DST configuration and step-record timestamps are not running-clock samples. See [clock-reading limits](docs/clock-reading-limits.md).
- Encoded timer arithmetic is not proof of an active countdown. Zero is not silently converted to 60 minutes. Light mode is short/long, not an asserted measured duration.
- Unknown auto-time enable is never replaced with disabled or a default minute. Its valid unknown observation is available with metadata, but a group success enum alone is not evidence of a current clock or completed time adjustment.
- Notifications have no transaction ID. Local phase guards reject early/duplicate frames but cannot prove peripheral causality of an identical late response.
- No calibrated battery percentage, step-goal read or watch-button event is claimed. Alarm/SIG editing is implemented, but its physical proof is limited to the disabled alarm-5 time roundtrip above. Enable/SIG toggling and uncertain-write recovery remain synthetic-only; all other settings writers remain withheld.
- Notification setup writes a GATT CCCD, distinct from changing a watch setting. The lifelog END `0411000000`, which may clear hourly details, is deliberately not sent.
- A later connected callback cannot revive a lost unified BLE link's authority. Cleanup stays on the original API generation, never a replacement connection.
- Raw logs, credentials, addresses, registry backups and third-party captures remain private and are not part of this repository.

## Development and tests

The tested environment uses Python 3.14, HA `2026.9.4`, `pytest-homeassistant-custom-component==0.13.367`, pytest `9.0.3` and aioesphomeapi `46.2.0`. Use an isolated environment with the target HA/Bluetooth integration requirements. Do not mix stub suites and real-HA suites in one process.

From the repository root, with `PYTHON` pointing to that environment:

```sh
# Offline protocol and HA-boundary tests; synthetic external IO.
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD" \
  "$PYTHON" -B -m pytest -p pytest_asyncio.plugin -p no:cacheprovider \
  -q tests/protocol tests/ha --ignore-glob='tests/ha/*runtime.py'

# Real HA registries, services, translations and lifecycle; synthetic native IO.
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD" \
  "$PYTHON" -B -m pytest -p no:cacheprovider -o asyncio_mode=auto \
  -q tests/ha/*runtime.py
```

Public fixtures are constructed synthetic data, not captured watch records. Fixture counts are not physical results. Local tests/reviews, installed source, physical receipt, visual comparison and reliability are separate verification layers.

## References and license

Protocol facts were cross-checked against [izivkov/gshock_api](https://github.com/izivkov/gshock_api/tree/ae817310dd9551d5e6e53ee61d89af4e8d954767), [GShockAPI 1.7.6](https://github.com/izivkov/GShockAPI/tree/5b1043a888ea3d97331c0c701f2da776ff638d8f), [CasioGShockSmartSync](https://github.com/izivkov/CasioGShockSmartSync) and [casiosync](https://github.com/taviso/casiosync). The [official module 3565 manual](https://support.casio.com/global/en/wat/manual/3565_en/UQIDSYhietkkjq.html) documents five daily alarms and a separate hourly signal. No casiosync code or third-party raw capture is redistributed here.

Project code is available under the [MIT License](LICENSE). Casio, G-Shock and Home Assistant names belong to their respective owners; this project is unaffiliated with them.
