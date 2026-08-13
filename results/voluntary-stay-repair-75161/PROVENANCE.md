# Voluntary-stay repair job 75161

- Candidate source: `96ccfd14fd60ba24dd2702d25e76bdcf9d495194`
- Candidate binary SHA-256: `642019203a8326ae5c3b5b199e98bfa57f239bc46c0a3c90d24b291ea7ff91c7`
- Immutable reference binary SHA-256: `be0d5ac41b9ab91b4354802221343c4a0b3b3a81fa3104fd2e9b7aa30f089728`
- Build job: 75054; smoke P1 11,280, P2 31,368, P3 86,895, all feasible
- Harness commit: `255bfa9`
- Diagnostic job: 75160, 100 iterations, not performance evidence
- Frozen kill-test job: 75161, 1,000 iterations
- Slurm job 75161: COMPLETED 0:0, elapsed 33s, TotalCPU 11:32,
  batch MaxRSS 720,016K
- Combined SHA-256 of the other 50 frozen files (sorted per-file digest
  stream): `12056dcf6d509c6f2a39bd12681c077fa419c7673cb989a0224a16dbf0c0f9a5`

The diagnostic established exact reference/control replay, 48/48 checker
feasibility, and 12 enabled-but-unselected semantic-null trajectories with no
mismatch.  Its 16 score pairs all tied; median treatment slowdown was 1.589x.

The frozen test's authoritative result is `summary.json`.  Reference/control
replay remained exact and all four observed semantic-null trajectories
matched.  Five P5 arms were checker-infeasible: reference/control/stay at seed
11 and reference/control at seed 23.  The stay arm made seed 23 feasible, but
the preregistered gate requires all 48 arms and 16 complete pairs.  The 14
comparable pairs all tied, so the verdict is
`reject_exhaustive_stay_repair` independently of that safety failure.
