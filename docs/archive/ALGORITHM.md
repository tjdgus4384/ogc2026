# OGC 2026 — Final Algorithm (clean reference, for direction decisions)

The submitted solver. Plain description so we can judge the approach against the leaderboard.
Package = solution/submission.zip: myalgorithm.py + solve.py improve.py packer.py cpsat_packer.py
master.py + provided utils.py (all flat at zip root). Eval: CPU-only, 4 cores, 16 GB, time limit per
instance (minutes–30 min). Evaluated on HIDDEN instances (our 40 are a dev proxy).

## What it optimizes (no approximation of the criterion)
The true weighted objective for ANY weights: minimize w1·Z1(total tardiness) + w2·Z2(load imbalance) +
w3·Z3(preference penalty). Regime-agnostic — never assumes a weight ordering.

## The one modeling approximation (of the PROBLEM, not the criterion)
Blocks are placed **footprint-disjoint** (their footprint AABBs never overlap when co-resident). This is
SUFFICIENT for feasibility: footprint-disjoint ⇒ no layer pair overlaps ⇒ collision (C4) AND crane (C5)
are automatically satisfied, and EXIT = ENTRY + P is always achievable. Fast (AABB tests, no geometry).
COST: gives up the extra density that interlocking / true-polygon packing could provide (measured: footprint
area ~1.6-2.4× the true polygon area; on congested instances this leaves tardiness on the table).

## Pipeline (myalgorithm.algorithm)
1. **FLOOR (bulletproof, instant)** — serialize_floor: one block per bay at a time (single occupancy ⇒
   always feasible; uses no weights/area ⇒ immune to edge crashes). The −1 insurance + final fallback.
2. **FOOTPRINT FLOOR** — footprint-disjoint greedy (better start); adopted only if feasible.
3. **MULTI-SEED IMPROVER (uses ~80% of the time limit, 4 cores)** — run up to 4 seeds in parallel; each:
   - **construct**: best of {pref, bal, obj} bay-assignment heuristics (obj weighs w1·load·u + w3·pref).
   - **improver (SA + adaptive operator weights)** over (assignment, per-bay order): operators = promote a
     tardy block earlier (Z1), swap order, reassign bay (Z1, dominant lever), bay-swap, move-to-preferred
     (Z3). Objective computed directly from placements (no Shapely) ⇒ thousands of moves/sec.
   - **LNS polish (CP-SAT repair)**: repeatedly re-optimize the hottest ~14 blocks of the most-tardy bay
     with CP-SAT while the rest of the bay is fixed (CP-SAT converges on the small free set, beating the
     greedy packer where it's stuck). Keep the best FEASIBLE seed.
   - Falls back to a single run if multiprocessing is blocked (e.g., firejail).
4. **GUARD (final)** — re-validate the chosen solution with utils.check_feasibility; if not feasible
   (should not happen), return the serialize floor. ⇒ never returns infeasible for a valid instance.

## What's validated (on the 40 train instances)
- 40/40 feasible; best-known total 891M (greedy baseline level) → ~319M (−64%). Time-safe at 120s incl.
  n=300. Fault-injection: forcing every solver to crash still returns a feasible serialize solution.
- The improver SCALES with time (e.g. prob_20 41M@15s → 1.08M@120s); multi-seed captures SA run-variance.

## Decision levers explored and REJECTED (measured, not assumed)
- Interlock / true-polygon packing: prototype gained only ~9% and is far slower (full NFP build needed).
- MCTS over insertion order: rollout-bound (~21 iters/30s at n=150), not competitive.
- Cumulative/area & DFF lower bounds and "pressure" master: valid but 1-2% loose (area ≪ 2-D packing).
- extreme-point/max-contact placement, beam-DP seed, cumulative-master assignment seed: all ABSORBED by
  the improver at equal budget (no net gain). The improver is robust and dominates component swaps.

## Known limits / open direction (this is the strategic question)
- The objective is dominated by congested "frontier" instances; their residual tardiness is largely
  FORCED (energetic min-peak 2-2.4× capacity even after optimal time-leveling) ⇒ bounded headroom for the
  cuboid approach. We are near its practical ceiling.
- **The one un-pushed lever with measured headroom = a proper polygon/NFP packer** (footprint→silhouette
  reclaims 1.6-2.4× area; targets the ~65%-of-objective frontier). Deprioritized as expensive/slow.
- 1st submission (hidden): 5/6 feasible (P3 was infeasible, now fixed) ⇒ tier was dragged by the P3 −1.
  The NEXT submission (P3 fixed) is the clean read on whether the cuboid-matheuristic objective is
  competitive: if still mid-tier ⇒ invest in polygon/NFP packing; if good ⇒ refine the matheuristic.
