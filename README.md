# [WIP] Casio ABL-100WE for Home Assistant

**Experimental. Not ready for everyday use. The first physical step read is currently blocked by a Bluetooth notification-subscription timeout.**

A local, manual step reader for the Casio ABL-100WE, using Home Assistant's Bluetooth stack and connectable ESPHome proxies. No Casio account or cloud service is required.

## Current status

- UI configuration, Spanish/English entity translations, a daily-step sensor, last-sync sensor and manual **Read steps** button are implemented.
- An experimental private installation loads in Home Assistant 2026.9.4. Spanish device UI and idle integration reload have been checked.
- Offline parsing/transport tests and real-HA lifecycle tests pass with **synthetic BLE data**. They do not establish hardware compatibility.
- The first physical trial connected over GATT, but timed out enabling DRSP notifications before requesting the step record. **No successful physical step read yet.**
- Time synchronization, alarm configuration and watch-button events are planned, not implemented. There is no automatic polling, presence tracking or light automation.

See [validation details](docs/validation.md) for measured results and remaining acceptance checks. This repository is not an official Casio or Home Assistant integration, is not in the HACS default catalog, and has no stable release.

## Experimental installation

Only use a test installation or an installation you can recover. Back up your configuration first.

1. Copy `custom_components/casio_abl100` into Home Assistant's `config/custom_components/` directory.
2. Restart Home Assistant Core.
3. Add **Casio ABL-100WE** under **Settings → Devices & services → Add integration**.
4. Select a discovered watch or enter its exact Bluetooth address. No watch is preconfigured.
5. Use a **connectable** ESPHome Bluetooth proxy. A scanner that only forwards advertisements is insufficient.

For a coordinated connection test, put the watch near the proxy and hold **C/MODE, lower left, for at least three seconds**, until `CNCT` and the Bluetooth icon flash. Temporarily disabling the phone's Bluetooth can avoid connection contention. Do not unpair or reset the watch. Press **Read steps** while the connection window is open; this is presently expected to encounter the blocker described above on the tested setup.

No read happens automatically during setup. Sensors have no value before a successful read. A failed read retains the last good same-day value with `fresh: false`; prior-day steps become unavailable after midnight. Last synchronization means the time of a valid read, not a heartbeat. Data does not persist across integration reloads in this pilot.

## Protocol and safety

The only application-level watch write is `00 11 00 00 00` to DRSP `26eb0023-b012-49a8-b1f8-394fb2032b0f`, after subscribing to DRSP and Convoy `26eb0024-b012-49a8-b1f8-394fb2032b0f`. The reader requires an announced 400-byte record, concatenates raw fragments, validates the date and daily-step fields, then disconnects.

It deliberately does **not** send the end request `04 11 00 00 00`, which can clear hourly details. It does not change time, alarms, pairing or other settings. GATT subscription itself writes the notification configuration descriptor; it is distinct from a watch-setting command. Notification order and cooperative backend cancellation have limits; these tests are not a guarantee against every firmware/backend behavior.

A failure is not grounds to clear watch buffers, bypass acknowledgements or run a time-setting initialization sequence.

## Development and tests

The tested environment uses Python 3.14, Home Assistant `2026.9.4`, `pytest-homeassistant-custom-component==0.13.367`, pytest `9.0.3`, Bleak `3.0.2` and bleak-retry-connector `4.7.0`. Use a separate virtual environment and the Bluetooth dependencies matching that Home Assistant version. The integration itself relies on HA's Bluetooth dependency rather than imposing conflicting package pins.

From the repository root, with `PYTHON` pointing to that environment's interpreter:

```sh
# Offline protocol and HA-boundary tests. No real Bluetooth connection.
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH="$PWD" "$PYTHON" -m pytest \
  -p pytest_asyncio.plugin -q tests/protocol tests/ha \
  --ignore=tests/ha/test_runtime.py

# Actual HA setup, registries, reload failure and scheduler dispatch.
# BLE clients are still synthetic; this is not a hardware test.
PYTHONPATH="$PWD" "$PYTHON" -m pytest -o asyncio_mode=auto \
  -q tests/ha/test_runtime.py
```

The public fixture is constructed synthetic test data. It is not a captured watch record, and its example step count must not be cited as a hardware result. Private raw logs, credentials and device identifiers are not included.

## References and license

Protocol facts were cross-checked against [izivkov/gshock_api](https://github.com/izivkov/gshock_api) at `ae817310dd9551d5e6e53ee61d89af4e8d954767`, [GShockAPI](https://github.com/izivkov/GShockAPI), [CasioGShockSmartSync](https://github.com/izivkov/CasioGShockSmartSync) and [casiosync](https://github.com/taviso/casiosync). No casiosync code or third-party raw capture is redistributed here.

Project code is available under the [MIT License](LICENSE). Casio, G-Shock and Home Assistant names belong to their respective owners; this project is unaffiliated with them.
