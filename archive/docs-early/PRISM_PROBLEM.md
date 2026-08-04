# Prism Problem — clean definition (for designing a near-optimal polygon algorithm)

Plain notation (no LaTeX). Subscript i = block index, j = bay index.

## Instance sizes & limits (measured on 40 training instances)
- n (blocks) in {100, 150, 200, 250, 300};  m (bays) in {2, 3, 4, 5}.
- Bay j is the rectangle [0, W_j] x [0, H_j]. Wide-short: W ~ 30..180, H ~ 15..29, W/H ~ 3..9, area A_j ~ 600..4000.
- Layers per block K: median 2 (range 1..4). Orientations O: median 8.
- Time is integer days. Horizon (max due date) ~ 50..140.
- Slack (D_i - R_i - P_i): tight, median 1..5, min 0. (No block is individually forced tardy.)
- Weights: w1 in [667, 29630], w2 in [1, 10], w3 in [13, 600].  (Z1 usually dominates; ~12 of 40 are Z3-sensitive.)
- Time limit: "a few minutes to ~30 minutes" wall-clock per instance (exact value hidden until contest ends).
- Server: 4 CPU cores, 16 GB RAM, no internet, no GPU. ortools / Gurobi / Xpress / shapely available.

---

## What each block is (PRISM approximation)
Each block i, in a chosen orientation o, is approximated by ONE polygon:
    U[i,o]  =  union of all its layer polygons  (the top-view silhouette, includes any overhang)
given in local coordinates (its reference point is the origin (0,0)).

Extruded over time it is a "prism": footprint U[i,o] held for the processing duration P_i.

Why this is valid (and nice):
- If two blocks' silhouettes U do not overlap (interiors disjoint) while they are both in the bay,
  then no layer of one can overlap any layer of the other => BOTH the layer-collision and the crane
  constraints are automatically satisfied, for ANY operation order.
- Consequently a block can always be removed the instant its processing is done:
    EXIT_i = ENTRY_i + P_i   (always achievable). So exit time is NOT a free variable; only entry is.

---

## Given (input)
For each block i:
  - R_i  release day      (cannot enter before this)
  - D_i  due day          (later exit => tardiness)
  - P_i  processing days
  - L_i  workload
  - S_ij preference score of putting block i in bay j   (integers, sum over j of S_ij = 100)
  - S_i^max = max over j of S_ij
  - For each orientation o = 0..O_i-1: the silhouette polygon U[i,o] (possibly non-convex), in local coords.
For each bay j: width W_j, height H_j, area A_j = W_j * H_j.
Global weights w1, w2, w3 >= 0.
Derived bay weight: u_j = avg_area / A_j, where avg_area = (sum over k of A_k) / m.
  (Bigger bay => smaller u_j.)

## Decide (per block i)
  - bay        a_i   in {0..m-1}
  - orientation o_i  in {0..O_i-1}
  - position   (x_i, y_i)   integers  (translate the reference point to (x_i, y_i))
  - entry day  e_i   integer
  (exit day f_i = e_i + P_i is forced; presence interval is [e_i, e_i + P_i).)

Notation: U_i := U[i, o_i] translated by (x_i, y_i)  = block i's actual footprint in its bay.

## Constraints
(1) Assignment: every block gets exactly one (a_i, o_i, position, e_i).
(2) Containment: U_i lies fully inside its bay:  0 <= x + corner <= W_{a_i} and similarly in y
    (equivalently: the axis-aligned bounding box of U[i,o_i] translated to (x_i,y_i) is inside [0,W]x[0,H]).
(3) Release:  e_i >= R_i.
(4) Non-overlap over time (THE geometric constraint):
    for any two blocks i != i' with a_i = a_{i'} (same bay) AND time overlap
        ( e_i < e_{i'}+P_{i'}  AND  e_{i'} < e_i+P_i ),
    their footprints are interior-disjoint:  interior(U_i) ∩ interior(U_{i'}) = empty.
    (Touching boundaries is allowed.)

## Tardiness
  T_i = max(0, e_i + P_i - D_i).

## Objective (minimize)
  minimize   w1 * Z1  +  w2 * Z2  +  w3 * Z3

  Z1 (total tardiness)      = sum over i of T_i
  Z2 (load imbalance)       = floor(  max over j of (u_j * Load_j)  -  min over j of (u_j * Load_j)  )
                              where Load_j = sum over i with a_i = j of L_i;  Z2 = 0 if m < 2.
  Z3 (preference penalty)   = sum over i of ( S_i^max - S_{i, a_i} )      (0 if every block in its best bay)

Note: measured magnitudes give roughly Z1 >> Z3 >> Z2, but the w1/w3 ratio ranges ~5..200 across
instances, so Z3 is NOT negligible on a sizeable minority — treat it as a genuine second objective.

---

## Structural facts you may exploit (all proven / measured)
F1 (separation): Z2 and Z3 depend ONLY on the assignment a. Z1 separates per bay given a. So
    optimum = min over a of [ w2*Z2(a) + w3*Z3(a) + w1 * (sum over j of PackCost_j(blocks of j)) ],
    where PackCost_j(S) = min total tardiness of packing block-set S into bay j over time.
F2 (timing is free): once you fix the assignment, orientations, positions, and the time-order of blocks
    in each bay, the best entry days e_i follow by a simple longest-path / LP and are integral. In the
    prism model it is even simpler since exit = entry + P. So the search is over (a, o, positions, order),
    NOT over the times directly.
F3 (finite candidate positions): you never need the full integer grid. Snug placements live at
    "no-fit-polygon" vertices (positions where U_i just touches another U_{i'} or a wall). Enumerate those
    (and their integer neighbors), a finite set, instead of all (x,y).
F4 (per-bay = packing-over-time): inside one bay the problem is "place silhouettes U over an open time
    axis (3rd dimension), non-overlapping, minimizing total tardiness" = 2D irregular packing + scheduling.

## Brute force (why naive enumeration fails)
Per block, naive choices ~ m (bay) * O (orient) * A (integer positions) * THorizon (entry day).
Joint space ~ (m * O * A * THorizon)^n  => exponential in n. Strongly NP-hard (contains 2D irregular
packing, parallel-machine total tardiness with release dates, and dynamic storage allocation).
Feasibility check of one full solution: O(n^2 * polygon-pair-intersection-cost).
=> exact enumeration is hopeless at n=100..300; you want a constructive/decomposition/approximation scheme
   that exploits F1-F4.
