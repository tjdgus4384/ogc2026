# OGC 2026 — Deductive Derivation: from MODEL.md to the optimal answer

Each step: **FROM** (which MODEL fact / premise) → **THEREFORE** (conclusion) → **ALTERNATIVES** considered →
**RISK** (assumption that, if wrong, breaks the step — the place to look when results disappoint). The chain is
meant to be *checkable*: if the final answer is suboptimal, walk these steps to find the broken link.

Objective (MODEL §5): `min w₁Z1 + w₂Z2 + w₃Z3`. Scoring is relative per hidden instance (`R−nb`); infeasible /
crash / timeout = `−1`. So two meta-goals: **(I) never score −1**, **(II) minimize the objective as much as
possible, and know how close to optimal we are.**

---

### D0 — Feasibility is non-negotiable and always achievable
**FROM** F5 (strongly NP-hard ⇒ we may not reach optimum) + scoring (`−1` for infeasible) + F7/§7 (safe mode &
serialize fallback always feasible). **THEREFORE** the engine must *always* hold a feasible incumbent and validate
the final output with `utils.check_feasibility`, falling back to safe/serialize if anything fails. **ALTERNATIVE:**
chase density and risk occasional infeasibility — rejected: one `−1` costs more than many small objective gains.
**RISK:** our local feasibility checker differs from the server's `utils.py` (MODEL §8). Mitigation: use the
provided `utils.py` verbatim for the final check.

### D1 — Split into assignment (outer) and per-bay packing (inner)
**FROM** F1 (Z2,Z3 = closed-form in `a`; Z1 separates per bay). **THEREFORE**
`min = min_a [ w₂Z2(a)+w₃Z3(a) + w₁ Σⱼ PackCostⱼ(a⁻¹(j)) ]`: optimize the assignment partition; each bay is an
independent sub-problem. **ALTERNATIVE:** monolithic search over everything at once — rejected: ignores the exact
separability, wastes effort. **RISK:** `PackCostⱼ` is itself hard, so the outer objective is only as good as our
`PackCost` estimate; a poor estimate misguides assignment. Mitigation: use a valid LB (F6) + the real packer for
chosen partitions.

### D2 — Never search over times; derive them
**FROM** F4 (timing is a polynomial TU-LP given the discrete shell). **THEREFORE** any candidate is specified by
its **shell** (`a`, orientations, positions, total operation order); the optimal `eᵢ,fᵢ,Tᵢ` follow exactly and
instantly from the timing LP. Search operates on the shell only. **ALTERNATIVE:** treat entry/exit as search
variables — rejected: wastes the search budget on a polynomially-solvable sub-part. **RISK:** the shell must be
*complete and consistent* — the operation order must respect the fixed positions (non-collision-free pair ⇒
time-disjoint; one-way crane ⇒ nesting; two-way ⇒ forbidden, C5′). If we feed an inconsistent shell the LP is
infeasible. Mitigation: construct shells only from consistent moves.

### D3 — Search only the topology; let LPs fill time AND space
**FROM** RESEARCH F (compaction/separation LP gives optimal positions for a fixed left/right/above/below topology)
+ D2. **THEREFORE** the genuine search space is the **combinatorial topology**: assignment, orientation, the
pairwise spatial relations (which side / interlock or not), and the operation order. Both the spatial optimum
(compaction LP) and temporal optimum (timing LP) are free given the topology. **ALTERNATIVE:** search continuous
positions directly — rejected: F3 (finite candidate points) + LP compaction dominate it. **RISK:** compaction LP is
only a *local* optimizer for a fixed topology; the topology choice is still NP-hard and is where quality is won or
lost. Also integer-rounding of LP positions may reintroduce tiny overlaps. Mitigation: re-check rounded positions;
treat LP as a strong local move inside the metaheuristic, not a global solver.

