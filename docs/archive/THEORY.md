# OGC 2026 — Complexity & Structure Map (theory-first)

Purpose: approach the problem *from itself* — establish, for each sub-structure, what is tractable, what
is hard, which parameters gate tractability, and where our measured instances sit. Then choose
decomposition(s) by evidence, not habit. Foundation = [MODEL.md](MODEL.md); methods catalogue =
[RESEARCH.md](RESEARCH.md); committed algorithm so far = [DERIVATION.md](DERIVATION.md).

Tags: **[PROVEN]** established here/in MODEL · **[KNOWN]** standard literature result · **[MEASURED]** from
data/train (40 inst) · **[CONJECTURE]** plausible, unproven · **[TO-PROVE]** open node to develop.

---

## 0. Methodological stance (why this document exists)

A decomposition **preserves the optimum** (the problem is fixed) but **does NOT preserve tractability or
bound quality**. Since the problem is strongly NP-hard ([MODEL F5]) we never reach the optimum, so what we
actually produce — a feasible UB and a valid LB — is *entirely* a function of the decomposition. Therefore:

1. Decompositions are **tools, not truths.** Different splits of the same problem are **equivalent in OPT,
   non-equivalent in achievable solution + bound.**
2. **Keep several, one per job**: a decomposition good for the **UB** (construction/search), a different one
   for the **LB** (relaxation), a third for **exact certification** of small pieces.
3. **Choosing a decomposition = the research.** Score candidates by four criteria:
   - **(C1) cut along weak coupling** — few linking variables/constraints;
   - **(C2) isolate the hard kernel** — push NP-hardness into ONE well-studied subproblem, keep the rest poly;
   - **(C3) expose a small parameter / structure** — let a tractability knob (k\*, m, height-bands, slack)
     govern one piece;
   - **(C4) match a solvable template** — Benders (feasibility sub → cuts), Dantzig–Wolfe / column generation
     (pricing sub), Lagrangian (relax coupling → bound), DP (small-state sequential).

---

## 1. The exact first cut (F1) and why it only *relocates* hardness

[PROVEN, MODEL F1] Exact reformulation:
```
OPT = min_{assignment a}  [ w2·Z2(a) + w3·Z3(a) + w1·Σ_j PackCost_j(a⁻¹(j)) ]
```
- `Z2(a), Z3(a)` closed-form in `a`; `Z1` separates per bay given `a`.
- `PackCost_j(S)` = min total tardiness to place block-set `S` in bay `j` over time (the inner problem).

This is the right FIRST cut — **(C1)** bays are spatially independent; the only linking variable is `a`. But
it **relocates** all hardness into `PackCost` + the coupling: OUTER cannot be evaluated without PackCost,
and PackCost depends on `a`. So F1 is *structural*, not *divide-and-conquer-into-easy*. **The real work is
decomposing INNER.** (OUTER is "search over `a` calling an INNER oracle"; [MEASURED] `a` swings the objective
up to 12× — prob_31 — so OUTER is operationally important even though INNER is the hard kernel.)

---

## 2. Complexity of each atom

| Atom | Complexity | Notes |
|---|---|---|
| **Timing** (e_i,f_i given the shell) | **[PROVEN] poly** | TU difference system, integral LP = longest-path ([MODEL F4]). Under footprint-disjoint, even trivial: EXIT=ENTRY+P ([memory] timing-LP moot). |
| **Z3 alone** | **[PROVEN] poly** | each block → argmax-pref fitting bay. |
| **Z2 alone** | **[CONJECTURE] pseudo-poly for fixed m** | minimize range of weighted bay-loads = balanced partition into `m` parts; NP-hard in general, but `m≤5` [MEASURED] ⇒ load-DP likely poly. Low value (w2≤10, floored, <0.5% of obj). |
| **PackCost (INNER)** | **[PROVEN] strongly NP-hard** | contains 2-D irregular packing / DSA + `1|rᵢ|ΣTᵢ` ([MODEL F5]). THE crux. |
| **OUTER given PackCost oracle** | as hard as INNER (coupled) | assignment/partition with a hard cost oracle. |
| **m=1 (single bay)** | **[PROVEN] NP-hard** | still full INNER (DSA). |

