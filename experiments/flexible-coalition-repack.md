# Flexible-coalition repack pre-registration

## Question and scope

The obstruction-exit experiments established two different facts.  A legal
`f-e>P` state can change the ALNS trajectory, but a single first-witness suffix
is too small: the live arm missed its frozen gate and the control-preserving
archive returned only three improvements of about `0.06%` in sixteen held-out
cells.  This document does not repeat that delivery at a different frequency.
It asks the next, deterministic question:

> When several costly blocks choose bay, entry day, orientation, integer
> geometry, same-day operation order, and exit duration jointly, does official
> one-way nesting open a materially better complete board than the identical
> fixed-duration shell?

The experiment is an offline sidecar.  It does not change `runPortfolio`, the
production emitter, or an ALNS trajectory.  A candidate has standing only
after a fresh Engine flexible audit and the unmodified official checker accept
its literal operation trace.

## What earlier zeroes do and do not prove

`repack2` used only three dates, a stride-two/capped grid, a P4-specific bay
order, saved tardy days rather than the full objective, and `f=e+P` in both its
conflict model and output.  The shipped K=25 slice first asks
`feasiblePositions`, which emits only minimum-duration, conservative-oracle,
earliest-per-bay candidates, and then applies the same fixed-duration
`pairConflicts`.  Those results close their stated fixed-duration vocabularies;
they do not contain a candidate that requires a resident wrapper to stay until
an inner block exits.

The new comparison shares one candidate menu, free set, objective, solver
seed, worker count, limits, and incumbent between its two arms.  It is a
paired test of duration/nesting semantics, not a claim of full-grid or global
optimality.

The authoritative public probe is hash-bound to the four preliminary
problems and all sixteen immutable plateau files at seeds `7,11,23,41`.  It
uses CP-SAT seed `1`, one worker, and exactly 60 seconds per arm.  The first
stop decision consumes seed 7 only.

## Frozen coalition and candidate menu

For a complete checker-authoritative incumbent with `n` blocks, let
`K=min(24,ceil(sqrt(n)))`.  Rank blocks by their current nonnegative personal
cost

```
w1 * max(0,f_i-D_i) + w3 * (Smax_i-S_i,bay_i)
```

then by decreasing tardiness, due day, and block id.  The first `K` form the
free coalition.  This uses objective data only; there is no problem id, seed,
saturation, elapsed-time, or learned dispatch rule.  Every non-free block has
exactly its incumbent `(bay,orientation,x,y,entry)` candidate.  Every free
block also retains that incumbent candidate, so the whole incumbent is an
explicit geometry/entry witness in both arms.  A minimum-duration incumbent
is also a literal timing witness in both arms.  If a checker-authoritative
source already has `f-e>P`, its literal timing is a FLEX witness while FIXED
contains the same geometry/entry projection with `f=e+P`; reports must expose
this asymmetry.

Additional entry days are the feasible, deduplicated event boundaries

```
R_i, D_i-P_i, incumbent_e_i,
e_k, f_k, e_k-P_i, f_k-P_i       for incumbent residents k.
```

This corrects a concrete search-vocabulary omission: ordinary repair samples
release, resident exits, and a coarse stride, but `e_k-P_i`/`f_k-P_i` are the
entry days at which the candidate's *exit* meets an event.  At each bay,
orientation, and event day, use integral bay/bounding-box corners and snapped
resident bounding-box contacts.  To bound raw enumeration before its score is
known, each `(block,bay,event)` call retains the first
`m*ceil(sqrt(n))` exact-rest-free rows in deterministic
`(orientation,y,x)` order.  Across those per-event prefixes, deduplicate and
keep at most `m*ceil(sqrt(n))` alternatives per free block after the incumbent,
ordered by the candidate's nonnegative immediate tardiness/preference cost and
then lexicographically.  These structural caps are frozen before score output
and identical in both arms.  Saturated-call counts, candidate hashes, and the
exact free ids are evidence fields; a negative certificate is conditional on
this prefix-bounded vocabulary.

The menu is deliberately bounded and therefore the only scope of a negative
certificate.  It is, however, broad enough to change bay, day, orientation,
and both coordinates together, unlike a one-witness exit repair.

All event generation, CP domains, and Engine audits share the inclusive bound

```
H = max(max_source_exit + max_processing,
        max_i(R_i+P_i),
        max_i(ceil(D_i))).
```

Every frozen event above has minimum exit at most `H`.  A least one-way
nesting closure only copies one of those exit endpoints, and any larger exit
can be reduced without hurting feasibility or the nonnegative primary
objective; the frozen secondary `sum(f)` also removes gratuitous slack.  Thus
`f==H` is legal and values outside the Engine's signed 32-bit range fail
closed.