### D3.1 — The concrete encoding we actually search (this is where optimality is won)
The full topology (explicit pairwise spatial relations + a total operation order) is `Θ(n²)` choices — too large
to search directly. Collapse it to a **compact genotype + deterministic decoder**:
> **Genotype** `G = (assignment a, orientation o per block, a priority sequence π)`.
> **Decoder** (deterministic, runs the model forward): take blocks in `π` order; for each, in its assigned bay,
> find the **earliest feasible ENTRY day** and a **candidate-point position** (NFP feasible region, F3) by a fixed
> placement rule (bottom-left / lowest-gravity-center), choosing safe-mode or one-way interlock per the local rule
> (D4); this induces positions, the spatial relations, and the operation order. Then the **timing LP** (F4) sets
> exact `eᵢ,fᵢ`; optionally the **compaction LP** tightens positions and we re-time.
So the search space is just **`(a, o, π)`** — a partition + per-block orientation + a permutation — and *everything
else is derived exactly by the decoder + two LPs*. This is the encoding the metaheuristic (D6) explores; it is
small, every point decodes to a feasible solution (D0), and the objective of each genotype is its decoded,
LP-timed value.
**Completeness caveat (where the optimum can hide):** a fixed decoder + `(a,o,π)` cannot represent *every* feasible
layout (decoder bias — e.g. some dense interlock patterns no greedy order produces). **THEREFORE** (i) the moves in
D6 must include position/interlock edits beyond pure re-ordering (relocate to any candidate point, toggle a
specific interlock) to widen reachability, and (ii) the **exact engine (D5)** — which does *not* go through the
decoder — is the only way to certify the true optimum on tractable instances. **RISK:** if we rely on `(a,o,π)`
alone, we are limited to decoder-reachable solutions; the LB gap (F6) tells us whether that limitation is costing
us. **ALTERNATIVES** for the genotype: explicit sequence-pair / BL-skyline encodings (rectangle-packing style) —
weaker for irregular shapes; explicit pairwise-relation MIP — exact but only small `n`.

### D4 — Inner = 2-D packing-over-time; footprint-disjoint is PRIMARY, interlock is a rare opportunistic add
**FROM** F2 (inner = DSA) + the crane geometry (MODEL §2: **footprint-disjoint ⟹ `δ∉Ψ` and `δ∉Φ`** — if two
blocks' xy footprints don't overlap, no layer pair can overlap, so crane AND collision are *automatically*
satisfied for any operation order) + the **real-data regime** (D8: `Z1≫Z3`, tight slack median 1–2, all crane
failures are stage-2).
**THEREFORE** the dominant strategy is **footprint-disjoint placement**: it makes the crane (C5) a non-constraint
by construction — eliminating the *exact* failure mode that makes the baseline infeasible (stage-2) — and reduces
feasibility to cheap AABB-overlap tests (no Shapely in the inner loop). Interlock (overhang nesting, C5′) buys some
extra density BUT forces the obstructer to **time-nest inside** the obstructed block's stay, freezing its exit; under
**tight slack** a frozen exit is directly tardiness. So interlock **trades space-density for schedule-rigidity** —
**net-negative in this Z1-dominant, tight-slack regime.** Use it only as a *rare opportunistic* move when it
strictly lowers `ΣTᵢ` and traps no near-due block.
**ALTERNATIVE (rejected as primary):** density-first selective interlock (the earlier committed edge) — on real
data the rigidity cost dominates the density gain; demoted to opportunistic. **RISK:** if a future instance is
loose-slack with heavy overhang, interlock could pay — gate it on measured slack and the `Ψ\Φ` gap, don't hardcode
off either. **Corroboration:** synthetic D8 showed overhang→huge baseline tardiness, but that was baseline
*serializing* overhang blocks (worst case); the correct remedy is footprint-disjoint placement, not interlock.

### D5 — Two regimes by hardness; always measure the gap
**FROM** F5 (no poly exact) + F6 (valid LBs). **THEREFORE**:
- **Small / loosely-packed instances:** run **exact** (LBBD with per-layer-area master + per-bay packing
  subproblem + lazy geometric cuts; or NFP-MIP / CP-SAT). Relative scoring ⇒ a proven optimum cannot be beaten,
  only tied — guaranteed top score there.
