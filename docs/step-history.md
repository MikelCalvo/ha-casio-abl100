# Observed daily step archive

The existing `steps_today` entity retains its identity and value rules. A value from yesterday is never shown as today's steps. New archive metadata remains visible even when today's sensor is unavailable.

## What is retained

Each accepted step read contributes its current-day sampled total and seven previous-day slots. The archive retains at most **366 distinct observed calendar dates**, newest first. No absent dates are manufactured. A watch slot with unknown steps remains `null`, never zero. A current-day sample is provisional, not a completed-day total.

For the same date:

- A known value is never replaced by an unknown slot.
- A known previous-day watch-history observation supersedes a provisional current-day sample, even if the count is lower. A later stale current-day window does not overwrite that history total.
- For equal source types, later observation time wins; equal timestamps keep the existing value. A known same-rank observation can recover a previously unknown slot even if its original timestamp is earlier. It retains that real timestamp, not the latest import time.

This is a deterministic merge policy, not a guarantee that the watch's date, host clock or historical totals are accurate. Dates and original UTC observation times are retained independently; no midnight event finalizes a day's count.

## HA attributes and presentation

`steps_today` exposes:

- `archived_daily_steps`: newest **30 observed dates**, each containing `steps`, `source` (`current` or `watch_history`) and original `observed_at`.
- `archived_day_count`: total distinct dates retained, up to366.
- `archive_retention_days`:366 (the dated-entry cap, not a rolling host-clock deletion schedule).
- `archive_restored`:true after loading saved observations, false after a newly accepted step capture.

Existing `daily_history`, freshness and current-date semantics remain. The archive metadata uses HA capability attributes so it survives the framework's unavailable-state formatting after midnight. This does not make an unavailable count valid or fresh.

A stock Recorder history graph still plots **times HA observed the current-day sensor**. The dated watch archive is separate; this integration does not inject historical Recorder samples or claim it provides a built-in calendar bar chart. The dashboard can display the attribute data or Recorder's own observation graph. Unknown days must stay visibly missing rather than drawn as zero.

## Storage and compatibility

The archive shares the existing entry/address-bound private observation Store and its single coalescing writer. No second storage owner, BLE client or background read is introduced. The optional `groups.step_archive` field holds typed dated rows; original six observation groups and their timestamps remain unchanged. A legacy saved step record bootstraps its eight observed dates when no archive field exists. A malformed archive is discarded as a whole without discarding the six valid groups. Unsupported future storage schemas retain the original read-only policy.

Reload restores data as stale without BLE. Unload, STOP and removal use the same owned Store lifecycle; removal must not resurrect a pending file. Downgrading to older code can display the original six groups, but that older version ignores and may discard the extra archive on its next save. **Archive preservation across downgrade is not promised.**

## Verification boundary

Pure tests cover overlapping windows, unknown protection/recovery, provisional-to-history precedence, equal/older timestamps, strict row validation, original timestamps and366-entry retention. Real HA tests with synthetic external wire exercise reading, overlap, reload, legacy/corrupt storage, unchanged36 entity IDs, progressive retention after later-group failure and actual `State.as_dict()` metadata while unavailable. The existing observation lifecycle suite covers the original shared writer's STOP/removal behavior. These are offline software proofs, not new physical watch reads, a full year of collected records or visual browser acceptance.
