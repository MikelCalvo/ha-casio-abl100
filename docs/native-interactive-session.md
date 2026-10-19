# Native interactive watch session

This local candidate adds three native Home Assistant buttons and one link-status binary sensor to the existing watch device. The original nineteen identities remain; there are twenty-three entities. Independent final-source review, operational binding, installation and a coordinated physical trial are separate gates. Local HA tests use synthetic external Bluetooth wire traffic; they do not establish physical connection stability or repair the previously observed watch-side ERR.

## Controls

| Stable unique-ID suffix | English | Spanish |
| --- | --- | --- |
| `connect_interactive` | Connect watch | Conectar reloj |
| `read_interactive` | Refresh connected watch | Actualizar reloj conectado |
| `close_interactive` | Close watch connection | Cerrar conexión del reloj |
| `interactive_connected` | Interactive connection | Conexión interactiva |

Home Assistant adds the existing device prefix once. Default entity IDs use the selected HA language; suffixes above, not display names or slugs, are the stable registry identity. Existing user-renamed entities remain renamed after reload.

For a separately authorized coordinated trial:

1. Keep other clients from taking the watch connection. Do not unpair, reset or change watch settings.
2. Press **Connect watch / Conectar reloj** once. Await `phase: waiting` with a future UTC `wait_expires_at` before the operator opens the watch's CNCT window. The button finishes only after the native link and services are confirmed. Connect does **not** read application data.
3. Once status is `on`, `link_state: connected`, `phase: connected` and `usable: true`, press **Refresh connected watch / Actualizar reloj conectado**. It reads the supported fixed groups on that same link, without reconnecting. A later explicit refresh requests a new cycle.
4. Press **Close watch connection / Cerrar conexión del reloj** and await completion. Close remains available while connecting, acquiring notifications, reading or cleaning up. It bypasses the lifetime mutex and joins the original owner's cleanup; submitting close is not proof that cleanup finished.

The four existing one-shot read buttons retain their ordinary meanings outside an interactive reservation. During that reservation they are unavailable and reject direct presses rather than queueing work for after close. Both automatic modes retain their existing meanings and consume busy opportunities without a deferred retry. There is no new stored option.

## Truthful status

The binary sensor describes only this integration's interactive native link, not another client's connection, watch-screen CNCT, ERR or remote wake capability:

- `on`: a confirmed connected link. Close intent alone does not turn this off.
- `off`: observed link loss or confirmed native disconnect.
- `unknown`: no link evidence yet, or cleanup/generation loss leaves closure uncertain. The sensor stays available so HA retains explanatory attributes.

Before any controller exists, the sensor is **unknown**, with `phase: idle`, `link_state: unknown`, `usable: false`, all expiry attributes explicitly null, `operation_result: never`, six `group_results: not_attempted`, `cleanup_result: not_attempted` and `close_reason: null`. No physical disconnected state is inferred from startup.

Attributes are bounded controller observations: `phase`, explicit `link_state`, strict boolean `usable`, `wait_expires_at`, `idle_expires_at`, `session_expires_at`, `operation_result`, `group_results`, `cleanup_result`, `close_reason`. Expiries are explicit null or aware UTC ISO timestamps. Public status is observational; the initiating service's own completion decides its outcome, not a mutable latest-state field.

Groups are `alarms`, `steps`, `zones`, `basic_settings`, `auto_time`, `timer`. Earlier validated groups survive a later failure. Requested freshness is reset for each admitted refresh without deleting last-good values/timestamps. Unknown auto-time remains unknown, not disabled. Observations are in memory and reset after reload; no historical values are injected into HA.

## Bounds and safety limits

The accepted core uses a 120-second idle limit, 300-second absolute link lifetime and 60-second refresh limit. Its existing fresh-RAW wait is bounded separately at 360 seconds. Limits revoke further work; already-held notification acquisition and cooperative disconnect still need their backend cleanup time. No forced release deadline is claimed.

No new client, scanner, setting write, time synchronization, pairing, firmware operation, retry, reconnect or keepalive is added. Service failures have fixed per-action translated messages, with no raw backend text; real caller cancellation remains cancellation and joins admitted cleanup. Failed cleanup remains explicitly uncertain.

The wire has no transaction IDs. Local guards reject idle and pre-query frames, but cannot authenticate an identical delayed old reply arriving after a repeated identical query starts. No quiet delay disguises that limit. See [unified read semantics](native-unified-sync.md) and [clock-reading limits](clock-reading-limits.md).
