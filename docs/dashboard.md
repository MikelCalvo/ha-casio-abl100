# Offline stock-card Casio dashboard

[Español](dashboard.es.md)

Generate a complete Lovelace dashboard using only `entities`, `button`,
`markdown` and `history-graph`. No HACS, custom JavaScript, credentials or network
is needed. The generator never contacts or modifies HA, ESPHome or the watch.
Opening the dashboard does not press anything. Installing it is a separate,
manual decision.

## Generate and use

Python 3.10+ with its standard library; no HA installation is needed to generate.
Run from the repository root:

```sh
python3 tools/generate_dashboard.py registry.json \
  --entry-id 'SELECTED_CONFIG_ENTRY_ID' --language en --output dashboard.yaml
# Spanish is the default. Omit --output for stdout.
```

Output is **indented JSON, also valid YAML 1.2**. A `.yaml` suffix does not imply
a PyYAML dependency. You may paste the whole document into the raw configuration
editor of a new Lovelace dashboard you manually create, not into a single-card
editor. Review the references before saving. No dashboard is installed for you.

Existing destinations, including the input, are refused. `--force` explicitly
permits replacement, **even of the input file**; do not use it on your only copy.
Without force, creation is exclusive and also refuses aliases/symlinks. Exit 0
means success; exit 2 is a fixed-message refusal that does not echo private IDs,
paths or registry contents.

Runnable example with **two fictional watches and customized entity IDs**:

```sh
python3 tools/generate_dashboard.py examples/dashboard-registry.synthetic.json \
  --entry-id selected-entry --language en --output my-example.yaml
```

`examples/dashboard.synthetic.es.yaml` is the actual generated Spanish output
from that fixture. Its fictional entities will not work in your HA: regenerate
from your own export rather than guessing translated entity names.

## Exact input and selection contract

Supply a local copy/export of the **entity registry**, not device registry,
states, diagnostics, `secrets.yaml` or `core.config_entries`. No live export is
performed and no token is needed. If you already have a copy of
`.storage/core.entity_registry`, use that copy without editing the live file.
Treat the export as private and do not commit it.

Supported JSON forms:

1. Row list: `[{...}, {...}]`, such as an exported registry list result.
2. Storage wrapper: `{"data": {"entities": [{...}]}, ...}`.
3. Already-exported successful WebSocket response:
   `{"type": "result", "success": true, "result": [{...}], ...}`.

An unwrapped `{"entities": [...]}`, failed responses and duplicate JSON keys
are not supported. Each required selected row must include `config_entry_id`,
`platform`, `unique_id`, `entity_id`, and `disabled_by` (`null` means enabled).
Other fields are ignored.

Explicitly choose the `config_entry_id` on the intended watch's
`platform: "casio_abl100"` rows, not its MAC address. All **36 current entities**
are required and must be enabled. The tool never enables them: review a disabled
entity intentionally and re-export if appropriate. Registry-enabled is not the
same as available: entities may be `unknown`/`unavailable` until a successful read
or connection.

Resolution uses immutable entry + platform + unique-ID suffix, with one
consistent identity prefix across all required entities. Reconfiguration can
change the transport address while retaining the old identity prefix; the
current address is neither needed nor used. Customized entity IDs are preserved
exactly, never inferred from language or device name. Missing/duplicate mappings,
mixed identities, wrong domains, invalid types and malformed IDs are refused.
Other entries are ignored; unknown future entities are not added to the panel.

Importable pure API:

```python
from tools.generate_dashboard import build_dashboard
panel = build_dashboard(export, entry_id, language="en")
```

Returns a new deterministic dictionary, even when rows are reordered, without
mutating the input. Refusals raise `ValueError` with fixed messages. Output copies
only selected entity IDs, not MAC/unique-ID/registry internals. **It does not
anonymize entity IDs themselves**: private information you put in an ID remains
in its reference. Review both input and output before sharing.

## What the cards mean

