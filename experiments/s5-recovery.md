# S5 rollout budget-claimant recovery

## Why this gate is live

docs/NEXT_SOLVER.md requires S5 after the S3 rollout seed was washed out by
the existing driver (F4). S2 found a deterministic rollout edge at 8/8 P4,
8/8 P5, and 1/8 P6 decisions; S3 improved the raw P4/P5 constructions by 9.57%
and 14.12%. Treating that board as one more start was refuted, but the specified
driver-replacement test was never completed: job 74298 was user-cancelled after
3m37s, produced no cell output, and has no ledger verdict.

The original S5 harness also violated its own equal-budget claim. Control paid
for seed construction outside its 400-second budget and then received a full
400-second portfolio, whereas the treatment subtracted rollout construction
from 400 seconds. The recovered harness times both arms from before
construction, gives the portfolio only the remaining budget, and scores the
production-gated checker result. The S3 decision rule itself is unchanged.

## Frozen first screen

The pre-existing cells are retained:

- P4 seed 7, 400 seconds, rollout share 0.15;
- P5 seeds 7 and 11, 400 seconds, rollout share 0.15;
- P5 seed 7, rollout share 0.30 as a non-pooled shape diagnostic.

The primary three 0.15 cells advance only if at least two improve, their median
delta is at most -3%, all outputs are checker-feasible, and neither arm exceeds
the common budget by more than gate overhead. The 0.30 cell cannot rescue a
failed primary screen. Passing triggers disjoint seeds and train_final;
failing closes S5 rather than spawning new share constants.

## Verdict: job 74950

The equal-budget rerun finished all independent processes and Slurm correctly
returned failure because two primary outputs were not checker-feasible.

| cell | control | candidate | delta | checker result |
|---|---:|---:|---:|---|
| P4 s7, 0.15 | 3,027,564 | 3,247,336 | +7.259% | both feasible |
| P5 s7, 0.15 | 10,924,222 | -- | -- | candidate rejected |
| P5 s11, 0.15 | -- | not run | -- | control rejected |
| P5 s7, 0.30 diagnostic | 10,815,920 | 10,306,196 | -4.713% | both feasible |

The primary screen has zero valid improvements, includes two checker failures,
and the sole valid primary comparison regresses.  The non-pooled 0.30 diagnostic
cannot rescue it by preregistration.  S5 is closed: there is no share sweep,
independent replication, or Train run.  The four raw process logs and a compact
verdict are frozen in `results/s5-recovery-74950/`.