- **Large / dense instances:** run a **metaheuristic** (D6) anytime; report `gap = (UB−LB)/UB` from F6 so we *know*
  how near optimal we are regardless of competitors.
- **Route** each instance by size & conflict density.

**D5.1 — what actually controls exact tractability (deduced).** The exact engine's cost is dominated by the
per-bay packing subproblem, whose hardest atom is packing the **maximum concurrent clique** (the most blocks that
must coexist in one bay at one instant) at candidate points — exponential in that clique size `k*`. The outer
partition (m^n) is tamed by LBBD/branch-and-price + the F6 LB. **THEREFORE the single best predictor of "exact will
converge in time" is `k*` = peak per-bay concurrency**, cheaply estimated *before* solving from the area-cumulative
LB's peak load (how many blocks' `[Rᵢ, Dᵢ]` windows pile onto one bay's area at once). Heuristic threshold (to
calibrate on data): exact viable when `k* ≲ 8–12` and `n` moderate; otherwise heuristic.

**D5.2 — prefer a portfolio with LB-sharing over a brittle hard route.** A fixed `k*`-threshold is fragile (one
misjudged instance ⇒ exact times out ⇒ only the WARM incumbent).
**REAL-DATA VERDICT (D8):** none of the 8 train instances is "small" (`n∈[100,250]`) and realized per-bay
concurrency is `k*≈9–12` with space-free demand up to 36 — *above* the `k*≲8–12` exact-viability band. So the
**exact engine is demoted to an LB producer only** (monotone per-layer-area / Lagrangian / LP bound to measure the
gap and prune), **not** a route expected to prove optima here. The heuristic (D6) is THE solver. Keep exact on
~1 core purely for the LB. **THEREFORE** on the 4 cores run **both** engines
concurrently: ~1 core to the exact engine (it yields a monotone-improving **LB** even if it never proves optimum)
and ~3 cores to ET-ALNS (the **UB**). Share the LB to ALNS (prune / stop when `UB=LB`) and the UB to exact (incumbent
for pruning). Return the best feasible; if exact proves optimum, even better. This removes the routing gamble:
small instances get proven optima, large ones get the best heuristic UB plus a real gap, with no per-instance
decision to mis-make. **ALTERNATIVE:** one method for all — rejected (exact wastes time on large; heuristic leaves
easy instances sub-optimal and unproven). **RISK:** per-layer-area LB may be loose ⇒ gap overstates true distance,
and exact may contribute little LB on hard instances. Mitigation: strengthen the LB (Lagrangian / LP relaxation),
and treat the gap as an upper estimate of suboptimality.

### D6 — The quality-maximizing metaheuristic (for the hard regime)
**FROM** RESEARCH B–C–F (SOTA constructors + improvers + the two-LP lever). **THEREFORE** the pipeline:
1. **Construct:** schedule order by **ATC + Time-Window-Decomposition**; place via NFP **bottom-left /
   lowest-gravity-center**; immediately tighten with the **compaction LP**.
2. **Improve (anytime):** **ET-ALNS** over the topology — reassign bay, reorient, relocate (candidate point),
   resequence operations, toggle one-way interlock, destroy-and-rebuild a bay/time-window; use **dynasearch** for
   the sequencing sub-move. After each move, refill positions (compaction LP) and times (timing LP); accept by the
   weighted objective; keep best feasible.
3. **Portfolio + multistart:** run several configs (constructors, ATC `κ`, placement policy, seeds, exact-vs-ALNS)
   on the 4 cores; **return the best feasible per instance** (relative scoring rewards per-instance wins).
4. **Anytime discipline:** stop at ~80–85% of the time limit (dev≠server speed); never regress below the incumbent.
**ALTERNATIVE:** a single GA, or pure SA — kept as portfolio members, but ET-ALNS+dynasearch is the evidence-backed
primary. **RISK:** ALNS operator weights and acceptance need tuning; CP/LP calls may be too slow inside the loop on
large instances. Mitigation: cheap incremental feasibility (STRtree), call LPs sparingly (periodic re-optimize, not
every move), calibrate on training.

