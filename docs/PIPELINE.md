# PIPELINE — the whole solver on one page

Control flow (`myalgorithm.py` → `sscore`):

```
algorithm(prob, TL)                      never raises; worst case _trivial
└─ _algorithm                            deadline = t0 + TL − 2.0
   └─ _arm_free
      ├─ _seed_free      6 insertion orders × reconstruct_ordered; keep best
      │                  engine + top-3 DISTINCT layouts (portfolio starts)
      ├─ run_portfolio   arms × anneal legs from {best seed, construction,
      │                  2nd, 3rd}; each leg = alns::run (destroy→reinsert→SA)
      │                  merge = best post-compaction objective
      ├─ _gate           checker → rescue ladder (nudge → retime → same-bay
      │                  move tight/loose → cross-bay move), deadline-guarded
      └─ candidate loop  gate failure ⇒ try runner-up thread solutions
   └─ fallback ladder    fresh seed → (time?) short anneal → gate → _trivial
```

Kernel call graph (one line per function, seams marked):

```
insertGreedy ── phase1 full scan @R ──┐
             ── phase2 exit events ───┼─ scanBayAt / scanLocal
             ── phase3 sparse sweep ──┘      │
scanBayAt: cornerSets → hitsSnap prefilter → vpConservativeAt → commitAt
           (candidates flattened in scan order; vpThreads>1 verifies chunks
            concurrently; first-feasible-in-order commit ⇒ trajectory is
            IDENTICAL to serial — vpThreads is a pure speed knob)
vpConservativeAt: the 5 checker rules, candidate side ALWAYS from arguments
           (rule1 restOverlapAt · rule2 entryOK · rule3 exitOK ·
            rule4/5 neighbour entry/exit with exact bbox early-outs)
alns::run: pick destroyer (adaptive weights, habitat masks) → remove →
           EDD reinsert (ops 8/9: forced-bay handoff) → SA accept → reheat
```

## Invariants (break one of these and the named failure returns)

1. Every placement passes vpConservativeAt at insertion. Rules 4/5's bbox
   early-outs are exact ONLY under this invariant (sec 171/176).
2. occ grid + areaUsed mirror the placed set exactly; the only mutators are
   commitAt/removeBlock/stamp (+bay-local best restore).
3. vp evaluates candidates WITHOUT state mutation; any new scan must pass
   the candidate through arguments, never through s (the restOverlap bug).
   Corollary for omp regions: a thread_local buffer filled OUTSIDE the
   region names a different (empty) object INSIDE it on every worker —
   bind plain local refs/pointers before the pragma (the vp4 OOB crash).
4. The gate is the only exit for solutions (checker byte-authority); the
   only checker-free return is _trivial (feasible by construction).
5. No stage starts work whose worst-case cost misses the deadline (server
   kills overruns): portfolio reserve, room(), budgeted fallback.
6. Z2/Z3 depend on assignment only (F1); ops that keep bays fixed cannot
   change them — the basis of priced-ejection gating and bay-local moves.

## Knob census — provenance-classified

[측정] = adopted through full-40 multi-draw judgment. [유산] = inherited,
validated only on the 1-core sandbox era — re-judgeable. [자의] = arbitrary —
first candidates for principled replacement.

| knob | value | site | class |
|---|---|---|---|
| deadline slack | TL − 2.0 s | _algorithm | [유산] |
| gate reserve | min(20, 4+0.05n, 0.12·TL) | _arm_free | [측정] (3 incidents) |
| room() margin | 1.3·ck_max + 0.5 | _gate | [측정] (overruns) |
| pref-first prob | 0.70 / 0.92 (prefStrong) | insertGreedy | [유산] v58 |
| area screen slack | ×1.02, orient-0 bbox | areaRoom | [유산] (+판정: 대안 2종 기각) |
| phase-3 stride | 6 (≤24 window: 1) | insertGreedy | [자의] |
| SA T0 | 0.002·obj | run | [유산] |
| cooling | 0.9995 | run | [유산] |
| reheat | sinceBest>1200 → 0.3·T0 | run | [자의] |
| hcFrac | 0.75 / 1.0 split | portfolio | [유산] |
| bigQProb | 0.04 (portfolio: 0 / 0.06) | run | [유산] |
| q small | 2..max(8, 3%·n) | run | [유산] |
| q big | 5–15%·n | run | [유산] |
| destroyer weights | decay 0.85, floor 0.05, score 3/1/0.3 | Destroyers | [자의] |
| op8/9 gates | w3·Z3 vs w1·Z1 (parameter-free) | run | [측정] |
| op8 pick | top-4 noisy, ±25% noise | pickRemoval | [자의] |
| portfolio splits | q ×{0.6,1,1.5}, T0 ×{0.5,1,2}, masks 0x1F/0x3FF | runPortfolio | [유산] (T0 split degenerate for 4 threads — measured no-fix) |
| rounds | 1 if sec>110 else 2 | _arm_free | [유산] |
| seed orders | EDD, slack, workload, LPT, 2×noisy(σ=8) | _seed_free | [유산] v59 |
| construct noise | σ=3.0 | constructInitial | [자의] |
| starts map | best/construct/2nd/3rd | _arm_free | [측정: 품질 중립, 보호관찰] |
| vp chunk | 16·vpThreads | scanBayAt | [자의] |
| horizon | (maxD+maxP+40)·hmul | _fresh | [유산] |

## Principled-replacement queue (strength via fewer knobs)

1. SA temperature: replace (T0, cooling, reheat) with acceptance-rate
   targeting (e.g., keep acceptance near a schedule by adjusting T) — 3
   knobs → 1 target curve; robust to instance-scale changes (new hidden
   instances!).
2. Destroyer credit: replace (decay, floor, scores) with a standard
   non-stationary bandit rule.
3. Seed orders: add ATC dispatch (the principled tardiness rule) and judge
   whether the hand-picked zoo can shrink.
4. stride/chunk/noise constants: derive from n or measure-and-fix.

Each replacement is judged distributionally (multi-seed, T60+T300+T600)
before adoption; canaries (p1 1499, p2 3690) plus the determinism check
(vp1 ≡ vp4 on a fixed reconstruction) run on every build.