## Exact pair semantics

Add a default-unused experiment API `pair_relations(flat)`, where rows are
`[block,bay,orientation,x,y,entry]`.  It returns only non-clear pairs and does
not filter by their current time overlap:

- `HARD`: same-layer collision or mutual crane obstruction;
- `A_OUTER`: B obstructs A, so B may coexist only nested inside A;
- `B_OUTER`: the symmetric relation.

Different bays, same-block alternatives, and geometry-clear pairs are omitted.
The existing production `pairConflicts` is unchanged.  Relation extraction
must bypass the lossy geometry memo and validate all indices/bounds
fail-closed.

For each block choose exactly one candidate.  FIXED enforces `f_i=e_i+P_i`.
FLEX uses integer `f_i>=e_i+P_i`.  For every selected same-bay pair:

```
HARD:    f_A<=e_B  OR  f_B<=e_A
A_OUTER: f_A<=e_B  OR  f_B<=e_A
                         OR (e_A<=e_B AND f_B<=f_A AND key_A<key_B)
```

with the symmetric formula for `B_OUTER`.  `key_i=e_i*(n+1)+rank_i`; keys are
unique, so an equal-entry nesting cycle cannot enter the CP solution.  All
incumbent singleton residents participate even if their original intervals
did not overlap, because an exit extension can activate a new relation and a
transitive wrapper extension.

The objective is the complete official objective, not saved-day count:

```
w1 * sum(max(0,f_i-D_i))
+ w2 * floor(max_j(u_j*load_j)-min_j(u_j*load_j))
+ w3 * sum(Smax_i-S_i,bay_i).
```

JSON numeric data are converted through exact rational strings and a common
integer scale.  The Z2 range and floor use exact integer division.  Any
non-finite value, denominator/LCM overflow, CP-SAT coefficient overflow, or
objective disagreement with the Engine/checker fails closed.  After fixing a
winning candidate vector and its official objective, a secondary minimization
of `sum(f_i)` removes gratuitous zero-cost stay slack before emission.

One CP-SAT worker and a fixed random seed are mandatory.  FIXED and FLEX get
the same independently enforced wall limit and deterministic search limits.
`OPTIMAL` is required for a negative conclusion.  A checker-certified
`FEASIBLE` improvement is valid positive evidence; FEASIBLE/UNKNOWN without
an improvement is unresolved, never a zero.

## Deterministic validation

Before public-board output, synthetic tests must cover:

1. a two-block one-way pair where the incumbent serial placement costs two
   tardy days, the early FIXED candidate is impossible, and FLEX wraps the
   inner resident to reach zero;
2. a three-block chain whose least exits require transitive propagation;
3. endpoint touch and `f==horizon` boundaries;
4. a same-entry directed cycle rejected by rank/order constraints; and
5. a two-bay workload case where saved-tardy-only ranking disagrees with the
   exact Z2-inclusive objective.

For every emitted leaf, reconstruct it in a fresh Engine, require complete
placement, release/duration/horizon validity, `audit_state(True)==[0,-1]`,
materialize `entry_ranks()` and `exit_ranks()`, and require the raw official
checker objective to equal the modeled objective.  No rescue ladder is used.

## Kill ladder and interpretation

Development inputs are the already consumed, checker-authoritative P3--P6
plateaus at seeds 7, 11, 23, and 41.  First run seed 7 on all four boards.  A
mechanism-positive result requires on P4, P5, or P6 a checker-certified FLEX
board at least `1%` below both the positive-valued incumbent and its
positive-valued paired FIXED result, with at least one strict `f-e>P` stay in
the emitted FLEX board.  This is an effect-size existence criterion, not an
optimality claim or an adoption threshold.

- If FIXED also beats the incumbent by `1%`, the new event/geometry coalition
  is separately live; do not attribute that part to flexible duration.
- If FLEX alone clears both `1%` margins, C5-prime is causally useful at the
  multi-block scale and the remaining twelve development plateaus may run.
- If both arms are OPTIMAL and tie the incumbent on all primary boards, close
  this bounded vocabulary.  Do not claim global flexible-duration optimality.
- If FLEX has no certified improvement but its bound can still clear the
  margin, record unresolved.  Only model/runtime engineering with unchanged
  candidates and thresholds is allowed; no cap, date, bay, seed, or margin
  sweep.

A development survivor freezes source, binary, candidate rules, limits,
checker, and a new disjoint seed mapping before any efficacy gate.  It then
enters the established order: fixed-iteration dominance against the unchanged
control, paired equal-wall validation, and only then all 40 `train_final`
instances with the structural split vetoes in
`experiments/generalization-protocol.md`.  No public-instance selector or
production wiring is permitted before those gates.
