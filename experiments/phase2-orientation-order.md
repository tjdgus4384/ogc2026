# Phase-2 orientation-order wiring audit

## Missed data-flow defect

`repair::insertGreedy` constructs one orientation order per insertion.  With
the shipped `oriPref=50`, that order is compact-union-bbox first on half the
draws and shuffled on the rest.  Phase 1 and phase 3 both enumerate the
resulting `oris` vector.  The ordinary phase-2 revival path does not:
`scanLocal` reconstructs `0, 1, ...` internally.  Only the phase-2
area-rejection fallback, which calls `scanBayAt`, sees `oris`.

The earlier pipeline audit classified `oriPref` as live because it had a
consumer.  That was a symbol-level check, not a complete data-flow check, and
missed the main phase-2 call.  This is not evidence that the fix wins, but it
is an untested, instance-independent mechanism with exactly one plausible
correction.

This does **not** reopen `sh1`.  That refuted candidate replaced the shipped
union-bbox metric with a shadow-area metric and lost 10W/24L, +2.67%.  The
candidate here changes no metric and adds no pure compact policy: it passes the
already-drawn 50%-compact/50%-shuffle order to the one path that discarded it.

## Frozen candidate

After the EXIT build chain has taken its immutable source snapshot, build one
diagnostic binary with a default-off `OGC_PHASE2_ORIS` switch.  When enabled,
`scanLocal` receives `const std::vector<int>& oris` and enumerates that vector;
when disabled it preserves ascending orientation indices.  The switch must:

- draw no RNG and change no RNG consumption;
- add or remove no position, bay, day, or orientation candidate;
- change no collision, crane, area, acceptance, or objective predicate;
- use no problem identity, size threshold, or board classifier; and
- expose non-invasive counters for phase-2 calls and successes.

Whether a selected orientation differs from OFF is determined by the paired
construction's byte hash, not by replaying an OFF shadow inside the hot path.
Such a shadow would add candidate scans and risk changing cost or state; a
nonzero orientation index is not a valid substitute for that comparison.

OFF and ON are run in separate processes from the same binary because knob
values are cached on first use.  A repeated OFF run and a repeated ON run must
each be byte-identical.  Every emitted board goes through the official checker.
Some loose deterministic construction orders carry an unrepairable exact-touch
artifact in both modes.  Such a pair is recorded as non-informative rather than
dropped or treated as a harness crash.  An OFF-feasible/ON-invalid pair is an
explicit loss; the reverse is an explicit recovery win.

## Judgment sequence

1. **Deterministic construction kill test.**  On P3-P6, run the six shipped
   construction orders with internal seeds 7 and 11 in both modes.  If no
   solution differs, or the phase-2 success counter is zero, the route is
   dormant and closes without a stochastic gate.  If every changed pair is a
   strict loss, it also closes.  Otherwise the test establishes reachability,
   not adoption.
2. **Primary fixed-iteration screen.**  Run 20,000 iterations for seeds
   7, 11, 23, and 41 on every P3-P6 board.  Advance only if all 32 arms are
   checker-feasible, the pooled paired median is non-positive, no board median
   regresses by more than 3%, and at least one board median improves by 3% or
   more.  Wall-clock scores are not used at this stage.
3. **Independent replication.**  Repeat with seeds 3, 17, 31, and 47.  Across
   both seed sets require more wins than losses, pooled median at most -2%, at
   least three of four board medians non-positive, and no board median above
   +2%.  Failure closes the route; no public-board selector is allowed.
4. **Train robustness.**  Run one seed-7 paired fixed-iteration comparison on
   all 40 `train_final` instances.  Require at least 27 informative wins, median at
   most -1%, no individual regression above 10%, and median regression no
   worse than +1.5% in each predeclared structural split (`m<=3`/`m>=4` and
   saturation `<1`/`>=1`).  These splits are rejection diagnostics only, not
   dispatch rules.
5. **Official wall gate.**  Only a Train survivor is rebuilt with the switch
   removed and the corrected order unconditional.  Recheck P3-P6 at official
   limits plus all checker, fallback, packaging, runtime, and RSS invariants.
   The eight unrelated hidden-final inputs are not in the repository; their
   actual result can only be measured by a later user-authorized submission.
   Until then `generalization-protocol.md` governs the honest proxy claim.
   Adoption still requires one universal solver with no instance-specific
   fallback.

The screen is deliberately effect-size gated.  A trajectory-only change below
this scale cannot plausibly close the current all-time-minimum gaps and should
not consume another multi-day campaign.
