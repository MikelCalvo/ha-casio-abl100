# [WIP] Casio ABL-100WE for Home Assistant

**Experimental. Two manual step reads through an ESPHome Bluetooth proxy matched the watch display. The alarm reader is implemented and tested locally with synthetic BLE, but has not been installed or physically verified. Long-term reliability is not established.**

A local, manual step and alarm reader for the Casio ABL-100WE, using Home Assistant's Bluetooth stack and connectable ESPHome proxies. No Casio account or cloud service is required.

## Current status

- UI configuration and English/Spanish entity names are implemented.
- The installed step-only revision `008f026` completed two real manual reads on 2026-10-15: **229 steps, then 276**, both matching the watch display. Both disconnected and left the proxy slot free. This installation has one device and three entities.
- The local alarm-reader candidate `3ae255ae8ea27505ee2adcae4c17622ef86cdeda` adds five alarm observations, the hourly time signal setting, an independent last-alarm-read timestamp and a manual **Read alarms** button. Real HA test fixtures create one device and **11 entities**, with no automatic BLE read. **This candidate has not been installed in the pilot.**
- **351 offline tests and 11 real-HA runtime tests pass with synthetic BLE clients.** The runtime tests exercise `button.press` through the session and protocol, EN/ES names, failures, reload and date rollover. They are not watch-hardware tests.
- Earlier physical step attempts failed during connection, subscription or parsing. Two later successes do not establish everyday reliability or prove subscription order was the sole cause. No physical alarm read has been performed, and enabled/SIG bit interpretations still need comparison with the watch.
- Editing alarms, synchronizing time, ordinary setting controls and watch-button events are not implemented. There is no automatic polling, presence tracking or light automation.

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

## Experimental installation

Use only an installation you can recover and back up the existing integration/configuration first. The alarm candidate needs separately coordinated physical acceptance; its local tests do not approve installing it into an existing system.

1. Copy `custom_components/casio_abl100` into Home Assistant's `config/custom_components/` directory.
2. Restart Home Assistant Core.
3. Add **Casio ABL-100WE** under **Settings → Devices & services → Add integration**.
4. Select a discovered watch or enter its exact Bluetooth address. No watch is preconfigured.
5. Use a **connectable** ESPHome Bluetooth proxy. Advertisement forwarding alone is insufficient.

For a coordinated read, put the watch near the proxy and hold **C/MODE, lower left, for at least three seconds**, until `CNCT` and the Bluetooth icon flash. Temporarily disabling the phone's Bluetooth can avoid connection contention; do not unpair or reset the watch. Press the selected read button **once** during that connection window. Step and alarm reads are separate transactions, serialized per watch; do not launch them concurrently from multiple actors. Preserve any failure instead of retrying until success and calling that reliable.

## Protocol and safety

### Steps

The step transaction subscribes to ALL_FEATURES `26eb002d-b012-49a8-b1f8-394fb2032b0f`, discarding its payload, then DRSP `26eb0023-b012-49a8-b1f8-394fb2032b0f` and Convoy `26eb0024-b012-49a8-b1f8-394fb2032b0f`. Each subscription is awaited. Its only application-level write is `00 11 00 00 00` to DRSP. It accepts bounded five- or seven-byte announcements with command `00`, category `11` and a 24-bit little-endian length of 400 in bytes 2–4, concatenates exactly 400 Convoy bytes, validates date and step fields, then disconnects. The optional two header bytes are not length; their meaning is not established.

### Alarms

The alarm-only transaction validates the discovered service, primary notify/CCCD metadata and read-request characteristic `26eb002c-b012-49a8-b1f8-394fb2032b0f`. After enabling primary notifications, its only application-level writes are the **single-byte queries `15`, then `16`**, to that read-request characteristic with `response=False`. It waits for a valid `15` response before the second query, parses exact five- and seventeen-byte responses into five slots, and disconnects before publishing. It does not subscribe DRSP/Convoy or request steps during an alarm read.

Each alarm entry is `[flags, auxiliary, hour, minute]`, with numeric hours/minutes. Enable mask `0x40` and SIG in AL1 mask `0x80` are source-derived interpretations, **not yet physically confirmed activated states for the pilot watch**. Unknown flags/auxiliary bytes stay in memory, not diagnostics. Malformed, missing, duplicate or locally unrequested alarm responses fail rather than becoming disabled/default alarms.

Notifications contain no transaction ID. Local phase guards reject responses delivered before the public query invocation, but cannot universally distinguish a stale same-opcode notification delivered afterward from a genuine immediate reply. Neither write completion nor `fresh: true` proves radio-level causality; freshness here means the last complete locally validated read succeeded.

### Both transactions

There are no watch-setting writes, time initialization, scratchpad initialization, pairing changes or automatic reads. GATT notification subscription itself writes a CCCD; it is distinct from an application setting command. The reader deliberately does **not** send the lifelog end request `04 11 00 00 00`, which can clear hourly details.

Connection/read/disconnect timeouts rely on cooperative backend cancellation. They cannot force a backend that indefinitely suppresses cancellation to terminate. For alarm reads, any positive public task-cancellation count is conservatively treated as caller cancellation, including cancellation history not cleared by the caller. A completed observation is committed before deferred HA observer delivery; delivery failure does not retroactively change that read, and pending delivery is suppressed on unload. A failure is not grounds to bypass acknowledgement, clear watch data or run a time-setting initialization helper.

## Development and tests

The tested environment uses Python 3.14, Home Assistant `2026.9.4`, `pytest-homeassistant-custom-component==0.13.367`, pytest `9.0.3`, Bleak `3.0.2` and bleak-retry-connector `4.7.0`. Use a separate virtual environment and Bluetooth dependencies matching that HA version. The integration relies on HA's Bluetooth dependency rather than imposing conflicting runtime pins.

From the repository root, with `PYTHON` pointing to that environment's interpreter:

```sh
# Offline protocol and HA-boundary tests; synthetic BLE only.
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH="$PWD" "$PYTHON" -m pytest \
  -p pytest_asyncio.plugin -q tests/protocol tests/ha \
  --ignore=tests/ha/test_runtime.py

# Actual HA registries, service calls, translations, reload and scheduler.
# BLE lookup, connector and client remain synthetic: no hardware test.
PYTHONPATH="$PWD" "$PYTHON" -m pytest -o asyncio_mode=auto \
  -q tests/ha/test_runtime.py
```

Public fixtures are constructed synthetic data, not captured watch records. Their example step counts, alarm times and flags are not hardware results. Raw logs, credentials and deployment/device identifiers are excluded; BLE addresses in tests are synthetic placeholders.

## References and license

Protocol facts were cross-checked against [izivkov/gshock_api](https://github.com/izivkov/gshock_api/tree/ae817310dd9551d5e6e53ee61d89af4e8d954767), [GShockAPI 1.7.6](https://github.com/izivkov/GShockAPI/tree/5b1043a888ea3d97331c0c701f2da776ff638d8f), [CasioGShockSmartSync](https://github.com/izivkov/CasioGShockSmartSync) and [casiosync](https://github.com/taviso/casiosync). The [official module 3565 manual](https://support.casio.com/global/en/wat/manual/3565_en/UQIDSYhietkkjq.html) documents five daily alarms and a separate hourly signal. No casiosync code or third-party raw capture is redistributed here.

Project code is available under the [MIT License](LICENSE). Casio, G-Shock and Home Assistant names belong to their respective owners; this project is unaffiliated with them.
