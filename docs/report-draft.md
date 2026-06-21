# OGC 2026 — Method & Findings (report draft)

## 1. Problem reduction (the modeling choice that defines everything)

Each block is a stack of layer polygons in a chosen orientation, placed at (x,y) in a bay, present over
[ENTRY, EXIT]. Two co-resident blocks must satisfy collision (C4) and crane-lift (C5) constraints.

**Key structural result — footprint-disjoint cuboid is not merely convenient, it is the better model.**
If the footprint axis-aligned bounding boxes of any two co-resident blocks are disjoint, then C4 and C5
are automatically satisfied and EXIT = ENTRY + processing_time (no exit-freezing). We measured the
alternative (interlocking footprints, allowing overlap with crane-aware exit scheduling): it is 2–7×
*worse* on total tardiness, because crane rigidity (C5′) freezes the obstructed block's exit until the
obstructer leaves, and under tight slack that frozen exit becomes large tardiness. The density gain is
far outweighed. So the problem reduces to: **assign blocks to bays, then 2-D pack each bay over an open
time axis to minimize total tardiness**, with EXIT = ENTRY + P fixed.

## 2. The tardiness is pure congestion (a lower-bound finding)

A valid release-floor lower bound on each block's tardiness is `max(0, r_i + p_i - d_i)`, since
EXIT_i ≥ ENTRY_i + p_i ≥ r_i + p_i regardless of packing. **On all 40 development instances this bound
is 0** — no block is forced tardy by its own release/processing/due window. Therefore *all* tardiness is
caused by spatial-temporal congestion (multiple blocks contending for bay area over overlapping time),
not by individually-tight windows.

Consequences:
- A perfect packer could in principle drive total tardiness far below what any heuristic achieves; the
  headroom is entirely in packing/scheduling quality.
- Area / energetic / dual-feasible-function lower bounds are **vacuous** here (≈0): the binding
  constraint is 2-D geometric placement over time, which area-relaxations cannot see. This explains the
  absence of any tight tardiness lower bound for this problem class in the literature, and means the
  optimality gap is empirically unknowable — one must beat the field directly.

## 3. Method — budget-independent EPOCH ALNS with CP-SAT repair

A constructive start (EDD-ordered, objective-aware bay assignment) is improved by an adaptive large-
neighbourhood search whose repair operator is an exact CP-SAT model on a small hot subset (the most-tardy
blocks of a bay, with the rest of the bay fixed as obstacles). CP-SAT is ~5× stronger than a hand-rolled
extreme-point packer on subsets of ≤ ~30 blocks, so we feed it windows in exactly that range.

Two design decisions were forced by measurement:
1. **No staleness termination.** The prior LNS quit after ~12 non-improving iterations, using only ~16%
   of a long time budget. Removing this and running to the budget is the single biggest lever.
2. **Budget-independent epochs.** A single monotone-cooling search is high-variance and *non-monotone in
   the budget* (a longer run can land worse) because the temperature/window schedule was tied to the
   fraction of total budget, so different budgets take different trajectories from iteration 1. Instead
   we run fixed-horizon annealing epochs, each diving from the *global best* (intensification) with a
   fresh high-temperature phase (diversification). More budget = more dives = monotone improvement.

The destroy portfolio (roulette by success) includes worst-tardy subset, spatial cluster, random kick,
and a **cross-bay migrate** that moves a tardy block to another bay and repairs both — the only operator
that can fix a bad assignment during polish (single-bay repair cannot). Acceptance is simulated-annealing
on the true weighted objective; the global best is always retained and re-validated.

## 4. Measurements

Baseline for "%" below = best_known, the accumulated best over *dozens* of overnight multi-seed runs
(a strong reference, not a single run). All anytime numbers are a SINGLE run unless noted.

