# Fixed-shell flexible-duration audit — jobs 75170/75171

Parent source commit: `fabb764620773e62355843bd1c0cf1d5cc2704e0` plus the
exact uncommitted tool copies frozen in this directory.

The two-block direction/counterexample test ran on Titan as job 75170 and
completed `0:0` in one second (`2 passed`).  Array job 75171 used tasks 3--6,
one deterministic CP worker per task, 60 seconds per fixed/flex model, seed 1,
and the official checker at `solver/utils.py` SHA-256
`d45aaeafdce8bf80d59d097f655c43313a4951bed43b6628e3b1cf62d4876a94`.
All four tasks completed `0:0`; elapsed times were 4s, 21s, 2m02s, and 2m03s.
Peak recorded batch RSS was 113,092 KiB.

Exact submitted sources:

- `flexible_time_closure.py`:
  `7d2f7229dc456112ae31e4fa585eb8b7b6f1f49fed4439ee1664a5d660bb0a81`
- `flexible_time_closure.sbatch`:
  `d69b6d727d17730ae7a1d8dd27514a76de59383f9f1f72ef40919ec4339437ba`
- `test_flexible_time_closure.py`:
  `b47a186888645639ad19f3efcc1f88ac221317749e1c480583b9c0ef7a45de2c`

Summary-file hashes:

- P3: `af093dbc6ac3150b71ede832161a28916842ba659a15a20c2b6af41c648bb165`
- P4: `a1ee6739e427ec7a836a3358dfbe0cb40a46f4b59a8c5817945abffa5175c1c9`
- P5: `4f7f93813bb046638cdab98879b9e2b6c54fd7641d063923e4703ee77914815f`
- P6: `2517bd54414743478f0d32e14ae578b0b06abd523788675e95aee66b9cc435dd`

Before adding this provenance file, the SHA-256 of the sorted per-file
SHA-256 listing for the frozen directory was
`515df14e3855a3f368cd46899a84c48e6108b81bf56b6736c8107b1ad3498922`.

## Result

| board | hard-disjoint | one-way | fixed | flexible | official result |
|---|---:|---:|---:|---:|---|
| P3 | 1,002 | 85 | OPTIMAL Z1=0 | OPTIMAL Z1=0 | both feasible, equal 76,230 |
| P4 | 375 | 16 | OPTIMAL Z1=149 | OPTIMAL Z1=149 | both feasible, equal 2,643,869 |
| P5 | 426 | 25 | FEASIBLE Z1=686, LB=0 | FEASIBLE Z1=686, LB=0 | both feasible, unresolved |
| P6 | 1,197 | 123 | FEASIBLE Z1=4,010, LB=0 | FEASIBLE Z1=4,010, LB=0 | both feasible, unresolved |

The frozen v1 CP minimized tardiness only, so it may return gratuitous
zero-tardiness exit extensions when scores tie.  Its `extended_blocks` count
is therefore not a canonical minimum-extension statistic and is not used to
select blocks or tune a repair.  The certified conclusions depend only on Z1,
status, and the official checker.

Verdict: `fixed_shell_zero_P3_P4_unresolved_P5_P6`.  There is no observed
direct re-timing gain on these four shells.  This does not reject an
obstruction-triggered repair whose closure changes the geometry selected for
the remaining suffix.
