# [WIP] Casio ABL-100WE for Home Assistant

**Experimental. Two manual step reads through an ESPHome Bluetooth proxy matched the watch display. One complete read of the five alarms and SIG on the installed alarm revision was also confirmed by the operator, although the watch displayed `ERR`; its cause is unresolved. This local branch adds a daily-history snapshot and an optional native step-window reader; neither has been installed or physically validated. Final integrated tests and reviews remain pending. Long-term reliability is not established.**

A local step and alarm reader for the Casio ABL-100WE, using Home Assistant's Bluetooth stack and connectable ESPHome proxies. Manual buttons remain available; optional automatic step reads are disabled by default. No Casio account or cloud service is required.

## Current status

- UI configuration and English/Spanish entity names are implemented.
- The step-only revision `008f026` completed two real manual reads on 2026-10-15: **229 steps, then 276**, both matching the watch display. Both disconnected and left the proxy slot free.
- Alarm revision `fefd06b10375bea34e84ec060f34acc2c9cb86c5` is installed in the pilot: one device and **11 entities**, with no automatic BLE read. A fresh complete snapshot of all five alarm times/enable states and SIG was confirmed by the operator. The watch displayed `ERR` despite the valid HA result; session completion and repeatability remain unresolved.
- The local daily-history increment extends the existing step parser and the **Steps today** entity with a bounded `daily_history` attribute. It sends no additional commands and is **not installed or physically validated**.
- The optional native step-window reader is **local, uninstalled and hardware-unvalidated**. It preserves the existing 11 entity identities and manual step/alarm buttons. Standalone observer checks are not acceptance of the final lifecycle wiring or combined history/reader bundle; final integrated tests and reviews remain pending. Historical synthetic test results in [validation details](docs/validation.md) must not be attributed to this combined candidate.
- Earlier physical step attempts failed during connection, subscription or parsing. Later successes do not establish everyday reliability or prove subscription order was the sole cause. Enabled alarms and disabled SIG were compared with the watch in the accepted alarm snapshot; an activated SIG remains untested.
- Editing alarms, synchronizing time, ordinary setting controls and watch-button events are not implemented. There is no periodic watch GATT polling, presence tracking or light automation. The optional reader observes Bluetooth metadata to request steps during a fresh window.

See [validation details](docs/validation.md) for exact revision boundaries and remaining acceptance. This is not an official Casio or Home Assistant integration, is not in the HACS default catalog and has no stable release.

## Read-only entities

Names below omit the device prefix, which Home Assistant adds once. Entity IDs depend on the selected UI language; unique-ID suffixes are stable.

| Observation or action | English name | Spanish name | Meaning |
| --- | --- | --- | --- |
| Daily steps | Steps today | Pasos de hoy | Last valid current-date total |
| Step timestamp | Last synchronization | Última sincronización | Last successful step read, not a heartbeat |
| Manual step query | Read steps | Leer pasos | One explicitly requested step session |
| Five alarm slots | Alarm 1–5 | Alarma 1–5 | Last observed `HH:MM`, with `enabled` and `fresh` attributes |
| Hourly signal | Hourly time signal | Señal horaria | Last observed SIG setting, not an alarm-ringing event |
| Alarm timestamp | Last alarm read | Última lectura de alarmas | Last successful complete alarm snapshot |
| Manual alarm query | Read alarms | Leer alarmas | One explicitly requested read of all five slots and SIG |

Alarm and step observations have independent timestamps and freshness. Before a successful read the corresponding observations are unavailable, not zero or off. A failed refresh retains the last good values and marks that feature's `fresh` attribute false. Disabled alarms still show their observed time. Step data for another date is unavailable; alarm times do not expire at midnight. Neither observation survives an integration reload in this pilot.

## Daily-history snapshot (local, not installed)

The source-described 400-byte ABL-100WE block contains **seven previous-day summary slots, plus today's separate total**. This is reverse-engineered protocol evidence, not a retention guarantee stated in the official manual. The seven slots are eight bytes each at offsets 318–373; their uint32 step total is followed by a distance word that this increment deliberately ignores. Slot dates are anchored to the validated watch record date: yesterday through seven days ago, newest first.

On a successful current-date step read, `Steps today` exposes `daily_history`, mapping those seven ISO dates to totals or `null`. A legitimate zero remains zero. Missing sentinel `0xFFFFFFFE` or a count above the existing conservative 99,999,999-step parser bound becomes unknown for that slot without shifting later dates or invalidating a valid today total. The history is an immutable in-memory snapshot; each attribute call returns a new mapping. A failed read retains the last good snapshot and timestamp with `fresh: false`. Date rollover does not relabel its dates, but the **Steps today** entity itself still becomes unavailable until a valid current-date read.