**Parameters that gate tractability** (and our [MEASURED] values):
- `m` bays = **2–5** (small) → fixed-m DP / enumeration plausible for OUTER & Z2.
- `k*` peak concurrency = **9–12** (borderline) → governs per-instant geometry size.
- height bands = blocks ≈ ⅓ H ⇒ **~3 bands** → potential 1-D structure.
- slack `D−R−P` = **tight, median 1–5** → small scheduling freedom (constrains search; 0 forced-tardy).
- non-convex **78–94%**, overhang **25–57%** → NFP costly; footprint-disjoint sidesteps it ([MODEL D4]).

---

## 3. Candidate decompositions of INNER (the comparison — this is the research)

INNER = "place set S in bay W×H over time, minimize ΣTᵢ". Four ways to cut it; **non-equivalent**. Score vs
C1–C4 and our measured params. (Footprint-disjoint assumed unless noted: crane vanishes, geometry = AABB.)

### D-A. Geometry ⊥ Schedule (fix the coexistence/topology, then time is free)
- **Cut**: decide the *topology* = positions + which pairs may coexist in time; then [MODEL F4] gives optimal
  e_i,f_i in poly. (= [DERIVATION D3] "search topology only".)
- **C1** weak? coupling = consistency (non-collision-free pair ⇒ time-disjoint). Medium.
  **C2** kernel = the topology choice (still NP-hard, but timing peeled off). **C3** none directly.
  **C4** matches metaheuristic-over-topology + LP decode.
- **Verdict**: best for the **UB / improver** (what we already do). Doesn't itself yield a bound or a tractable
  exact piece. **[in use]**

### D-B. Band / shelf decomposition (slice HEIGHT into ~3 bands)
- **Cut**: partition `[0,H]` into bands of block-height; within a band, packing-over-time ≈ **1-D strip /
  dynamic storage allocation** (x-axis + time). Coupling = blocks taller than one band / overhang spanning bands.
- **C1** weak iff few blocks span bands ([MEASURED] overhang 25–57% — NOT few; coupling is real).
  **C2** isolates each band to **1-D-over-time** where strong theory exists. **C3** exposes the band count (≈3).
  **C4** 1-D strip packing has **[KNOWN] APTAS** (Kenyon–Rémila); 1-D DSA has **[KNOWN] O(1)-approx**.
- **Use**: *snap* blocks to bands = a **restriction** ⇒ valid **UB heuristic** (fast, structured). (The LB use
  via projection is **invalid in our short-block regime — see §4.**) **[TO-PROVE]** whether band-snapping loses
  much vs free placement on our instances.

### D-C. Time-window decomposition (slice TIME at event points)
- **Cut**: at each interval between consecutive entry/exit events, a *static* set must pack disjoint in W×H.
- **C1** weak? NO — a block present across windows keeps ONE fixed position in all of them, so windows are not
  independent static packings; it collapses back to 2-D DSA. **C2/C3** weak.
  **C4** good for **bounds**: each window's concurrent set must fit ⇒ per-window packing/area necessary conditions.
- **Verdict**: mainly a **LB / feasibility-pruning** lens, not a solving decomposition.

### D-D. Concurrency-clique (parameter k\*)
- **Cut**: the hardest geometric atom is packing the **maximum concurrent set** (≤k\* footprints) at candidate
  points; the rest is sequencing.
- **C1** n/a. **C2** isolates geometry to ≤k\* objects. **C3** **exposes k\*** directly.
  **[CONJECTURE/known-ish]** packing ≤k\* footprints at the finite candidate-point set ([MODEL F3]) is **poly for
  fixed k\*** (enumerate placements) — so per-instant geometry is FPT-in-k\*; BUT the *sequencing* over all n
  blocks is NOT bounded by k\*, so PackCost is **not** FPT-in-k\* overall. **C4** feeds an **exact per-bay
  CP/MIP** whose per-node geometry is small.
