# Casio ABL-100WE — experimental read-only Home Assistant pilot

A small, **manual** daily-step reader for Home Assistant 2026.9.4 with a connectable Bluetooth proxy. Not deployed, HACS-certified, or proven on a physical watch yet. No automatic scan-triggered connection, polling, time/alarm modification, pairing, phone-finder action, light action or write besides the DRSP start command.

## Install / use (after independent review)

Copy `custom_components/casio_abl100` into HA's `/config/custom_components/`, restart Core, then Add Integration → Casio ABL-100WE. Select a discovered matching name (including the observed `CASIO ABL-100WE\0`) or enter the watch's exact MAC address. No watch is preconfigured. Put the watch into BLE connection mode near a **connectable** ESPHome Bluetooth proxy and press **Read steps**. The proxy may observe passive advertisements without being able to connect; no local BlueZ fallback is attempted. If the phone has the BLE connection, try again later. No automatic read happens during setup.

The steps and last-sync sensors have no data before a successful read. A failed read retains the last good value for the same calendar day, marked `fresh: false`; after midnight old steps become unavailable rather than being mislabeled today's total. Date mismatch in a new record is rejected; the record date is exposed as an attribute. Last synchronization is the timestamp of the last valid read, not a periodic heartbeat. A coherent zero-step full record is valid. Data does not persist across integration reloads in this pilot.

## Protocol / safety

The sole watch write is `00 11 00 00 00` to DRSP `26eb0023-b012-49a8-b1f8-394fb2032b0f`. Subscribes to DRSP and Convoy `26eb0024-b012-49a8-b1f8-394fb2032b0f` first, requires the exact 400-byte length announcement and 400 concatenated bytes, validates the date, uint32LE steps at offset 374 and BCD total at 396, then disconnects without an end-transaction write (`04 11 00 00 00` clears hourly details in some paths). BLE preserves GATT notification ordering; there are no sequence numbers or claimed reorder recovery. The sixth timestamp header byte is not assumed to be seconds. No imported mutable global protocol facade is used.

Wire constants derived from MIT-licensed [izivkov/gshock_api](https://github.com/izivkov/gshock_api) at `ae817310dd9551d5e6e53ee61d89af4e8d954767`; no upstream code vendored. The test capture `tests/protocol/abl_real_capture.txt` is from taviso/casiosync `86b76d93ac4c708e5169a829d30d7c7d8ce77594`, `logs/260801071501.txt`, SHA256 `e189b91a293504f8ef3d6f6d467de8d04efe28818c48c3c34d09f3cd8bf6c6b0`; source annotation says watch display **1485 steps**, and record contains **572 m**. The casiosync code is not vendored (no root license observed). This fixture is historic upstream data, **not a live user-device result**.

## Offline development

Use an isolated Python 3.14 venv with `homeassistant==2026.9.4`, `pytest`, `pytest-asyncio`, HA Bluetooth's `bleak==3.0.2` and `bleak-retry-connector==4.7.0`. The Bluetooth dependency already ships with HA's `bluetooth` component, so this integration declares no conflicting runtime pin. The offline suite imports actual HA classes and mocks only BLE boundary calls; a separate test loads a real HA config entry and checks registries/unload. Neither validates a physical proxy connection. Example commands:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH="$PWD" /path/to/isolated-venv/bin/python -m pytest -p pytest_asyncio.plugin -q tests/protocol
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH="$PWD" /path/to/isolated-venv/bin/python -m pytest -p pytest_asyncio.plugin -q tests/ha --ignore=tests/ha/test_runtime.py
# Runtime test loads a real HA config entry and verifies fresh entity/device
# registries and unload, with the BLE connection boundary mocked. It does not
# execute a BLE proxy/GATT read or translate the frontend in a browser.
PYTHONPATH="$PWD" /path/to/isolated-venv/bin/python -m pytest -o asyncio_mode=auto -q tests/ha/test_runtime.py
```

A physical opt-in read, Spanish/English UI rendering on the target installation, and unload under an actually connected proxy remain acceptance gates before calling this working on hardware. A failure is not grounds to retry with a destructive end command or time-setting routine.
