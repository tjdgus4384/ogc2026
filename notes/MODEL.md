# OGC 2026 — Rigorous Problem Model (foundation; must be error-free)

The single source of truth. Everything in DERIVATION.md is deduced from here. Restating the problem is fine; being
wrong is not. Items not fully certain from the PDF are flagged **[verify]** (confirm against the official
`utils.py` / latest problem doc).

---

## 1. Data (given per instance)
- Bays `M={0..m−1}`. Bay `j` is the axis-aligned rectangle `[0,Wⱼ]×[0,Hⱼ]`; area `Aⱼ=WⱼHⱼ`.
- Blocks `N={0..n−1}`. Block `i` has `Kᵢ` layers (all layers the same height; layer 0 = base; no empty/floating
  layers, adjacent layers attached) and `Oᵢ` orientation options.
- For orientation `o∈{0..Oᵢ−1}` and layer `ℓ∈{0..Kᵢ−1}`: a polygon `P_{i,o,ℓ}` given by its vertex list
  (possibly **non-convex**; vertices fractional, ≤4 significant digits). The first vertex of layer 0 is the
  **reference point** `(0,0)`; all other vertices are relative to it. **[verify]** whether `Kᵢ` is constant across
  orientations — **RESOLVED (utils.py):** each orientation's `shape[o]["layers"]` is an *independent* list, so
  layer count and per-layer polygons are **per-orientation** (`K_{i,o}`); collision/crane use `min(len A,len B)`
  over the two *chosen* orientations. utils re-anchors by subtracting layer-0's first vertex, so it tolerates a
  non-`(0,0)` reference, though the generator guarantees `(0,0)`.
- `Rᵢ` release, `Dᵢ` due, `Pᵢ` processing time, `Lᵢ` workload, `Sᵢⱼ` preference (integers; `Σⱼ Sᵢⱼ = 100`),
  `Sᵢᵐᵃˣ = maxⱼ Sᵢⱼ`. Weights `w₁,w₂,w₃ ≥ 0` (integers). Time = integer days. All indices 0-based.
- Derived: `Ā = (Σₖ Aₖ)/m`,  bay weight `uⱼ = Ā / Aⱼ` (larger bay ⇒ smaller weight).

## 2. Geometry primitives
**Placement.** If block `i` uses orientation `o` at integer position `(xᵢ,yᵢ)`, its layer `ℓ` occupies
`P_{i,o,ℓ} ⊕ (xᵢ,yᵢ)` (translation). Two shapes *collide* iff their **interiors** intersect (shared edges are
allowed = not a collision).

**No-Fit Polygon (NFP).** For polygons `A` (frame of block `i`) and `B` (frame of block `i'`), with relative
offset `δ := (xᵢ'−xᵢ, yᵢ'−yᵢ)`:
`int(A⊕(xᵢ)) ∩ int(B⊕(xᵢ')) ≠ ∅  ⟺  δ ∈ int(nfp(A,B))`, where `nfp(A,B) := A ⊕ (−B)` (Minkowski sum, `−B` =
`B` reflected through the origin).
- Convex `A,B`: `nfp` is convex, built by angular edge-merge in `O(|A|+|B|)`.
- Non-convex: decompose `A=⋃ₐAₐ`, `B=⋃_b B_b` into convex pieces; since Minkowski sum distributes over union,
  `nfp(A,B)=A⊕(−B)=⋃_{a,b} (Aₐ⊕(−B_b))` (a union of convex polygons, possibly with holes). **"δ outside a
  non-convex region" is NOT one OR-of-edges.** The *exact*
  test is `δ ∉ int(Φ)` where `Φ` is the **merged** union (compute its interior via a polygon Boolean op, then a
  point-in-polygon test). A per-piece encoding `∀k: δ∉int(Cₖ)` (MIP: AND over pieces of OR over each piece's edges,
  binary per (piece,edge) + big-M) is only a **necessary / conservative** condition: at an internal seam shared by
  two pieces, a point can be outside every `int(Cₖ)` yet inside `int(⋃ₖCₖ)`, so the per-piece form forbids some
  *legal edge-touching* placements (a measure-zero loss, usually harmless, but note it where exactness matters).
  For the authoritative feasibility check, use the merged region (and the provided `utils.py`).

**Collision region** (governs C4, same level): `Φ_{ii'} := ⋃_{ℓ ≤ min(Kᵢ,Kᵢ')−1} nfp(P_{i,oᵢ,ℓ}, P_{i',oᵢ',ℓ})`.
Blocks `i,i'` are collision-free at offset `δ` ⟺ `δ ∉ int(Φ_{ii'})`.

