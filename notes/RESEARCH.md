# OGC 2026 — Related Research & Existing Theory (single reference file)

What the literature offers for each sub-structure of MODEL.md, and how it maps to our problem. Used by
DERIVATION.md when choosing methods. Findings from a web sweep (sources at end).

## A. Problem-class identification
Our problem = **2-D irregular (nesting) packing with TIME as the open 3rd axis** + parallel-machine
tardiness scheduling with release dates + **crane (vertical-access) precedence** + assignment costs. Its inner
per-bay core is **2-D Dynamic Storage Allocation (DSA)** (MODEL F2).
**Note on the scheduling objective:** Z1 is **unweighted total tardiness** `Σ Tᵢ` (the global `w₁` is not a
per-block weight) → the sub-problem is `1|rᵢ|ΣTᵢ`. The weighted-tardiness literature below applies directly with
unit weights (`wᵢ=1`); e.g. ATC with `wᵢ=1` reduces to a slack/due-date priority. We keep the general `wᵢ` notation
since the methods are identical, but our case is the unit-weight special case.

## B. Geometry / irregular nesting (the spatial half)
- **No-Fit Polygon (NFP)** is the standard non-overlap primitive (Burke et al.; Bennell & Oliveira survey). Build
  for non-convex via convex decomposition + union (MODEL §2). NFP turns "no overlap" into "stay outside a region".
- **Placement policies:** bottom-left (BL), **lowest-gravity-center** (flatter frontier → better later fits),
  max-packing-ratio, min-boundary-length. Try several; cheap.
- **Fast scalable bottom-left-fill** with a **semi-discrete representation** (Leão et al., EJOR 2022) — practical
  for large instances.
- **Compaction & Separation LP/NLP** (Li & Milenkovic, EJOR 1995; Imamichi et al., EJOR 2009; Toledo et al.
  separation-lines, arXiv:1707.07177): given a *topology* (which side each pair sits), a **compaction LP** slides
  pieces to tighten the layout, a **separation LP** removes overlaps with minimal motion. This is the SOTA local
  optimizer inside SA/ILS for irregular strip packing. **Key mapping:** it is the *spatial twin* of our timing LP —
  fix topology ⇒ positions optimized by LP (polynomial). So **both time and space sub-optimizations are LPs given
  the topology**; the metaheuristic searches only the topology.
- **Hybrid GA + LP** (MDPI Appl. Sci. 13(22):12474, 2023): GA over order/orientation, LP compaction decode.
- **CG:SHOP 2024 "Maximum Polygon Packing"** (arXiv:2403.16203): a polygon-packing contest; typical strong entries
  = SA/ILS with translate/rotate moves over NFP/raster layouts + compaction. (PDF not text-extractable in sweep;
  treat as corroboration of the SA/ILS + compaction pattern, not a specific cited method.)

## C. Scheduling (the temporal half): weighted tardiness, release dates, parallel machines
- **Construction:** **ATC (Apparent Tardiness Cost)** dispatching, ideally with **Time Window Decomposition (TWD)**
  — the literature standard for `Σwⱼtardiness` with release dates; good solutions, small CPU.
- **Single-machine `1||Σwⱼtardiness`:** strongly NP-hard; `1||Σtardiness` is NP-hard but pseudo-poly (Lawler DP);
  `1|rⱼ|Σtardiness` strongly NP-hard. **Dynasearch** searches an exponential neighborhood in polynomial time (DP)
  — strong sequence-improvement move.
- **Parallel machines / very large neighborhoods:** Della Croce, Garaix & Grosso (2012) — ILS + very-large
  neighborhoods, SOTA for parallel-machine total tardiness; generalized pairwise interchange + machine-based moves.
- **ET-ALNS** (earliness/tardiness adaptive large neighborhood search) — reported to outperform other methods by a
  significant margin on parallel-machine weighted tardiness. Good primary improver.
- **Population-based ILS** with multiple neighborhoods + TWD seeding (Springer Optim. Lett. 2024) — diversification.
- **Unified ET heuristic + annotated bibliography** (arXiv:1509.02384) — broad method/bound catalogue.

## D. Exact frameworks (for small instances + lower bounds + report)
- **Time-indexed / NFP-linearized MIP** (Fischetti–Luzzi for the convex non-overlap encoding; non-convex needs the
  convex-decomposition AND-of-OR, MODEL §2). Gurobi 13 / Xpress 9.8 available on server.
- **CP / CP-SAT (ortools)**: interval vars + `cumulative` (capacity relaxation) + reified geometry; multi-threaded.
- **Logic-Based Benders Decomposition (LBBD)** (Hooker): master = scheduling/assignment relaxation, subproblem =
  per-bay geometric feasibility, infeasibility → no-good/lazy cuts. Natural fit; isolates the NP-hard geometry.
- **Branch-and-price / column generation:** master = set-partition of blocks into bay-packing patterns; pricing =
  per-bay packing. Strong bounds; report-worthy.
- **Lower bounds:** per-layer area-cumulative (MODEL F6), Lagrangian relaxation of coupling constraints, LP
  relaxation, preemptive 1-machine bounds. These both prune exact search and *measure the optimality gap*.

## E. Learning-based (CPU-only at eval; pretrain offline)
- **PackCost surrogate** (low-risk): a fast regressor predicting `PackCostⱼ(S)` to steer assignment search without
  running the full packer. Train offline on packer outputs.
- RL dispatching/placement policies (pointer/attention nets): report novelty, high risk, must run on CPU.

## F. The two-LP insight (consolidated)
Given a fixed **topology** (discrete shell), the problem splits into two *polynomial* LPs that return exact optima:
- **Timing LP** (MODEL F4): optimal `eᵢ,fᵢ` (TU difference constraints).
- **Compaction/Separation LP** (B): optimal positions for a fixed left/right/above/below topology.
⇒ design principle: **metaheuristics search topology only; both temporal and spatial optima are free from LPs.**
This is the unifying lever across the packing-SOTA and scheduling-SOTA literatures.

## Sources
- Bennell & Oliveira, *The geometry of nesting problems: a tutorial* (EJOR 2008); *survey* (2009).
- Li & Milenkovic, *Compaction and separation algorithms for non-convex polygons* (EJOR 1995).
- Imamichi, Yagiura, Nagamochi, *ILS based on NLP for irregular strip packing* (EJOR 2009).
- Toledo et al., *Irregular strip packing with separation lines* (arXiv:1707.07177).
- Leão et al., *fast scalable bottom-left-fill, semi-discrete representation* (EJOR 2022).
- MDPI Appl. Sci. 13(22):12474 (2023), *Hybrid GA + LP for 2-D irregular packing*.
- Fischetti & Luzzi, *MIP models for nesting with general polygons*.
- Della Croce, Garaix, Grosso (2012), *ILS + very large neighborhoods, parallel-machine total tardiness*.
- Springer Optim. Lett. (2024), *population-based ILS, parallel batch machines, release dates, tardiness*.
- arXiv:1509.02384, *unified heuristic + annotated bibliography, earliness-tardiness scheduling*.
- Hooker, *Logic-Based Benders Decomposition* (Math. Programming 2003).
- CG:SHOP 2024 *Maximum Polygon Packing* (arXiv:2403.16203).
- Lawler, *pseudo-polynomial DP for `1||Σtardiness`*; Della Croce et al., *dynasearch*.
- Note: past OGC problems differ yearly (2024 = bundle delivery routing), so prior winning code is not directly
  reusable; finalist code/reports are published open-source per the rules (useful for method style only).
