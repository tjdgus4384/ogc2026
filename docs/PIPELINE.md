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
   (Executable framework, both directions of a change:
    BEFORE implementing -- `python tools/preflight.py <class>` prints
    the law-derived prediction and required validation chain for
    kernel-semantics / kernel-speed / driver / knob / zip changes.
    AFTER implementing -- `python tools/test_driver.py` asserts the
    dispatch table, spec gate, env precedence, and invariant 7's driver
    half in seconds; tools/battery.sbatch is the kernel half: build,
    audits, canaries 1499/3690, fixed-iteration determinism.)
7. Simultaneous-exit LOCKSTEP: _recs_to_sol emits same-day EXITs later-
   entry-first (myalgorithm sort key -e), and vp assumes exactly that in
   rule 3 (f[k]==fN -> obstacle iff e[k]<eN) and rule 5 (f[k2]==t ->
   obstacle iff e[k2]<e[k]). Change either side alone and vp diverges
   from the checker: moves get accepted that the gate then rejects
   (rescue churn, budget loss), or legal moves get vetoed (quality loss).

## Architecture boundary decisions (declined on principle, 07-30)

- C++ knobs live in src/knobs.hpp (one file, layer-grouped accessors,
  magic-static once-per-run semantics) -- the consolidation that buys
  at-a-glance mastery at zero hot-path cost. Going further to a plumbed
  per-layer parameter STRUCT was declined: it would thread state through
  RunParams/Ctx for zero behavioural gain and touch every hot signature
  days before finals (pure L2 risk, no score).
- Replacing _spec_rule's (m<=3, sat<1.0) gate with a direct Z3-share
  gate was declined as CIRCULAR: Z3-share is a property of the solved
  board; (m, sat) is the runtime-computable predictor of it, and it is
  what the 5-seed measurements judged. The Z3-share alignment is the
  gate's EXPLANATION, not an implementable replacement.
- STRIDE/phase-3 stays despite being unreached at official TLs: it is
  the reachable last-resort on short-TL/small boards (train canaries),
  i.e. a safety net, not dead code.

## Change-prediction laws (what happens if you touch X)

L1 REJECT-FREE SCAN: a prefetched linear resident scan rejects at
   ~1-2ns/entry — visit-reduction schemes (indexes, masks, memos,
   screens) are bounded by that ~0 and were all measured/derived dead.
   vp cost = qualifying work only: restOverlap on truly-overlapping
   pairs, pres rebuilds, entryOK/exitOK conjunctions (r45>r1>r2>r3
   because rules 4/5 pay a FULL pres rebuild + crane check per
   qualifying entrant/exiter). Any future speed idea must attack
   qualifying geometry, and memoizing it already measured cache-thrash.
L2 (VALIDATED 07-30 on live surgery: single-decision _spec_rule
   refactor + layer map -> canaries 1499/3690 exact, full s8 official-TL
   run byte-matched a prior draw on all four boards)
   SEED IS A LOTTERY, ENGINE IS THE DISTRIBUTION: any change that
   alters RNG consumption order, byBay order, or float rounding
   reshuffles every trajectory (draw spread ±5-8%/instance). Judgment
   therefore needs multi-seed distributions; byte-identity at fixed
   iters is the ONLY valid equivalence test for refactors. Corollary:
   semantics-preserving speedups convert to quality ONLY through the
   governance rate table (measured: x1.15 rates -> -0.4..-8.2%).
L3 INSERTION-ORDER KNOBS TRADE Z3 vs Z1: their sign per board is
   predicted by Z3-share of the objective (>=23% helps, <=7% hurts);
   _spec_rule's (m<=3, sat<1.0) is that variable's proxy on these six
   boards. STRIDE/phase-3 knobs sit on paths that never fire at
   official TLs (str3 byte-identical) — touching them does nothing.
L4 DESTROY-MIX KNOBS act only through the adaptive weights: masked ops
   (persona 0x1F) still leak ~5% via rejection-loops; decay/floor
   reshape the mix slowly (60-iter credit). Uniform or rarely-chosen
   op changes wash (op10 five-seed 0 new-bests). P5 (the one 4-bay
   board) is the mix-sensitive canary — check it first.
