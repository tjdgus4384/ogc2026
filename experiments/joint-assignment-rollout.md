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
The baseline is the lowest production-gated objective among all six shipped
orders, not the lowest raw kernel score.  After the null passes, the same gate
scores the 20 lowest-raw alternative actions and selects their lowest gated
objective.  Twenty is the frozen kill-test vocabulary; a survivor requires a
separate exhaustive/beam evaluator before adoption.

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

Job 74922 exited before launching Python because the first harness version
assumed that a linked worktree duplicated the canonical checkout's
`.miniforge` environment.  It produced no measurement.  The harness now
resolves that environment through Git's common directory while keeping all
source and output artifacts in the persistent worktree.

## Causal-null verdict: job 74923

The corrected audit completed, and the existing `builds/pc2` instrument fails
the same-bay null on every board:

| board | byte-identical / tested | same-bay raw delta range |
|---|---:|---:|
| P3 | 155 / 200 | -7.11% .. +59.27% |
| P4 | 120 / 150 | -8.48% .. +13.68% |
| P5 | 167 / 200 | -6.42% .. +21.04% |
| P6 | 207 / 250 | -1.39% .. +5.85% |

All four reports have `interpretation_valid=false`; the harness consequently
ran no checker comparison for an alternative action.  The apparent best raw
deltas (-7.11%, -16.98%, -8.92%, and -2.59%) are explicitly invalid and must
not appear in an advance or adoption claim.

The direct cause is in `insertGreedy`: a forced block constructs a one-element
bay vector *before* the normal sort/shuffle.  `std::shuffle` then consumes a
different RNG stream from the free baseline, changing orientation order and
every later insertion.  The next instrument must always construct and
sort/shuffle the complete bay vector, then filter it to the forced bay.  Job
74923 is a diagnosis of the old instrument, not a candidate score.

The repaired path is diagnostic-only and defaults OFF in production.  The
harness explicitly sets `OGC_CAUSAL_BAY_RNG=1`; with that switch,
`insertGreedy` performs the full-vector sort/shuffle and only then filters.  A
new isolated build must still prove all same-bay controls byte-identical.  The
code change alone is not accepted as proof.

## Repaired-instrument attempt 74932

The causal RNG repair passed every measured same-bay null: P3 200/200, P4
150/150, P6 250/250.  Those cells also produced checker-feasible apparent
improvements, but the job is not the final kill-test verdict.  P5 selected the
lowest *raw* baseline order, whose exact-touch board remained checker-invalid
after the production rescue ladder; the process failed before writing P5's
null report.  This exposed a second asymmetry: candidate actions were gated by
the checker while the seed-order choice was made by the kernel alone.

The next rerun selects the baseline by the checker-authoritative objective
across all six orders and screens the fixed top-20 candidate vocabulary through
the same gate.  No P3/P4/P6 delta from 74932 advances the route until that
four-board rerun completes.

## Causal construction verdict: job 74933

The symmetric rerun completed on the frozen `builds/jasg-causal` artifact
(`SOURCE_COMMIT=e19c7510...`, binary SHA-256 `37d650...`).  Every same-bay
intervention reproduced its free baseline byte-for-byte, so the score changes
below are causal assignment effects rather than new RNG draws.

| board | checker-selected order | baseline | one forced decision | gated delta | null |
|---|---|---:|---:|---:|---:|
| P3 | noisy_a | 539,249 | block 197 -> bay 0: 501,605 | -6.981% | 200/200 |
| P4 | due_long | 8,040,292 | block 13 -> bay 2: 6,674,831 | -16.983% | 150/150 |
| P5 | due | 20,111,373 | block 57 -> bay 1: 18,296,095 | -9.026% | 200/200 |
| P6 | noisy_a | 43,224,763 | block 183 -> bay 1: 42,103,320 | -2.594% | 250/250 |

All four selected boards were checker-feasible and replay-exact.  P5 is also a
useful warning against kernel-only selection: seven of its fixed top-20 raw
actions were rejected by the checker.  The checker-authoritative screen, not
the raw reconstruction score, selected the reported survivor.  The frozen
advance rule is met independently on P3, P4, and P5.

This is not a solver result.  The action was found exhaustively on each public
board, and historical law F4 says a better seed commonly washes out under
ALNS.  Historical hard-lock results also show that a constraint can preserve a
decision while worsening the reachable basin.  Those two effects must be
separated before spending a full driver budget on a sequential selector.

## Frozen plateau-transfer kill test

Job 74933's four JSON files are the immutable action manifest.  On the same
causal build with `OGC_CAUSAL_BAY_RNG=1`, replay the recorded order and action
and run three arms for exactly 20,000 single-thread ALNS iterations at seeds 7,
11, 23, and 41:

1. `baseline`: the recorded free construction with no lock;
2. `start`: the causal construction, releasing the lock before ALNS; and
3. `lock`: the identical causal construction, retaining only the selected
   block-to-bay lock throughout ALNS.

Source audit after launch found that the shipped ALNS intentionally clears
`forcedBay` at 60% of its budget.  Therefore the first job's third arm is a
60%-guidance diagnostic, not the persistent `lock` arm described above; its
score cannot decide the hard-lock gate.  Baseline and `start` are unaffected.
A default-off `OGC_KEEP_ASSIGNMENT_LOCK=1` audit switch must be frozen in a
new build, and only a rerun under that switch may populate the lock column.
That rerun also repeats all 16 baseline arms on the new build.  Their starting
hash, final hash, objective, ALNS statistics, audit, and checker result must be
exactly equal to the original baseline before old `start` and new `lock` rows
may be joined.  Timing is deliberately excluded from this A/A identity test.

