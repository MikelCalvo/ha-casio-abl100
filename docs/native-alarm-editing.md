# Alarm/SIG draft controls — corrected local candidate

This source increment adds 13 controls to the existing 23 identities on the
same watch device: five time drafts, five enable drafts, one hourly-signal
draft and explicit Apply/Discard buttons. It is not installed, independently
accepted or physically write-tested. The reload safety and finite fixture
corrections have local synthetic coverage; independent SPEC then QUALITY are
still required before installation. Historical rejected checkpoints remain blocked.

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

1. Stage only the intended alarm/SIG fields.
2. Use **Connect watch / Conectar reloj** to establish the existing bounded
   interactive owner. Connect itself sends no setting SET.
3. Explicitly press **Apply alarm draft / Aplicar borrador de alarmas**.
4. Inspect `apply_result` and the observed sensors, not the button timestamp.
5. **Discard alarm draft / Descartar borrador de alarmas** clears only local
   overrides; it never undoes physical changes.

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
models canonical SET auxiliary 40 separately from preserved GET auxiliary
00/07; physical equivalence still requires a separately authorized watch test
with concrete intended changes and rollback values.

Zone, basic-setting, timer and automatic-time writers remain withheld. Current
clock reads, physical write reliability and the cause of watch ERR are not
established by these source/synthetic checks.
