# Native alarm/SIG draft controls

## Current installed pilot — 2026-10-20

Reviewed component revision `badfe909c74f63a2b2d81c69b3b872df596e1b1c` is
installed with 34 component files and 36 entity identities on the existing
watch device. It preserves the original 23 identities and adds 13 controls:
five time drafts, five enable drafts, one hourly-signal draft and explicit
Apply/Discard buttons. This documentation revision changes no component bytes
and is not a new deployment. Both automatic-read modes remain off.

A coordinated native Apply changed **disabled alarm 5 `00:00` → `00:01`**;
a second Apply restored **`00:00`**. New native readback verified each result,
including alarm 5 still disabled and the other alarms/SIG preserved. The
operator confirmed **`12:01`, then `12:00`** on the 12-hour watch display.
Explicit Close completed cleanly, and fresh post-use source, identity,
ownership/proxy and node-health checks passed.

The watch ended in **ERR on both write trials**. Successful readback and a
visual time comparison do not explain or clear ERR. The operator's roughly
20 seconds is an estimate, not measured service/closure timing. Enable/SIG
toggling and uncertain-write recovery remain **synthetic-only**; everyday
reliability is unproven. See [current and historical validation](validation.md)
and the [short Spanish usage guide](alarm-editing.es.md).

### Historical local checkpoints

Before this installation and trial, the draft implementation and its reload
safety/finite fixture corrections were local candidates awaiting independent
SPEC then QUALITY. Those gates describe their then-current artifacts, not the
installed status above. Rejected intermediate checkpoints remain rejected;
the physical time roundtrip does not retroactively accept them.

## Requested versus observed

Editing a time, enable or SIG control only stages a local sparse override.
Untouched fields reflect the last observation; absent observations remain
unknown, not midnight or disabled. Reading cannot overwrite an override.
Drafts are not stored or restored. Reload discards pending execution intent.

Times have minute semantics. `HH:MM` and `HH:MM:00` are accepted; nonzero
seconds, microseconds and timezone-bearing values are rejected, not rounded.
Home Assistant's native TimeEntity state formatter displays `HH:MM:00`; this
is not a claim about a frontend minute-only widget.

## Explicit connected Apply

1. Keep both automatic modes off and use one operator with competing phone/
   client connections excluded. First record the observed values and intended
   restoration; a new physical change needs its own coordinated decision.
2. Use **Connect watch / Conectar reloj**. Wait for the connection sensor's
   `phase: waiting` and future `wait_expires_at`, then open CNCT by holding
   **C/MODE (lower left) for at least three seconds**. Connect sends no setting SET.
3. When `usable: true`, use **Refresh connected watch / Actualizar reloj
   conectado** and inspect the complete alarm/SIG observation. Stage only the
   intended fields; time-only edits do not require toggling enable or SIG drafts.
4. Explicitly press **Apply alarm draft / Aplicar borrador de alarmas** once.
   Inspect `apply_result`, `needs_reconciliation` and the observed sensors, not
   the button timestamp or draft value alone. Compare the intended watch display.
5. Finish with **Close watch connection / Cerrar conexión del reloj**, await
   cleanup and inspect the watch screen separately. Restoration is another
   explicit staged Apply/readback, not automatic rollback.

**Discard alarm draft / Descartar borrador de alarmas** clears only local
overrides; it never undoes physical changes. See the [Spanish guide](alarm-editing.es.md)
for result handling. These instructions do not arm a new trial or broaden the
physical proof to enable/SIG writes.

Apply does not connect automatically or queue behind another operation. It
borrows the original native connection and primary notification subscription.
Stage/Discard are unavailable during Apply. A complete fresh GET15/16 baseline
is required even when semantic values have been restored from HA Store.
Known changes since the first staged field cause `conflict`, publish the fresh
observation and require Discard/review/restage. Unsupported baseline flags or
auxiliary bytes fail closed before SET.

Both canonical SET frames are built before invocation, sent sequentially on
the resolved 002d characteristic with response enabled, then checked against
an actual new GET15/16 observation. ATT completion is not device verification.
Only `verified` or a fresh proven `unchanged` clears the frozen draft revision.
A valid differing observation is published as `mismatch` or
`preservation_mismatch`, never converted into requested success.

## Uncertainty and limitations

`unknown` means a SET might have been submitted but the final state could not
be verified. The last valid observation/date is retained and freshness is
revoked. The draft remains pending; Discard does not remove uncertainty.
Further Apply is blocked until an explicitly initiated GET-only interactive
refresh obtains a complete alarm group. That reconciliation does not change
the historical failed Apply into `verified`. No implicit retry, rollback,
reconnect, compensating SET, device END or host clock synchronization exists.
Cleanup status is separate from the alarm outcome.

A separate entry-bound local safety journal retains the reconciliation barrier
and bounded outcome metadata across reload and Core restart. It stores no draft,
desired value, raw frame or connection authority. Before the first possible SET,
an executor writes a conservative pending barrier under HA's `.storage`, flushes
and fsyncs the file, atomically replaces it and fsyncs the directory. A failed or
cancelled save admits zero SET. The original native owner joins outstanding local
IO and rechecks entry, generation, cancellation and deadline before dispatch.
Only a complete actual readback or explicitly initiated **Refresh connected
watch** full alarm-group GET can clear the barrier. Apply's own baseline cannot
satisfy a preexisting prerequisite. A later unrelated group failure does not
undo a completed alarm reconciliation. GET-only recovery preserves historical
`unknown`; Discard and reload do not make it `verified`.

Missing initial journal means never-written; malformed/unreadable metadata or a
changed immutable entry/selected-address binding blocks Apply but leaves GET-only
recovery available. Changing address does not erase uncertainty. Removing the
entry deliberately removes its owned record after joining its owner. Restored
metadata is not a fresh physical observation; pending recorded before a crash
can conservatively indicate possible submission even if dispatch never happened.
Local acknowledgment is limited to successful filesystem API returns, not hostile
filesystem, backup rollback, dishonest storage or hardware/power-loss guarantees.
Do not delete the safety file to bypass recovery. There is no extra BLE client,
lock, automatic retry or executable recovery intent.

Wire notifications have no transaction ID. Phase and duplicate/error checks
cannot authenticate an identical delayed response as belonging to the newest
identical GET; a sleep is not proof of correlation. The synthetic memory wire
models canonical SET auxiliary `0x40` separately
from preserved GET auxiliary `0x00`/`0x07`. The alarm-5 trial confirms the
specific time changes and complete native readback comparison on this watch,
not arbitrary baseline support or lossless opaque-byte roundtripping. Both
SET15 and SET16 encode the full alarm group; a sparse local edit is not a
single-slot wire write. Unsupported baseline flags/auxiliaries are refused,
not guessed or silently normalized as observations.

Zone, basic-setting, timer and automatic-time writers remain withheld. Current
clock reads are unimplemented. Enable/SIG toggles and uncertainty recovery
remain synthetic-only; physical write reliability and watch ERR remain unresolved.