L5 GOVERNANCE: trajectory = f(seed, chunk count); machines only choose
   the chunk count (monotone in effective thread speed — dept=2core
   basin 92.9k, 4core 91k, server above). More iterations via rounds
   restart from best and never regress; extensions rarely gain (sticky
   optima). Rate-table errors only truncate at the wall, never break
   correctness.
L6 GATE IS BYTE-AUTHORITY: vp is a conservative mirror, not the truth.
   Loosening vp admits checker-rejects (rescue churn); tightening vp
   vetoes legal moves (search loss). Either way the OBJECTIVE moves,
   feasibility never breaks (invariant 4). The rescue ladder ordering
   (nudge -> retime -> move) is least-destructive-first on purpose:
   retime +1 day is free geometrically, relocation pays Z1/Z3.

## Knob causal graph (mechanism layers; census rows map here)

CONSTRUCTION (seed orders, cnoise, pref-first/PREFP, area screen,
  stride, horizon) -> initial layouts + portfolio starts. Acts once per
  round; interacts with personas via the starts map (seed x persona
  interaction is strong: exploit vs explore basins differ 18% on P5).
DESTROY/REPAIR MIX (10 ops, weights/decay/floor, masks, q ranges,
  bigQProb, pick noise, op2/8/9 params) -> which blocks churn. Personas
  = mask+q+T0 splits; L4 governs.
ACCEPTANCE (T0, cooling, reheat) -> exploration depth; scale-free in
  obj (T0 = 0.002*obj); q and T0 interact (bigger ruins need higher T).
GOVERNANCE (rate table, 0.9 coeff, rounds, spec rule, slack/reserve)
  -> how many iterations and restarts fit; L2/L5 govern; spec rule is
  the Z3-share proxy (L3).
GATE/RESCUE (candidate width, ladder, fallback budget) -> converts the
  portfolio's top boards into a checker-feasible answer; L6 governs.
Cross-layer edges that matter: speed->rates (L2), seed->starts->persona
(L2/L4), PREFP->spec-rule (fires only when the rule does), rounds->
per-round core (halves), exit-order<->vp rules 3/5 (invariant 7).


## Knob census — provenance-classified

[측정] = adopted through full-40 multi-draw judgment. [유산] = inherited,
validated only on the 1-core sandbox era — re-judgeable. [자의] = arbitrary —
first candidates for principled replacement.

