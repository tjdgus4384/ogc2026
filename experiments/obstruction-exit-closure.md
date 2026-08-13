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

### First-delivery result (commit `9002c73`, jobs 75426/75538--75540/75548/75617)

The frozen build and unit test passed, all 48 reference/control/treatment arms
completed, and the collector accepted every integrity contract.  All final and
iteration-1,000 checkpoint outputs were checker-feasible.  Reference and OFF
were exact, every OFF closure counter was zero, and the one ON cell with no
accepted closure was an exact semantic null at both checkpoint and final.

The final 16 paired deltas were 10 wins, 4 losses, and 2 ties.  Their pooled
median was `-1.9623787728%` and mean was `-1.5293210266%`.  Board medians were
P3 `-1.2569469693%`, P4 `-3.0989571717%`, P5 `-1.6219754017%`, and P6
`-1.1015045338%`.  Thus the sign condition, the P4/P5 condition, and all board
vetoes passed, but the preregistered pooled `<= -2%` condition missed by
`0.0376212272` percentage points.  The verdict is therefore
`reject_first_witness_exit_closure`; the threshold is not relaxed and the
equal-wall gate is not authorized.

This is a rejection of the bounded first-witness delivery, not of flexible
exit times.  The treatment made 93,016 structural attempts, built 27,833
closures, selected 6,155 repaired suffixes, and accepted 1,817, but only 50
accepted closure transitions were immediate new incumbents.  No phase-3 seed
or failed-insertion rescue fired.  Median slowdown was `1.1321x`, while the
worst paired regression was P3 seed 23 at `+7.408%`.  The next hypothesis must
therefore reduce indiscriminate speculative delivery or strengthen the
transition criterion without fitting an instance, seed, firing frequency, or
post-hoc threshold.  The first-witness arm itself must not be wired into the
production portfolio.

The checker-authoritative plateau states, all arm reports, aggregate summary,
and a provenance receipt are frozen under
`results/obstruction-exit-closure-9002c73/`.

## Successor: control-preserving closure archive

The first-delivery data identify live-trajectory substitution, not closure
feasibility, as the next defect.  Of 1,817 accepted closure transitions only
50 became an immediate incumbent.  P3 seed 23 accepted 15 closures but none
became an incumbent; ON stayed at its iteration-1,000 value while OFF later
escaped to a board 7.408% better.  P4 seed 11 also shows that allowing only an
immediate incumbent into the live walk is insufficient: its sole accepted
closure was a new best, yet its final result still lost.

The successor therefore treats closure as a sidecar incumbent oracle, never
as a Markov transition.  The normal repair, its acceptance decision, RNG,
destroyer scores, temperature, `sinceBest`, live solution and occupancy, and
the incumbent used by return-to-best remain exactly the OFF trajectory.  A
fully replayed, officially audited closure suffix may update a disjoint archive
using true objective only.  At termination the returned board is the better of
the OFF incumbent and the archive; the OFF incumbent is separately exposed for
trajectory comparison.  The archive must not seed a later neighbourhood,
checkpoint, restart, or portfolio round.

For the fixed-iteration experiment this creates the hard dominance contract

```
J(returned ON) = min(J(exact OFF incumbent), J(closure archive))
               <= J(exact OFF incumbent).
```

Any paired regression, difference in the exposed OFF incumbent, checkpoint,
common statistics, or RNG-derived trajectory is an implementation failure,
not sampling noise.  The existing live first-witness arm remains available
only to replay its rejected evidence; archive mode is a new default-off,
mutually exclusive experimental flag.

The archive keeps the same official one-way rule and first raw witness so this
test changes one causal factor only: feedback into the live walk.  A sound
partial-suffix lower bound may skip replay when the already placed blocks'
nonnegative tardiness and preference cost cannot beat either incumbent.  The
bound may not include a positive load-balancing term, assume future tardiness,
or use an instance, seed, size, elapsed time, or firing-frequency threshold.
Its prune decisions and time are counted.

### Frozen validation ladder