- **Connection:** `interactive_connected`, `phase`, `link_state`, `usable`,
  `wait_expires_at`, `idle_expires_at`, `session_expires_at`, `operation_result`,
  `group_results`, `cleanup_result`, `close_reason` and public failure diagnostics.
  Connect / Refresh / Close are distinct explicit actions. Connect may be bounded
  by the installed mode's timers. Long retained mode is a separate product
  candidate; it is not claimed installed or accepted here.
- **Read All — one shot:** distinct from Refresh. Starts one read operation,
  not a subscription or automatic polling; unavailable while an interactive
  session reserves the connection. Separate group read buttons are also included.
- **Observed alarms:** five read-only time sensors with `enabled` and `fresh`,
  observed hourly signal and last successful alarm read, separate from drafts.
- **Local drafts:** five `time` and five `switch` entities plus SIG. Changing
  drafts **does not touch the watch**. `pending`, `observed_value`, `observed_at`
  and `observed_fresh` distinguish intent from observation. Apply explicitly
  writes and verifies; Discard clears intent, not already-submitted writes.
  `draft_pending`, revisions, `apply_phase`, `apply_result`, `write_submitted`,
  `needs_reconciliation`, `verified_at` and `cleanup_result` are actual control
  attributes. The durable uncertain-write barrier may require a reconciliation
  read. Discard does not remove that barrier; do not retry blindly. Alarm
  enabling/SIG remain physically unverified. See the
  [editor contract](native-alarm-editing.md).
- **Steps:** `steps_today`, `record_date`, `fresh`, last successful step read.
  The graph shows **HA Recorder observations, NOT imported watch daily history
  or backfilled samples**. Recorder must include this entity. No dates or zeroes
  are invented; the `daily_history` attribute is not treated as Recorder samples.
- **Time zones/settings/timer:** observed configuration, last-read timestamps
  and freshness where actually provided. Standard offsets/configured DST are not
  necessarily current effective offsets. Encoded timer duration **is not a
  running countdown**. No live clock, time synchronization or generic write
  service is provided. `fresh` is not an age-limit guarantee.

Without data, HA may display unknown, unavailable or `—` for attributes not yet
published by the producer. This is not zero or a successful read. HA updates the
states/attributes; the dashboard does not synthesize clocks.

**Automatic mode:** choose it in integration options only if wanted and after
installing a tested release. This generator/dashboard never enables it or
silently changes options.

## Verification and limits

Keep stub-based and real HA suites in separate processes:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -B -m pytest -p no:cacheprovider -q \
  tests/dashboard/test_generator.py
# Development interpreter with HA 2026.9.4 and its pytest plugin:
env -u PYTHONOPTIMIZE -u PYTEST_DISABLE_PLUGIN_AUTOLOAD \
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH='' python -B -m pytest \
  -p no:cacheprovider -o asyncio_mode=auto -q tests/dashboard/test_dashboard_runtime.py
```

The composition test uses existing native fixtures and HA's actual registry row
formatter, sets up two entries, renames all 36 selected entities, changes
transport without changing identity, reloads and exercises API/CLI. Only the
external exchange is synthetic: no real BLE/ESP or dashboard installation.

The optional `tests/dashboard/check_frontend.cjs` gate executes source-extracted
card/attribute-row `setConfig` methods and the actual button action chain from
**frontend 20260826.7**, commit
`380e9b5a81ada29a1d187b4123c54fb3d6fbcc89` (the version pinned by HA 2026.9.4).
It needs TypeScript and an upstream source copy containing the files listed in
`tests/dashboard/frontend-manifest.json`; it verifies their SHA-256 hashes first:

```sh
node tests/dashboard/check_frontend.cjs /path/to/frontend examples/dashboard.synthetic.es.yaml
# Set TYPESCRIPT_PATH to the typescript module if Node cannot resolve it.
```

This is **structural/action composition, not visual E2E**. The dashboard has not
been rendered in a browser or checked for mobile layout. No physical watch
writes or validation were performed. Future versions that change producers need
new compatibility checks, not an assumed automatic guarantee.