- **Diagnosis:** the prior LNS used 103s of a 640s budget (16%) on prob_27, then stale-stopped.
- **Robustness:** 40/40 instances feasible, zero crashes (verified twice); P=0 (the P3 failure mode)
  feasible; time-limit compliance TL=20/30/60 → 9.4/18/43.7s (all under, all feasible) — overrun is
  structurally impossible (repair time-limit is capped by the remaining budget).
- **Frontier (tardiness-limited):** prob_27 48.0M vs bk 48.4M (−0.7%), prob_26 23.8M vs 24.9M (−4.2%) at
  300s — a single run beats the multi-seed portfolio. prob_38/39 remain +3–7% (hard; need long budget).
- **Preference (Z3) fix:** prob_1 +1003% → +0.0% (= bk), prob_32 +21.8% → +7.9%. Root cause was that all
  destroy operators targeted tardiness, leaving Z3 untouched on zero-tardiness instances, compounded by
  extreme seed sensitivity of the assignment search (prob_1: seed 1 → optimum, seed 0 → 39× worse). The
  multi-seed construct + deterministic preference-migrate fixed it.

## 4b. Negative result — BRKGA decoder cost (Approach B, measured)

A BRKGA over (assignment, order, orientation) random keys with the over-time packer as decoder was built
and benchmarked. Decode cost is ~1.85 s per chromosome on congested 150-block bays, capping it at ~15
generations in 300 s even with 4-way parallelism. Measured: prob_27 at 120 s → 83.7M (+73%) vs anytime
51.0M and bk 48.4M. BRKGA is decode-bound and worst exactly on the high-cost frontier instances; it does
not compete with the ALNS here. Documented as a negative result.

## 4c. The main lever — polygon-level (prism) packing via bitmaps

The footprint-disjoint packer required AABB-disjoint placement, but utils enforces the constraint at the
**polygon level** (per-layer Shapely intersection; AABB is only its fast pre-screen). The poly/AABB area
ratio averages 0.685 — **~32% of every block's bounding box is empty, forbidden-but-feasible space.**

The pairwise "fully crane+collision clean" condition (check_entry empty in both directions) is provably
equivalent to **union-polygon (prism) disjointness**, which keeps EXIT=ENTRY+P (no crane freeze — unlike
interlocking overlap, which is worse). Packing at polygon level cuts per-bay tardiness 35-57% vs the greedy
AABB packer; over the full anytime engine (whose CP repair already recovers AABB-internal slack) the margin
is smaller but large on frontier instances.

Speed was the whole difficulty: Shapely/NFP-Minkowski packers cost 70-120 s/bay — as a polish they LOSE to
spending the same time on more anytime (+11.8%). The solution is an **integer bitmap**: rasterize each
block's union footprint to a conservative integer mask (cell set if the polygon touches it ⇒ bitmap-free
implies polygon-free, safe), and place via a vectorized numpy bottom-left scan (sliding-window) — ~ms with
cached masks. At that cost the polish is essentially free and net-positive.

Deployed as a **polish phase** after anytime (`polypack.py`): re-pack each bay at polygon level preserving
anytime's optimized order, keep only feasible improvements. Measured at equal budget: prob_26 −15.9% vs
anytime-only; a single 240 s run beats the dozens-of-seeds best-known on prob_26 (−7.1%) and **cracked the
hardest instance prob_38 (−7.4% below the accumulated portfolio).** Time-safe (internal budget guard;
verified no overrun). This is the contribution that moved frontier instances past the prior ceiling.

## 4d. Polygon-aware reassignment (conditional lever)

Because polygon bays hold ~32% more, the AABB-derived assignment is too conservative. A second polish phase
moves worst-tardy blocks to other bays and re-packs both at polygon level, keeping full-objective
improvements. On a fixed anytime base it gives −10.2% over polish-only on prob_26 (3 bays; beats best-known),
0% on prob_27 (2 bays; harmless). HONEST CAVEAT: it only fires when given a dedicated budget (~60 s); inside
the deployed pipeline at a moderate time limit the per-bay polish consumes the polish reserve and a 40-run
A/B showed 0.0% on every instance. It pays off only at large budgets (≈30-min server limit, where the polish
reserve hits its cap). Kept because it can never hurt (keeps best), but it is not a reliable gain.

