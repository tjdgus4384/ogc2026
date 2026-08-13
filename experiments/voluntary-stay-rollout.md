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

## One-decision verdict: job 74969

The isolated build came from source commit
`f751861d0e30da7d9dc00d80540ed94fead0c357`; its extension binary SHA-256 is
`be0d5ac41b9ab91b4354802221343c4a0b3b3a81fa3104fd2e9b7aa30f089728`.
The unchanged production path passed the build smoke at preliminary P1
11,280, P2 31,368, and a checker-feasible P3 86,895.

Every deterministic replay and all 800 semantic no-ops reproduced the frozen
baseline byte-for-byte.  The checker-authoritative winners are:

| board | voluntary action | baseline | one action | delta | Z1 change |
|---|---|---:|---:|---:|---:|
| P3 | block 168, exit 66 -> 67 | 539,249 | 531,784 | -1.384% | 3 -> 3 |
| P4 | block 115, exit 20 -> 23 | 8,040,292 | 7,223,445 | -10.159% | 547 -> 488 |
| P5 | block 4, exit 24 -> 27 | 20,111,373 | 19,069,335 | -5.181% | 1462 -> 1380 |
| P6 | block 102, exit 28 -> 29 | 43,224,763 | 42,285,462 | -2.173% | 6287 -> 6154 |

The extended block remains on time in every arm.  Its small occupancy cost
changes the suffix construction and saves 59, 82, and 133 total tardy days on
P4-P6 respectively; P3 instead trades assignment terms at unchanged Z1.
P5 demonstrates why the production gate is load-bearing: 10 of the fixed 20
lowest-raw actions were checker-invalid, while the selected action needed no
rescue and replayed exactly.

Both P4 and P5 independently exceed the preregistered 5% edge.  The frozen
verdict is `advance_voluntary_stay_rollout`.  This is a causal construction
result, not a solver score: the action came from an exhaustive public-board
scan and has not yet faced the ALNS plateau, disjoint seeds, or Train.  Full
reports are frozen under `results/voluntary-stay-rollout-74969/`.
