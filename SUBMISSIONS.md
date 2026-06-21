# Submission ledger

Every shipped zip + its full-40 result snapshot. Rule: **never re-run a prior strategy to compare** —
read its frozen snapshot in `results/`. Build a submission only via `python3 tools/ship.py --tag <name>`
(writes `submissions/<date>_<tag>.zip`, freezes results, appends a row here).

| date | tag | zip | results snapshot | notes / score |
|------|-----|-----|------------------|---------------|
| 2026-06-21 | overnight_best | — | `results/2026-06-21_overnight_best.json` | aggregate moving-best frozen (hours of compute). NOT a real per-submission record — earliest clean reference we have. Per-submission records before this date were never saved (file-management gap, now fixed). |
| 2026-06-21 | entryaware-route | `submissions/2026-06-21_entryaware-route.zip` | (freeze at ship) | SHIPPABLE. Engine = entry-aware packer + size-adaptive: small Z1=0-reachable → cpsat_polish/cpsat_z3 (construct-basin) + asls(Z2); Z1-bound → brkga2 route. Clean-extract validated (16 files, all feasible, no crash). Spread @120s: mean +5.6% vs best, 7/10 at-or-below best; prob_20/34 NEW BEST, prob_3 stable 43250, prob_4 23575. |

## Convention
- `submissions/<date>_<tag>.zip` — the actual shipped artifact (built from `solution/dev/` at ship time).
- `results/<date>_<tag>.json` — that build's full-40 eval (obj + Z1/Z2/Z3 per instance), frozen forever.
- Compare any two: `python3 tools/compare.py --tag <new> --vs <old>` (both read from `results/`, no re-run).
- The leaderboard score, once known, goes in the notes column.
