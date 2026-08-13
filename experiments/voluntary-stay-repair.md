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

This exhaustive implementation is a mechanism ceiling, not the intended
delivery form.  It owns a second full solution/occupancy cache and rebuilds up
to two suffixes per eligible prefix, so any survivor must report peak RSS and
must later replace exhaustive firing with an endogenous structural trigger
(for example, normal-repair failure or an observed one-way obstruction whose
interval nesting can be closed).  Random frequency fitting is not sufficient
evidence for hidden robustness.

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

This first screen starts from the deliberately weak job-74969 free
construction (P4 8.04M and P5 20.11M).  Passing therefore establishes only
that the action is live and can accelerate an early trajectory; it must not be
read as evidence that the final search plateau moves.  Before any wall-cost
or sparse-frequency gate, replay the 16 checker-feasible 20,000-iteration
baseline plateaus frozen by job 75041, require their hashes to match, and run
the same paired 1,000-iteration reference/control/stay continuation from each
plateau.  Use the same selection-habitat, win/loss, pooled -1%, combined P4/P5
-1%, and per-board +2% veto above.  Only a second pass there authorizes design
of a structural trigger and an equal-wall, disjoint-seed cost gate.

Neither screen authorizes production or Train.  Failure of either closes this
exhaustive, zero-own-cost half/due one-stay transition; it does not revive
construction starts, public action tables, or persistent exit locks.  It also
does not close the full exit-time variable: a rule-driven successor may extend
an obstructing interval exactly to another block's exit boundary, including
past its own due date only when the completed repair's net weighted objective
strictly improves.  Such an exit-closure repair must be triggered by an
observed one-way obstruction, not by a fitted day or frequency sweep.