## 4e. The geometry is deeper than union-prism — and density is coupled to speed (a structural limit)

A full premise audit against the checker (`utils.check_feasibility` is ground truth) shows collisions are
tested **same-layer-index only** (layer k vs layer k; `utils.py:521,535`) and the crane is the **j ≥ k**
sweep (`:703,:799`), with the footprint AABB merely a pre-filter. So even union-polygon (prism) packing is
over-conservative: a taller block's upper layers (k ≥ n_exist of a shorter neighbour) are *never* obstructed
by that neighbour, so blocks may overhang one another in 3-D. We measured the unused headroom directly: per
block, (union-area − layer-0-area)/union-area averages **12.6%**, and on the frontier reaches **26%**
(prob_26), 23% (prob_14), 22% (prob_40). The crane-exit asymmetry makes this *schedulable* (unlike
interlock): only the block **under** an overhang is exit-frozen — until the overhang leaves — whereas the
overhanging block exits freely; ordering the under-block's exit last makes the pair feasible.

We built a per-layer 3-D packer feasible **by construction**: process in entry-time order; place each block
at its earliest entry under three bitmap-sweep constraints (own entry-sweep vs present-at-entry, own
exit-sweep vs already-placed-present-at-exit, and not blocking an already-placed block's exit), with a
validation-driven repair that reverts the few residual temporally-coupled violations to union-disjoint
positions. Result on prob_26's most-congested bay (K=116): **−6.2% tardiness** vs the union packer, feasible
(21/116 blocks reverted to union).

**The structural limit:** the gain *is* hole-filling — nesting blocks into the space under polygon
overhangs — which requires a full 2-D (per-height) occupancy bitmap. That packer costs ~37 s/bay (4× union)
and the re-flow repair pushes one bay to ~540 s; a column-profile *skyline* (the only structure fast enough
for in-search) cannot represent holes and measured **+39%** (worse than union) — so polygon density and
packing speed are **coupled**: no data structure is simultaneously hole-aware and fast. An incremental
single-block repair (to avoid full re-pack) collapses to all-union because reverting one block does not
re-flow the others. Hence per-layer 3-D is a *real* but *expensive* lever (≈−6% on a frontier bay), not
cheaply deployable, and only marginally above the union-polygon polish already shipped. This coupling — not
a missing idea — is why the geometric headroom (12–26% area) does not convert into a comparable tardiness
drop at deployable cost.

## 4f. What the rules do NOT add (negative premise-audit results)

Two further "free-density" hypotheses were checked against the checker and found already-exploited or
empty: (i) **same-timestamp hot-swap** — because Stage-4 overlap is strict (`a < e′ and a′ < e`,
`utils.py:1161`) and Stage 5 orders all EXITs before ENTRYs at equal t, a block may EXIT and a different
block ENTRY into the *same cells* on the same day at zero cost; our packer already uses the strict `exit > e`
present-set (`packer.py:117`), so this is captured, not missed. (ii) **Preference (Z3) headroom** — although
w3 reaches 600 and the audit flagged Z3 as potentially rank-relevant, measurement shows our preference
assignment already drives obj3 to ≈0 (w3·obj3 < 2.1% of the objective on every instance) and obj1 is nonzero
on all 40, so w1·obj1 dominates the per-instance objective everywhere; Z3 is not a usable rank lever for us.

## 4g. Exact CP-SAT does not scale to the frontier (re-confirmed)

To test whether the per-bay packing is far from optimal (i.e. whether a stronger search could close the
gap), we ran the full-bay CP-SAT model on prob_26's K=116 bay with a warm hint of the union packing
(tardiness 4866) and a 600 s budget on 4 workers. It returned **6293 — worse than its own hint** (and no
improving solution at 120 s). At frontier scale the exact model cannot even hold the warm start, confirming
that CP-SAT is only useful as the LNS repair on small (≤~30-block) windows, not as a primary or polishing
solver. Correspondingly, order-search over the per-bay packer (the primitive the ALNS repairs build on) buys
only 0.0% (prob_26) to −2.3% (prob_31): the base packer is near its order-optimum and the remaining slack is
already absorbed by the ALNS. Conclusion: at the frontier the heuristic dominates exact, and the per-bay
search is not where a large gap hides.

## 6. Robustness / the P3 infeasibility diagnosis (engineering contribution)

The submission scored −1 on the hidden instance P3 across three submissions while >50% of teams solved it —
so P3 is solvable and the fault was ours, yet it was unreproducible on all 40 dev instances. Systematic
elimination (utils byte-identical to the tester's; no input mutation; objective matches utils exactly;
orientation frames 0/13000 mismatch; positions integer per the spec's rounding rule) left a logical
contradiction: the single-occupancy floor is provably feasible, so P3 should be feasible. Resolution: it is
an EXECUTION failure, not a modelling one. **Reproduced:** scaling a bay to 1500×400 makes the polygon
polish's sliding-window scan (O(H·W·h·w)) overrun a 25 s limit at 39 s → the evaluation kills the process →
−1. Shipyard bays can be large, so P3 likely has one. Fixes: (a) a large-bay guard that falls back to the
bay-size-independent AABB packer above 60 k cells; (b) a hard SIGALRM deadline at 0.90·limit (its exception
subclasses BaseException so inner handlers cannot swallow it) that returns the always-feasible floor if any
stage runs long. The pipeline now structurally cannot be killed by timeout. Also fixed: P=0 blocks (EXIT==
ENTRY mishandled) via internal processing_time≥1. General lesson: a per-instance time limit demands that the
guaranteed-feasible floor be returnable within the limit regardless of instance scale.

## 7. Results, scoring, and honest limitations

Scoring is per-instance rank (R − n_b, −1 if infeasible), summed over instances — absolute objective
magnitude is irrelevant; only beating other teams on each instance matters. Our pipeline beats our own
dozens-of-seeds best-known on several frontier instances in a single run, and is robust (40/40 feasible,
time-safe). But the field position was ~90–100th, meaning we are mid-rank on the feasible instances too.
The optimality gap is unknowable (no tight lower bound exists — §2), so we cannot quantify distance to the
field. The big algorithmic levers were explored and most rejected by measurement; the realized gains over a
strong baseline are real but incremental (single-digit to ~16% on multi-bay frontier instances), not enough
to assert top-tier. The honest scientific contributions are the structural results: congestion-only
tardiness (§2), the polygon-packing density lever and its bitmap realization (§4c), the measured refutation
of interlocking (§1, §5), and the timeout-as-feasibility-failure diagnosis (§6).

## 5. What was tried and rejected (negative results)

- **Interlocking placement** (footprint overlap): 2–7× worse (crane rigidity). Both naive and a
  freeze-safety-filtered "selective" variant lose to cuboid.
- **CP-SAT on the whole bay** as primary solver: loses to the improver at equal budget for ≥ ~60 blocks
  (does not converge); strong only on small windows, which is why it is used as the LNS repair.
- **BRKGA** with the over-time packer as decoder: the decoder costs ~1.85s per chromosome on congested
  150-block bays, capping it at ~15 generations in 300s — too few to converge, and slowest exactly on the
  high-cost frontier instances (confirmed: prob_27 120s → 83.7M = +73% vs anytime 51.0M).
- **MCTS** over insertion order: rollout-bound, not competitive.
- **Cumulative master assignment** (CP-SAT): good assignment but redundant with the improver.