- **Verdict**: route to **exact certification** on instances/bays where k\* is small enough ([MEASURED] 9–12 is
  borderline — test the threshold). **[TO-PROVE]** the practical k\* cutoff for CP-SAT/MIP convergence.

**Summary**: use **D-A for the UB**, **D-B (project) + D-C-area for the LB**, **D-D for exact certification**.
They are different lenses on the same fixed OPT — that is the point of §0.

---

## 4. Lower bounds (the compass — valid relaxations only)

Two SCOPES of LB, do not conflate:
- **LB on the FOOTPRINT-DISJOINT optimum** = the compass for OUR solver (it lives in this subclass). Tells us
  how near-optimal improver+polish is within footprint-disjoint; if LB≈UB we're optimal there and only interlock
  could do better.
- **LB on the TRUE optimum** (allows interlock) = needed to claim *global* optimality. Weaker tools only
  (interlock can pack denser ⇒ true OPT ≤ footprint-disjoint OPT).

[MEASURED] Area-cumulative LB ≈ 0 (pooled and per-bay) — area too loose ([memory] lb-area-too-weak). Why all
relaxations gave 0: they over-permit concurrency (anything fits by area) ⇒ no forced tardiness. Fix = a
NECESSARY condition for concurrent packing that is STRONGER than area.

- **[REFUTED for our regime] 1-D line-projection (Σwidth ≤ W) LB** — crossing a cut line is placement-dependent;
  forced only if h_i > H/2, but [MEASURED] blocks ≈ ⅓ H. Invalid here (caught on paper).

- **[N2 — LEADING, TO-IMPLEMENT] DFF-cumulative LB (Dual-Feasible Functions).** [KNOWN: Fekete–Schepers;
  Carlier–Clautiaux–Moukrim] For maximal DFFs `u,v:[0,1]→[0,1]`, any set packing in `W×H` satisfies
  `Σ_i u(w_i/W)·v(h_i/H) ≤ 1` (identity DFF ⇒ the area bound; non-trivial DFFs are STRICTLY stronger). Build the
  LB by: demand `d_i = u(w_i/W)·v(h_i/H)`, capacity 1; "at any instant Σ_present d_i ≤ 1" is a valid necessary
  condition ⇒ the cumulative total-tardiness relaxation under it is a valid LB; take **max over a DFF family**
  (e.g. the >½ rounding DFF `f(x)=1 if x>½, x if x<½, ½ at ½` catches "wide blocks can't be side-by-side", plus
  FS_k families). **SCOPE: bounds the footprint-disjoint optimum** (DFF necessity is for axis-aligned rectangle
  non-overlap = our subclass; NOT valid for the interlocking true problem, since interlocking footprints overlap).
  [CONJECTURE] gives LB>0 exactly on congested instances (area-ratio≈1: prob_27 1.03, prob_38 0.99) and 0 on
  slack ones (correct — their Z1≈0 so 0 is tight). Implementation: extend tools/lb.py — per-bay-assignment
  cumulative (CP-SAT) with DFF-transformed integer demands, capacity scaled to 1, loop a small DFF family, keep
  max. Compare against the footprint-disjoint UB (improver+polish, or CP-SAT per-bay from N3).

- **[TO-PROVE, weaker, TRUE-optimum scope] inscribed-rectangle 2-D no-overlap** (CP-SAT AddNoOverlap2D, shrink to
  inscribed rect ⇒ valid) and **area** — the only valid bounds on the TRUE optimum; likely loose. Use only if a
  global-optimality claim is needed.

- **[TO-PROVE] configuration / column-generation LB** — columns = concurrent packable sets; LP over active
  configs/period; pricing = 2-D packing feasibility (FPT-in-k\*, D-D). Strongest, heaviest; the eventual
  report-grade bound if DFF proves too loose.

