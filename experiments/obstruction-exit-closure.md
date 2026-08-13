# Obstruction-triggered exit closure

## Corrected structural rule

Let `B(i,j)` mean that resident block `j` obstructs moving block `i` at the
fixed positions.  For a collision-free one-way pair with `B(i,j)=1` and
`B(j,i)=0`, coexistence requires the obstructer `j` to be time-nested inside
the obstructed block `i`:

```
e_i <= e_j  and  f_j <= f_i.
```

The exit that must grow is therefore `f_i`, the obstructed outer/wrapper, not
`f_j`, the inner obstructer.  When `e_i <= e_j < f_i < f_j`, the componentwise
minimal repair is exactly `f_i := f_j`.  It is not `f_j+1`: on the common exit
day the inner block exits first and then the outer block exits.  Extending an
obstructer makes `f_j <= f_i` harder and is the wrong direction.

For fixed entries and positions, repeat this update to a least fixed point.
An extended outer may cross another entry day and activate another pair, and
an inner exit may itself grow, so both newly active pairs and reverse
dependencies must be revisited.  Reject a state that needs coexistence for a
same-layer collision, a two-way obstruction, or an entry-order inversion.
Same-entry one-way arcs must be acyclic.  Apply the complete exit vector at
once and validate the whole bay; rejecting intermediate states would discard
a closure that becomes legal only after all parents reach the same boundary.

The only structural target days are actual inner exits and their transitive
maximum.  `D_i` is an objective kink, not a feasibility event.  Half-slack,
due-date, problem id, size, and a random firing frequency are not part of this
mechanism.

## Why the old timing conclusion is invalid

The historical `archive/misc/patches/exsched.py` fixed
`f_i = e_i + P_i`, omitted the symmetric EXIT condition, and did not represent
the selectable same-day operation order.  Its claim that the incumbent timing
was globally optimal at fixed geometry therefore applies only to a stricter,
incompletely encoded model.  It is not an official-model lower bound.

`tools/flexible_time_closure.py` replaces that audit.  It freezes bay,
orientation, and integer `(x,y)`, derives same-layer and both directed crane
relations through the official checker geometry, and compares:

1. `fixed`: `f-e=P`;
2. `flex`: `f-e>=P`.

For a one-way pair the CP chooses exactly one of outer-before-inner,
inner-before-outer, or inner-nested-in-outer.  A global entry key makes a
same-day nesting direction strict and rejects entry cycles; exits are emitted
in reverse entry-key order.  Every candidate must pass `solver/utils.py`.
`OPTIMAL` and `FEASIBLE` statuses remain distinct, and the checker result, not
the CP value, is the feasible incumbent authority.

This fixed-shell audit is diagnostic, not an ALNS kill gate.  A zero flexible
gain says the current coordinates have no direct timing gain.  It does not
refute a closure that changes which coordinates the remaining repair suffix
chooses.

### Frozen first audit (job 75171)

Run the deterministic one-worker fixed/flex comparison for 60 seconds per
model on the four checker-feasible archived boards in
`archive/best-boards-2026-08-04`, P3--P6.  Use Titan array `3-6%4`, seed 1,
three checker/no-good rounds, and no fitted instance dispatch.  Source hashes
at submission were:

- checker `solver/utils.py`: `d45aaeaf...d4876a94`;
- tool: `7d2f7229...60bb0a81`;
- P3--P6 boards: `824b9f90...b5dfbc`, `6ca5aed7...f91c7e`,
  `3a44e1e...546b9`, `e0997d4f...31452`.

Classify each board as positive only when a checker-feasible flex solution has
lower Z1 than the certified fixed optimum and uses at least one non-minimum
stay.  Equal certified optima are direct fixed-shell zero.  A timeout or a gap
between bound and verified incumbent is unresolved.  Do not use a positive
block id or target day to tune the ALNS trigger.

### First-audit result

Jobs 75170/75171 completed with every emitted incumbent checker-feasible.  P3
and P4 were solved to optimality in both models and tied exactly: Z1 `0=0` and
`149=149`.  P5 and P6 reproduced Z1 686 and 4,010 in both models but retained
a zero lower bound after 60 seconds, so both are unresolved rather than zero.
The verdict is `fixed_shell_zero_P3_P4_unresolved_P5_P6`: no direct timing gain
was observed on these shells, while the geometry-changing ALNS hypothesis
remains open.  Frozen sources, solutions, summaries, logs, hashes, and Slurm
receipts are under `results/flexible-time-closure-75171/`.

## Frozen ALNS delivery gate

