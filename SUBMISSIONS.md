# Submission ledger

Every shipped zip + its full-40 result snapshot. Rule: **never re-run a prior strategy to compare** —
read its frozen snapshot in `results/`. Build a submission only via `python3 tools/ship.py --tag <name>`
(writes `submissions/<date>_<tag>.zip`, freezes results, appends a row here).

| date | tag | zip | results snapshot | notes / score |
|------|-----|-----|------------------|---------------|
| 2026-06-21 | overnight_best | — | `results/2026-06-21_overnight_best.json` | aggregate moving-best frozen (hours of compute). NOT a real per-submission record — earliest clean reference we have. Per-submission records before this date were never saved (file-management gap, now fixed). |

## Convention
- `submissions/<date>_<tag>.zip` — the actual shipped artifact (built from `solution/dev/` at ship time).
- `results/<date>_<tag>.json` — that build's full-40 eval (obj + Z1/Z2/Z3 per instance), frozen forever.
- Compare any two: `python3 tools/compare.py --tag <new> --vs <old>` (both read from `results/`, no re-run).
- The leaderboard score, once known, goes in the notes column.
