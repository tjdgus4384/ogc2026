# Which knobs actually reach the anneal (read from code, sec 149)

The shipped pipeline anneals through `run_portfolio`. That function builds a
FRESH `repair::Ctx c2` per worker thread and copies only these fields:

    pb, s, oc, horizon, eventLocal, nestAnchors, waveG, scoreLocal, forcedBay

Anything else set from Python on the engine's own `ctx` NEVER reaches a worker.
This single fact explains sec 147's contradiction (v61's `set_pref_allow(false)`
was a total no-op) and is the first thing to check before adding any knob.

## Live in production
| knob        | how it reaches the workers                  | set by |
|-------------|---------------------------------------------|--------|
| eventLocal  | copied into c2                              | pipeline: `set_event_local(True)` |
| forcedBay   | copied into c2                              | migrate / reconstruct paths |
| prefStrong  | set INSIDE runALNS on the thread's own ctx   | v62 predicate `(z1<=0 && z3<n)` |
| RunParams   | built per-thread inside runPortfolio (tid)   | opMask 0x1F/0xFF, hcFrac, bigQProb, T0mul |

## Dead or constant in production (verified by reading, no experiment)
| knob        | production value | why |
|-------------|------------------|-----|
| useSkyline  | REMOVED in v63   | never set; also not copied to c2, so unreachable even if set. Block + skyline.hpp deleted. |
| eventSweep  | constant `true`  | no setter remains; not copied to c2 (workers get the default) |
| nestAnchors | `false`          | copied to c2, but nothing ever sets it |
| waveG       | `0` (off)        | copied to c2, but nothing ever sets it |
| scoreLocal  | `false`          | copied to c2, but nothing ever sets it |
| eMin        | `nullptr` in the anneal | only the reconstruct/plan paths set it |

`nestAnchors` / `waveG` / `scoreLocal` branches sit inside `scanLocal`, the
540M-call hot path. They are dead, but secs 127/129 showed every edit to that
loop backfired (sorting overhead, prefilter overhead), so they are left in place
and documented rather than excised. Removing them buys nothing measurable.

## Rule for future work
Before believing a new knob does anything: grep for it in the `c2.` copy list of
`runPortfolio`. If it is absent and it is not set inside `runALNS`, it is dead.

# RunParams audit (sec 150)

`RunParams` has 8 fields. `runPortfolio` sets 7 of them per thread and pinned
the 8th to its default:

    qloF, qhiF   = qlo/qhi * (0.6 | 1.0 | 1.5)   by tid % 3
    T0mul        = T0mul * (0.5 | 1.0 | 2.0)     by (tid/3) % 3
    cooling      = argument
    hcFrac       = 1.0 (even tid, pure SA) | 0.75 (odd)
    bigQProb     = 0.0 (even) | 0.06 (odd)
    opMask       = 0x1F (even) | 0xFF (odd)
    bestOfK      = 1  <-- explicitly pinned, "server-first, unvalidated"

## bestOfK: provably dead, REMOVED in v64
Pinned to 1 by the portfolio, and every other `insertGreedy` call site used the
default 1. So the `Cand` / `found` / `pickbest` scoring path (first-feasible-day
candidate collection, sorted by pref-loss + bottom-left) never executed. Removed
along with the parameter itself: kernel 2314 -> 2177 lines, 8 -> 7 files.
Verified: p1/p2/p22/p28 bit-identical at T22; p38 differs inside the 3.5% noise
floor (sec 148), i.e. no measurable effect either way.

### Do NOT "revive" bestOfK without re-reading this
The old event-local fast path was guarded by `c.eventLocal && c.eventSweep &&
bestOfK <= 1`. Setting bestOfK > 1 therefore DISABLED the main insertion
accelerator. Since convergence is time-bound (sec 126), that trade is predicted
harmful before any experiment.

## opMask: destroy ops 8-11 are unreachable in production
12 destroyers exist (bit 11 = era defrag) but the portfolio only ever uses
0x1F (ops 0-4) and 0xFF (ops 0-7). The in-source note records the reason:
"kinds 8-10 masked off -- A/B showed harm on P4-proxy (p28) and no benefit on
small (p22)". Op 11 is likewise never enabled. Treat ops 8-11 as documented
dead capability: available, previously rejected on p28, never evaluated on the
over-saturated P6 board (p38), which sec 143 showed is a different regime.

# Thread-parameter spread: a real code flaw that does NOT matter (sec 153)