This does **not** imply seven days of full hourly detail, persistent storage, automatic synchronization, or backfilling past dates into Home Assistant Recorder/statistics. The history decoder itself adds no entities, transport writes, setting changes or background tasks; the optional reader below has its own observer and metadata timer. A reload clears the in-memory observation. Synthetic records demonstrate decoding and HA behavior only; the physical watch's historical totals have not been compared.

## Experimental installation

Use only an installation you can recover and back up the existing integration/configuration first. This combined history/optional-reader candidate needs final integrated tests, independent review and separately coordinated physical acceptance; local component checks do not approve replacing an existing installation.

1. Copy `custom_components/casio_abl100` into Home Assistant's `config/custom_components/` directory.
2. Restart Home Assistant Core.
3. Add **Casio ABL-100WE** under **Settings → Devices & services → Add integration**.
4. Select a discovered watch or enter its exact Bluetooth address. No watch is preconfigured.
5. Use a **connectable** ESPHome Bluetooth proxy. Advertisement forwarding alone is insufficient.

For a coordinated **manual** read, put the watch near the proxy and hold **C/MODE, lower left, for at least three seconds**, until `CNCT` and the Bluetooth icon flash. Temporarily disabling the phone's Bluetooth can avoid connection contention; do not unpair or reset the watch. Press the selected read button **once** during that connection window. Step and alarm reads are separate transactions, serialized per watch; do not launch them concurrently from multiple actors. Preserve any failure instead of retrying until success and calling that reliable.

## Optional native step-window reader (local, not installed)

Under **Settings → Devices & services → Casio ABL-100WE → Configure**, the standard integration options expose the English/Spanish boolean `auto_read_steps`, **off by default**. Enabling or disabling it reloads only the selected entry. Observations are in memory: a reload clears step/history and alarm observations, which remain unavailable until their respective fresh reads. No new entity or identity is added.

With the option off, open `CNCT` as described above and press **Read steps** once. With it enabled, open a **fresh** `CNCT` window near the connectable proxy; the observer can request the existing step transaction without the HA button press. Avoid competing phone connections and simultaneous manual requests. Alarm reads still require **Read alarms**. This is a usage procedure for an unvalidated candidate, not a report of successful automatic physical operation.

The entry-owned observer uses Home Assistant's public Bluetooth API, a **PASSIVE**, exact-address, connectable matcher and a **one-second timer reading public cached Bluetooth metadata**. HA can deduplicate callbacks for identical advertising payloads while packet timestamps continue advancing, so callbacks alone are insufficient. The timer does not connect to or poll the watch over GATT, and there is no second ESPHome scanner/subscription.

- Cached target bursts present at setup/reload are consumed without a read. If there is no cached target, the first fresh post-setup observation can qualify.
- At most one request is made per observed timestamp burst. Advances separated by **less than 30 seconds** belong to the same burst; a gap of **at least 30 seconds** can start another. An opportunity must be **no more than five seconds old**, including a final age check when the scheduled task starts.
- This is a timestamp heuristic, **not a physical `CNCT` identifier**. Phone, automatic time-connection or FIND windows may also qualify. Scanner outages and missed observations make window boundaries ambiguous. There is no guarantee of exactly one request per physical gesture or evidence that automatic time windows actually transfer steps.
- Busy, stale or failed opportunities are consumed without a queued automatic retry. The existing connector's bounded internal connection attempts (maximum two) remain; they are not another automatic window request.
- Disable/unload unregisters the callback and timer and cancels/joins the owned read cooperatively. An uncooperative backend cannot be forced to release the connection by this guarantee.

Only steps are requested, including the existing seven dated `daily_history` snapshot slots when a valid record is received. This does not backfill Recorder/statistics. The reader does not query alarms, time, settings or AppInfo; change pairing or firmware; or send END. The existing read-only DRSP START operation is unchanged. Unattended reliability and resolution of the watch-side `ERR` are **not established**.

## Protocol and safety

### Steps

The step transaction subscribes to ALL_FEATURES `26eb002d-b012-49a8-b1f8-394fb2032b0f`, discarding its payload, then DRSP `26eb0023-b012-49a8-b1f8-394fb2032b0f` and Convoy `26eb0024-b012-49a8-b1f8-394fb2032b0f`. Each subscription is awaited. Its only application-level write is `00 11 00 00 00` to DRSP. It accepts bounded five- or seven-byte announcements with command `00`, category `11` and a 24-bit little-endian length of 400 in bytes 2–4, concatenates exactly 400 Convoy bytes, validates date and step fields, then disconnects. The optional two header bytes are not length; their meaning is not established.