**D6.1 — target moves by expected objective gain (not random).** The factorization (F1) maps each operator to the
term it can move, so we never waste moves:
- **Z3 (preference):** reassign a block toward its higher-`Sᵢⱼ` bay — directly cuts Z3, cheap.
- **Z2 (balance):** move workload `Lᵢ` from the max-weighted-load bay to the min — directly cuts the range.
- **Z1 (tardiness, usually dominant):** the hard term — cut via earlier entry (better placement/order), more
  concurrency (interlock/compaction), or better sequence (dynasearch on `π`).
**Critical-path targeting (the key efficiency deduction).** The timing LP (F4) is a longest-path computation; its
solution exposes the **critical path** — the chain of precedences whose tightness *causes* each tardy block's late
exit. Tardiness only drops if we relax a **critical** precedence. **THEREFORE** focus destroy/interlock/resequence
moves on critical-path blocks and their binding precedences (the bay/window/pair on the critical chain), not on
slack blocks. This is critical-path-guided local search (as in job-shop) and gives the **highest expected `Z1`
reduction per move**. Adaptive ALNS weights then reward whichever operators recently improved; Z2/Z3 polish moves
are applied when Z1 plateaus (consistent with the near-lexicographic weight regime, D7). **RISK:** the critical
path shifts after each move (re-solve the cheap timing LP to refresh it); over-focusing can trap in a basin —
keep a fraction of randomized/diversifying moves.

### D6.2 — Machine learning as a quality booster (training instances are provided)
**FROM** the contest provides **training instances** (so the hidden-instance distribution can be learned offline)
+ server limits (CPU-only, no GPU/internet, ≤4 cores/16 GB, 15 MB zip ⇒ train offline, ship weights, CPU
inference). **THEREFORE** ML is a legitimate quality lever — *complementing*, not replacing, the exact/LP/feasibility
backbone:
- **PackCost surrogate** (low-risk, high-value): a fast regressor predicting `PackCostⱼ(S)` to steer the assignment
  search (D1) cheaply, avoiding a full pack per candidate.
- **Learned dispatching/priority**: replace/augment ATC (D6) with a priority function (learned weights or a small
  GNN / pointer net) fit to the training distribution — can beat hand-tuned ATC.
- **Learned placement policy** for nesting; **RL/bandit** for ALNS operator + parameter selection (D6.1);
  **learned branching/LB guidance** for the exact engine (D5).
**Why NOT the core:** (i) feasibility/geometry/crane must be exact — ML *proposes*, the exact checker (`utils.py`)
*validates*; ML cannot guarantee feasibility; (ii) ML cannot prove optimality (the exact engine does, D5);
(iii) hidden-instance generalization risk + CPU inference budget. **RISK:** overfitting to training instances /
slow CPU inference inside the loop. Mitigation: keep models small, validate generalization on held-out training
instances, always gate outputs through the exact feasibility check, fall back to ATC/heuristic if a model
underperforms. **Bonus:** a learned component is genuine novelty for the technical report (part of the final score).

### D7 — Per-instance adaptivity
**FROM** MODEL §5 (weights given per instance) + the regime spread. **THEREFORE** at runtime read `w₁,w₂,w₃` and
the realized magnitudes: if `w₁` dominates (e.g. one tardiness day ≫ any preference/balance swing) behave nearly
lexicographically (`Z1 ≫ Z3 ≫ Z2`) and prioritize density/compactness; otherwise weight preference/balance in
assignment. Detect conflict density and the `Ψ\Φ` gap to set interlock aggressiveness. **RISK:** misreading the
regime (e.g. assuming lexicographic when a balanced instance appears) wastes effort on the wrong term. Mitigation:
compute the actual trade-off ratios from the instance, don't hardcode.

