# PIPELINE — the whole solver on one page (v5, 2026-08-04)

Control flow (`myalgorithm.py` → `sscore`):

```
algorithm(prob, TL)                      never raises; worst case _trivial
└─ _algorithm                            deadline = t0 + TL − 2.0
   └─ _arm_free
      ├─ _seed_free      6 insertion orders × reconstruct_ordered; keep best
      │                  engine + top-3 DISTINCT layouts (portfolio starts);
      │                  spec boards (m≤3 ∧ sat<0.6): partial preference lock
      ├─ population OR portfolio:
      │    _population_search  memetic bursts + consensus-lock crossover,
      │                        fires iff sec≥150 ∧ sec/block ≤ 1.6 (P3 class)
      │    run_portfolio       4 arms × anneal legs from {best seed,
      │                        construction, 2nd, 3rd}; leg = alns::run
      │                        (destroy→reinsert→SA); merge = best
      │                        post-compaction objective
      ├─ _slice_refine   tail 30% of TL (portfolio path only): 6%-slices go
      │                  to whichever arm earned more per second — burst
      │                  anneal from incumbent vs CP-SAT exact re-placement
      │                  of ~25-block tardy-seeded subsets (candidates from
      │                  the engine, conflicts from C++ pair_conflicts,
      │                  vp-verified, reserve-protected)
      ├─ _assign_descent exact-delta (Z2/Z3 closed form, Z1 via the mover's
      │                  own tardiness) relocate/swap descent, engine-verified
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
scanBayAt: cornerSets → hitsSnap prefilter → vpConservativeAt until first
           feasible → commitAt (plain first-fit; the NFP2/BESTFIT
           candidate variants were judged refuted 08-04 and excised).
vpConservativeAt: the 5 checker rules, candidate side ALWAYS from arguments
           (rule1 restOverlapAt · rule2 entryOK · rule3 exitOK ·
            rule4/5 neighbour entry/exit with exact bbox early-outs)
alns::run: pick destroyer (adaptive weights, 60-iter credit, decay .85) →
           remove q (3%·n cap, 4-6% big jolts on explore arms) →
           hardest-first reinsert (ops 8/9: forced-bay handoff) →
           shift(10%)/swap(2%) atomic moves → SA accept (scale-invariant
           schedule: cool exp(−.06/n), reheat @6n stall) → return-to-best
           @20n stall. Post-round: spatialCompact(6) + compactLeft(3).
```

## Config surface (complete)

Kernel: `src/knobs.hpp` — adopted constants with provenance inline, plus
the internal OGC_PREFP channel (driver→kernel, spec rule).
Driver: OGC_SEED (portfolio seed) · OGC_DIAG (stderr diagnostics) ·
OGC_SLICE=0 (slice ablation). SMAX_DEBUG gates kernel-side dumps
(diagnostics only). Nothing else reads the environment.
Every refuted knob was excised WITH its code path in the v5 pass
(commit cdfc3e7, canary-byte-identical); the full experiment chronicle:
`archive/docs-early/PIPELINE-fronts-2026-0730.md`, UNDERSTANDING.md.

## Invariants (break one of these and the named failure returns)

1. Every placement passes vpConservativeAt at insertion. Rules 4/5's bbox
   early-outs are exact ONLY under this invariant.
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
6. Z2/Z3 depend on assignment only; ops that keep bays fixed cannot
   change them — the basis of priced-ejection gating and bay-local moves.
7. Simultaneous-exit LOCKSTEP: _recs_to_sol emits same-day EXITs later-
   entry-first, and vp assumes exactly that in rule 3 (f[k]==fN ->
   obstacle iff e[k]<eN) and rule 5 (f[k2]==t -> obstacle iff e[k2]<e[k]).
   Change either side alone and vp diverges from the checker.
   (Gates: `python tools/test_driver.py` = driver half in seconds;
    `sbatch -p titan tools/battery.sbatch` = kernel half: build, audits,
    canaries 1499/3690. `python tools/preflight.py <class>` prints the
    law-derived validation chain BEFORE a change.)

## Change-prediction laws (what happens if you touch X)

