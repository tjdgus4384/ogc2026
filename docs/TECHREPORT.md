# OGC2026 Technical Report — draft (anonymous; ≤10 pages when typeset)

> Working draft for [OGC2026_TechReport]_MSGA.pdf (due Aug 6 14:00 KST).
> Keep ANONYMOUS in the final PDF: no team name, no author, no affiliation.
> Code bundle: [OGC2026_AlgorithmCode]_MSGA.zip = submissions/*-finals.zip
> + build.txt.

## 1. Problem understanding

Blocks must be assigned to bays, placed as polygons at integer positions
with a chosen orientation, and scheduled (entry/exit days) subject to five
feasibility rules — pairwise same-layer polygon collision, in-bay bounds,
and crane entry/exit accessibility (a block moves only through free
corridors above its layer). The objective mixes total tardiness (w1),
normalized peak workload imbalance across bays (w2), and bay-preference
regret (w3). Weights make tardiness dominant on saturated instances and
preference regret dominant once tardiness reaches zero.

Two measured structural facts organised our work:

1. **Geometry binds, capacity does not.** A CP relaxation that keeps only
   per-bay area budgets (ground-layer polygon areas over time) admits
   zero-tardiness schedules on every preliminary instance — even the one
   whose on-time bbox demand is 1.5× total bay area. The whole competition
   therefore lives in the gap between area feasibility and geometric
   (packing + crane) feasibility at congested time windows.
2. **Search fixed points are locally optimal at every neighbourhood scale
   we could reach.** Exhaustive single-block relocation sweeps, targeted
   window repacks, and branch-and-bound over joint
   (entry × orientation × position) schedules of congested windows all
   terminate with zero improvements on the annealer's outputs.

## 2. Algorithm

One pipeline solves every instance (no per-instance hand tuning; two
adaptive rules are keyed on runtime-computable structure).

### 2.1 Construction
Six deterministic insertion orders (EDD, slack, workload, due-with-long-
tie-break, two noisy-EDD draws) are reconstructed with a greedy
earliest-entry, first-fit corner placer; the best layouts seed the search.
An adaptive horizon ladder retries with doubled occupancy horizons on
congested instances.

### 2.2 Portfolio ALNS
Four annealing legs run in parallel with distinct personas (removal-size
multipliers, start temperatures, pure-SA vs hill-climb tails, operator
subsets), starting from distinct basins (best seed / fresh construction /
2nd / 3rd). Ten destroy operators feed an EDD reinserter built on the same
corner placer. Two directed operators do priced work no random removal can
compose: a preference ejection chain (evict from a preferred bay whoever
values it least, while the eviction price stays under the mover's gain)
and a tardy min-cut ejection (find the cheapest conflict set blocking an
on-time entry). Both are habitat-gated by a parameter-free dominance test
(w3·Z3 vs w1·Z1) so they never burn iterations outside their regime.

### 2.3 Structure-keyed configuration
Measured on the released preliminary instances (5 seeds each, official
time limits): restarting the portfolio from the merged best (rounds=2) and
a uniform preference-first insertion probability of 0.85 win on ≤3-bay,
under-saturated instances (P3 −2.1%, P4 −8.2%) and lose on 4-bay or
over-saturated ones. The final solver applies them exactly when
(bays ≤ 3) ∧ (bbox-area saturation < 1.0) — both computable from the
instance in microseconds.

### 2.4 Feasibility gate and rescue ladder
The official checker is byte-authoritative: every candidate solution
passes through it before submission, and gate failures walk a
least-destructive rescue ladder (nudge → entry/exit retime → same-bay
move → cross-bay move), deadline-guarded with a TL-scaled reserve. The
portfolio's runner-up solutions form a candidate set: checker artifacts
are a per-layout lottery, so a failed winner falls back to the next
candidate rather than to a fresh solve. A feasible-by-construction
trivial solution bounds the worst case.

### 2.5 Reproducible time governance
Wall-clock-driven search is machine-dependent: the identical submitted
binary, same seed, same time limit, produced 92,900 on our cluster
(byte-reproducible across nodes and repeats) and 87,140 on the grading
server — and no local time budget up to 3.75× reproduces the server draw.
The final solver therefore runs each portfolio round on a FIXED iteration
budget derived from instance structure and nominal time; the wall clock
only decides how many rounds fit (best-so-far is monotone in that count).
Trajectories become a function of (seed, round count): byte-identical
across machines per round count, with faster hardware yielding only
monotone extensions. We believe this transparency matters more at finals
scale than any residual tuning.

## 3. Judgment methodology

Per-instance draw spread is ±5–8%, so nothing was adopted on single runs:
2-seed screens filtered candidates, 4–5-seed distributional comparisons at
official time limits decided adoption, and every kernel build passes a
battery (fixed canary instances, full-state audits, fixed-iteration
same-seed determinism). Fifteen structural levers were refuted this way —
each with a mechanism, recorded to prevent re-litigation; the adopted
engine is the survivor set.

## 4. Results

Preliminary final board (rank 27/397): P1 11,280, P2 31,368 (universal
fixed points), P3 87,140, P4 3,259,259, P5 11,089,821, P6 32,950,799 →
finals qualification. The finals solver improves the P3/P4 class by
−2.1%/−8.2% (5-seed means at official TLs) with best local draws 86,625
and 3,118,626, and adds the reproducibility governance above.

## 5. What did not work (abridged)

Candidate-level parallel feasibility checking (fork/join dwarfs sub-µs
checks); depth-vs-breadth splits (4-persona breadth beats any deeper
fewer-arm split); fragmentation-aware best-fit placement scoring (fights
crane feasibility and kills orientation diversity); acceptance-targeted
temperature control (the anneal never freezes: acceptance holds ~0.5 at
long TLs); uniform larger ruins; window bulldozer repacks (plain and
area-certified); independent restart-max (no half-TL plateau);
deterministic single-relocation and pairwise polish (neighbourhoods
exhausted at fixed points); plan-seeded and assignment-locked pipelines
(locked search loses to free search; aggregate-area feasibility is far
from peak-window geometric feasibility).

## 6. Build

g++ -O3 -std=c++17 -funroll-loops -fopenmp -shared -fPIC
$(python3 -m pybind11 --includes) src/api.cpp -o sscore…so
(python 3.12, pybind11; single translation unit, ~1.7k lines C++,
~450 lines python driver).
