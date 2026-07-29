# solver/ — the engine

Whole pipeline in two layers. Read top-down; every file's job is one sentence.

```
myalgorithm.py   entry point. build engine -> best-of-6 seed orders ->
                 one continuous 4-thread anneal -> checker gate (nudge/move
                 legalization) -> _trivial safety net. Never raises.
src/
  geometry.hpp   positive-area polygon overlap, Shapely-equivalent (EPS 1e-9)
  crane.hpp      the checker's crane rule: layer k vs layer j>=k, entry/exit
  core.hpp       Problem, Sol, vpConservative (the 5 checker rules for one
                 block), evaluate (Z1/Z2/Z3)
  occ.hpp        bit-grid prefilter: certain same-layer overlap => reject.
                 Sound: whatever it admits still passes vpConservative.
  repair.hpp     THE insertion primitive: insertGreedy = earliest entry,
                 first fit, 3 phases (release day / exit events / dense net)
  alns.hpp       the search: 8 destroy ops -> due-order reinsert -> SA accept;
                 compactLeft; constructInitial; audit (dev assertion)
  api.cpp        Engine + pybind. API is grouped: shipped path (9 methods) vs
                 experiment tools (probes, arm-level A/B, state I/O).
```

Data flow of one solve:

```
_seed_free    6 insertion orders x reconstruct_ordered -> best objective
run_portfolio 4 threads anneal from the seed; threads split exploit/explore
              (removal size, T0, op subset 0x1F/0xFF, hill-climb tail);
              odd threads restart from noisy-EDD constructions (round 0);
              global best + compactLeft
_gate         official checker; on float-artifact violations: 1-unit nudges,
              then bay re-moves; adopt only if feasible
_trivial      one block at a time, sequential -- feasible by construction
```

Invariants that everything relies on:

- Every placement in the engine passed vpConservative at insertion. The
  rule-4/5 bbox early-outs are exact ONLY under this invariant; an operator
  that writes placements directly must re-verify (KERNEL_SPEC sec 176).
- The occupancy grid and areaUsed mirror the placed set exactly; removeBlock/
  stamp are the only mutators.
- The checker (utils.py) is the server's; treat float rejections at exact
  touch as artifacts to legalize around (sec 182), not as engine bugs.

Build: `./build.sh` (needs the py3.12 conda env; the grading server runs 3.12).