---

## D8 — MEASURED on the REAL training set (2026-06-14; data/train, 9 instances)
Tools: `tools/analyze_train.py`, `tools/analyze_geom.py`, `tools/run_baseline_train.py`. The real instances differ
sharply from my earlier synthetic proxy — **the proxy's regime assumptions were wrong; trust only the numbers below.**

**Real distribution (measured):**
- `n∈{100,150,200,250}`, `m∈{2,3,4}`. All large; none "small".
- **Weights uniformly Z1-dominant:** `w1∈[10000,29630]` (outlier prob_40 `w1=667`), `w2∈[1,7]`, `w3∈[13,200]`.
  No balanced or preference-dominant instance exists (the proxy's `bal`/`pref` regimes were fictional).
- **Slack `D−R−P` TIGHT:** median 1–4, min 0, max 4–13; 0 blocks forced-tardy (`LB0=0`) — so all tardiness is
  contention-induced and the tight slack makes it bite hard.
- Layers `K`: median 2 (range 1–4 — blocks are LOW). Orientations `O`: median 8. **Non-convex 78–93%.**
  **Overhang 29–43%.** Bays wide-short (W/H ≈ 3–9). Blocks ≈ ⅓ bay height ⇒ 2–3 stack in y (**2-D, not 1-D**).
  Peak space-free window-overlap per bay `= 8–36`.

**Baseline on real data (actual `utils.check_feasibility`, tl=20 s):**

| inst | n | m | feasible | stage | Z1 | Z3 | k* | sec |
|---|---|---|---|---|---|---|---|---|
| prob_4 | 100 | 2 | ✓ | 5 | 183 | 510 | 12 | 20 |
| prob_5 | 150 | 3 | ✓ | 5 | **0** | 791 | 10 | 23 |
| prob_6 | 150 | 3 | ✗ | **2 crane** | – | – | – | 53 |
| prob_7 | 150 | 3 | ✗ | **2 crane** | – | – | – | 33 |
| prob_8 | 150 | 2 | ✓ | 5 | 84 | 8 | 9 | 22 |
| prob_9 | 200 | 3 | ✗ | **2 crane** | – | – | – | 33 |
| prob_38 | – | – | corrupt download (truncated at 30000 B) — re-download |
| prob_39/40 | 250 | 3/4 | construction overran tl (no phase-1 time guard) ⇒ would time out on server |

**Findings (each rewrites a priority — these REPLACE the synthetic D8):**
1. **`−1` is a REAL risk.** Baseline infeasible on 3/6 readable instances (all **stage-2 crane**) and overruns the
   limit during construction at `n=250`. (Refutes the synthetic "96/96 feasible".) ⇒ a **fast, unconditional
   always-feasible floor** (serialize, MODEL §7) is mandatory, and **construction MUST be time-bounded** — the
   baseline's phase-1 is not, so it dies at scale.
2. **The crane is THE binding constraint** (every failure is stage-2). ⇒ **footprint-disjoint placement (D4)
   eliminates it by construction** — the single highest-leverage choice. No crane/collision Shapely needed when
   footprints are AABB-disjoint.
3. **Objective ≈ lexicographic `Z1 ≫ Z3 ≫ Z2`** (MODEL §8.8): `w1·Z1` dominates when `Z1>0`; once `Z1=0` (prob_5)
   `Z3` decides (89% of residual); `Z2` always negligible. ⇒ minimize `Z1`, then `Z3` among `Z1`-optima, ignore `Z2`.
4. **Exact per-bay is infeasible** — realized `k*=9–12`, demand to 36 (D5 demoted to LB-only).
5. **Feasibility-check cost is the scaling wall.** Baseline re-runs O(n²) Shapely `check_feasibility` every repair
   pass ⇒ can't converge at `n≥150` in budget. ⇒ footprint-disjoint enables **incremental AABB feasibility (no
   Shapely)** ⇒ the improver can actually use the full time budget.