Job 74949 showed that suppressing only the 60% release is not a persistent-lock
instrument: all four P3 lock arms ended with block 197 back in bay 2 rather
than its required bay 0, so the harness failed before writing those rows.
This was not a new unknown mechanism.  The repository's earlier lockprobe had
already enumerated six leaks: unlocked seed replay, mid-run release, the
ops-8/9 temporary vector, their target-block override, API single-block
overrides, and direct atomic swap.  The first audit build closed only the
second.  No score from job 74949 is a hard-lock verdict.  A valid rerun must
port the already-proven default-off guards for every path exercised by the
harness and must again fail closed on any final bay mismatch.

The discovered 60% release is also a distinct, production-relevant treatment,
not merely a failed instrument.  A fourth `guide60` arm starts from the same
causal bytes, holds the single decision for the shipped first 60% of ALNS, and
then releases it.  It is rerun from the original frozen causal build and joined
to the original baseline/start rows by exact initial hash.  Retaining the bay
at the end is recorded but not required.  `guide60` uses the same effect-size
and checker thresholds as `start` and persistent `lock`; it cannot borrow a
win from either arm.

## Start-only verdict: job 74943

All 16 baseline and all 16 start-only searches completed before the invalid
lock arm made the Slurm job fail closed.  The start comparison itself remains
valid: every pair used identical ALNS seeds and iteration counts, and only the
recorded causal starting bytes differed.

| board | informative | W/L/T | median delta |
|---|---:|---:|---:|
| P3 | 4/4 | 1/3/0 | +3.378% |
| P4 | 4/4 | 3/1/0 | -2.746% |
| P5 | 3/4 | 3/0/0 | -7.867% |
| P6 | 4/4 | 3/1/0 | -1.605% |

The 15 checker-informative pairs pool to 10 wins, 5 losses, and median
-2.431%, but the frozen gate rejects the arm: P3 exceeds the +2% board veto,
and P5 seed 11 remains checker-infeasible after the production rescue ladder.
This is another direct F4 result.  A strong construction effect and even a
favorable pooled median do not authorize using the causal board as just one
more unlocked seed.

## 60%-guidance verdict: job 74948

The production-style guidance arm replayed the same causal bytes on the
original frozen build, held the selected bay for the first 60% of each search,
and then released it.  Its paired reference is the immutable job-74943
baseline; every pair used the same seed and exactly 20,000 iterations.

| board | informative | W/L/T | median delta |
|---|---:|---:|---:|
| P3 | 4/4 | 2/2/0 | +2.241% |
| P4 | 4/4 | 4/0/0 | -4.452% |
| P5 | 3/4 | 3/0/0 | -5.544% |
| P6 | 4/4 | 3/1/0 | -2.332% |

The 15 checker-informative pairs pool to 12 wins, 3 losses, and median
-2.801%; the selected action remained in the final engine state in 9 of 16
arms.  Nevertheless the frozen gate rejects this treatment.  P5 seed 11 was
checker-infeasible, and P3's +2.241% median exceeds the +2% board veto.  The
correct scope is narrow: temporary guidance helps the public P4-P6 oracle
actions more often than start-only seeding, but a board-tuned single action is
not a universal, checker-safe policy.  It does not advance to Train or enter
the production solver.  The complete row-level result is frozen in
`results/assignment-guidance-74948.json`.

## Frozen sequential construction gate

The failed start-only form does not erase the construction mechanism: P4/P5
still show a plateau signal, and a single forced decision changes 47/64 later
bay assignments respectively.  The next construction-only question is whether
more causal decisions accumulate without accepting assignment debt.

Start from each immutable job-74933 one-action manifest.  Run a beam of width 2
for at most two additional decisions.  For every beam parent, exhaustively set
one previously-free block to each bay and rebuild the full board with the same
order and RNG-preserving causal instrument.  Setting that block to its current
parent bay is a semantic null; every such null must reproduce the parent bytes
and components exactly.  Across all parents, the official gate screens the
fixed 20 lowest-raw children.  A child enters the beam only when its checker
objective strictly improves its own parent; parents are carried, so the best
board can never regress.  Duplicate lock vectors are removed.

This is the same board-independent action rule on P3-P6.  The construction
axis advances only if all nulls and final boards are checker-valid, no board
regresses from its one-action parent, and P4 or P5 gains at least another 5%.
A survivor is still only a constructor: its final action sequence must face a
new fixed-iteration plateau test, then a native 4-core/budget-capped
implementation and all declared Train splits.  Per-board action sequences are
never production constants.

The `start` and `lock` states must have the same byte hash before search, every
run must execute exactly 20,000 iterations, and the lock arm must finish with
the selected block in the selected bay.  The official checker and production
gate are authoritative; all 48 arms must be feasible.

Each treatment is judged against its paired baseline.  It passes only with a
pooled median at most -2%, more wins than losses, at least one P3/P4/P5 board
median at most -3%, and no board median above +2%.  A passing `start` arm
supports a universal action-selector/seed route; a passing `lock` arm supports
a persistent steering route.  Neither authorizes the per-board oracle actions
in production.  A survivor still needs a budget-capped sequential/beam action
selector, equal-budget full-driver validation, and the declared Train splits.
