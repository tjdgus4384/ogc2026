# Voluntary-stay rollout kill test

## Missed decision variable

The official rule is `exit - entry >= processing`, but every normal kernel
insertion writes `exit = entry + processing`.  The production gate can extend
an exit by one day to legalize an already-invalid board; the search itself
does not use exit slack as a decision variable.

That restriction is material under the crane rule.  A one-way obstruction can
coexist only when the obstructing block's stay is nested inside the obstructed
block's stay.  Voluntarily retaining an early block until its due date can
make such nesting legal at zero tardiness cost for that block, potentially
allowing several suffix blocks to enter earlier.  Geometry, timing, and the
future suffix must be evaluated together.

This is not the retired `OGC_WAIT_STAYS` treatment.  That treatment extended a
placement only after its minimum exit was already infeasible and accepted the
first later resident-exit event.  It neither voluntarily extended an already
feasible stay nor compared suffix rollout values.  The later exact-event
schedule screen held geometry fixed, so it also could not construct a new
nested layout.

## Frozen one-decision screen

Use each job-74933 checker-selected order, free assignment, and internal seed
7.  First require the new no-treatment API to reproduce the frozen baseline
flat bytes, components, and production-gated objective exactly.

For every block whose baseline exit is before its due date, rebuild twice with
exactly one absolute exit target:

1. halfway through the remaining exit slack (at least one day); and
2. the due date.

The intervention is applied immediately after the normal placement and before
constructing the suffix.  It draws no RNG.  For every block, an explicit target
equal to its normal exit is a semantic null and must reproduce the baseline
byte-for-byte.  A target that makes the current prefix illegal is rejected;
the instrument never weakens the kernel feasibility predicate.

The same action vocabulary and exhaustive rule run on P3-P6.  There is no
problem id, block id, size class, load threshold, or fitted coefficient in the
mechanism.  The official production gate screens the fixed 20 lowest raw
actions and its objective is authoritative.  Raw scores from a failed null
audit are non-causal and cannot advance.

The axis advances only if every deterministic replay and same-exit null passes
and at least one of P4 or P5 has a checker-feasible improvement of 5% or more.
Selection is non-regressing because the free baseline remains available.  A
passing result authorizes only a sequential width-2 stay/geometry rollout,
followed by a fixed-iteration plateau test, disjoint seeds, and the declared
Train splits.  It does not authorize a public-board action table or production
change.  Failure closes zero-own-cost, existing-prefix-geometry stay extension;
it does not claim that paid extension or a joint placement-and-stay action is
impossible.
