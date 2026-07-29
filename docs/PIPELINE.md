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
scanBayAt: cornerSets → hitsSnap prefilter → candidates ordered by fitScore
           (v6fit experiment: fragmentation-aware best-fit; v2.1's implicit
           order was bottom-left) → vpConservativeAt until first feasible →
           commitAt. vp effort matches first-fit; only the ORDER changes.
(2026-07-29 depth-bet CLOSED: speculative-parallel probes reached 1.38-2.0x
 but arms<4 LOST on the real boards -- P4 +60% at arms=1; 4-persona breadth
 is structural. The parallel machinery lives only in exp/v4spec history.)
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
| SA T0 | 0.002·obj | run | [유산] (동결 아님 실측: acc 0.48-0.63 @T20-120) |
| cooling | 0.9995 | run | [유산] (같은 실측; v5temp 대체 기각) |
| reheat | sinceBest>1200 → 0.3·T0 | run | [자의] |
| hcFrac | 0.75 / 1.0 split | portfolio | [유산] |
| bigQProb | 0.04 (portfolio: 0 / 0.06) | run | [유산] |
| q small | 2..max(8, 3%·n) | run | [판정: ×2/×3 확대 기각 (공식 TL 실보드)] |
| q big | 5–15%·n | run | [유산] |
| destroyer weights | decay 0.85, floor 0.05, score 3/1/0.3 | Destroyers | [측정: 사문화 — 실보드에서 전 op 바닥 = 균일 선택. 균일 대체(v13uni) 판정 중] |
| op8/9 gates | w3·Z3 vs w1·Z1 (parameter-free) | run | [측정] |
| op8 pick | top-4 noisy, ±25% noise | pickRemoval | [자의] |
| portfolio splits | q ×{0.6,1,1.5}, T0 ×{0.5,1,2}, masks 0x1F/0x3FF | runPortfolio | [유산] (T0 split degenerate for 4 threads — measured no-fix) |
| rounds | 1 if sec>110 else 2 | _arm_free | [유산] |
| seed orders | EDD, slack, workload, LPT, 2×noisy(σ=8) | _seed_free | [유산] v59 |
| construct noise | σ=3.0 | constructInitial | [자의] |
| starts map | best/construct/2nd/3rd | _arm_free | [측정: 품질 중립 + 시드×페르소나 교호 강함 (v12: exploit 밑 10.66M vs explore 밑 12.52M)] |
| horizon | (maxD+maxP+40)·hmul | _fresh | [유산] |

## The finals front (2026-07-29/30, real P1-P6 in data/preliminary_test)

Gap to rank-1 lives in: P3 pure Z3 (ours 494-516 vs ~350-390); P4/P5/P6 pure
Z1 (196-229 vs ~110 / 740-786 vs ~535 / 4731-4823 vs ~3700 days). CP-SAT
area relaxation proves Z1=0 possible on ALL boards (tools/z1bound.py) — the
binding constraint is GEOMETRY. frag_diag.py: 51.5%/53%/18.3% of tardy
blocks' waiting days (P4/P5/P6) had enough free area somewhere.

Closed fronts (each refuted by controlled measurement, 07-29/30):
- candidate-grain vp parallelism 0.69-0.76x; (bay,event)-grain reached
  1.38-2.0x but arms<4 lost the breadth (P4 +60%) — depth bet CLOSED.
- best-fit placement scoring (contact/BL/affinity): fights crane
  feasibility + kills ori-shuffle diversity; production +1.5~8.4%.
- SA-freeze hypothesis: false — acceptance holds 0.48-0.63 at long TLs.
- uniform bigger ruins (QMUL small-q x2/x3): all boards neutral-to-worse.
- op10 tardy-window bulldozer (plain AND area-certified): 5-seed wash,
  0 new-bests ever; ops 9/10 are passengers on the real boards.
- independent-restart-max: no half-TL plateau (second half still earns
  2-20%) — variance harvest would trade real convergence.
- compactPref (deterministic single-block Z3 sweep): runs in 0.0-0.3s and
  moves 0-1 blocks — the single-relocation neighbourhood is EXHAUSTED at
  the anneal's fixed points. P3's Z3 surplus is chain-locked.
- z3bound: aggregate area-day budgets admit Z3=0 even at util 0.7 — yet
  argmax-pref was 0-packable (June) — so peak-WINDOW geometry binds, not
  aggregate capacity.

CONVERGENT DIAGNOSIS (07-30): every board's gap reduces to one competency —
packing more blocks into preferred/on-time positions at congested windows
than corner-greedy + ALNS reaches. The fixed points sit 25-40% above best
known; single/double-move polish, bigger ruins, restarts, depth, placement
scoring are all measured dead ends around them.

- v12seed budgeted-preference plan seed @ exploit persona, 5 seeds:
  P3 -1.0~-1.4% (s9 86,625 BEATS the board 87,140) but P4/P5/P6 +0.6~0.9%
  -- wash overall. The plan basin wins rarely but with real Z3 structure
  (P5-s7 Z3 3134). Start x persona interaction is STRONG (same plan:
  10.66M under exploit, 12.52M under explore) -- seed value is defined by
  its polishing persona. Parked as a P3-conditional lever.

NEXT STRUCTURAL CANDIDATES (in order):
1. Exact window subproblem: at a congested (bay, window), solve the true
   placement problem exhaustively-ish (branch-and-bound over corner
   positions of the window's blocks with vp feasibility, seconds budget) —
   replace the window's layout wholesale when better. The deterministic
   big-neighbourhood the stochastic ops cannot compose.
2. Assignment-first construction executed properly: budgeted-preference
   assignment (z3bound machinery, util-tightened) + plan-chase packing as
   a P3-class portfolio seed (v9plan pattern, clean base).
3. If both fail: profile-guided speed (10x iterations at same quality
   would also move fixed points).

## Principled-replacement queue (strength via fewer knobs)

1. SA temperature: replace (T0, cooling, reheat) with acceptance-rate
   targeting (exp/v5temp built, unvalidated) — 3 knobs → 1 target curve;
   robust to instance-scale changes.
2. Destroyer credit: replace (decay, floor, scores) with a standard
   non-stationary bandit rule.
3. Seed orders: add ATC dispatch (the principled tardiness rule) and judge
   whether the hand-picked zoo can shrink.
4. stride/noise constants: derive from n or measure-and-fix.

Each replacement is judged distributionally (multi-seed, T60+T300+T600)
before adoption; canaries (p1 1499, p2 3690) plus the determinism check
(vp1 ≡ vp4 on a fixed reconstruction) run on every build.
