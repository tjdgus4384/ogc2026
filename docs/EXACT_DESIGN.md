# EXACT — whole-horizon search design (Track 2 build, started 07-30)

Goal: close the P4-class rank-1 gap (1.908M vs our 3.24M) that every
measured local/steering mechanism cannot reach. Evidence constraints the
design must respect:
- Window-local realizations are optimal (600-repack, all-scale probes)
  => value lies ONLY in cross-window global coupling.
- Area relaxation is loose (CP-SAT Z1=0 vs geometric reality).
- Grading env: 480s, 4 cores, no ortools; python + own native code.

## Architecture: best-first block-schedule DFS with geometric oracle

State: prefix of EDD-ordered blocks placed (the EDD order is measured
right); each placed block has (bay, entry, x, y, ori).
Branching per block: candidate (bay, entry) pairs only -- position is
delegated to the kernel scan (first-feasible at that bay/entry; the
probes showed position choice within (bay, entry) does not matter).
Entry candidates: earliest-feasible e0 per bay PLUS a bounded set of
DELAYED entries {e0+d} -- delays are the one degree of freedom greedy
construction can never express (measured: greedy always earliest), and
cross-window value must come from somewhere.

Bounds/pruning:
- g = exact objective of the prefix (weighted tardiness of placed).
- h = per-remaining-block lower bound: w1*W_i*max(0, R_i+P_i-D_i ... )
  plus a bay-capacity relaxation over remaining area (CP-SAT-free greedy
  bound; must be monotone and cheap).
- Dominance: two prefixes with the same placed SET where one has
  per-bay-per-day occupancy pointwise <= the other AND g <= -- prune the
  dominated (approximate via occupancy hashes; start without).
- Beam fallback: pure best-first will blow up; run as anytime beam
  (width B, restart with wider B while budget lasts) so it degrades
  gracefully to the greedy solution.

## Milestones
1. M1: python prototype over the existing engine primitives
   (insert_capped for (bay, eCap) branching = entry-days via caps;
   snapshot/restore via get/set_solution). Validate on P4 at tiny beam:
   does ANY delayed-entry branch beat greedy construction after anneal
   ... NO -- after the anneal-wash lesson, judge the tree's LEAF boards
   directly (no anneal) vs our final boards. Target: any leaf < 3.24M.
2. M2: if M1 shows signal, port the tree search to C++ (api-level),
   4-thread parallel beam, calibrate width to 480s.
3. M3: integrate as the P4-class pipeline arm (spec-keyed: sat>=0.6
   boards), judge 3 seeds, all boards.
KILL CRITERIA: M1 finds no leaf below greedy-anneal boards at beam
width ~hundreds => the delayed-entry dimension is empty too, and the
exact route dies with it (then the honest ceiling stands).

## M1 RESULT (07-30): KILLED by its own criterion
Width-40 beam, delays {0,3,8}: best leaf 31,654,641 vs anneal 3,244,599
(10x worse), 655s > budget. The myopic frontier cannot compete with the
anneal's global rearrangement; no admissible h closes a 10x gap in 480s.
The whole-horizon tree route is dead. Kept for the record.
