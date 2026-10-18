# Native steps/history validation candidate

## Status: locally tested; independent SPEC and QUALITY pending

Read steps now uses the generation-bound native ESPHome connection for selected
native sources. This candidate has **not** been installed or physically exercised.
Local synthetic acceptance is not permission to deploy. Existing alarm operations
remain available through the same factored connection owner.

## Read boundary

- Setup performs no watch operation. The existing eleven entity identities,
  labels and history schema are retained.
- Read steps has independent `phase` and UTC `wait_expires_at` attributes. It waits
  at most 360 seconds for a fresh exact-address raw advertisement from the selected
  proxy generation. Cached device absence requires unambiguous native selection.
- An affirmative connected reply is required before service discovery.
- Metadata resolves the feature service, ALL_FEATURES, DRSP, Convoy and one CCCD
  per characteristic. Notify support, DRSP response-write support, distinct valid
  handles and service membership are validated before subscriptions.
- Three finite notify acquisitions are joined; each CCCD receives `01 00`.
  The sole characteristic write is DRSP `00 11 00 00 00`, response=True. There is
  no settings SET, clock command, end command, new retry, cache-policy change,
  API connection closure or firmware change.
- The existing record parser and local-date acceptance are retained. Values and
  UTC success time commit after transfer and cleanup. Failures preserve last-good
  data/time and mark only steps stale. Outstanding caller cancellation counts
  conservatively outrank ordinary/backend failures.
- Active and lock-queued operations are entry-owned. Cleanup targets only the
  captured original generation. Non-native sources retain the Bleak route.
- Observer delivery is best effort after commit, outside the read task. One
  observer's exception or backend-only cancellation cannot undo success or skip
  another current listener. Scheduling failure does not change the transaction;
  shutdown cancels pending deliveries and late callbacks recheck closure.

## Response-future correction

A malformed length header can finish the response future exceptionally during
START, followed by a write failure before the response future is awaited. The
previous finalizer cancelled only unfinished futures, leaking the done exception
to the event loop. The narrowly authorized correction also retrieves the exception
of a done, noncancelled future. No other transport behavior changes.

The unchanged regression
`test_native_step_runtime.py::test_invalid_frame_and_failed_write_leave_no_unretrieved_future`
was observed failing at the actual loop-exception assertion before the correction,
and passing afterward. It releases task/traceback references before GC. Separate
acceptance cases assert the original write exception remains the causal error,
backend-only cancellation becomes a safe read failure, and genuine caller
cancellation remains cancellation. Earlier misleading service-based lifetime
coverage and the original blocked commit/report are preserved privately.

## Owner cleanup error priority

A later composed-path review found that a failing owner fallback disconnect could
replace the original START failure after adapter cleanup also failed. The owner
now retains an active primary error while still attempting the existing fallback
and releasing its callbacks. With no primary error, cleanup failure still rejects
the transaction. The session retains genuine caller-cancellation priority.

`test_native_owner_priority_runtime.py` preserves the independent review's
producer-shaped probes. Two step variants and the matching preexisting alarm
case failed before the correction and pass afterward. Successful cleanup,
cleanup-only failures, genuine cancellation, native-date rejection and retained
late callbacks are separate controls. This changes error selection, not packets,
connection policy, cleanup attempts or timeouts.

## Synthetic evidence boundary

`tests/ha/test_native_step_runtime.py` exercises real HA ConfigEntry management,
registries, `button.press`, entities, session, NativeAPI and installed APIClient
methods. Only external APIConnection exchange, discovery and test dates are
synthetic. The adapter suite uses synthetic metadata and 400-byte history records.
No test values or addresses are physical observations.

`tests/ha/test_native_step_acceptance_runtime.py` adds finite acceptance closures:

- Two real HA entries share one native API generation, with producer-addressed
  protobuf responses. B's advertisement cannot start A. A's unload while waiting,
  acquiring DRSP notify, or disconnecting preserves B's callbacks and unrelated
  original callbacks. A-address frames cannot complete B; B alone publishes its
  response. A replacement generation receives zero requests/handlers, and both
  old-generation readers refuse publication after replacement.
- Ordinary and backend-cancellation observer faults preserve committed steps and
  other delivery. Ordinary/backend-cancellation scheduler faults preserve success,
  read failure and caller cancellation. Unload suppresses retained deliveries.
- Last notify acquisition (10-second native budget) and disconnect (5-second
  native budget) are held through repeated caller cancellation, then complete near
  a synthetic finite backend deadline. Children finish before the owner returns,
  original unrelated callbacks survive, and replacements receive no traffic.
  The external fake compresses its clock to 0.1 seconds and completes at 90%; no
  production timeout is shortened or changed. This proves joining, not real-time
  behavior at 9/4.5 seconds or forced termination of an uncooperative backend.
- A failed step refresh preserves a successful alarm snapshot, timestamp and
  freshness while retaining stale last-good step values/time.

The shared primitive's existing alarm tests remain applicable to exact-source
proxy selection, generation checks and child-entry guards; the new step consumer
has its own three-subscription, publication and HA isolation assertions. Existing
fixture-only adaptations preserve old behavior assertions and the ten original
subscribers, separately accounting for the step progress subscriber.

### Composed finite budget

After acquiring the shared session lock, a conservative padded envelope is
**497 seconds**: advertisement 360; connect 30 plus vendor timeout disconnect 5;
services 10; three acquisitions plus descriptors at 10+10 each; START 10; record
response 12; adapter disconnect 5; owner fallback disconnect 5. This intentionally
sums even mutually exclusive failure allowances; outer 12-second transfer-stage
limits can shorten it. Normal event-loop execution and the backend honoring its
finite timeouts/cooperative cancellation are prerequisites. No separate total
service timer was added. A queued request also waits for earlier lock owners;
there is no unconditional bound for arbitrary queue length, suspended event loops
or indefinitely uncooperative external code. An external trial/controller must
budget beyond this envelope and separately allow queued work, not use a short
polling timeout as a service deadline.

## Remaining gates

Exact-byte causal RED/GREEN, later characterization runs, argv/environment,
stdout/stderr, real exits, coverage mapping and final manifest/archive are in the
private `steps-correction-r1/` and `steps-correction-r2/` packets, alongside
unchanged historical evidence.
The original native-route RED and adapter API-existence RED remain distinguished
from later semantic coverage. The finite matrix is representative, **not** an
exhaustive Cartesian product of all faults and cancellation interleavings.

Independent full-delta SPEC then QUALITY remain required. Installation, a real
proxy/watch transfer, watch-screen outcome and physical history comparison are
separate pending gates. Live automatic settings and device state were untouched.
