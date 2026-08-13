# Voluntary-stay repair rollout kill test

## Mechanism

The construction screen proved that voluntarily retaining an already-feasible
prefix block can change the geometry selected for the remaining suffix.  The
fixed-start plateau gate then rejected that delivery form: the effect survived
on P3/P4/P6 but reversed on P5.  This test moves the same decision to the point
where the suffix is repeatedly rebuilt: one ALNS destroy/repair transition.

For ordinary destroy operators 0--7 and non-big-Q neighbourhoods, retain the
normal repair as an immutable candidate.  From the identical destroyed state
and identical pre-repair RNG, enumerate every repaired prefix block that has
positive exit slack.  For each such block, try the same two absolute targets as
the construction proof: halfway through its remaining slack and its due date.
After the extension, rebuild the complete remaining repair suffix.  The full
penalized objective is evaluated only after the neighbourhood is complete.

The normal repair wins every tie.  A stay branch can be selected only when it
strictly improves that completed neighbourhood, or when it completes a repair
that the normal branch could not complete.  Speculation never owns the future
RNG stream: after comparison, the engine restores the RNG state produced by
the normal branch.  A failed or non-improving enumeration is therefore a
semantic null in both solution and future randomness.  Extension is limited to
the chosen block's due date, consumes no RNG, and is accepted only after that
block and every current bay mate pass the unchanged kernel feasibility oracle.

Operators 8/9 retain their existing forced-bay semantics and big-Q repairs are
not branched in this first screen.  The shipped portfolio never enables this
instrument; it is exposed only as a default-off `run_alns` argument.

## Frozen short screen

Start every arm from the same free construction and order frozen by job 74969.
For P3--P6 and ALNS seeds 7, 11, 23, and 41, run exactly 1,000 single-thread
iterations with the full ten-operator mask and the same annealing parameters as
the plateau tests.  Run three arms:

1. `reference`: immutable `builds/jasg-stay-rollout` with no new API argument;
2. `control`: the new build with stay rollout disabled; and
3. `stay`: the new build with exhaustive local stay rollout enabled.

All 16 control trajectories must reproduce the reference start/final hashes,
objectives, audits, checker results, and common run statistics exactly.  Every
one of the 48 outputs must pass the production checker; missing outputs are
failures.  Report rollout neighbourhoods, branches tried/built/selected,
normal-failure rescues, aggregate local objective gain, and wall slowdown.

This mechanism screen advances only if:

- P4, P5, and P6 each select at least one completed stay branch;
- stay has more paired wins than losses;
- the pooled paired median is at most -1%;
- the combined P4/P5 paired median is at most -1%; and
- no board median exceeds +2%.

Passing authorizes a sparse-frequency wall-cost gate on disjoint seeds.  It
does not authorize production or Train yet.  Failure closes this exhaustive
one-stay local suffix transition; it does not revive construction starts,
public action tables, or persistent exit locks.
