# Current solver and score authority

Machine-readable authority: `results/current-baseline-2026-08-13.json`.
Historical campaign tables are evidence for their own dates, not aliases for
the current solver.

Three states must remain separate:

1. The historical preliminary submission scored P3 87,140 / P4 3,259,259 /
   P5 11,089,821 / P6 32,104,939.
2. The last server-measured finals submission is v5.1 (2026-08-04).  Its eight
   finals inputs are different from preliminary P1-P6, so those objectives
   cannot be compared with the preliminary minima.
3. The local submission-ready artifact is v6.3, commit `83a344b` plus the
   comment-only correction `69f9206`.  It has not received a server response.

For v6.3 at preliminary official limits, the comparable local baseline is:

| board | all-team minimum | seed 7 | gap | 8-seed median | 8-seed best |
|---|---:|---:|---:|---:|---:|
| P3 | 64,695 | 78,705 | +21.7% | 78,710 | 78,705 |
| P4 | 1,908,043 | 2,879,770 | +50.9% | 2,926,072 | 2,730,819 |
| P5 | 7,718,486 | 10,864,216 | +40.8% | 10,645,653 | 10,146,205 |
| P6 | 25,452,695 | 28,154,533 | +10.6% | 28,341,017 | 28,154,533 |

The seed-7 column is the one-line local submission comparison.  The median is
the robustness description; the best is not a submission baseline.  In
particular, repo-wide best values from rejected candidate arms must never be
reported as the current solver's score.