A tight footprint-disjoint LB → know when to STOP improving (LB≈UB) vs switch to interlock, per instance, and is
report-worthy. The TRUE-optimum LB (harder) is what would let us claim provable global optimality.

---

## 5. Known theory we can plug in (see RESEARCH.md for sources)
- **[KNOWN]** 1-D strip packing **APTAS** (Kenyon–Rémila); 2-D bin packing APTAS variants — feed D-B.
- **[KNOWN]** `1||ΣT` pseudo-poly DP (Lawler); `1|rᵢ|ΣTᵢ` strongly NP-hard; **dynasearch** (poly search of an
  exponential neighborhood) — feed the scheduling half of D-A.
- **[KNOWN]** ATC + Time-Window-Decomposition dispatching — construction.
- **[KNOWN]** Logic-Based Benders (master = assignment+scheduling relaxation, sub = per-bay geometry, infeasible
  → no-good cut) — the principled exact-ish frame matching F1 + D-D.
- **[KNOWN]** Compaction/separation LP for fixed topology — local optimizer inside D-A.

---

## 6. Where our instances sit (the map located)
[MEASURED] All large (n=100–300), none "small". `m` small (2–5). `k*` borderline (9–12). ~3 height bands.
Tight slack. Heavy non-convex/overhang. ⇒ **No global exact**; per-bay exact only where k\* small (D-D).
Structure most exploitable along **height-bands (D-B)** and the **topology search (D-A)**. Weight regime spans
Z1-strong → Z3-heavy (12 Z3-sensitive instances) ⇒ OUTER must trade w1·ΔZ1 vs w3·ΔZ3 ([memory]
train-distribution-40).

---

## 7. Open nodes (develop rigorously, one at a time)
- **N1** [TO-PROVE] Band decomposition for the **UB**: conditions for band-snapping to be near-optimal; measure
  density loss vs free placement on real bays.
- **N2** [TO-IMPLEMENT] **DFF-cumulative LB** for the footprint-disjoint optimum (Fekete–Schepers necessary
  condition + cumulative tardiness relaxation, max over a DFF family). The compass for our solver. Extend
  tools/lb.py with DFF-transformed demands. (Inscribed-rect/area = weaker TRUE-optimum bounds, secondary.)
- **N3** [TO-PROVE] k\* threshold for exact per-bay (CP-SAT NoOverlap2D + tardiness): at what k\*/n does it
  converge? Which real bays qualify?
- **N4** [CONJECTURE] Is per-bay PackCost FPT in (k\* + horizon)? Tight slack ⇒ small horizon; combine with k\*.
- **N5** [TO-PROVE] OUTER given a PackCost surrogate/LB: fixed-m structure for the assignment trade-off.

## 8. Status / decisions
- F1 first cut accepted; INNER is the locus. UB lens = D-A (in use, improver scales [memory] solver-progress).
- Caught & removed an invalid LB (line-projection) on paper — §4.
- N3 done [memory n3-cpsat-packer]: CP-SAT proof-reach k≤30; CP-SAT > greedy packer; lever ranking
  assignment≫order≫packing; hybrid improver→CP-SAT-polish validated.
- Compass: **N2 DFF-cumulative LB IMPLEMENTED & CLOSED (negative).** DFF > area (prob_27: 77 vs 0, theory
  confirmed) but the DFF-relaxation optimum is PROVEN = 77 vs footprint-disjoint UB ~4376 — ~56x too loose.
  Definitive: no scalar per-instant capacity relaxation (area/DFF/any 1-D resource) can be tight here; tardiness
  is 2-D-arrangement-over-time, not capacity. Tight LB ⇒ configuration-LP/column-generation only (deferred,
  report-grade). Navigate by best-known + the N3 footprint-disjoint UB diagnostic (improver slack, not a valid LB).
- Standing: submission solver (FLOOR→improver→CP-SAT polish→GUARD) 40/40 feasible, best-known total 385.6M
  (891M start, −57%). Open: Z3-polish weakness when Z1=0 (improver lacks a move-to-preferred-bay op); congested
  frontier prob_27/38/40.