| knob | value | site | class |
|---|---|---|---|
| deadline slack | TL − 2.0 s | _algorithm | [유산] |
| gate reserve | min(20, 4+0.05n, 0.12·TL) | _arm_free | [측정] (3 incidents) |
| room() margin | 1.3·ck_max + 0.5 | _gate | [측정] (overruns) |
| pref-first prob | 0.70 / 0.92 (prefStrong) | insertGreedy | [측정 4시드: 0.85 균일 → P3 −2.8 / P4 −6.9 / P5 +5.8 / P6 +1.5% — P5(유일 4-베이)만 손해. 국면 규칙 검토 중] |
| area screen slack | ×1.02, orient-0 bbox | areaRoom | [측정: ×1.00 전보드 +5~9% 기각; ×1.05 P6 −3% 신호 확인 중] |
| phase-3 stride | 6 (≤24 window: 1) | insertGreedy | [자의] |
| SA T0 | 0.002·obj | run | [유산] (동결 아님 실측: acc 0.48-0.63 @T20-120) |
| cooling | 0.9995 | run | [유산] (같은 실측; v5temp 대체 기각) |
| reheat | sinceBest>1200 → 0.3·T0 | run | [자의] |
| hcFrac | 0.75 / 1.0 split | portfolio | [측정: 유지 — 전-0.75가 P4 +5.8% 등 전보드 악화 (공식 TL 2시드)] |
| bigQProb | 0.04 (portfolio: 0 / 0.06) | run | [측정: 유지 — 0.12가 P4 +4.1% / P6 +2.6%] |
| q small | 2..max(8, 3%·n) | run | [판정: ×2/×3 확대 기각 (공식 TL 실보드)] |
| q big | 5–15%·n | run | [유산] |
| destroyer weights | decay 0.85, floor 0.05, score 3/1/0.3 | Destroyers | [판정: 유지 — 균일 대체(v13uni)가 P5 +5.0% (4/4 draws). 바닥 상태여도 마스킹-op의 유령 1.0이 리젝션-루프로 op0 ~5% 추가 주입, 이 동적 혼합이 유효] |
| op8/9 gates | w3·Z3 vs w1·Z1 (parameter-free) | run | [측정] |
| pick noise ±25% | ops 1/8/9 공통 (0.75+0.5·U) | pickRemoval | [자의] |
| op8 pick top-k | 4 (OGC_OP8TOP) | pickRemoval | [자의] (스크리너 준비) |
| op2 공간 가중 | 0.25·(|Δx|+|Δy|), 베이 벌점 50 | pickRemoval | [자의] |
| op9 축출 가격 | 1 + (slack<0? 2 : slack<3? 1 : 0) | pickRemoval | [자의] |
| credit 갱신 주기 | 60 iters | run | [자의] |
| 압축 패스 수 | 3 (라운드별 compactLeft) | runPortfolio | [자의] |
| 후보-게이트 폭 | 차순위 3개 ([1:4]) | _arm_free | [측정: p22 구출 사례] |
| 폴백 anneal 예산 | left − 6.0s, 1라운드 | _arm_free | [측정: 예산-초과 사고 후] |
| portfolio splits | q ×{0.6,1,1.5}, T0 ×{0.5,1,2}, masks 0x1F/0x3FF | runPortfolio | [유산] (T0 split degenerate for 4 threads — measured no-fix) |
| rounds | 1 if sec>110 else 2 | _arm_free | [측정 5시드: rounds=2 → P3 −1.3 / P4 −3.7 / P5 +2.7 / P6 +1.3% — P4-s8 3.01M 신기록. 채택은 합성 판정 후] |
| seed orders | EDD, slack, workload, LPT, 2×noisy(σ=8) | _seed_free | [유산] v59 |
| construct noise | σ=3.0 | constructInitial | [측정: σ=6 중립 — 유지] |
| starts map | best/construct/2nd/3rd | _arm_free | [측정: 품질 중립 + 시드×페르소나 교호 강함 (v12: exploit 밑 10.66M vs explore 밑 12.52M)] |
| horizon | (maxD+maxP+40)·hmul | _fresh | [유산] |
| spec rule | m≤3 ∧ sat<1.0 → r2+pp0.85 | _spec_rule | [측정: 5시드 P3 −2.1/P4 −8.2, P5/P6 제외로 중립] |
| gov rate table | (m,n)-실측 iters/s | _rate | [측정] (본선 새 인스턴스에서 재교정 대상) |
| gov coefficient | 0.9·명목시간 | _arm_free | [측정-보정: 0.6→0.85→0.9 수렴] |
| gov ext/margin | core/3, 1.15, rounds≤16 | runPortfolio | [자의] (확장은 거의 무득점 — 안전 여유) |

## The finals front (2026-07-29/30, real P1-P6 in data/preliminary_test)

Gap to rank-1 lives in: P3 pure Z3 (ours 494-516 vs ~350-390); P4/P5/P6 pure
Z1 (196-229 vs ~110 / 740-786 vs ~535 / 4731-4823 vs ~3700 days). CP-SAT
area relaxation proves Z1=0 possible on ALL boards (tools/z1bound.py) — the
binding constraint is GEOMETRY. frag_diag.py: 51.5%/53%/18.3% of tardy
blocks' waiting days (P4/P5/P6) had enough free area somewhere.

Objective decomposition (v31d4 boards, w1*Z1 + w2*Z2 + w3*Z3, exact):
  P3 = 0 + 16,580 (19%) + 69,150 (81%)      -- preference board, Z1=0
  P4 = 2,226,611 (76%) + 26,810 + 681,750 (23%)
  P5 = 9,759,756 (93.5%) + 18,508 + 661,143 (6.3%)
  P6 = 31,474,507 (96.7%) + 15,024 + 1,071,300 (3.3%)
This grounds the _spec_rule/PREFP phase rule causally: pref-first
probability trades Z3 against Z1 through insertion order, and its
measured sign per board aligns EXACTLY with Z3-share -- boards at
>=23% Z3-share improved (P3 -2.8%, P4 -6.9%), boards at <=7% worsened
(P5 +5.8%, P6 +1.5%). The (m<=3 AND sat<1.0) gate is a proxy that
coincides with the Z3-share threshold on these six boards; weights are
known at runtime, so a Z3-share gate is the principled successor IF a
new instance ever breaks the proxy (re-judge before changing -- the
proxy is what was measured). It also names the rank-1 frontier per
board: P3 is a pure Z3 packing-preference problem; P4/P5/P6 are pure
tardiness (geometry-bound Z1 days), where CP-SAT proves area allows
Z1=0 -- the gap is geometric realizability, not assignment.