With THR=4 the expression `(tid/3) % 3` yields T0mul factors {0.5, 0.5, 0.5, 1.0}:
three threads pinned to the coldest start, and the intended hot arm (x2.0) never
used. The designed space {0.5, 1.0, 2.0} is therefore not covered.

This was fixed (explicit `qF[4]/t0F[4]` tables covering the space) and judged with
the sec 152 apparatus:
    Set A (converged, 8):  8/8 identical            -> quality guard PASS
    Set B @T5  (12):       mean +1.36%  (8 losses)  -> worse
    Set B @T12 (12):       mean -0.20%  (4 wins)    -> looked like a win
    Big boards @T25 (3):   mean +0.05%              -> indistinguishable
The T12 "win" did not survive a third budget: rejected.

Conclusion: temperature-spread diversity is NOT a lever on this problem. The
portfolio's value comes from the exploit/explore split (mask 0x1F vs 0xFF,
hcFrac 1.0 vs 0.75, bigQProb 0.0 vs 0.06) and from seed diversity -- not from
T0mul coverage. The degenerate arithmetic is left in place deliberately: fixing
it buys nothing and costs at small budgets. Do not "fix" it again.

# Repair order: why "big rocks first" must NOT be generalised (sec 154)

The repair loop sorts removed blocks by due date (EDD) for every operator, and
only `kind == 11` (era defrag -- masked off in production, sec 151) additionally
sorts by footprint descending. So FFD ordering exists in the source but is
unreachable in shipped runs.

Suite-wide diagnosis said it should help: in 12/12 Set B instances the
above-median-footprint blocks are far tardier than the below-median ones
(mean tardy days 22.6 vs 6.1 on s16, 33.6 vs 13.8 on s19, 8.9 vs 5.2 on s15),
and on the boards with real headroom those delays are sequencing-caused
(sec 152 diagnosis: s16 140/142 sequencing-delayed, not release-bound).

Tried: EDD primary + footprint-descending tie-break inside each due group
(pure area-desc is already known catastrophic, sec 143: seed Z1 10960 vs 4231).
Judged with the sec 152 apparatus:
    Set A (converged, 8):  7/8 identical, s14 -0.176%  -> PASS (small real gain)
    Set B @T5  (12):       mean +0.04%  (4 wins / 8 losses)
    Set B @T12 (12):       mean +1.30%  (3 wins / 8 losses, s09 +12%)
=> REJECTED.

## The principle this establishes
Z1 is TOTAL tardiness (a sum, not a max). Protecting a few large blocks by
delaying many small ones increases the sum. So large blocks being tardier is
not a defect -- it is what correct optimisation of a sum objective looks like:
the anneal already accepts long delays on a few big blocks because that is
cheaper than short delays on many small ones. Any "large-first" bias fights the
objective. This also explains why the sec 143 LPT seed order produced only a
convergence-speed effect and no converged-quality gain (sec 152).
Corollary: do not target block-size skew in the tardiness distribution. Look for
levers that reduce the COUNT of delayed blocks, not the size of the worst delays.

# Pipeline audit: two post-processing stages were unreachable (sec 156)

`_arm_free` computed the anneal budget as `sec = deadline - now - 2.5`, i.e. it
consumed everything, then asked for `>16s` (window-LNS sweep) and `>5s` (tardy
rescue) of remaining time. Measured: both executed **0 times** at T20 and T60.
Two validated stages had been dead in every shipped run.

Fixing reachability (reserve = min(0.2*rem, 80*measured_gate_cost); entry guards
rescaled to 12x/3x gate cost) was judged with the sec 152 apparatus:
    Set A (converged):   8/8 identical           -> no quality change
    Set B @T12:          mean +3.19%  (1 win / 9 losses)
    p28 @T60:            -1.66% ; p38 @T60: +1.49%   (a wash)
    p28 @T90:            +0.03%                   -> indistinguishable
The anneal is simply more productive per second than either tail. Both date from
an era when the anneal was weaker (secs 46/81/110). REMOVED, along with the
kernel's now-unused `reinsertWindow`: python 364 -> 271 lines, kernel 2177 ->
2157. Set A stays bit-identical, confirming the removal is behaviour-preserving.

## Near-miss worth recording
The first removal attempt cut from `def _tardy_rescue(` to `def _trivial(` --
which also deleted `_arm_free`, sitting between them. Every instance silently
fell back to `_trivial` (objectives ~1e8-1e9). The converged-set check caught it
immediately. Structural edits must be verified on Set A, not by canary alone.

