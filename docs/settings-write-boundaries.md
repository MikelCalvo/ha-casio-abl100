# Preparing settings safely: implemented boundaries

## Status

`settings_write.py` is a **pure, unwired preparation module**, not a new Home Assistant control or a working physical writer. It never opens Bluetooth, submits requests, reads the host clock, changes drafts or stores raw baselines. Its tests use invented source-derived buffers, not watch captures. Existing native reads and alarm editing are unchanged.

The next physical trial should first validate the new connection policy. These helpers alone are **not** enough to enable settings editing: native ownership, fresh baseline acquisition, conflict detection, durable uncertainty handling, explicit Apply and complete readback still need their own implementation and review.

## API and exact limits

All inputs are copied immutable `bytes`, `bytearray` or `memoryview`. The full baseline and typed intent are validated before no-op detection. Booleans are never accepted as integer settings. Errors are fixed `ProtocolError` messages without raw data. Sparse intent fields use `None` for unchanged.

- `prepare_auto_time(raw, AutoTimeIntent(enabled=None, adjustment_minute=None))`: exact GET11 envelope, 15 bytes. Enable byte12 must already be `00` or `80`; minute byte13 must be 0–59. Change only the requested field and preserve every other byte. Unknown baseline fields are refused, not replaced with false, zero or a fallback minute. This changes **automatic-adjustment configuration**, not the current running clock.
- `prepare_zone(raw, index, ZoneIntent(standard_offset_minutes=None, configured_dst_increment_minutes=None, rule_code=None))`: exact indexed GET1E, seven bytes, index 0 or 1. Preserve opcode/index and opaque bytes2/3. Byte4 uses signed 15-minute quarters; byte5 uses unsigned quarters; byte6 is a numeric Casio rule code. Representable wire bounds are -1920…1905 minutes, 0…3825 minutes and 0…255 respectively. **These are encoding bounds, not valid geographical choices, a supported rule table or proof that the watch accepts them.** Slot roles after Home/World swap remain unverified. No IANA city or effective-DST inference.
- `prepare_primary_dst(raw, PrimaryDSTIntent(on=None, auto=None))`: exact GET1D envelope, 15 bytes. Primary byte3 supports ON bit01 and AUTO bit02 independently. Upper baseline bits cause refusal; all other bytes, including shared/secondary state, are copied. No secondary active-DST setter is provided.

Each returns a frozen `SettingsPlan(baseline, expected, edited_masks, frame)`. `frame` is `None` for an effective no-op, otherwise the complete source-derived candidate bytes. `edited_masks` records only explicitly requested fields; it is used for comparison, never runtime permission. No transport may treat a public plan object as fresh connection or write authority.

`compare_settings_readback(plan, raw)` requires the complete same envelope/index and returns:

- `verified`: complete raw equality to the expected configuration.
- `mismatch`: differences confined to explicitly edited bits/fields.
- `preservation_mismatch`: any difference outside those bits/fields, including unsupported values in previously opaque positions. This takes priority if both kinds differ.

Malformed input raises instead of producing a result. A structurally valid differing observation is not discarded just because a preparer would refuse it as a future baseline. Equality alone proves neither freshness, causality, ATT acknowledgement, physical effect nor persistence across reconnection.

## Deliberately blocked fields

| Requested improvement | Concrete missing evidence |
| --- | --- |
| Set running time / synchronize from HA | No established GET/readback for either running clock. The generic time setter also prepares DST/zone data. No fabricated clock sample or implicit time write. |
| 12/24 h, key tone, light duration, power saving | ABL GET13 is 12 bytes; historical SET13 has both 12-byte and 17-byte variants. The generic writer allocates a fresh buffer, so padding/truncating would guess opaque values. Short/long light labels also avoid conflicting seconds labels. |
| Timer setting | GET18 is 15 bytes, generic SET18 is seven. Opaque tail, running/reset state and encoded zero versus the manual's 60-minute setting are unresolved. |
| Automatic-adjustment and zone/DST HA controls | Pure clone transforms exist here, but fresh native RMW, durable uncertainty safety, UI ownership and physical effects are not yet established. Unknown current auto-adjustment enable state is not silently changed. |

There is no generic opcode service, keepalive, application END, reset or pairing route. A failed or uncertain future SET must never be replayed automatically.

## Source provenance

All links pin GShockAPI 1.7.6 to commit `5b1043a888ea3d97331c0c701f2da776ff638d8f`, inspected statically:

- [TimeAdjustmentIO.kt, lines78–92](https://github.com/izivkov/GShockAPI/blob/5b1043a888ea3d97331c0c701f2da776ff638d8f/api/src/main/java/org/avmedia/gshockapi/io/TimeAdjustmentIO.kt#L78-L92): clone full data and replace bytes12/13. The parser's fallback minute is deliberately not copied.
- [DstForWorldCitiesIO.kt, lines28–44](https://github.com/izivkov/GShockAPI/blob/5b1043a888ea3d97331c0c701f2da776ff638d8f/api/src/main/java/org/avmedia/gshockapi/io/DstForWorldCitiesIO.kt#L28-L44): seven-byte clone, fields4/5/6.
- [DstWatchStateIO.kt, lines17–34](https://github.com/izivkov/GShockAPI/blob/5b1043a888ea3d97331c0c701f2da776ff638d8f/api/src/main/java/org/avmedia/gshockapi/io/DstWatchStateIO.kt#L17-L34): clone and modify primary byte3. Its 14-byte comment is not used as a response-length oracle; retained ABL responses establish 15 bytes.

See [settings-read-contract.md](settings-read-contract.md) for the independent GET lengths, masks, source distinction and physical limits. No Kotlin encoder or official app was executed by this module's tests.

## Verification

`tests/protocol/test_settings_write_contract.py` exercises literal nonzero opaque-byte vectors, signed quarters, sparse edits, no-op validation, unknown baseline refusal, strict numeric/boolean types, immutable copies and whole-buffer comparison. API absence and semantic no-op-scaffold failures were retained separately before implementation. Parameterized cases are synthetic coverage, not independent physical outcomes. No installation, BLE request or new native writer is enabled by this document or module.