**Crane region** (governs C5; asymmetric, "lift `i` straight up past placed `i'`"):
`Ψ_{i→i'} := ⋃_{ℓ₁ ≤ ℓ₂} nfp(P_{i,oᵢ,ℓ₁}, P_{i',oᵢ',ℓ₂})` (over `ℓ₁∈{0..Kᵢ−1}`, `ℓ₂∈{0..Kᵢ'−1}`, `ℓ₁≤ℓ₂`).
`i` is crane-clear of `i'` ⟺ `δ ∉ int(Ψ_{i→i'})` (with `δ = posᵢ' − posᵢ`). The reverse relation uses the
**reverse offset**: `i'` is crane-clear of `i` ⟺ `(−δ) ∉ int(Ψ_{i'→i})` where `Ψ_{i'→i} := ⋃_{ℓ₁≤ℓ₂}
nfp(P_{i',oᵢ',ℓ₁}, P_{i,oᵢ,ℓ₂})` (now `i'` is the craned block, `i` the obstacle). `Ψ_{i'→i}` is a *different*
region from `Ψ_{i→i'}` and is evaluated at `−δ`, not `δ`. Because `{ℓ₁=ℓ₂} ⊆ {ℓ₁≤ℓ₂}`, **`Ψ_{i→i'} ⊇ Φ_{ii'}`**.

**Inner-Fit Polygon / containment (C3).** A polygon lies inside an axis-aligned rectangle ⟺ its AABB does
(**exact**, no approximation). With `(i,o)`'s footprint AABB `[xₘ,x_M]×[yₘ,y_M]` (relative to reference), the
allowed reference positions in bay `j` are the rectangle `IFP_{i,o,j} = [−xₘ, Wⱼ−x_M] × [−yₘ, Hⱼ−y_M]`.

## 3. Decision variables (per block i)
- `a_{ij}∈{0,1}` bay assignment, `Σⱼ a_{ij}=1` (exactly one bay).
- `b_{io}∈{0,1}` orientation, `Σₒ b_{io}=1`.
- `(xᵢ,yᵢ)∈ℤ²` reference position (integers; server rounds).
- `eᵢ` = ENTRY day, `fᵢ` = EXIT day. Presence interval `[eᵢ, fᵢ)` (half-open).
- A block is placed once and **never moves/rotates**; exactly one ENTRY and one EXIT.
- `Tᵢ := max(0, fᵢ − Dᵢ)` tardiness.

## 4. Constraints
**(C1) Assignment / (C2) orientation:** `Σⱼ a_{ij}=1`, `Σₒ b_{io}=1`.

**(C3) Containment:** `(xᵢ,yᵢ) ∈ IFP_{i,oᵢ,j}` for the chosen `j,oᵢ` (§2).

**(C4) Spatial collision (same level, whole coexistence):** for any two blocks `i≠i'` in the **same** bay whose
intervals overlap (`[eᵢ,fᵢ) ∩ [eᵢ',fᵢ') ≠ ∅`): `δ_{ii'} ∉ int(Φ_{ii'})`. Equivalently, the central disjunction
per same-bay pair:
```
a_{ij}=a_{i'j}=1  ⟹  ( fᵢ ≤ eᵢ' )  ∨  ( fᵢ' ≤ eᵢ )  ∨  ( δ_{ii'} ∉ int(Φ_{ii'}) )
                       └──── time-disjoint ────┘     └─ space-disjoint (per level) ─┘
```