# Pipeline audit, part 2 (sec 157)

## `rounds`: not a lever, but a fidelity trap
`_arm_free` sets `rounds = 1 if sec > 110 else 2`. The server budget (300-600s)
always takes the `rounds=1` branch; every local test at T<110 took `rounds=2`.
So all local Set B measurements ran a portfolio structure the server never uses.
Measured impact:
    Set A (converged):  8/8 identical  -> the quality guard is rounds-INVARIANT,
                                          so every past Set A verdict still holds
    Set B @T12:         rounds=1 mean +0.31% vs rounds=2 (3 wins / 7 losses)
    p28 @T35:           rounds=1 -6.0% vs rounds=2 (single run)
Reading: a mid-run migration (all threads jump to the global best) pays when
there is very little time left and costs diversity when there is more, so the
existing threshold points the right way and the server already gets the better
setting. Not a lever. But judging must pin rounds=1 to stay server-like; the
0.31% shift is well inside the noise floor, so no past rejection flips.

## Dead import that could have killed the whole submission
`import assign as _assign` sat at module level. `_assign` was never called --
the exact-assignment arm has been dormant since sec 106 -- yet the import pulled
in ortools CP-SAT:
    cost: 0.83s of startup on every run (module load 0.97s -> 0.143s once removed)
    risk: a MODULE-LEVEL single point of failure. `algorithm()` wraps its body in
          try/except, but an import error happens before any of that, so a
          missing or mismatched ortools in the grading environment kills the
          entire submission.
Proven with a stub that makes `import ortools` fail:
    v68 -> ImportError, total failure
    v69 -> survives, p2=3690 feasible
Removed the import and dropped assign.py from the shipped bundle (source kept in
this archive for reference). Shipped files: 4 -> 3. Set A stays bit-identical.

## Rule
Ship no module-level import that the shipped code path does not call. Verify by
running the bundle with the dependency stubbed out to fail.

# Failure-mode hardening (sec 158)

`algorithm()` wraps its body in try/except, which creates a false sense of
safety: everything that happens at MODULE IMPORT time, and anything the gate
itself throws, bypasses that guard. Three collapses were reachable, all proven
with stubs:

| scenario                        | before            | after            |
|---------------------------------|-------------------|------------------|
| `import ortools` fails (sec 157)| ImportError, zero | n/a (import gone)|
| `import sscore` fails           | ImportError, zero | `_trivial` answer |
| `import utils` fails            | ImportError, zero | kernel answer     |
| `check_feasibility` throws      | p2 3690 -> 9.6e8  | p2 3690           |

Fixes:
1. `sscore` and `utils` imports are guarded; both are optional at import time.
   The kernel is needed for QUALITY, not for a valid answer (`_trivial` is pure
   python), and the checker is only a gate.
2. `_check()` wraps every `check_feasibility` call. If the checker is unusable
   the gate returns the kernel's own solution, which `vpConservative` already
   validated against the same physics (resting overlap + crane entry/exit);
   sec 133 records only an EPS-level gap. Measured: the kernel-only solution is
   checker-feasible (p2 3690 verified by the real checker afterwards).
Set A stays 8/8 bit-identical with a healthy checker, so none of this changes
behaviour on a normal run.

## Rule
Test the bundle with each dependency stubbed to fail. A guard inside the entry
function does not protect the import section or the guard's own callees.

# Exhaustive bottom-left constructor: real capability, rejected as a seed (sec 165)

The sec 164 "1576x" cost figure conflated implementation with algorithm: the
shapely prototype needed 145 s, but the SAME full-grid bottom-left scan using
the kernel's own hits/vpConservative tests costs 0.19 s (p28) / 0.53 s (p38)
and produces constructive Z1 233 (vs 437 corner) / 4042 (vs 4810). The
capability is preserved as `exhaustive_gate.patch` in this archive (Ctx flag +
~40-line front gate in insertGreedy + reconstruct propagation).

Judged as a best-of seed candidate (v73) with the sec 152 apparatus:
    Set A:                         8/8 identical -> PASS
    Set B @T5  (rounds=2):         mean -1.52%
    Set B @T12 (rounds=1, server): mean -0.02%
    BIG7  @T22 (rounds=1):         mean +0.65%
    REAL boards across budgets:    s12 (p28) +10.6 / +8.3 / +2.2  -- 3/3 worse
                                   s16 (p38) -0.5 / +5.7 / +7.2(direct) -- mostly worse
    perturbed variants:            consistently better (s17/s18/s19: -3..-8%)
