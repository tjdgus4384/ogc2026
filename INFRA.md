# Infra cheatsheet

How to run, measure, compare, ship. State + plan = `notes/STATUS.md`. Problem truth = `notes/MODEL.md`.

## Layout
```
solution/dev/        production engine (13 .py + placer.c). Edit here.
solution/archive/    shelved-but-revivable code (mcts, polygon, unified_cpsat).
solution/baseline/   competition-provided (utils.py, alg_tester). Don't edit.
tools/               eval, compare, ship, instances, analysis. tools/archive/ = dead.
data/                train/*.json, best_known.json (moving best), lb.json, instances.json (metadata).
results/             FROZEN per-run full-40 snapshots (never overwrite). variance_profile.json.
submissions/         dated+tagged shipped zips (one per ship).
SUBMISSIONS.md       ledger: which zip, when, which results, leaderboard score.
```

## Core rule
**Never re-run a prior strategy to compare.** Every run freezes a `results/<tag>.json`. Compare reads frozen
files. If a comparison needs a version we never froze, that's a process bug — freeze going forward.

## Commands
```bash
# metadata / bottleneck map (structural; fast)
python3 tools/instances.py

# evaluate engine, freeze results, auto-diff vs best_known
python3 tools/compare.py --tag <name> --tl <sec> --inst 3,4,8,15,27,38   # spread
python3 tools/compare.py --tag <name> --tl <sec> --inst all              # full 40 (baseline freeze)
python3 tools/compare.py --tag <name> --tl <sec> --inst 15 --reps 3      # average out variance

# compare two FROZEN versions (no re-run)
python3 tools/compare.py --tag <new> --vs <old> --diff-only

# classify each instance's bound (Z1 vs Z2 vs Z3 dominant) from a frozen run
python3 tools/compare.py --tag <name> --breakdown

# ship: build dated zip from dev/, prints ledger row + freeze command
python3 tools/ship.py --tag <name> --date <YYYY-MM-DD>
```

## Metrics
- Primary: **mean delta% vs reference** + **WIN/~tie/REGRESS** counts (compare.py footer).
- `~tie` = delta within the instance's measured variance band (`results/variance_profile.json`; default 2%).
  prob_15 has ~14% variance — single-run diffs there are noise; use `--reps`.
- `best_known.json` = moving aggregate best (hours of overnight compute). A soft ceiling, NOT the bar; it gets
  overwritten by good runs, so freeze a dated copy in results/ before trusting it as a fixed reference.

## Workflow for a change
1. Edit `solution/dev/`. If `placer.c` changed: `gcc -O3 -shared -fPIC -o solution/dev/placer.so solution/dev/placer.c`.
2. `compare.py --tag <change> --vs <baseline> --inst <spread>` — check WIN/REGRESS.
3. If clean across the spread, freeze full-40 (`--inst all`), then `ship.py` + log in SUBMISSIONS.md.