Speed program (07-30, byte-identity contract: fixed-iteration trajectories
must equal the reference -- 6/6 batteries held): ADOPTED rb-cache + AVX2/LTO
= x1.12-1.20 -> governance rates x1.15 -> real boards improve on both seeds
(P4 -2.5%, P5 -2.3%, P6 -1%; cumulative P4-s7 -13.8% vs v2.1) = v3.1.
REFUTED: snap-bypass (no-hit candidates pay full day loops, 0~-10%);
flags-alone (+1-6%, memory-bound); two-sided pair-mask rule screens
(sound + byte-identical but +0-1%: dense packings land in the exact-test
gap). Cost map (measured): scan machinery ~55% (gen was ori-repeated
resident scans -- fixed), vp 24-30% (r45 > r1 > r2 > r3), snap ~2%.
REFUTED also: overlap memoization (-0~-4%; vp cost = ~22ns/entry loop
iterations x 5 passes x pop, NOT geometry -- layer-bbox early-outs already
gate the exact work; 32MB memo thrashes cache). CORRECTED TARGET: vp's five
internal nb loops (the 26-34% block) via a Sol-level per-bay time-bucket
index -- pop (60-100) -> overlappers (15-30) per pass; consumers are
order-insensitive so byte-identity holds. rbCollect/fev variants of the
same idea REFUTED-by-measurement first (they are only 2-7%).
Attempt 1 (stay-interval buckets + epoch dedup) REFUTED by battery
57693: byte-identical 6/6 but 0.946-0.980x -- stays are long vs TB=16,
so a full-stay span revisits each long-stay resident span/16 times and
scattered epoch writes beat byBay's linear prefetch. Attempt 2
(zero-duplicate, exp/v40speed): each vp rule is one of three query
shapes with a no-dup structure -- stab(t) "present at t" = tbk[t/16]
one bucket; "e in [lo,hi)" = ebk[e/16] one home; "f in [lo,hi)" =
fbk[(f-1)/16] one home. Rule 1 = stab(eN) + enter(eN+1,fN), provably
disjoint, so NO dedup state exists. Maintenance split stands:
addIdx/delIdx (byBay+buckets) at legacy placement sites; addTbk/delTbk
(buckets only) at e/f retime sites (compactLeft, retimeBlock) --
retimes never touched byBay in the reference and a del/add cycle would
swap-reorder it, which IS trajectory-relevant. entryOK/exitOK are pure
conjunctions over the obstacle set, so bucket-order pres fill is
byte-safe. retimeBlock's wrap is mutation-tight (neighbour vp(k) must
see i at NEW times). audit code 5 = index rebuild-and-compare.
VERDICT (battery 57694): byte-identical 6/6 but p27 1.034x / p38 0.983x
/ real-P4 1.011x -- REFUTED at adoption threshold.
CORRECTED CAUSAL MODEL (derived from the v31d4 solutions, replacing the
first "dense tiling, pop ~ overlappers" guess which the data refutes):
measured rule-1 overlap fractions are only 0.17 (P3) / 0.45 (P4) / 0.52
(P5) / 0.31 (P6) -- the zero-dup index really did cut visits by
~55-75%, yet time moved ~1%. Therefore the REJECT path (2 int compares
on a prefetched linear scan) costs ~1-2ns amortized == free, and vp's
11.2us/call lives almost entirely in QUALIFYING work: restOverlap on
actually-overlapping pairs, pres pushes, entryOK/exitOK conjunctions
(r45 > r1 > r2 > r3 cost order == where per-qualifier work is largest).
No visit-reduction scheme can remove qualifying work, so EVERY index
flavour was doomed a priori -- this derivation, not the battery, is
what closes the front. It also re-derives the earlier refutations:
masks/memo/pair-screens all targeted reject cost that was already ~0.
The kernel sits at its qualifying-work floor; the only theoretical vp
lever left is caching qualifying GEOMETRY outcomes, and memoization
already measured as cache-thrash. 5x-10x by constants: NO, derived.
exp/v40speed kept as the archive, NOT folded.

