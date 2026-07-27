# OGC 2026 — Top-Tier Strategy Design

Date: 2026-06-19. Status: approved direction = **do A, B, C sequentially**.

## Diagnosis (why we are not top-tier)

The model is right (footprint-disjoint cuboid; interlock refuted by measurement — crane
rigidity makes overlap 2-7x worse). The gap to #1 is **search quality at scale**:

| Signal | Reading |
|---|---|
| Overnight SA gained 0.006% in hours | Plateaued at a local-search ceiling, not a floor |
| No usable lower bound | Blind — unknown whether we are 5% or 200% above optimal |
| CP-SAT beats our packer 2-5x at k≤30, loses at k≥60 | 2-5x headroom inside the model, uncaptured at scale |
| Server budget up to 30 min; we tuned for 120-300s | We waste most of the budget |
| `solve_lns` stops on `stale < 3·m+3` | LNS quits ~12 non-improving iters in — cannot use 30 min |
| Z1 (tardiness) dominates every instance's weights | Minimize total tardiness; Z2/Z3 are tie-breakers |
| Final judging explicitly rewards novel theory | LB / decomposition scores in the report, not just leaderboard |

Scoring is `R − n_b` per instance (rank vs field). Tier-based board; top 30-40 advance.

## Approach A — Budget-scaling ALNS + CP (raw objective, lowest risk)

Rebuild `solve_lns` into an **anytime** solver (`solution/dev/anytime.py :: solve_anytime`)
that exploits the full budget. Keep `solve_lns` intact as fallback.

Changes vs current LNS:
1. **No stale-stop** — run until the time budget; replace staleness with diversification.
2. **Adaptive destroy portfolio** (roulette by recent success):
   - D1 most-tardy subset of worst bay (current behavior)
   - D2 random spatial cluster (boxes overlapping a random region)
   - D3 time-window co-residents (random interval)
   - D4 **cross-bay migrate** — move worst-tardy block(s) to an alt bay, CP-repair BOTH bays
     (the structurally new lever: current polish cannot fix a bad assignment)
   - D5 random-K subset of a random bay (pure diversification kick)
3. **K and rep_tl scale with budget** — grow toward K≈25-30, rep_tl≈5-8s where CP is strongest.
4. **SA acceptance in polish** — accept sideways/slightly-worse by an annealing schedule keyed
   to remaining budget; always retain and return the global best feasible.
5. **Anytime guarantee** — best feasible tracked, never regressed; utils-validated before return.

Validation: anytime objective curves on frontier instances (prob_27/38/26/39) at 300/600/1800s
vs current best-known. Success = strict, repeatable improvement at 600-1800s.

## Approach B — BRKGA global search (new global search)

Chromosome = (bay-assignment keys, insertion-order keys, orientation keys). Decoder = a
deterministic skyline/bottom-left-over-time packer placing blocks in decoded order, each at the
earliest time its footprint fits current occupancy (ENTRY = max(release, earliest-fit)). Biased
reproduction, elite set, anytime, parallel across the 4 cores. Escapes the local optima the 5 SA
operators cannot reach. Compare/blend with A (A can polish B's best).

## Approach C — Lower bound + decomposition (report novelty)

Per-bay energetic / Lagrangian relaxation lower bound on total tardiness, to measure the true gap
and direct effort; forced-vs-flexible block decomposition (energetic reasoning identifies blocks
tardy in every feasible schedule → focus compute on the flexible interactions). Primary value is
the technical report (final judging rewards theoretical innovation) and effort-direction, not
direct leaderboard points.

## Sequencing

A (measure plateau → rebuild anytime → validate) → B (decoder + GA, blend with A) → C (LB +
decomposition, feeds report). Ship the best validated pipeline; keep the P3-fixed cuboid as the
guaranteed-feasible floor in `myalgorithm.py`.
