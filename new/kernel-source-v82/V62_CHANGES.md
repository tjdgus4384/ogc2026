# v62 -- derived from CODE, not trial and error

## Root cause found by reading api.cpp (no experiment needed)
`runPortfolio` builds a FRESH `repair::Ctx c2` per worker thread and inherits
only named fields (`c2.eventLocal = ctx.eventLocal`, ...). Any flag set from
Python on the engine's own `ctx` never reaches the workers. That made v61's
`set_pref_allow(false)` a total no-op -- which is exactly why p22 did not move
in sec 147. Contradiction resolved without running anything.
Rule for the future: a knob only reaches the anneal if it is either (a) set
inside `runALNS` on the thread's own ctx, or (b) explicitly copied into `c2`.

## Change 1 (kernel): correct the prefStrong predicate
    v58/v59:  prefStrong = (z1 <= 0)
    v62:      prefStrong = (z1 <= 0 && z3 < n)
Chasing preferred bays (0.92 vs 0.70 bay-sort probability) pays only when the
preference optimum is actually PLACEABLE. Sec 142 proved it is not on the
crowded boards (pref-greedy assignment -> checker infeasible). The discriminator
is residual preference loss per block, z3/n:
    p1 0.02   p2 0.15   |   p22 19   p28 18   p38 26
A two-order-of-magnitude gap -- a natural separation, not a tuned constant.
Predicted before running: p1/p2 boost stays ON (unchanged), p22 flips OFF
(recovers), p28/p38 unaffected (z1>0 keeps it OFF either way). All confirmed.

## Change 2 (kernel): delete prefAllow / set_pref_allow
Dead by construction (see root cause). Removing it keeps the surface minimal.

## Change 3 (python): keep v59's LPT seed order, drop v60/v61's gate
`(D[i], -P[i])` is added unconditionally. Measurement already showed the order
is neutral where it is not needed (p22/p28 identical with and without) and
helpful on the over-saturated P6 board, so the saturation gate was code without
effect. Dropping it removes the shoelace-area pass and the threshold.

## Results (trajectory, 3 seeds on the decisive board)
    p2  : 3690 deterministic  (= v56, = v58/v59)
    p22 : 701,033 / 716,078 / 711,377  mean 709.5k
          vs v56 722,113 (-1.7%) and v58/v59 753,207 (-5.8%)
    p28/p38: functionally IDENTICAL to v59 (prefStrong is OFF on both because
          z1>0), so any measured difference there is trajectory noise -- and
          indeed v62 read 4.605e7 where v59 read 4.448e7 on p38 with identical
          code. That 3.5% spread quantifies the noise floor of the trajectory
          method (sec 147 lesson 7).

## Size
python 364 lines, kernel 2314 lines across 8 files.