6. **2-D, low-and-wide ⇒ shelf/level packing along x** is the natural near-optimal structure (blocks ≈⅓ bay height,
   wide-short bays, 1–2 layers).

*Synthetic D8 (proxy) is superseded:* its `bal`/`pref` regimes don't occur, its loose slack made `LB0=0` trivial and
`k*≤8` an artifact, and its "feasibility solved" conclusion was false. Synthetic tools kept only for scaling tests.

## D9 — PROTOTYPE VALIDATION on real data (tools/floor.py): the guarantee is real, the design works
Two minimal prototypes of the committed design, run on all real instances, AABB-only, **no Shapely**:
`serialize` (one block at a time per bay — the `−1` insurance) and `footprint` (concurrent footprint-disjoint greedy
— the keystone: footprint-disjoint ⟹ crane+collision free). `Z1` = total tardiness (lower better).

| inst | n | baseline `Z1` | serialize `Z1` (sec) | footprint `Z1` (sec) |
|---|---|---|---|---|
| prob_4 | 100 | 183 | 15278 (0.0) | **47** (0.0) |
| prob_5 | 150 | **0** | 21796 (0.0) | 325 (0.0) |
| prob_6 | 150 | **INFEASIBLE** | 23930 (0.0) | **346** (0.1) |
| prob_7 | 150 | **INFEASIBLE** | 21962 (0.0) | **819** (0.0) |
| prob_8 | 150 | 84 | 29803 (0.0) | **3** (0.0) |
| prob_9 | 200 | **INFEASIBLE** | 39783 (0.0) | **142** (0.1) |
| prob_39 | 250 | **TIMEOUT** | 134169 (0.1) | **4357** (0.8) |
| prob_40 | 250 | **TIMEOUT** | 393439 (0.1) | **24633** (2.9) |

**Findings (hard evidence, not extrapolation):**
1. **Feasibility guarantee DEMONSTRATED:** both prototypes feasible on **8/8 readable** instances — including the 3
   where baseline is infeasible and the 2 (n=250) where it times out. Serialize is ≤0.1 s and structurally always
   feasible (single occupancy). This is the `−1`-elimination, proven on the real set.