### Alarms

The alarm-only transaction validates the discovered service, primary notify/CCCD metadata and read-request characteristic `26eb002c-b012-49a8-b1f8-394fb2032b0f`. After enabling primary notifications, its only application-level writes are the **single-byte queries `15`, then `16`**, to that read-request characteristic with `response=False`. It waits for a valid `15` response before the second query, parses exact five- and seventeen-byte responses into five slots, and disconnects before publishing. It does not subscribe DRSP/Convoy or request steps during an alarm read.

Each alarm entry is `[flags, auxiliary, hour, minute]`, with numeric hours/minutes. Enable mask `0x40` and SIG in AL1 mask `0x80` are source-derived interpretations; one pilot snapshot confirmed enabled/disabled alarm states and disabled SIG. An enabled SIG and repeatability remain unverified. Unknown flags/auxiliary bytes stay in memory, not diagnostics. Malformed, missing, duplicate or locally unrequested alarm responses fail rather than becoming disabled/default alarms.

Notifications contain no transaction ID. Local phase guards reject responses delivered before the public query invocation, but cannot universally distinguish a stale same-opcode notification delivered afterward from a genuine immediate reply. Neither write completion nor `fresh: true` proves radio-level causality; freshness here means the last complete locally validated read succeeded.

### Both transactions

There are no watch-setting writes, time initialization, scratchpad initialization or pairing changes. Automatic step requests occur only when the optional reader is enabled; alarm requests remain manual. GATT notification subscription itself writes a CCCD; it is distinct from an application setting command. The reader deliberately does **not** send the lifelog end request `04 11 00 00 00`, which can clear hourly details.

Connection/read/disconnect timeouts rely on cooperative backend cancellation. They cannot force a backend that indefinitely suppresses cancellation to terminate. For alarm reads, any positive public task-cancellation count is conservatively treated as caller cancellation, including cancellation history not cleared by the caller. A completed observation is committed before deferred HA observer delivery; delivery failure does not retroactively change that read, and pending delivery is suppressed on unload. A failure is not grounds to bypass acknowledgement, clear watch data or run a time-setting initialization helper.

## Development and tests

The tested environment uses Python 3.14, Home Assistant `2026.9.4`, `pytest-homeassistant-custom-component==0.13.367`, pytest `9.0.3`, Bleak `3.0.2` and bleak-retry-connector `4.7.0`. Use a separate virtual environment and Bluetooth dependencies matching that HA version. The integration relies on HA's Bluetooth dependency rather than imposing conflicting runtime pins.

From the repository root, with `PYTHON` pointing to that environment's interpreter:

```sh
# Offline protocol and HA-boundary tests; synthetic BLE only.
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH="$PWD" "$PYTHON" -m pytest \
  -p pytest_asyncio.plugin -q tests/protocol tests/ha \
  --ignore=tests/ha/test_runtime.py \
  --ignore=tests/ha/test_auto_runtime.py \
  --ignore=tests/ha/test_auto_lifecycle_runtime.py

# Actual HA registries, service calls, translations, reload and scheduler.
# BLE lookup, connector and client remain synthetic: no hardware test.
PYTHONPATH="$PWD" "$PYTHON" -m pytest -o asyncio_mode=auto \
  -q tests/ha/test_runtime.py tests/ha/test_auto_runtime.py \
  tests/ha/test_auto_lifecycle_runtime.py
```

Public fixtures are constructed synthetic data, not captured watch records. Their example step counts, alarm times and flags are not hardware results. Raw logs, credentials and deployment/device identifiers are excluded; BLE addresses in tests are synthetic placeholders.

## References and license

Protocol facts were cross-checked against [izivkov/gshock_api](https://github.com/izivkov/gshock_api/tree/ae817310dd9551d5e6e53ee61d89af4e8d954767), [GShockAPI 1.7.6](https://github.com/izivkov/GShockAPI/tree/5b1043a888ea3d97331c0c701f2da776ff638d8f), [CasioGShockSmartSync](https://github.com/izivkov/CasioGShockSmartSync) and [casiosync](https://github.com/taviso/casiosync). The [official module 3565 manual](https://support.casio.com/global/en/wat/manual/3565_en/UQIDSYhietkkjq.html) documents five daily alarms and a separate hourly signal. No casiosync code or third-party raw capture is redistributed here.

Project code is available under the [MIT License](LICENSE). Casio, G-Shock and Home Assistant names belong to their respective owners; this project is unaffiliated with them.