=> REJECTED: it wins only on synthetic perturbations and loses on both real
competition boards -- the exact failure the apparatus exists to catch.

## The strengthened principle (third independent confirmation)
This anneal does not convert seed QUALITY into final quality; it responds to
seed STRUCTURE (sec 143 LPT, sec 152, now this). Bottom-left-dense seeds are
hostile terrain for the current destroy/repair operators on the real boards,
even though they are 16-47% better as constructions. Do not retry seed-quality
plays without changing the operators to match.

# Full read of the 4 never-audited files (sec 166)

geometry.hpp / occ.hpp / crane.hpp / core.hpp were read end to end for the
first time. Findings and dispositions:

## Fixed (behavior-identical, Set A 8/8 bit-identical, throughput +2-3%)
1. `vpConservative` heap-allocated a fresh `std::vector<Placement>` in rules 2
   and 3 and PER NEIGHBOUR EVENT in rules 4 and 5 -- millions of allocations
   per anneal. Now one thread_local buffer (same pattern repair.hpp already
   uses).
2. `overlapsOff` copied both polygons and then `overlapsArea` recomputed
   bbox() per call and, on every no-edge-cross (disjoint-confirmation) path,
   ran the O(V^2)-ish interiorPoint() search -- per call, for rigid shapes.
   Now: per-(orient,layer) bbox and interior point are cached once at load
   (api.cpp addOrient) and translated by offset; `overlapsLay` gates on the
   cached layer bbox BEFORE any vertex copy and calls `overlapsAreaIP` with
   the cached samples. Same EPS predicates, so same verdicts.
3. The dead legacy `overlapsOff` was removed once callers migrated.
Measured back-to-back on the same machine: p28 537 -> 553 iters/s (+3%),
p38 172 -> 176 (+2%). Small because the mask prefilter absorbs most probes;
the exact path is a minor share of runtime.

## Identified, deliberately NOT taken (documented headroom)
4. `hits()` re-scans grid layers j = l..L-1 per mask layer. A cumulative
   ">= l" grid would cut reads ~L/(L+1) at the cost of multiplying stamp
   writes by up to L. Estimated <= 10-15% of hits time; invasive (stamp,
   clear, snap all change). Not worth the risk at the current margin.
5. `hitsSnap` tests SAME-layer only (sound under-filter); strengthening it to
   the j>=l rule would raise prefilter precision and cut vpConservative
   calls, but the miss rate is unmeasured and layer-1 masks are small.
These two are the only known remaining micro-headroom in the kernel.

# NFP-engine program, increments 1-2: both rejected; bet (a) refuted at premise (sec 169)

Increment 1 -- vertex-sum contact anchors ("NFP collapses free"): ear-clipping
adds no vertices, so all NFP-piece vertices are pairwise original-vertex sums;
a ~30-line generator fed them, bottom-left-ordered, ahead of the bbox grid in
every candidate loop. Measured: p38 constructive +22..26%, p38 anneal T30x2
+3.0%, throughput -22%; p28 constructive mixed (-1..-9%). Rejected. The subset
misses edge-edge contact positions and exact slides, and lround() breaks
contacts into 0.5 gaps; corner-kiss-first fragments crowded boards.

Increment 2 -- core-premise test: the archived exhaustive gate enabled INSIDE
the anneal (equal wall time, p38 T30, seeds x2):
    normal repair      Z1 3274   248 it/s
    exhaustive repair  Z1 3697   46  it/s     (+12.9% worse, 5.4x fewer iters)
=> Best-possible per-insertion placement LOSES at equal time. The binding
resource is iteration count, not placement quality (sec 126, strongest form).

## Consequence for the sec 168 bet ladder
(a) incremental-NFP engine: REFUTED at premise for this anneal architecture --
    an NFP engine can only pay inside a few-large-moves search, i.e. it folds
    into bet (b). Do not restart (a) against this SA.