The already observed P3--P6 seeds 7, 11, 23, and 41 are development data only.
A synthetic unit must force an archive return, and consumed P3 seed 23 must
prove that the internal OFF checkpoint/final trajectory still reaches 100,770
even if the returned ON board differs.  At least one consumed P4/P6 cell must
exercise an archive update.  These checks establish implementation activity,
not efficacy.

After that smoke test, freeze source, binary, checker, harness, caps, all seeds,
and thresholds.  The held-out plateau gate uses evaluation seeds 3, 17, 31,
and 47.  To remove treatment-dependent start selection, map them in that order
to the four already frozen checker-authoritative plateau states 7, 11, 23, and
41 for each board.  The mapping is fixed before any archive run.  A frozen
`9002c73` reference, candidate OFF, and archive ON each run 20,000 single-thread
iterations with an iteration-1,000 read-only OFF checkpoint.

All 48 raw outputs must be present and checker-feasible without rescue.
Reference and candidate OFF must agree exactly.  ON's separately exposed OFF
checkpoint, final flat/hash/objective, common statistics, and audit must also
agree exactly with reference, regardless of which board ON returns.  Archive
counters obey the existing candidate/attempt/suffix caps; OFF counters and
timing are zero.  Exact exit ranks are used only when the returned archive has
a strict overlong stay.

The efficacy thresholds are not weakened after the near miss: more wins than
losses, pooled median at most `-2%`, P4 or P5 median at most `-3%`, and every
board median at most `0%`.  The last condition is stricter than the prior `+2%`
veto because archive dominance makes a fixed-iteration regression impossible.
A pass advances the unchanged binary to equal-wall testing on disjoint seeds
59, 73, 89, and 101 at 240/480/600/900 seconds, counterbalanced AB/BA on four
cores.  That gate retains pooled median `<=-1%`, combined P4/P5 median
`<=-1%`, `W>L`, every-board median `<=+2%`, 32/32 raw feasibility, no timeout
or overrun, and measured RSS.

Only both public gates authorize the universal 40-instance `train_final`
paired test at fixed-iteration seed 113.  It retains 40/40 feasibility,
`W>=27`, pooled median `<=-1%`, worst regression `<=+10%`, and medians
`<=+1.5%` for each predeclared `m<=3`, `m>=4`, saturation `<1`, and saturation
`>=1` split.  Any code or rule change consumes the remaining held-out seeds
and requires a new candidate and new data split.

### Archive result (source `1dcdef0`, jobs 75644/75642/75731/75785)

The synthetic run-level unit forced a real archive return and proved exact
OFF/ON equality of the live solution, occupancy, checkpoint, common counters,
and full serialized RNG state.  The held-out gate then completed all 48 raw
arms.  The fail-closed collector accepted every pinned source, binary,
problem, plateau, checker, emitter, telemetry, and dominance contract with
zero mismatch.  This separates efficacy failure from implementation failure.

The returned archive produced **3 wins, 0 losses, and 13 ties**.  The pooled
median delta was `0.000%` and the mean was `-0.0116269%`.  Board medians were
P3 `0.000%`, P4 `0.000%`, P5 `0.000%`, and P6 `-0.0296221%`.  The three strict
wins were only `-0.0665818%` on P5 and `-0.0602044%`/`-0.0592442%` on P6.
Although 81 intermediate archive updates occurred across P4--P6, P3 had none
and only three final archives beat the exact control incumbent.  Median
slowdown was `1.11836x`.

The preregistered `W>L` and non-regression conditions pass, but pooled median
`<=-2%` and P4-or-P5 median `<=-3%` fail decisively.  The frozen verdict is
`reject_closure_archive`.  No equal-wall, Train, production wiring, threshold
relaxation, or frequency/board selector is authorized.  Flexible exit remains
a sound auxiliary timing primitive, but a single repaired suffix is not a
large enough neighbourhood to address the target gap.  The next mechanism
must jointly change a multi-block assignment/timing/geometry shell rather than
repeat this one-witness delivery more often.

All 16 plateau states, 48 raw reports, the pinned aggregate summary, and the
run/collector provenance are frozen under
`results/obstruction-exit-archive-d77d188/`.