The treatment is default-off and the existing normal repair remains an
immutable candidate.  During that normal scan, observe a candidate rejected
because of a one-way EXIT obstruction even if `insertGreedy` later finds a
legacy placement and the whole normal repair succeeds.  Requiring whole-repair
failure would make the mechanism effectively dormant: job 75161 had no normal
failure rescue, and the older failure-rate audit found ordinary insertion
failure near zero.  The estimand is the earlier rejected geometry versus the
later normal first fit.

In scan order, inspect at most eight bbox-overlap rejected candidates in a
neighbourhood.  The first pure one-way raw witness owns the single closure
certification attempt whether certification succeeds or fails; do not search
for a later successful witness.  Phase 3 may build at most four treatment-only
same-layer snapshots before that raw witness.  These are computational bounds,
not random firing rates or fitted instance thresholds.  A certified witness
must strictly increase at least one outer exit to an actual inner exit.

Calculate its minimal transitive exit closure, then replay the suffix once in
a disjoint solution, occupancy cache, and RNG state.  Thus hard invariants are
`closure_attempts <= eligible_neighbourhoods` and
`extra_suffixes <= eligible_neighbourhoods`.  There is no due-date cap: a paid
closure is selectable only when the completed repaired suffix has a strictly
smaller true weighted objective as well as a smaller search objective.  Every
tie belongs to normal.

The normal branch owns future randomness.  Couple treatment acceptance to the
same uniform draw the completed normal repair would consume.  If closure turns
a non-improving normal candidate into an improving one, consume the normal
dummy draw before the forced acceptance.  A rejected, failed, tied, or
regressing closure must therefore be a semantic null in solution, `byBay`,
occupancy bits, floating area cache, RNG, anneal state, and common statistics.
No speculative call may mutate live objective flags.  Because closure can
extend a resident outside the destroy set, its Sol and occupancy cache stay
branch-local through the acceptance decision; copying it live and relying on
the ordinary removed-block rollback is forbidden.  Failure closes only this
bounded first-witness delivery, not the whole exit variable.

Phase 3 needs a treatment-only observation path for candidates rejected by
the crane-aware `occ::hits` policy but not by the same-layer snapshot.  The
default-off path must retain the existing `continue` byte-for-byte.  A closure
is official-order feasible only when the whole bay passes the flexible EXIT
oracle and the emitter uses the corresponding entry and LIFO exit ranks.
Ordinary suffix insertions retain the conservative production oracle; only
the simultaneous closure state uses the flexible oracle.  This makes unrelated
flexible-only suffix placements structurally impossible.  The experimental
emitter uses exact ranks only when the returned incumbent still contains a
strict overlong stay; OFF and semantic-null outputs retain legacy ordering and
hashes.

Telemetry is aggregate and bounded: candidates inspected, one-way raw
witnesses, closure attempts/built, same-entry cycles, two-way/rest rejects,
phase-3 windows/policy witnesses, budget hits, suffix tried/built, repair
selected, SA accepted, new-best retained, extended blocks/days, paid tardy
days, true local gain, detector/suffix time, wall time, and peak RSS.

### Fixed-iteration plateau test

Use P3--P6 and seeds 7, 11, 23, 41.  Run OFF and ON for 20,000 single-thread
iterations from the same checker-feasible starts, recording a read-only
iteration-1,000 checkpoint and the final incumbent.  The 1,000 checkpoint is
activity/transient diagnosis only.  The final adoption gate is:

- every output checker-feasible; missing or invalid output is a failure;
- OFF replays the frozen start/final hashes, objectives, audit, and common
  statistics exactly;
- more paired wins than losses;
- pooled median delta at most `-2%`;
- P4 or P5 median at most `-3%`; and
- every board median at most `+2%`.

Classify `cert=0` as dormant, `cert>0/selected=0` as locally dominated, an
early-only pass as transient, and a final pass as a plateau mover.  Do not
discard infeasible pairs or relax thresholds after seeing them.

### Equal-wall and generalization tests

Only a plateau pass advances.  On disjoint seeds 3, 17, 31, 47, compare OFF
and ON in counterbalanced AB/BA order at official P3--P6 limits
240/480/600/900 seconds.  Require all 16 pairs feasible with no overrun,
pooled median at most `-1%`, P4/P5 combined median at most `-1%`, more wins
than losses, and no board median above `+2%`.  A fixed-iteration pass followed
by an equal-wall failure is a throughput illusion and is rejected.

Only both passes authorize `train_final`: all 40 instances, universal OFF/ON,
fixed-iteration seed 7, with no subset or per-instance dispatch.  Require
40/40 feasibility, at least 27 wins (exact sign-test `p<0.05`), median at most
`-1%`, worst regression at most `+10%`, and each predeclared split (`m<=3`,
`m>=4`, saturation below/above one) median at most `+1.5%`.  Hidden robustness
can be claimed only as proxy robustness until a new server submission is
measured.