(b) time-slice large-neighbourhood optimisation: surviving, untested at kernel
    level (sec 156's rejected tails were python+checker-gated, different beast).
(c) raw throughput x5-10 (SIMD/bitboard, incl. the two sec-166 headrooms):
    surviving, and the only lever with a measured monotone payoff curve.

# (c)-spike: ablation profile, dual-plane, and the rules-4/5 early-out (secs 170-171)

## Ablation profile (measured, T15 single-thread)
    hits same-layer-only:   x1.03 (p28) / x1.01 (p38)  -> layer-fold headroom DEAD
    vp skipped entirely:    x3.28 / x2.11              -> vp = 53-70% of runtime
    crane rules only off:   x1.83 / x2.26              -> crane geometry dominates vp
The sec-166 item #4 (cumulative >=l grid) is closed: ceiling 1-3%, not worth
invasive surgery. The real target was always vpConservative.

## Dual-plane prefilter (sec 170): theory lesson, then rejected
Strengthening hitsSnap to the checker's j>=l rule WINDOW-WIDE broke the canary
(3690 -> 5440, +29% it/s): the entry rule is valid AT THE ENTRY INSTANT only,
and window-wide folding kills legal layered-overlap (cantilever) placements --
the same-layer filter was load-bearing, not sloppy. The theory-correct version
(rule-1 plane OR'd over [e,f) + a second plane folded j>=l at time e only) is
sound (canary 3690, Set A 8/8 bit-identical) but adds +0.4% / -1.8% on top of
the early-out = nothing. Rejected for lean code; archived as dual_plane.patch.

## Rules-4/5 bbox early-out (sec 171): ACCEPTED, shipped in v82
vpConservative's rules 4/5 rebuilt an O(nb) `pres` list PER NEIGHBOUR EVENT
(O(nb^2) per call). Exact skip: if A's union bbox does not overlap k's, A
cannot be a blocker of k (the j>=k rule needs positive-area overlap with A);
any remaining entryOK/exitOK failure would be A-independent, hence
pre-existing, hence impossible in an incrementally vp-verified state.
    proof:      20/20 deterministic constructive passes byte-identical
    throughput: +29.0% (p28) / +7.1% (p38), alternating separate processes
    Set A:      7/8 identical, s14 -0.069% (faster engine, same wall time)
    Set B @T5:  mean -1.25% (8W/2T/2L)
    Set B @T12 rounds=1 (server-faithful): mean -2.38% (10W/2T/0L)
    real boards @T12: s12 (p28) -6.03%, s16 (p38) -5.66%
First change in the campaign to pass the full sec-152 acceptance criterion.

# Pairwise reduction of rules 4/5 (sec 173): exact, proven, zero gain -- archived

Extending the sec-171 induction to its limit: in an incrementally vp-verified
state, A is the ONLY candidate blocker of any k's entry/exit (k's own rules
2/3 cover earlier residents; each later resident's rules 4/5 cover itself;
removals only delete blockers; tryWaveAlign re-verifies movers and their
overlappers). So rules 4/5 collapse to a single A-vs-k layer sweep -- no pres
rebuild, no entryOK/exitOK call, bay re-check skipped.

    proof:      20/20 deterministic constructive passes identical to v74
    throughput: +0.2% (p28) / -1.2% (p38) on top of the early-out = NOTHING
Why nothing: entryOK/exitOK already bbox-gate each pres element, so the
feared O(nb^2) was cheap pointer pushes + bbox rejects; the polygon work was
already effectively pairwise. Kept out of the shipping kernel (no measured
benefit); archived as pairwise_45.patch (44 lines, net code shrink) so future
work knows rules 4/5 cost does NOT live in the pres builds.

# Server-like long-T validation of v82 (sec 173)
T=120 s, 4 threads, rounds=1 (auto), single draws:
    p38: v74 42.99M (Z1 3100) -> v82 42.41M (Z1 3044)   -1.34%
    p28: v74  2.254M (Z1 118) -> v82  2.174M (Z1 111)   -3.53%
Direction consistent with the T5/T12 suite verdicts; v82's advantage persists
at server-like budgets.

# bet (b): op-12 time-slice window repack -- five designs, REJECTED (secs 175-176)

The gap secs 165/169 left open: those verdicts tested per-BLOCK quality
investment. op-12 tests the LARGE MOVE -- rebuild a whole time window and let
SA judge it as one accept/reject. Window = a tardy seed's interval padded by one
processing time; members = same-bay blocks fully inside it; rebuild = best of 3
insertion strategies under an entry cap.

## Real optimisations found on the way (kept in the patch)
- The exhaustive gate called hits() per candidate, and hits() re-walks the
  window's time axis. Building one Snap per (bay, entry) instead: 171 -> 91 ms
  per fire. This is also what finally justified sec 170's dual-plane snapshot
  (rule 1 over the window + the entry rule at e), archived there as worthless
  in the main path.
- Cost-aware operator weighting: the base scheme scores per USE, so an operator
  accepted ~33% of the time keeps a healthy weight while eating 25% of the
  clock. Dividing its score by cost relative to an average iteration is the
  correct economics for any expensive operator.

## Measured verdicts (arm-level A/B, 3 seeds, separate processes)
    design                          p28-family      p38-family
    arm swap (2 arms)               --              Set A s14 +0.069% FAIL
    arm swap (1 arm, minimal)       --              Set A s14 +0.069% FAIL
    all arms, self-regulating       -8.6%           +4.1%
    all arms, cost-aware weight     -7.2%           +2.2%
    all arms, stall-gated           -7.7%           +3.4%
Cost-awareness halved the p38 damage but never removed it, and the cheap-rebuild
variant was also harmful there (3516 vs 3441) -- so the harm is the MOVE, not
just its price. Dense repacked windows are hostile terrain for this SA on
unconverged boards, exactly as sec 165 found for dense seeds. Board-conditional
=> REJECTED (the sec-152 apparatus exists to catch precisely this).

## The finding that outlives the operator
sec 171's bbox early-out makes vpConservative exact only under the invariant
"every placed block passed vp at its insertion". op-12 installed its chosen
configuration by direct field writes, breaking that precondition: the internal
objective read a clean 3690 while the official checker rejected the state
("Stage2 entry obstructed"), and _gate fell back to a safe reconstruct (410964).
Re-verifying each restored block with vpConservative fixed it.
**Any future operator that writes placements without per-block vp verification
must re-verify, or v82's early-out is unsound for that operator.**
Archived: timeslice_op12.patch (all five designs' final state, with the fix).

# op-13 focus relocate + LAHC acceptance family: both rejected (sec 177)

## op-13: the monotone remnant of op-12
Take THE worst-tardiness block, exhaustive-BL reinsert with entry capped
strictly before its current entry: success => Z1 strictly decreases and nobody
else moves; failure => rollback. Single block through insertGreedy, so the
sec-171 vp invariant holds by construction (RAW-feasible canaries confirmed).
    arm A/B, 3 seeds:   p38 +7.26% (3/3 worse)   p28 -2.74%
Mechanism: the worst-tardy block is tardy BECAUSE no earlier feasible slot
exists, so the exhaustive scan runs its full range and fails -- ~100 ms of
pure waste per fire -- and the deterministic worst-pick repeats the same
guaranteed failure. Even a MONOTONE-improving operator loses to its own scan
cost. This is sec 169b's law in its purest form: iteration economics governs;
per-move quality investment of ANY granularity (window, single-block) is
unpayable inside this SA on the big boards. Archived: focus_relocate.patch.

## LAHC: the acceptance FAMILY test sec 153 never ran
sec 153 swept temperature schedules WITHIN Metropolis. LAHC (accept if better
than incumbent or the incumbent Lh steps ago), scale-free history:
    Lh = n   : p38 +18.5% (3/3), p28 +0.7%
    Lh = 20n : p38 +21.7% (3/3)  -- longer history trends WORSE
Two points, wrong direction => family rejected, not mis-sized. The tuned
Metropolis-SA is superior on this problem at both sampled scales.
Archived: lahc.patch.

# secs 178-183: the last four axes, all closed (and two self-corrections)

## sec 178 non-SA engine (bit-parallel exhaustive) -- REFUTED AT PREMISE
Snapshot-based exhaustive constructor: p28 0.087 s (Z1 224), p38 0.489 s
(Z1 3790) -- 2.2x faster AND better than sec 165's, because the dual-plane
snapshot stops over-rejecting legal cantilever placements.
    iterated restarts, best-of-N (p38):  1:3585  5:3536  25:3524  50:3353
                                         100/150/250: 3353  (FLAT from N=50)
    large-ruin hill-climb, cheap insert: 3549
    large-ruin hill-climb, exhaustive:   3699
    SA baseline (same wall time):        3284
The best-of-N curve saturates, so a 5-10x bit-parallel speedup buys NOTHING;
the bottleneck was never construction speed. A ~3-week engineering project
killed at premise in about an hour.

## sec 179 hits() is over-strict -- real, but worth ~0 in production
hits() applies the j>=l ENTRY rule across the whole window, which excludes
legal layered-overlap placements. Replacing it with the dual-plane snapshot in
the two production fallbacks: p38 +0.09%, p28 -0.91% (3 seeds) = noise. The
fallbacks are reached too rarely to matter. Archived in the sec-178 work.

## sec 180 exhaustive placement in the CONVERGED regime -- still rejected
Every earlier rejection was measured unconverged, so this filled the missing
cell using Set A: s14 6660216 -> 6661962 (+0.03%), s04 3690 -> 6430 (+74%).
Also found a real bug in the archived gate: index-ordered bays ignore
preference (s04 3690 -> 75140); fixed with pref-ordered bays, still worse.
Verdict: the rejection is regime-independent.

## sec 181 the Z3 axis -- 34% headroom is real, two designs cannot claim it
p22: pipeline Z3 1801 vs assignment lower bound 1234 => 34% of the objective
sits on the Z3 axis, and the insertion primitive (earliest-e first-feasible)
can never propose "later entry in a better bay".
    single pref-upgrade relocate: 11,115 fires / 2 successes (argmax bays are
                                  capacity-blocked -- sec 106's price, measured)
    cross-bay pref SWAP:          p22 -0.25% over 3 seeds (one seed showed
                                  -6.9%, another +3.4%: noise)
Third independent confirmation of sec 106. The blocker is geometric capacity,
not search strategy.

## sec 182 the residual infeasibility is a CHECKER FLOAT ARTEFACT
Traced with the checker's own geometry: the violating pair's intersection area
is 3.44e-15 -- exact contact, where Shapely can return a ~1e-15 sliver, and
check_entry tests `area > 0` with no epsilon. Our vp (EPS 1e-9) is
geometrically right; a closed-set vp would forbid every flush placement and
wreck density. _gate's existing sec-81 nudge already repairs these (v82: 5/5
seeds feasible at the gate), so the campaign's shipping path was never broken
-- an earlier claim that v82 "relies on _gate luck" was overstated, based on
looking at RAW output only. A checker-verified nudge variant was built and
passes 5/5, but is NOT shipped: no measured live failure.

## sec 183 SELF-CORRECTION: there is no wasted-iteration reserve
RunStats is {iters, accepted, improved, failed, best}, not
{iters, failed, accepted, improved, best}. Read correctly: failed = 0 --
insertGreedy NEVER fails -- and the "30-48% failure rate" was the ACCEPTANCE
rate (p28 30%, p38 48%), which is healthy. The original-position fallback
built on that misreading was provably dead code (fallback attempts = 0 under
instrumentation) and was reverted.
**Rule: read the struct definition before interpreting positional fields.**
This is the second such trap today (the first was _extract's [bay,x,y,ori,e,f]).

# secs 184-195: the objective's three layers, each settled (and four self-corrections)

## What the layers are
Z1 = sum of tardiness (schedule + packing), Z2 = MAX over bay pairs of
|u_a*load_a - u_b*load_b| (assignment only), Z3 = sum of preference loss
(assignment only). Measured term shares: prob_1 0/73/27 %, prob_2 0/39/61,
prob_22 0/1/99, prob_28 70/0/30, prob_38 96/0/4.

## sec 192 SCHEDULING IS PROVABLY OPTIMAL
Freeze (bay, ori, x, y) and the geometry disappears: what is left is a
disjunctive scheduling problem over a conflict graph (rest overlap -> disjoint
intervals; asymmetric entry/exit blocking -> e_i not inside [e_k, f_k)). CP-SAT
solves it exactly:
    p28: rest 313 / entry 10 pairs -> OPTIMAL Z1 = 166 == ours
    p22: rest 262 / entry  6 pairs -> OPTIMAL Z1 = 0   == ours
    p38: rest 1055 / entry 36     -> FEASIBLE 3712 vs our 3606 (we win)
So the kernel's earliest-feasible entry choice is optimal for the layout it
produces. Tool kept as exsched.py: it decides, for any future layout, whether
remaining tardiness is a SCHEDULING or a PACKING problem.

## sec 189-191 ASSIGNMENT: exactly optimisable, capacity-saturated
Z2+Z3 is an integer program (Z3 linear, Z2 minimax linearisable). Unconstrained
optimum on p22: w2Z2+w3Z3 = 36937 against our 769034 -- 95% of the
assignment-determined objective. Every attempt to realise it failed:
 - global area-time capacity model: too coarse (a bay saturated on average
   still has idle windows -- which is why ejection chains work and this did not)
 - time-indexed capacity screen (schedule frozen, so Z1 cannot worsen):
   of 26 objective-improving transfers, 24 die on AREA and the surviving 2 die
   on GEOMETRY (0/2 and 0/12 realised) -- the free area is fragmented
 - dependency-free local search reaches IP-grade quality in 0.03 s, so ortools
   is not needed; the wall is not the solver
Peak instantaneous density is 0.45-0.53 REGARDLESS of assignment (incumbent,
height-matched, area-balanced all measured), and the pure-preference assignment
cannot even be constructed. Capacity is assignment-invariant: the Z3 wall is
real, and this is the fourth independent confirmation of sec 106.

## sec 187-195 EJECTION CHAIN: real mechanism, rejected three times
To claim a preference upgrade one must FREE the space: a single forced transfer
fails on time and costs 5-25x the gain with a free entry. Evict whoever values
the target bay least, only while the running eviction cost stays under the
mover's gain (evicting the temporally nearest residents instead lost 1.7% --
in a binary preference structure that displaces as many winners as it creates).
    p22 arm-level: -5.8 to -7.6% ; p22 T35 x4 seeds: -2.47% (2W/0L)
    Set A: 8/8 identical
    Set B @T12 r=1: v83 (no gate) 0W/4L; v84 (share >= 0.25, a magic constant)
                    0W/2L; v85 (parameter-free w3*Z3 > w1*Z1) 0W/2L
The parameter-free gate matched the tuned one, so the design was right, but
0 wins / 2 losses fails the sec-152 criterion and tightening further would be
suite overfitting. Archived as ejection_chain.patch.

## Four self-corrections this stretch
1. "Z2 has 73% headroom on prob_1": computed by isolating Z2. Priced with Z3,
   ZERO transfers improve the objective (best: w2 -616 against w3 +800).
   RULE: never derive a bound from an isolated term.
2. "IP assignment -6.73%": the locked variant used a free run to calibrate PLUS
   a locked run, i.e. double the baseline's budget. At equal budget it loses.
   RULE: equalise total budget before comparing.
3. "delta is a function of the assignment (+37%)": that measured area-time
   utilisation over the horizon, not packing density. Peak density is
   assignment-invariant.
4. "greedy earliest-feasible scheduling is myopic": disproved by sec 192's
   exact optimum.

# sec 196 THE PLACEMENT AXIS IS CLOSED -- and sec 163's premise is retracted

## Sliding compaction (built, measured, reverted)
cornerSets proposes bbox-corner offsets, which cannot express polygon interlock.
Rather than enlarge the candidate set (exhaustive scanning loses on throughput,
sec 169b), sharpen each candidate: push it down/left until it touches, driven by
the cheap bitmask test, with one vp at the end and the un-slid position as a
known-good fallback (+20 lines, kernel 2219).
    slide fires on 70-72% of surviving candidates in the anneal -- it is real
    arm-level A/B, 6 seeds: p28 -5.5% then +8.0%; p38 +1.9% then -1.6%
    => sign flips between seed batches, inside the 6.5% single-seed band
PEAK AND MEAN DENSITY DO NOT MOVE:
    p28 peak 0.632/0.653 -> 0.631/0.633 ; p38 0.665/0.628 -> 0.671/0.603
    mean ~0.475 both
So a better per-insertion rule does not raise achieved density.

## The retraction that matters
sec 163 reported a dedicated nester at density 0.757 against the kernel's 0.666
and concluded "the density gap belongs to the primitive" (Phase 0 PASS). That
comparison was NOT apples-to-apples: the nester CHOSE 9 blocks out of a 40-block
pool (mean footprint 135) while the kernel had to place the 12 blocks the time
windows forced on it (mean footprint 89). Larger blocks pack denser.
Re-run apples-to-apples -- exhaustive bottom-left, all 8 orientations, 14
restarts, exact shapely overlap, forced to place THE KERNEL'S OWN 12-block
resident set:
    nester: 12 placed, density 0.632   kernel: 0.632   IDENTICAL
**The kernel's placement is already density-optimal for the sets it must place.**
This dissolves the premise behind secs 163/164's "-34% placement quality" and
behind the whole nester/NFP/bit-parallel line: those projects were chasing a gap
that was a selection artefact. Archived as slide_compact.patch.

## Consequence: all three layers of the objective are now closed
    Z1 scheduling : PROVEN OPTIMAL for our layouts (sec 192, CP-SAT OPTIMAL)
    Z2+Z3 assignment: exactly optimisable, but capacity-saturated and capacity
                      is assignment-invariant (secs 189-191)
    placement     : matches an exhaustive nester on identical sets (this sec)
Nothing in the local structure is leaking. Any remaining leaderboard gap must
come from finding globally different assignment+schedule combinations, which is
the search space secs 149-195 exhausted knob by knob.