**(C5) Crane (vertical access at each ENTRY and EXIT):** the bay changes after every operation, and within a day
**all EXIT ops precede all ENTRY ops**; the order among same-type ops is also a decision. Let the solution induce a
**total order of all `2n` operations** (each block's ENTRY before its EXIT; same-day EXITs before same-day ENTRYs).
At the moment block `i`'s ENTRY (resp. EXIT) op executes, the *present set* is exactly the blocks in `i`'s bay at
that point of the order — those entered earlier in the order and not yet exited. For every such present `i'`, `i`
(the moving block) must be crane-clear of `i'`: `δ_{ii'} = posᵢ' − posᵢ ∉ int(Ψ_{i→i'})`. The present set depends
on the **operation order**, not day indices alone (half-open intervals + EXIT-before-ENTRY mean a block leaving on
day `d` is absent for an op arriving on day `d`). **Tie convention (per `utils.py` Stage 5):** at a single time
point, all EXIT ops execute before all ENTRY ops; among same-type ops the listed order is the executed sequence and
the present set is updated op-by-op — so same-time ordering is a genuine decision the solver controls.

**(C5′) Crane ⇒ directed time-precedence (a derived consequence, used by the scheduler).** Consider a coexisting,
collision-free pair (`δ∉Φ`) and their crane relation:
- `i` obstructed by `i'` only (`δ∈Ψ_{i→i'}`, `δ∉Ψ_{i'→i}`): `i` can be craned only when `i'` is absent ⇒ `i'`
  must be **time-nested inside `i`'s stay**: `eᵢ ≤ eᵢ'` and `fᵢ' ≤ fᵢ`.
- `i'` obstructed by `i` only (`(−δ)∈Ψ_{i'→i}`, `δ∉Ψ_{i→i'}`): the symmetric nesting `eᵢ' ≤ eᵢ` and `fᵢ ≤ fᵢ'`.
- **both directions** (`δ∈Ψ_{i→i'}` AND `(−δ)∈Ψ_{i'→i}`): each would need the other strictly nested inside itself ⇒
  impossible ⇒ they **cannot coexist** ⇒ forced **time-disjoint** (`fᵢ≤eᵢ'` or `fᵢ'≤eᵢ`).

**Verified interlock example (counterintuitive — implement carefully).** Tall block A (layer0 narrow, layer1 wide
overhang) over short block B sitting under the overhang: B and A are collision-free (Stage 4 compares same-index
layers only; B has just layer 0). But B is obstructed by A's upper layer on **both** its ENTRY *and* EXIT (the
crane lowering/lifting B sweeps through A's overhang, `j≥k`), while A is craned **freely** past B (`A.layer1` vs
`B.layer0` has `j=0<k=1`, not checked). ⇒ one-way: B obstructed by A. By C5′ the **obstructer A nests inside the
obstructed B** (`e_B≤e_A`, `f_A≤f_B`): B must be present-longest and wrap A. *Empirically confirmed against
`utils.py`* — the naive "B nested in A" is INFEASIBLE (B can't even enter under A); "A nested in B" is FEASIBLE.

## 5. Objective
`minimize  w₁·Z1 + w₂·Z2 + w₃·Z3`, with
- **Z1 (total tardiness):** `Z1 = Σᵢ Tᵢ = Σᵢ max(0, fᵢ − Dᵢ)`.
- **Z2 (max normalized workload imbalance):** with `L(j)=Σ_{i∈j} Lᵢ`,
  `Z2 = floor( max_{j≠j'} | uⱼ·L(j) − uⱼ'·L(j') | ) = floor( (maxⱼ uⱼL(j)) − (minⱼ uⱼL(j)) )` (range of weighted
  loads, then floored to an integer). If `m<2`, `Z2=0`. **CONFIRMED from `utils.py` `check_feasibility`** (the
  outer `math.floor` was the only correction vs the earlier reading; the "range" form is exact).
- **Z3 (preference regret):** `Z3 = Σ_j Σ_{i∈j} (Sᵢᵐᵃˣ − Sᵢⱼ) = Σᵢ (Sᵢᵐᵃˣ − Σⱼ a_{ij}Sᵢⱼ)` (=0 iff every block is
  in its most-preferred bay).

## 6. Proven structural facts (true properties of the model)
**F1 — Objective factorization.** `Z2` and `Z3` depend **only on the assignment `a`** (closed form, no timing/
geometry). `Z1` **separates per bay** (each `fᵢ` is determined inside `i`'s own bay; bays have independent cranes
and space). Hence `min = min_a [ w₂Z2(a) + w₃Z3(a) + w₁ Σⱼ PackCostⱼ(a⁻¹(j)) ]`, where
`PackCostⱼ(S)` = min total tardiness of packing block-set `S` into bay `j`.

**F2 — Inner problem = 2-D Dynamic Storage Allocation.** Per bay, (C4) says *time-overlapping blocks must be
space-disjoint (per level)* — exactly 2-D DSA (offsets = positions, lifetimes = intervals), plus the directed
crane precedences (C5′).

**F3 — Candidate-point finiteness (with the integer-grid caveat).** For a block placed over a fixed interval among
fixed temporally-overlapping blocks, the feasible reference set is the rectangle `IFP` minus a union of translated
`Φ` obstacles — a bounded region. In the *continuous* relaxation a bottom-left-minimal feasible point lies at a
region **vertex** (rectangle/NFP edge intersection). **But positions are integers** (`utils.py` rounds to int), so
the continuous vertex need not be feasible/optimal on the lattice. Correct finite candidate set: the **integer
points** near each region vertex — concretely `⌈·⌉`/`⌊·⌋` of each vertex's coordinates (and a small neighborhood),
intersected with the feasible region — still **finite**, but the strict "optimum at a continuous vertex" is replaced
by "search the region's integer points (seeded at the snapped vertices)". So placement search is finite/discrete,
not over the continuous plane; just enumerate integer candidates, don't assume the continuous vertex is the answer.

**F4 — Timing is polynomial (given the discrete shell).** Define the **discrete shell** =
`{ a, b, (xᵢ,yᵢ) for all i, and a feasible total operation order }`. The total order fixes every pairwise
time-overlap (hence which C4 space-constraints are active) and every present-set (hence which C5 crane constraints
apply), and must be consistent with the fixed positions (a non-collision-free pair must be time-disjoint; a one-way
crane obstruction imposes its nesting; a two-way one is forbidden, C5′). Given the shell, the only remaining
variables are the integer days `eᵢ,fᵢ` (and `Tᵢ`), and **all** remaining constraints are difference constraints:
`eᵢ ≥ Rᵢ`, `fᵢ − eᵢ ≥ Pᵢ`, order/precedence `(·) − (·) ≥ 0`, `Tᵢ − fᵢ ≥ −Dᵢ`, `Tᵢ ≥ 0`; objective `Σ Tᵢ` linear.
This constraint matrix is **totally unimodular** ⇒ the LP has an **integral** optimum, computable in polynomial
time (dual = shortest/longest path on the precedence graph). *All hardness is in choosing the shell; timing is a
free exact subroutine.* (This does **not** make the whole problem polynomial — see F5.)

**F5 — Hardness.** The full problem is **strongly NP-hard**: it contains (a) 2-D (irregular) packing / orthogonal
packing, (b) total tardiness scheduling with release dates `1|rᵢ|ΣTᵢ` (Z1 is **unweighted** — every block counts
equally; the global `w₁` is not a per-block weight; `1|rᵢ|ΣTᵢ` is strongly NP-hard, while `1||ΣTᵢ` without release
dates is only pseudo-poly), and (c) DSA — each strongly NP-hard.
⇒ no polynomial or pseudo-polynomial exact algorithm in general (unless P=NP). Exact methods are viable only for
small/structured instances; large instances need heuristics + bounds.

**F6 — Valid lower bounds.**
- `LB0` (per block): each block exits no earlier than `Rᵢ+Pᵢ` ⇒ `Z1 ≥ Σᵢ max(0, Rᵢ+Pᵢ−Dᵢ)`. The bound is
  **attained** ⟺ every block can be scheduled in its **earliest window** `[Rᵢ, Rᵢ+Pᵢ)` *simultaneously feasibly*
  (a scheduling-feasibility condition). Pairwise footprint-disjoint placement ("safe mode") is a **sufficient**
  condition for that attainability, **not** an equivalent one (a block may still hit its earliest exit while
  interlocked, as long as nothing blocks it); conversely collision-free-alone is **not** sufficient (a crane
  obstruction can force waiting/nesting). So: *safe ⇒ LB0 attainable*, but not iff.
- `LB1` (per-**layer** area-capacity): for each level `ℓ`, the layer-`ℓ` polygons of concurrent blocks are disjoint
  in a region of area `Aⱼ` ⇒ `Σ`(layer-`ℓ` area of concurrent blocks with `Kᵢ>ℓ`) `≤ Aⱼ`. This *per-level* area is
  a valid necessary condition ⇒ a cumulative-resource **total-tardiness** relaxation per level; take the max over
  levels. **Do NOT** use top-view footprint area (interlock lets footprints overlap, so footprint-area capacity
  excludes feasible solutions and is not a relaxation). **Do NOT** use `Σwidth≤Wⱼ` / `Σheight≤Hⱼ` (blocks stack
  along the other axis, so these are not necessary conditions).
- `LB2`: since `Z2,Z3` depend only on `a` (F1), a pure assignment/partition relaxation bounds them.

## 7. Feasibility devices
- **Safe mode = a *certificate*, not an existence guarantee.** *If* a set of co-present blocks is placed pairwise
  footprint-disjoint (`δ∉Φ`, `δ∉Ψ_{i→i'}`, `(−δ)∉Ψ_{i'→i}` — projected footprints disjoint), *then* C4 and C5 hold
  automatically for any operation order. But such a disjoint layout **need not exist** for a given concurrency
  (the bay may be too small to hold all those footprints at once). So safe mode certifies feasibility *when it
  succeeds*; it does not guarantee a high-concurrency solution exists.
- **Serialize = the existence guarantee.** Processing a bay's blocks one at a time (next ENTRY ≥ previous EXIT) is
  the special case of safe mode with **zero temporal overlap**, so it always admits a footprint-disjoint layout
  (only one block present at a time) and is **always feasible** provided each block individually fits its bay
  (guaranteed by the instance). Worst-case high tardiness, but **never infeasible**.
- **Therefore** the leaderboard `−1` (infeasible/crash/timeout) is always avoidable: keep a serialize-based
  incumbent as the floor, improve toward safe-mode concurrency and selective interlock above it, and validate the
  final solution with `utils.check_feasibility`.

## 8. RESOLVED against the provided `utils.py` (authoritative; server overwrites it)
1. **Z2:** `floor(range of uⱼL(j))`, `0` if `m<2` (§5). ✔
2. **Layer count varies by orientation:** each `shape[o]["layers"]` is independent; collision/crane use
   `min(len A, len B)` and `j≥k` over the *chosen orientation's* layers. So `Kᵢ` is per-orientation — do **not**
   assume constant. ✔
3. **Crane = the j≥k rule** (vertical crane): lowering/lifting block layer `k` sweeps through every height `j≥k`;
   `j=k` final-position collision, `j>k` descent/ascent sweep, `j<k` never collides. Matches `Ψ_{i→i'}` (§2). ✔
4. **Feasibility = 5 sequential stages** (first failure returns): S1 assignment+timing (1 ENTRY & 1 EXIT per block,
   valid bay/orient, `entry≥release`, `exit−entry≥proc`, tol 1e-6); S2 crane-entry vs present set
   `{k: entry_k < entryᵢ < exit_k}` (strict); S3 crane-exit vs `{k: entry_k < exitᵢ < exit_k} ∪ {self}` (strict);
   S4 per-level collisions among time-overlapping pairs + AABB containment; S5 **chronological replay** with a
   per-bay `present` set, **all EXIT before ENTRY at a time point**, crane re-checked against the live set. A
   solution must pass **all five** (S2/3 snapshot AND S5 sequential). ✔
5. **Positions** are `int(round(x)), int(round(y))`; reference = first vertex of layer 0; collision uses polygon
   interiors (`area>0`; shared edges are not collisions). Weights default to 1.0 if absent. ✔
6. **Containment** = AABB of *all* layers of the chosen orientation inside `[0,Wⱼ]×[0,Hⱼ]` (`bounding_rect`,
   inclusive `≤`). Exact for this placement model (no rotation of placed blocks). ✔
7. **Crane dominates collision:** Stage-2/3 `j≥k` includes `j=k`, so `Ψ_{i→i'} ⊇ Φ_{ii'}` is confirmed in code —
   a mutually crane-clear coexisting layout is automatically collision-free; crane is the binding geometric
   constraint to design against. ✔
8. **Numeric regime — MEASURED on the 8 real training instances (data/train): effectively `Z1 ≫ Z3 ≫ Z2`.**
   Real weights: `w1∈[10000,29630]` (one outlier prob_40 `w1=667`), `w2∈[1,7]`, `w3∈[13,200]`. Measured baseline
   objective decomposition: `w1·Z1` dominates by orders of magnitude whenever `Z1>0` (e.g. prob_4 `Z1=183` →
   ~4.0M of 4.12M total). `w2·Z2` is always negligible (`Z2` floored, `w2≤7` ⇒ ~0.1–0.4% of total). `w3·Z3`
   becomes the **deciding term once `Z1→0`** (prob_5: `Z1=0`, residual obj 133.5k = `w2·2127 + w3·791`, of which
   Z3 is 118.7k = 89%). **Consequence:** treat the objective as near-lexicographic **minimize Z1, then Z3, ignore
   Z2** — NOT a balanced multi-objective. (Supersedes the earlier "genuinely multi-objective" reading, which was
   extrapolated from the single PDF example, not the real set.)
Our internal solver must reproduce these exactly; for the final feasibility check, call this `utils.py` itself.