Per-instance seed dispatch (OBSOLETE + REMOVED 07-30: the finals use NEW hidden instances, so a content-hash table can never match; code deleted from myalgorithm.py, v31d* zips are archive only. History for the record):
_SEED_BY_SIG maps content-hash(prob_info) -> best locally-measured seed
(15-seed official-TL sweep, runs/seedsweep-s*): P3->s5 86625, P4->s12
3010900, P5->s1 10610896, P6->s9 32652585; P1/P2 seed-invariant.
Governed trajectories reproduce on the server, so the menu transfers.
OGC_SEED env overrides (all harnesses set it -> batteries/canaries
byte-stable); unmatched instances fall back to 7. eval_prelim.sbatch
PSEED=auto exercises the dispatch path. Mining CLOSED at 35 seeds
(stop rule: 2 consecutive dry rounds); final table P3 s25 85,730 /
P4 s12 2,935,171 / P5 s28 10,439,407 / P6 s20 32,561,231; canonical
artifact 2026-07-30_v31d4-finals.zip, shipcheck-verified TOT
46,064,187 = -3.1% vs the best single seed. The menu is
config-specific: re-sweep before shipping any engine change.

Knob panel CLOSED (07-30, jobs 57696-99, +/-2% rule vs same-seed v3.1
draws): slk105-s8 mixed noise (P5 +5~7%, P6 -2%); str3-s8 byte-identical
to baseline -- the phase-3 dense-fallback it gates never fires on these
boards at official TLs (unreached-path no-op); dec95-s8 P5 +1.3~2.8%
(the -7.4% one-seed signal was a lottery draw); dec95-s9 P4 +9.1%.
ALL REFUTED -- v3.1 defaults stand, the knob census is complete.

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

SPEED-SELECTS-BASIN (07-30, asym probe, the submitted zip on real P3):
effective per-thread speed monotonically selects the wall-clock engine's
attractor: dept 4-core 92,900 (pinned) == titan 2-core (heavy contention)
92,900 x3; titan 3-core 91.4k; titan 4-core 91.0-91.4k; grading server
(faster) 87,140. The server "luck" is a speed-ladder rung our hardware
cannot reach; TL extension is NOT equivalent (TL is not speed). v3.0's
governance absorbs the same speed benefit as monotone chunk gains instead
(titan >= dept per seed, proven), making local seed-menu values SERVER
LOWER BOUNDS. Finals plan: per-submission seed iteration (governed results
reproduce on resubmission -- impossible with wall-clock).

SERVER-ENVELOPE FORENSIC (07-30, the submitted zip itself on real P3):
TL240 x2 = 92,900 byte-reproduced; TL270 92,895; TL300 91,435; TL600
91,040; TL900 89,870. The server's 87,140 @TL240 is unreachable at ANY
local time budget -- it lives on a hardware-pacing branch of the
wall-clock-driven trajectory. Local pipeline integrity proven; wall-clock
governance is the flaw (fixed by v31det: iteration-budgeted rounds,
cross-node byte-identity proven, quality parity with v30).

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

Closed structural candidates (07-30 continued):
- exact window B&B (v14exact): first full run on real P4 (15 windows x 20k
  nodes, audit-clean, deterministic) found ZERO improving joint
  (entry,position,ori) reschedules. Third independent confirmation that the
  fixed points are locally optimal at every searchable neighbourhood scale
  (after compactPref and op10). Budget-bounded search cannot reach deeper
  within competition time; folded. Two engineering lessons banked: the
  thread_local identity trap has a RECURSION flavour (per-depth pools), and
  wall-clock test budgets must be iteration/window-bounded.
- locked plan-anneal: refuted (see closed fronts).

- P3 assignment frontier (CP min-Z3 under area budgets -> locked chase +
  anneal, util sweep 0.24-0.33 x 240s): Z1 STALLS at 22-63 instead of
  reaching 0 (w1 17,778 => >=390k, vs the free anneal's 89.5k). The trade
  curve (Z3 rises slowly as budgets tighten) was real but the locked packer
  cannot clear the last tardy days -- same law as lockplan/v3deep: locked
  or decomposed search loses to the free anneal, and aggregate-area
  feasibility is far from peak-window geometric feasibility. REFUTED.

NEXT STRUCTURAL CANDIDATES (in order):
1. Assignment-first construction executed properly: budgeted-preference
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
