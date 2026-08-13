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

The axis advances only if at least one of P3, P4, or P5 has a checker-feasible
one-decision improvement of at least 5%.  A smaller result is not on the scale
of the all-time-minimum gap and closes this route.  Passing leads to a
budget-capped sequential/beam rollout and only then an equal-budget full-driver
test; it does not justify a public-board selector or production change.