L1 REJECT-FREE SCAN: a prefetched linear resident scan rejects at
   ~1-2ns/entry — visit-reduction schemes (indexes, masks, memos,
   screens) are bounded by that ~0 and were all measured/derived dead.
   vp cost = qualifying work only. Any future speed idea must attack
   qualifying geometry; memoizing it already measured cache-thrash.
L2 SEED IS A LOTTERY, ENGINE IS THE DISTRIBUTION: any change that
   alters RNG consumption order, byBay order, or float rounding
   reshuffles every trajectory (draw spread ±5-8%/instance; slice/pop
   paths are additionally wall-clock nondeterministic). Judgment needs
   multi-seed distributions; byte-identity at fixed iters is the ONLY
   valid equivalence test for refactors. Corollary: semantics-preserving
   speedups convert to quality ONLY through the governance rate table.
L3 INSERTION-ORDER KNOBS TRADE Z3 vs Z1: sign per board is predicted by
   Z3-share of the objective (≥23% helps, ≤7% hurts); _spec_rule's
   (m≤3, sat<1.0) is that variable's runtime proxy.
L4 DESTROY-MIX KNOBS act only through the adaptive weights; masked ops
   still leak ~5% via rejection-loops. P5 (the one 4-bay board) is the
   mix-sensitive canary — check it first.
L5 WALL BUDGET (v5 correction): the shipped path is wall-clock, not
   iteration-governed -- v3.x carried a governed mode, the v4 driver
   stopped feeding it a core budget, and the v5 audit deleted the
   unreachable branch. Consequence: FASTER MACHINES GET DEEPER
   ANNEALING on the same trajectory (the grading server outruns every
   node we own), and local values are server LOWER BOUNDS. The cost is
   machine-dependent trajectories -- accepted deliberately: governance
   would convert the server's speed into extra rounds instead, and
   extra rounds measured "rarely gain" (sticky optima). Rounds still
   restart from best and never regress.
L7 EXACT-ARM CONFLICT MODEL MUST MIRROR BOTH SWEEPS: the CP
   re-placement's pair model is only as good as its crane coverage.
   Entry-descent alone (the model until 08-04) silently admits
   arrangements the checker rejects on the EXIT sweep, and the vp
   re-check then discards the whole solve -- so the arm looks
   "converged" while its dense proposals are being thrown away.
   Symptom to recognise: vp-infeasible CP solutions whose bad blocks
   are ALL inside the freed set, never frozen ones. Rule of thumb: any
   pairwise relaxation of vp must cover rule 1 (same-layer overlap),
   rules 2/4 (entry descent, both directions when entries tie) and
   rules 3/5 (exit ascent, LOCKSTEP breaking simultaneous exits).

L6 GATE IS BYTE-AUTHORITY: vp is a conservative mirror, not the truth.
   Loosening vp admits checker-rejects (rescue churn); tightening vp
   vetoes legal moves (search loss). Either way the OBJECTIVE moves,
   feasibility never breaks (invariant 4). The rescue ladder ordering
   (nudge → retime → move) is least-destructive-first on purpose:
   retime +1 day is free geometrically, relocation pays Z1/Z3.
   KNOWN COST: a successful move-rescue can be expensive (tf-v50 s7
   prob_22: move21 paid ~4x objective) and the gate keeps it without
   comparing against runner-up candidates — an open G004/G005 item.

## The finals front (objective decomposition, v31d4-era boards)

  P3 = 0 + 16,580 (19%) + 69,150 (81%)      -- preference board, Z1=0
  P4 = 2,226,611 (76%) + 26,810 + 681,750 (23%)
  P5 = 9,759,756 (93.5%) + 18,508 + 661,143 (6.3%)
  P6 = 31,474,507 (96.7%) + 15,024 + 1,071,300 (3.3%)

P4-P6 are pure-tardiness games (geometry-bound Z1 at congested windows;
CP-SAT area relaxation proves Z1=0 is area-feasible — the binding
constraint is GEOMETRY). P3 is a preference-capacity game. Campaign
targets and per-problem gaps: STATUS.md.
