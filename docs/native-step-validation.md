# Native steps/history validation candidate

## Status: blocked before SPEC acceptance

This candidate moves the existing Read steps operation onto the generation-bound
native ESPHome connection for selected native sources. It has **not** been
installed, physically exercised, or approved for deployment. The existing alarm
API remains available through the same factored connection owner.

## Read boundary

- Entry setup performs no watch operation. Existing eleven entity identities,
  names and history schema are retained.
- The step button has its own `phase` and UTC `wait_expires_at`, independent of
  alarm progress. A request waits at most 360 seconds for a new exact-address raw
  advertisement on the selected existing proxy generation. Cached device absence
  is allowed only with unambiguous native proxy selection.
- An affirmative native connected reply is required before service discovery.
- Metadata resolves the feature service, ALL_FEATURES, DRSP, Convoy and one CCCD
  per characteristic. Notify support, DRSP response-write support, distinct valid
  handles and service membership are checked before subscriptions.
- Three finite notify acquisitions are joined, and each CCCD is enabled with
  `01 00`. The only characteristic write is DRSP `00 11 00 00 00`, response=True.
  No settings SET, clock command, end command, API connection closure, retries,
  cache policy or firmware changes are introduced.
- Existing record parser and local-date acceptance are retained. Native values
  and UTC success time commit only after transfer and cleanup. Failed refreshes
  retain last-good data/time and mark only steps stale. Positive caller
  cancellation counts conservatively outrank ordinary/backend failures.
- Active and lock-queued operations are entry-owned. The original API generation
  alone may receive cleanup. Non-native sources retain the existing Bleak route.

## Synthetic test boundary

`tests/ha/test_native_step_runtime.py` runs real HA ConfigEntry management,
registries, `button.press`, entities, session, NativeAPI and the installed
APIClient method bodies. Only the external APIConnection exchange, discovery and
clock are synthetic. Metadata is serialized through the existing native alarm
wire producer; DRSP and Convoy payloads use synthetic 400-byte history records.
No test values or Bluetooth addresses are physical observations.

`tests/protocol/test_native_step_adapter.py` exercises the unchanged parser and
transport via the new adapter. Its small API double models confirmed-disconnect
notification removal; real library registration/cancellation cleanup is tested
separately by the HA runtime suite.

Existing fixture-only adaptations preserve the original assertions: local
Bluetooth doubles now carry nonempty local backend details; the historical mixed
native-alarm/Bleak-step lock test explicitly selects those original routes; the
listener census still asserts all ten original subscribers and separately adds
the step-button progress subscriber.

## Concrete ownership blocker

Reproduction:

```
tests/ha/test_native_step_runtime.py::test_invalid_frame_and_failed_write_leave_no_unretrieved_future
```

Deliver a malformed length header during the real response-write invocation,
then raise a synthetic private write exception before returning its ACK. The
unchanged `transport.read_record` completes its local future with `ReadError`,
but exits via the write error before awaiting that future. Its `finally` cancels
only an unfinished future; it never retrieves an already-failed future's error.
Once the completed read task and its traceback are released, the event loop
receives `Future exception was never retrieved`.

The test uses direct session invocation for this memory-lifetime assertion,
removes task references, runs GC and inspects the real loop exception handler.
A prior service-based version passed while its task/traceback was retained, but
stderr still exposed the error later. That misleading pass is retained privately
and is not acceptance evidence.

The narrow remedy belongs in `transport.py`: consume a completed noncancelled
response future during finalization, preserving the original error and caller
cancellation policy. That file is explicitly frozen by this task. This candidate
therefore stops **blocked** without changing it or duplicating the parser inside
the adapter. Authorization for that narrowly scoped correction and its TDD cycle
is required before claiming a clean full runtime gate.

## Evidence and remaining gates

Private exact-byte RED snapshots, every runner argv/environment, stdout/stderr,
actual exit and source hashes are under the task's `steps-implementation/`
evidence directory. The initial service RED fails at missing native waiting
readiness, not at fixture import/setup. The adapter RED establishes absence of
its API, not semantic proof for every later metadata guard.

Most new semantic cases and existing alarm regressions pass; expanded cases are
characterization/acceptance coverage, not retroactively claimed causal RED.
The required future-cleanup case fails and blocks the candidate regardless of
aggregate passing counts. The broader cancellation matrix is not claimed as an
exhaustive Cartesian product. Two-entry isolation, observer fault permutations,
cleanup near-budget lateness and full cancellation/error cross-products require
review against the frozen contract after resolving the transport ownership
conflict. No additional production instrumentation was added to evade that gap.

Independent SPEC then QUALITY, installation, real proxy/watch transfer,
watch-screen result and physical history comparison all remain pending. Nothing
here changes the live auto-read option or requests a physical trial.
