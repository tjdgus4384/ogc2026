# STATUS — current state + plan

Living doc. Problem/constraint truth = `MODEL.md`. Key facts also in `.claude/.../memory/`.

## Problem (1-line)
Assign N blocks to M bays + pack each bay (footprint-disjoint over time, EXIT=ENTRY+P).
Objective = **w1·Z1 + w2·Z2 + w3·Z3**:
- **Z1** = total tardiness (per-bay PACKING result; dominant weight).
- **Z2** = workload imbalance across bays (ASSIGNMENT-only).
- **Z3** = bay-preference penalty (ASSIGNMENT-only).

So cost splits cleanly: **packing drives Z1, assignment drives Z2+Z3.**

## Engine (`solution/dev/myalgorithm.py`)
Size-adaptive pipeline, gate `cpsat_useful = blocks/bay ≤ 52`:
1. seeds: `anytime` (ALNS+CP repair) → `assign_lns.construct_seeds` → `solve.ilp_assign`
2. large bays (`not cpsat_useful`): `brkga2` (BRKGA + C-placer decoder + beam polish)
3. small bays (`cpsat_useful`): `cpsat_polish` (exact per-bay re-pack) → `cpsat_z3` (Z3 descent w/ CP-SAT Z1=0 cert)
4. final: `assign_lns.placement_polish` → `polypack`
Every stage is best-of + utils-validated + try/except → can't regress within a run, can't return infeasible (`_ok` guard).

## Per-bay packer (`packer.py` + `placer.c` C placer)
**entry-aware** (fixed 2026-06-21): a resident constrains a new block's window [e,e+p) only if
their windows OVERLAP (exit>e AND entry<e+p), not merely exit>e. Old code over-constrained → blocks
pushed late → avoidable tardiness.
- Controlled per-bay A/B (same assignment+order): congested −32~37% Z1 + faster; small/Z1=0 unchanged.
- This is solid (controlled). It helps **Z1-bound (congested)** instances only.

## What's validated vs not
- ✅ entry-aware packer per-bay better/equal (controlled A/B).
- ✅ engine variance LOW (~0.8% at fixed budget) → run-to-run numbers are trustworthy, NOT noise.
- ⚠️ end-to-end OLD-vs-NEW packer not cleanly isolated (OLD brkga2/assign_lns overwritten; only per-bay A/B exists).
- 🔴 **possible budget anti-monotonicity**: prob_15 @120s=80078 but @300s=89435 (more time → worse). If real
  (variance is low), the search lands in a worse basin with more budget — a genuine bug to find.

## Bottleneck map (per instance, what limits the score)
- **Z1-bound (congested/large)**: prob_27, prob_38 — packing-limited; entry-aware packer helps (prob_27 beat
  overnight best @120s). prob_38 still above best (needs more budget or assignment help).
- **Z2/Z3-bound (assignment)**: prob_3, prob_15 — Z1=0 already; cost is assignment. prob_15 obj is 76% Z3.
  LARGE ones skip `cpsat_z3` (the Z3-descent lever) → Z3 not minimized. **Unaddressed lever.**
- **small**: prob_4 — cpsat path; cracked to 34346 < best 39343 (Z1=0) at 300s, but verify it's real not lucky.

## Forward plan (priority order)
1. **Verify the anti-monotonicity** (prob_15 120s vs 300s, multi-budget): is more-time-worse real? If so, find
   why (best-of not honored across stages? a stage overwriting best? gating flip at budget threshold?). This is
   the "algorithm tangled" suspicion — settle it first.
2. **Controlled engine baseline**: lock a fixed instance×budget grid, save to `results/`, so every change is
   measured against a stable reference (no more cherry-picked single runs).
3. **Attack the assignment lever (Z2/Z3) for LARGE instances** — the unaddressed bulk. The entry-aware (fast)
   packer now enables a `cpsat_z3`-style Z3-descent on large bays (re-pack 2 touched bays with the fast packer
   instead of CP-SAT). Targets prob_15 / prob_38-type directly.
4. Only after 1-3 are clean: rebuild submission from `dev/`.

## How to run / compare
- Eval: `python3 tools/eval.py submit --tl <sec> --inst 3,4,8,...`
- Comparison harness: `python3 tools/compare.py --tag <name> --tl <sec> --inst ...` → writes `results/<tag>.json`,
  diffs vs `results/baseline.json`. Use this for every change.
- Baseline: `data/best_known.json` = overnight ceilings (hours of compute), NOT a 30-min bar. Treat as a soft
  reference, not the target.

## Layout
- `solution/dev/` — production engine (13 .py + placer.c).
- `solution/archive/` — shelved, maybe-revive (mcts, polygon nfp/prism, unified_cpsat).
- `solution/baseline/` — competition-provided (utils.py, alg_tester).
- `tools/` — eval, compare, analysis; `tools/archive/` = dead-approach scripts.
- `results/` — version-tagged eval outputs for comparison.
- `docs/archive/` — long-form derivation/theory/research notes (key facts mirrored in memory).
