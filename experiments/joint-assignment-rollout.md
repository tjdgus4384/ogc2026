# Deterministic joint-assignment rollout kill test

## Missed capability

The repaired S2 instrument correctly replaced RNG-consuming `insert_capped`
replays with deterministic `reconstruct_ordered`, but it then removed bay
actions as "not expressible".  That premise is false: the API's first argument
is a partial per-block assignment, where `-1` is free and a bay index is a hard
choice.  Existing production and probe code already use that contract.

This matters because P3 is assignment-dominated, while P4/P5 need coupled
assignment, schedule, and geometry.  A completed-board relocation is the wrong
instrument on a full bay; rebuilding the whole board after one bay decision is
the deterministic rollout question that was skipped.

## Frozen first test

For each preliminary P3-P6 board, select the best of the six shipped seed
orders under a free assignment.  Hold that order and internal seed 7 fixed.
For every block and every non-baseline bay, force only that one assignment,
rebuild the complete board, and score the true weighted objective.  The best
candidate and the baseline must replay byte-identically and both must match the
official checker objective.

The same exhaustive rule is used on every board.  There is no problem ID,
size, bay-count, saturation, or score threshold inside the algorithm under
test.  This is a causal mechanism screen, not an adoption result.

For every block the harness also forces the bay that the free baseline already
chose.  These semantic no-ops must reproduce the baseline byte-for-byte.  This
control is necessary because shrinking the C++ bay vector can otherwise change
`std::shuffle` RNG consumption and turn an assignment comparison into a fresh
construction draw.  No alternative-bay score is interpretable unless all
same-bay controls pass.  Raw exact-touch boards are sent through the same
production checker gate in both arms; the gated objective is authoritative.

The axis advances only if at least one of P3, P4, or P5 has a checker-feasible
one-decision improvement of at least 5% **and** every same-bay null is byte
identical.  A smaller result or a failed null control cannot advance.  Passing
leads to a budget-capped sequential/beam rollout and only then an equal-budget
full-driver test; it does not justify a public-board selector or production
change.

If the null fails, the harness records the raw diagnostic distribution but
does not run the alternative through the official checker.  In that state
there is no candidate result to validate: the next step is to repair the
instrument so a same-bay intervention preserves RNG consumption.

## Instrument attempt 74887

The first job was stopped by its own fail-closed checker assertion on P5: the
raw baseline carried a known zero-area exact-touch violation, while P3/P4/P6
completed.  Their apparent deltas are not verdicts because the same-bay RNG
control had not yet been measured.  The corrected rerun adds that control and
uses the production gate symmetrically; no score from 74887 is eligible for an
advance decision.