2. **Footprint-disjoint greedy is competitive-to-dominant on `Z1`** while always feasible and fast: it **beats
   baseline** on prob_4 (47<183) and prob_8 (3<84), is **feasible where baseline is not** (6,7,9,39,40), and solves
   n=250 in ≤3 s with no Shapely. It **loses** only on prob_5 (325 vs baseline's 0) — expected, it's a *constructor*,
   not the improver.
3. ⇒ Even a trivial prototype of the committed design **never scores `−1` and matches/beats the field's reference
   (baseline) on 7/8 instances.** The IMPROVE stage (critical-path moves, better orientation/concurrency, full budget)
   targets the residual `Z1` (still >0: prob_39=4357, prob_40=24633) and prob_5's regression. This is the concrete
   basis for the rank argument: guaranteed-feasible + already ahead of baseline almost everywhere.

## Resulting answer (the algorithm we commit to — REAL-DATA-DRIVEN, supersedes the synthetic-era version)
**Always-feasible, anytime, footprint-disjoint shelf-packer with Z1-then-Z3 search:**
`PRE` (parse; per orientation footprint AABB + exact footprint polygon; per-layer areas; LBs) →
`FLOOR` (fast unconditional **serialize / greedy footprint-disjoint** feasible incumbent in ~O(n log n) — the `−1`
insurance; never depends on a slow repair loop) →
`CONSTRUCT` (assign blocks to bays balancing per-bay contention for Z1 + honoring preference for Z3; per bay schedule
by ATC/EDD-with-release; place **footprint-disjoint via shelf/bottom-left** packing so crane+collision are auto-satisfied;
set exact `e,f` by the **timing LP**, F4) →
`IMPROVE` (anytime local search / ALNS over assignment+orientation+sequence+shelf-layout, **critical-path-targeted**
at tardy blocks; **incremental AABB feasibility — no Shapely in the loop**; re-time by LP; keep best feasible; uses the
WHOLE budget) →
`POLISH` (once `Z1` can't drop, reassign for `Z3` without raising `Z1`; `Z2` free) →
`GUARD` (final validate with `utils.check_feasibility`; on any failure fall back to FLOOR) → emit. Report `gap` vs LB.
*Interlock (C5′ nesting):* OFF by default; rare opportunistic move only when it strictly lowers `ΣTᵢ` and traps no
near-due block (D4). *Exact engine:* LB producer only, not a solver (D5, k* too large).

**Effort priority (from real-data D8, the uniformly Z1-dominant + tight-slack + crane-bound regime):**
1. **Fast unconditional feasibility floor + time-bounded construction** — baseline goes `−1` on 3/6 real instances
   (stage-2 crane) and times out at n=250; this is the #1 differentiator (beat every team that fails).
2. **Footprint-disjoint placement** — eliminates the crane failure mode by construction AND makes feasibility O(AABB).
3. **Minimize Z1** (tardiness): denser concurrent footprint-disjoint packing + better entry order + critical-path moves.
4. **Use the full time budget** with incremental feasibility (the lever baseline lacks at scale).
5. **Then Z3** (preference) among Z1-optima; **Z2 ignored** (w2≤7, floored, ~0.3% of objective).

## Where this could still be wrong (audit checklist for suboptimal results)
1. **D0:** local checker ≠ server `utils.py` → feasible-looking solution scores −1. *Check first.*
2. **D1:** `PackCost` estimate misguides assignment → good packing, wrong bay split.
3. **D2/D3:** inconsistent shell or LP-rounding overlap → infeasible/over-tight.
4. **D4:** footprint-disjoint forfeits too much density on some instance ⇒ tardiness higher than an interlocking
   competitor. *Mitigation:* gate opportunistic interlock on measured slack; measure the density loss vs LB.
5. **D5:** LB too loose → gap uninformative. (Exact-as-solver risk is gone — demoted to LB-only.)
6. **D6:** improver under-tuned / feasibility check accidentally invokes Shapely in the hot loop → poor anytime quality.
7. **D7:** an unseen instance breaks the `Z1≫Z3≫Z2` order (e.g. all-`Z1=0`) and Z3/Z2 handling is too crude.
8. **MODEL §8 items — RESOLVED.** The official `utils.py`, `baseline_greedy.py`, `alg_tester`, `ogc2026_env.yml`,
   and the problem text are now in the repo and audited line-by-line: Z2 (`floor` of range), per-orientation layer
   counts, crane `j≥k` / tie semantics, 5-stage feasibility, AABB containment — all confirmed exact. The model is
   no longer a risk; the *only* remaining unknown is the hidden/training instance distribution (below).

## Status of unknowns (training data ARRIVED + analyzed 2026-06-14)
Resolved from `data/train` (see D8 for numbers): sizes `n,m`; weight regime (uniformly `Z1≫Z3≫Z2`); slack (tight);
geometry (2-D not 1-D — **the 1-D-DSA hypothesis is REFUTED**; blocks ≈⅓ bay height); overhang 29–43%; non-convex
78–93%; realized `k*=9–12`; baseline failure mode (stage-2 crane + n=250 timeout).
**Still open:**
- **prob_38 is a corrupt/truncated download** (30000 B, JSON cut mid-array) — re-download to complete the set.
- **prob_39/40 (n=250) baseline outcome** — construction overran the short tl; need a run at a realistic limit to
  measure attainable Z1 (not yet measured, not assumed).
- **Hidden time limits** ("few minutes to half an hour") — set the anytime budget; tune the FLOOR→IMPROVE split so
  even the shortest limit yields a feasible, decent answer.
- **True optimal Z1 per instance** — our LB (per-layer area / scheduling relaxation) vs the achievable UB; gap still
  unmeasured. Build the LB next to quantify how much of baseline's Z1 (e.g. prob_4=183, prob_8=84) is recoverable.
