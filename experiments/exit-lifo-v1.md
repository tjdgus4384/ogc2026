# EXIT-LIFO v1 pre-registration

## Claim under test

For a same-bay, same-day EXIT batch, a checker-legal order is available without
search: descending ENTRY day, then reverse of the actual checker-legal ENTRY
topological order.  A later entrant cleared every earlier resident while
descending, and the official checker applies the identical `j >= k` geometry
while ascending.

This exposes a strict false-reject in the shipped equal-ENTRY/equal-EXIT id
tie-break.  The old `m15` experiment did not test the claim soundly: its
candidate was absent from the event index during validation.  Later
`exact-events` measurements bundled several changes and used underpowered,
mostly unpaired draws; they do not close this isolated mechanism.

## Treatment

The legacy insertion scan remains authoritative.  On a fixed-oracle reject,
the treatment remembers at most the first official-semantic candidate and
continues the complete legacy scan.  Any later legacy success wins unchanged.
The remembered candidate is committed only when the insertion would otherwise
fail.  Completed-board polishing is a separate switch and is OFF in this gate.

No public-instance selector is used.  Same-day ENTRY ordering is also made
size-unbounded; the previous 16-block fallback was not justified for hidden
instances.

## Deterministic gates

- Build: `builds/exitlifo1`, native SHA-256
  `75019dc282f57188fc9360eb61fe0489ad4a71f9e95098a5efe9b112611e91b2`.
- Build/smoke job `74742`: P1/P2/P3 checker-feasible.
- Checker audit jobs `74747` (OFF), `74748` (ON): synthetic false-reject,
  forced fallback, a 20-block ENTRY batch, and 20 historical boards.
- Fixed-iteration job `74753`: four preliminary boards x four seeds x
  OFF/SHADOW/ON, 20,000 single-thread iterations from common serialized starts.

For all 16 cells, OFF and SHADOW must have identical search-state hashes,
objective/trajectory counters, and production-gated output hashes.  All ON
outputs must pass the production checker gate.

The treatment advances to Train only if:

1. overall ON wins are at least overall losses;
2. no P3-P6 board has a four-seed median regression greater than 2%; and
3. the measured runtime ratio does not erase the mechanism at wall time.

A single large preliminary improvement cannot override a failed condition.
Passing this screen is not adoption: the next gate is broad `train_final`, with
`train` used as an independent distribution check before any submission change.

## v1 result (job 74753)

All deterministic safeguards passed: 16/16 OFF/SHADOW trajectories and gated
solutions were identical, and every ON result passed the production checker.
The score result was **1W/0L/15T**.  The sole changed cell was P6 seed 11,
33,605,218 -> 33,374,730 (**-0.686%**); every P3-P5 cell and the other three P6
cells was byte-identical.  Per-board median ON/OFF time ratios were 1.00-1.01x.

Thus v1 satisfies its stated non-regression thresholds and proves that the
false-reject can occasionally turn a failed repair into a better trajectory.
It does not yet justify a broad Train efficacy claim: only one of sixteen
deterministic cells consumed a fallback in a way that survived to the final
incumbent.  Raw summary SHA-256:
`bf14f336cd2c590b716db2f8e62ae20c81a029851d9b11433f1dd23b3d0a32c5`.

## v2 pre-registration: objective-dominant choice

The two earlier policies bracket the useful decision incorrectly:

- immediate exact-first accepts a checker-legal candidate even when a later
  legacy candidate has a lower true score; its old fixed-iteration screen was
  mixed and regressed P4/P5;
- v1 always takes the later legacy candidate, even when the remembered exact
  candidate is strictly better; it changed only 1/16 outputs above.

v2 remembers the first exact-only candidate exactly as v1 does.  When the first
legacy-feasible candidate is found, it evaluates both on the same partial board
with `core::evaluate` and chooses the exact candidate only if its total weighted
objective is strictly smaller.  A tie preserves the legacy candidate.  If no
legacy candidate exists, v1's exact fallback remains.  The comparison consumes
no RNG, uses no instance ID or size threshold, and is valid for arbitrary
Train/final boards.

Before looking at any v2 score result, its 20,000-iteration screen advances only
if all of the following hold:

1. 16/16 OFF/SHADOW trajectories and gated outputs are identical, and 16/16 ON
   outputs pass the production checker;
2. at least four cells change, overall wins exceed losses, and no board has a
   four-seed median regression above 2%;
3. shadow diagnostics observe at least one strict objective-dominant comparison
   (otherwise the proposed mechanism was not exercised); and
4. the median SHADOW/OFF runtime ratio is at most 1.05x.

This is still only a causal routing screen.  Passing it leads to a broad
`train_final` fixed-iteration gate, then paired wall-time validation; it is not
an adoption decision.

## v2 result: closed (jobs 74846, 74850, 74851, 74852)

The isolated build and semantic audits passed.  Build 74846 reproduced the
P1/P2 exact canaries and a checker-feasible P3 smoke.  OFF/ON audit jobs 74850
and 74851 passed the synthetic dominance witness, 20 historical boards, the
20-block batch, and the dynamic 70-block batch with zero failures.  Thus the
closed-form EXIT ordering theorem and its unbounded implementation remain
valid diagnostics.

The fixed-iteration routing policy failed its frozen score gate.  Of the 15
cells that produced both arms, the result was **5W/7L/3T**.  P3 was 1W/3L with
deltas -2.40%, +9.83%, +9.64%, and +3.55%, for a **+6.60% median regression**
against the allowed +2%.  P5 also had two losses and one tie among completed
cells.  More decisively, P5 seed 11 ON ended at a Stage-2 exact-touch board
that the production rescue ladder could not legalize, while its byte-identical
OFF/SHADOW control gated successfully at 13,233,145.  Job 74852 therefore
failed closed and produced no complete summary.

This is a policy rejection, not a retraction of the EXIT semantics: exact-only
candidates are real and checker-legal at insertion time, but selecting them
changes later geometry enough to worsen the distribution and can end in an
unrecoverable exact-touch board.  v2 fails conditions 1 and 2, does not advance
to Train, and must not be enabled in the production solver.
