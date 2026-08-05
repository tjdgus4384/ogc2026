"""Shipyard block placement -- solver v5 (map: docs/PIPELINE.md).

  algorithm(prob, TL) never raises; worst case returns the always-feasible
  trivial constructor. Pipeline: kernel-native seeding (6 insertion orders,
  top-3 distinct layouts) -> 4-persona portfolio ALNS on wall-budget rounds
  (each round restarts every thread from the round's best; faster machines
  anneal deeper on the same trajectory) -> _slice_refine tail (anneal bursts race
  CP-SAT exact re-placement) -> checker gate with a least-destructive
  rescue ladder over the portfolio's candidate set -> deadline-guarded
  fallback ladder.

  Structure-keyed config (_spec_rule, measured 5-seed at official TLs):
  <=3-bay AND under-saturated boards get pref-first 0.85 + a partial
  preference lock (P3 -2.1% / P4 -8.2%); rounds come from the budget
  (2 when sec <= 110, else 1). 4-bay/over-saturated boards keep legacy.

  Env hooks, all inert on the grading server (unset): OGC_SEED
  (portfolio seed, distributional testing), OGC_DIAG (stderr
  diagnostics), OGC_SLICE=0 (slice-refine ablation). Everything else
  was retired with its refuted experiment (history: archive/, ledger).
"""
import json
import math
import os
import random
import re
import sys
import time

# =============================================================== config
# The WHOLE env surface. Kernel constants live in src/knobs.hpp (adopted
# values with provenance); the causal map and change-prediction laws
# L1-L6 live in docs/PIPELINE.md.
#
#  OGC_SEED    portfolio seed (distributional testing; server default 7)
#  OGC_DIAG    stderr diagnostics (harness logs; silent on the server)
#  OGC_SLICE   =0 disables the slice-refine tail (ablation switch)
#  OGC_PREFP   internal driver->kernel channel set by _spec_rule
#  SMAX_DEBUG  kernel-side dump hook (api/alns), unset in production
_DIAG = os.environ.get("OGC_DIAG")


def _diag(tag, **kw):
    """env-gated stderr diagnostics: harness logs capture them, the grading
    server (no OGC_DIAG) stays silent. Never allowed to raise."""
    if _DIAG:
        try:
            print(json.dumps({"diag": tag, **kw}), file=sys.stderr, flush=True)
        except Exception:
            pass

# sec 158: no single import may take the submission down. `algorithm()` guards
# its body, but a module-level ImportError happens before that guard, scoring
# zero instead of something. The kernel is required for quality but NOT for a
# valid answer (`_trivial` is pure python), and the checker is only used to gate
# -- so both are optional at import time.
try:
    import sscore
except Exception:                                     # pragma: no cover
    sscore = None
try:
    import utils
except Exception:                                     # pragma: no cover
    utils = None

_THR = min(4, os.cpu_count() or 1)   # the server grants 4 cores (sec 100)

# portfolio seed: env-overridable for DISTRIBUTIONAL testing (an engine change
# reshuffles the RNG stream = one frozen lottery redraw; single-draw A/Bs
# compare tickets, not engines). Absent env (the grading server) => 7, the
# historical value.
_PSEED = int(os.environ.get("OGC_SEED", "7"))

# ---------------------------------------------------------------- engine io
def _build_engine(p, pref_p=-1.0):
    if sscore is None:
        return None
    B = p["blocks"]; bays = p["bays"]; w = p["weights"]
    n = len(B); m = len(bays)
    eng = sscore.Engine()
    eng.init_problem(
        n, m,
        [int(math.ceil(B[i]["release_time"] - 1e-9)) for i in range(n)],
        [int(round(B[i]["due_date"])) for i in range(n)],
        [max(1, int(math.ceil(B[i]["processing_time"] - 1e-9)))
         for i in range(n)],
        [float(B[i]["workload"]) for i in range(n)],
        [[float(v) for v in B[i]["bay_preferences"]] for i in range(n)],
        [float(b["width"]) for b in bays],
        [float(b["height"]) for b in bays],
        float(w["w1"]), float(w["w2"]), float(w["w3"]))
    eng.set_pref_p(float(pref_p))
    eng.set_exact_events(os.environ.get("OGC_EXACT_EVENTS") == "1")
    for i in range(n):
        for oi in range(len(B[i]["shape"])):
            L = [[list(v) for v in lay]
                 for lay in B[i]["shape"][oi]["layers"]]
            rx, ry = L[0][0]
            eng.add_orient(i, [[(x - rx, y - ry) for (x, y) in lay]
                               for lay in L])
    return eng


def _fresh(p, hmul=1, pref_p=-1.0):
    B = p["blocks"]; n = len(B)
    D = [int(round(b["due_date"])) for b in B]
    P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
    eng = _build_engine(p, pref_p)
    base = max(D) + max(P) + 40
    if hmul is None:
        releases = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
        horizon = max(base, max(releases) + sum(P) + 1)
    else:
        horizon = base * hmul
    eng.build_occ(horizon)
    return eng, D, P


def _extract(eng, n):
    out = {}
    for i in range(n):
        b = eng.get_block(i)
        if b[6]:
            out[i] = [b[0], b[2], b[3], b[1], b[4], b[5]]
    return out


def _recs_to_sol(recs, event_rows=None):
    if event_rows is not None:
        ops = {}
        for t, kind, i, j in event_rows:
            row = {"type": "EXIT" if kind == 0 else "ENTRY",
                   "block_id": int(i), "bay_id": int(j)}
            if kind != 0:
                v = recs[int(i)]
                row.update(x=int(v[1]), y=int(v[2]), orient_idx=int(v[3]))
            ops.setdefault(str(int(t)), []).append(row)
        return {"operations": ops}
    ent, ext = {}, {}
    for i, v in recs.items():
        ext.setdefault(int(v[5]), []).append(i)
        ent.setdefault(int(v[4]), []).append(i)
    ops = {}
    for tt in sorted(set(ent) | set(ext)):
        L = []
        for i in sorted(ext.get(tt, []), key=lambda i: -recs[i][4]):
            L.append({"type": "EXIT", "block_id": i,
                      "bay_id": recs[i][0]})
        for i in ent.get(tt, []):
            v = recs[i]
            L.append({"type": "ENTRY", "block_id": i, "bay_id": v[0],
                      "x": int(v[1]), "y": int(v[2]),
                      "orient_idx": int(v[3])})
        ops[str(tt)] = L
    return {"operations": ops}


# ------------------------------------------------------------------- stages
def _saturation(p):
    """on-time bbox-area demand / (capacity x horizon) -- the runtime
    congestion signal. <1.0 gates the spec rule; <0.6 additionally gates
    the preference lock (P3 0.464 helps, P4 0.796 hurts: near saturation
    pinned blocks steal the space Z1 needs)."""
    bays = p["bays"]; B = p["blocks"]
    cap = sum(by["width"] * by["height"] for by in bays)
    D = [b["due_date"] for b in B]
    R = [b["release_time"] for b in B]
    span = max(D) - min(R)
    if span <= 0 or cap <= 0:
        return 99.0
    demand = 0.0
    for b in B:
        vs = [v for L in b["shape"][0]["layers"] for v in L]
        xs = [v[0] for v in vs]; ys = [v[1] for v in vs]
        demand += (max(xs) - min(xs)) * (max(ys) - min(ys))             * b["processing_time"]
    return demand / (cap * span)


def _spec_rule(p):
    """Board-class rule for the measured winner config (5 seeds each):
    rounds=2 + pref-first 0.85 takes P3-class boards (-1.8%) and P4-class
    (-7.0%) but pays on 4-bay (P5 +4.5%) and oversaturated (P6 +2.6%)
    boards. Keys on runtime structure -- bay count and the bbox-area
    saturation of the on-time demand -- not on problem identity."""
    if len(p["bays"]) >= 4:
        return False
    return _saturation(p) < 1.0


def _seed_free(p, diversify=True, asg=None, pref_p=-1.0, deadline=None):
    """kernel-native seeding, best-of-5 insertion orders (sec 87).
    asg: optional partial assignment lock (-1 = free) applied to every
    construction -- see _arm_free's partial-lock block."""
    B = p["blocks"]; n = len(B)
    D = [int(round(b["due_date"])) for b in B]
    P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
    R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
    A = [b["workload"] for b in B]
    orders = [sorted(range(n), key=lambda i: D[i])]
    if diversify:
        random.seed(5)
        orders += [
            sorted(range(n), key=lambda i: (D[i] - P[i] - R[i], -A[i])),
            sorted(range(n), key=lambda i: (-A[i], D[i])),
            # v59 (sec 143): due-order with LONG-processing tie-break. On
            # over-saturated boards (p38/P6: on-time area demand = 1.58x
            # capacity) tardiness concentrates on long blocks -- measured
            # avg tardy 24.0 days for P>=22 vs 2.5 for P<22. Giving long
            # blocks priority WITHIN their due group cut seed Z1 4810->4231
            # (-12%). Pure P-desc (ignoring due) is catastrophic (10960),
            # so the due ordering must lead.
            sorted(range(n), key=lambda i: (D[i], -P[i])),
            sorted(range(n), key=lambda i: D[i] + random.uniform(-8, 8)),
            sorted(range(n), key=lambda i: D[i] + random.uniform(-8, 8)),
        ]
    # Fast horizons first; the final max-release + serial-duration bound is a
    # guaranteed schedule horizon rather than another guessed multiplier.
    for hmul in (1, 2, 4, None):
        if deadline is not None and time.time() >= deadline:
            break
        eng, _, _ = _fresh(p, hmul, pref_p)
        cands = []                       # (objective, flat solution)
        best = None
        lockv = asg if asg is not None else [-1] * n
        for od in orders:
            if deadline is not None and time.time() >= deadline:
                break
            budget = (0.0 if deadline is None else
                      max(0.001, deadline - time.time()))
            if eng.reconstruct_ordered(lockv, od, False, 7, budget) == -1:
                ob = eng.objective()[3]      # [z1,z2,z3,total] -> total
                cands.append((ob, eng.get_solution()))
                if best is None or ob < best[0]:
                    best = (ob, od, eng.get_solution())
        if best is not None:
            eng.set_solution(best[2])
            eng.build_occ(eng.horizon())
            # top DISTINCT seeds, best first: the portfolio's basin hedge
            # (different insertion orders often collapse to the same layout;
            # duplicates would waste a thread on the same basin).
            cands.sort(key=lambda t: t[0])
            starts, seen = [], set()
            for ob, flat in cands:
                key = tuple(flat)
                if key not in seen:
                    seen.add(key)
                    starts.append(flat)
                if len(starts) >= 3:
                    break
            return eng, starts
    return None, []


def _check(p, sol):
    """Return an official checker result, or ``None`` when it is unusable.

    A kernel board is never evidence of feasibility: only the official checker
    can authorize it.  The checker is optional at import time, so failures and
    incompatible return shapes are reported as unavailable rather than being
    mistaken for an infeasible board.
    """
    if utils is None:
        _diag("checker", state="unavailable", reason="import")
        return None
    try:
        res = utils.check_feasibility(p, sol)
    except Exception:
        _diag("checker", state="unavailable", reason="call")
        return None
    if (not isinstance(res, dict)
            or type(res.get("feasible")) is not bool
            or not isinstance(res.get("violations"), list)
            or (res["feasible"]
                and (type(res.get("objective")) not in (int, float)
                     or not math.isfinite(res["objective"])))):
        _diag("checker", state="unavailable", reason="shape")
        return None
    return res


def _gate(p, eng, n, deadline=None):
    """Checker gate: legalize float artifacts, LEAST-DESTRUCTIVE FIRST.

    The violations this gate exists for are measure-zero exact-touch
    artifacts (sec 182: shapely emits ~1e-15 slivers at legal contact; the
    checker has no epsilon). Rescue ladder, ordered by objective damage:
      1  pair nudges     1-unit spatial shifts     (zero cost, often boxed in)
      2  retimes         entry +1 day / exit +1 day (cost <= a few tardy days;
                         a blocked instant usually clears when the obstructor
                         leaves -- time sidesteps what space cannot)
      3  relocations     move within the bay, TIGHT first, loose only then
                         (loose can park a block arbitrarily late: suspected
                         +92 tardy days on prob_35 @T900)
    Deadline-aware: each stage runs only if another checker call still fits
    (the server kills overruns; a rescue that misses the deadline is worth
    negative points).
    """
    sol = _recs_to_sol(_extract(eng, n), eng.ordered_events())
    t0 = time.time()
    res = _check(p, sol)
    ck = max(0.3, time.time() - t0)          # measured checker cost (max seen)
    if res is None:
        return None, None
    if res["feasible"]:
        return sol, res

    def room():
        # 1.3x on the WORST observed checker cost: the first call is often the
        # cheapest, and a stage that starts a check it cannot finish overruns
        # the hard TL (server = kill). Measured: +0.1-0.3s overruns on the
        # n>=200 boards with the naive first-call estimate.
        return deadline is None or time.time() + 1.3 * ck + 0.5 < deadline

    def recheck(stage):
        nonlocal sol, res, ck
        sol = _recs_to_sol(_extract(eng, n), eng.ordered_events())
        t1 = time.time()
        res = _check(p, sol)
        ck = max(ck, time.time() - t1)       # keep the worst-case estimate
        if res is not None:
            _diag("gate", stage=stage, feasible=res["feasible"],
                  nviol=len(res["violations"]))
        return res

    _diag("checker", state="infeasible", stage="raw",
          nviol=len(res["violations"]))
    _diag("gate", stage="raw", feasible=False,
          nviol=len(res["violations"]),
          first=str(res["violations"][0] if res["violations"] else "?")[:120])

    # stage 1: pair nudges
    for v in res.get("violations", [])[:6]:
        ids = [int(x) for x in re.findall(r"block (\d+)", str(v))[:2]]
        hit = False
        for bi in ids:
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                           (1, 1), (-1, -1), (1, -1), (-1, 1)):
                if eng.nudge_block(bi, dx, dy):
                    hit = True
                    break
            if hit:
                break
    if not room():
        return None, res
    r = recheck("nudge")
    if r is None:
        return None, None
    if r["feasible"]:
        return sol, r

    # stage 2: retimes -- entry obstruction: shift the entrant's stay +1 day;
    # exit obstruction: extend the stay's exit +1 day (EXIT-ENTRY >= P allows
    # remaining longer). Up to 3 passes.
    for _pass in range(3):
        moved = 0
        for v in res.get("violations", [])[:8]:
            m = re.search(r"block (\d+) (entry|exit) obstructed", str(v))
            if not m:
                continue
            bi, kindv = int(m.group(1)), m.group(2)
            if kindv == "entry":
                moved += bool(eng.retime_block(bi, 1, 1))
            else:
                moved += bool(eng.retime_block(bi, 0, 1))
        if not moved or not room():
            break
        r = recheck("retime")
        if r is None:
            return None, None
        if r["feasible"]:
            return sol, r

    # stage 3: relocations, tight first
    for sd in (21, 22, 23, 24):
        ids = []
        for v in res.get("violations", [])[:6]:
            for x in re.findall(r"block (\d+)", str(v)):
                if int(x) not in ids:
                    ids.append(int(x))
        moved = 0
        for bi in ids:
            b = eng.get_block(bi)
            if not b[6]:
                continue
            if (eng.move_block(bi, b[0], True, sd)
                    or eng.move_block(bi, b[0], False, sd)):
                moved += 1
                continue
            # same-bay relocation can deterministically re-find the exact
            # same exact-touch hole (first-fit) -- the measured persistent-
            # violation loop (prob_36: nviol constant through the whole
            # ladder -> trivial). A CROSS-BAY move breaks that cycle: it
            # costs a little Z2/Z3 and saves the instance from a 1000x loss.
            for j2 in range(len(p["bays"])):
                if j2 != b[0] and (eng.move_block(bi, j2, True, sd)
                                   or eng.move_block(bi, j2, False, sd)):
                    moved += 1
                    break
        if not moved or not room():
            break
        r = recheck(f"move{sd}")
        if r is None:
            return None, None
        if r["feasible"]:
            return sol, r
    return None, res


def _assign_descent(p, eng, deadline):
    """Exact-delta assignment descent (relocate + swap) on w2*Z2 + w3*Z3.

    Z2 and Z3 are functions of the ASSIGNMENT ALONE (invariant 6), so a
    relocate's or a swap's delta in those two terms is exact arithmetic
    -- no geometry, no sampling. Only geometric feasibility needs the
    engine, so every candidate is ranked perfectly up front and engine
    time is paid only for the promising ones. Each trial is snapshot-
    guarded: the full objective (Z1 included) decides, so a move that
    buys balance by adding tardiness is rejected automatically.

    All THREE terms are priced exactly in the ranking: Z2 and Z3 by
    their closed-form deltas, Z1 by the mover's own tardiness (a TIGHT
    move lands it at a punctual entry or fails, so its tardiness either
    goes to zero or the move never happens). The score is therefore an
    exact upper bound on the improvement of that single move.

    This supersedes the preference-only polish, which ranked candidates
    by PREFERENCE GAIN alone and skipped tardy blocks outright, so it
    could see neither a move trading a little regret for a lot of
    balance nor a move that rescues a tardy block into another bay.
    Measured share of w2*Z2 where it matters: prelim P3 = 16,310 of
    82,610 (20% of the objective), and no operator ever targeted it --
    op8 prices its ejections in w3*Z3 alone.
    """
    B = p["blocks"]; bays = p["bays"]; W = p["weights"]
    n = len(B); m = len(bays)
    if m < 2:
        return
    L = [float(b["workload"]) for b in B]
    pref = [b["bay_preferences"] for b in B]
    areas = [bb["width"] * bb["height"] for bb in bays]
    u = [(sum(areas) / m) / a for a in areas]
    w1 = float(W["w1"]); w2 = float(W["w2"]); w3 = float(W["w3"])
    D = [int(round(b["due_date"])) for b in B]
    P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
    R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
    hor = eng.horizon()

    def z2_of(S):
        V = [u[j] * S[j] for j in range(m)]
        return math.floor(max(V) - min(V))

    moves = 0
    while time.time() < deadline - 3.0:
        blk = [eng.get_block(i) for i in range(n)]
        asg = [b[0] if b[6] else -1 for b in blk]
        tard = [max(0.0, blk[i][5] - D[i]) if blk[i][6] else 0.0
                for i in range(n)]
        S = [0.0] * m
        for i in range(n):
            if asg[i] >= 0:
                S[asg[i]] += L[i]
        z2 = z2_of(S)
        cands = []
        for i in range(n):
            a = asg[i]
            if a < 0:
                continue
            for b in range(m):
                if b == a:
                    continue
                S2 = list(S); S2[a] -= L[i]; S2[b] += L[i]
                d = (w2 * (z2_of(S2) - z2)
                     + w3 * (pref[i][a] - pref[i][b])
                     - w1 * tard[i])          # tight move ends its tardiness
                if d < -1e-9:
                    cands.append((d, 0, i, b))
        byb = {}
        for i in range(n):
            if asg[i] >= 0:
                byb.setdefault(asg[i], []).append(i)
        for a in range(m):
            for b in range(a + 1, m):
                for i in byb.get(a, ()):
                    for k in byb.get(b, ()):
                        dz3 = ((pref[i][a] - pref[i][b])
                               + (pref[k][b] - pref[k][a]))
                        S2 = list(S)
                        S2[a] += L[k] - L[i]; S2[b] += L[i] - L[k]
                        n_ie = max(R[i], blk[k][4])
                        n_ke = max(R[k], blk[i][4])
                        new_tard = (max(0.0, n_ie + P[i] - D[i])
                                    + max(0.0, n_ke + P[k] - D[k]))
                        d = (w2 * (z2_of(S2) - z2) + w3 * dz3
                             + w1 * (new_tard - tard[i] - tard[k]))
                        if d < -1e-9:
                            cands.append((d, 1, i, k))
        if not cands:
            break
        cands.sort(key=lambda c: c[0])
        improved = False
        for tried, (d, kind, i, t) in enumerate(cands):
            if tried >= 150 or time.time() > deadline - 3.0:
                break
            before = eng.objective()[3]
            snap = eng.get_solution()
            ok = (eng.move_block(i, t, True, 7) if kind == 0
                  else eng.swap_blocks(i, t))
            if ok and eng.objective()[3] < before - 1e-9:
                improved = True
                moves += 1
                break                      # re-rank from the new state
            eng.set_solution(snap)
            eng.build_occ(hor)
        if not improved:
            break
    _diag("assign", moves=moves, obj=eng.objective()[3])


def _population_search(p, eng, seed, sec, starts, base_asg):
    """Memetic outer loop (the redesigned core, 07-30): a small population
    of boards evolved by wall-clock anneal BURSTS and CONSENSUS-LOCK
    crossover. Rationale from the measured laws: single-trajectory
    annealing converges to rigid basins and transient starts wash out;
    the only steering that works is an anneal-long assignment constraint.
    Crossover therefore locks the bays two elite parents AGREE on (their
    shared backbone) and lets the anneal explore where they differ.
    Population lives as flat solution vectors; the engine is re-seated
    per burst (set_solution + build_occ)."""
    n = len(p["blocks"])
    t_end = time.time() + sec
    burst = max(20.0, sec / 8.0)
    horizon = eng.horizon()

    def anneal(flat, locks, bsec, rseed):
        eng.set_solution(flat)
        eng.build_occ(horizon)
        eng.set_assignment_lock(locks if locks is not None else
                                (base_asg or []))
        eng.run_portfolio(_THR, bsec, 1, 0.05, 0.15, 0.002, 0.9995,
                          rseed, 1, [eng.get_solution()])
        eng.set_assignment_lock(base_asg or [])
        return eng.objective()[3], eng.get_solution()

    # founders: the distinct constructions, each hardened by one burst
    pop = []
    rs = seed
    for st0 in (starts or [[]]):
        if time.time() + burst > t_end:
            break
        if len(st0) != n * 7:
            continue
        rs += 1
        pop.append(anneal(st0, None, burst, rs))
    if not pop:
        return
    pop.sort(key=lambda t: t[0])
    # evolve: consensus-lock children replace the worst. The FINAL burst
    # shrinks to the remaining wall instead of being dropped (the old
    # "remaining < burst -> stop" rule wasted up to burst-1 seconds --
    # measured 28s of P3's 240s TL, the pop path's whole under-use).
    while t_end - time.time() >= 8.0:
        rs += 1
        bsec = min(burst, t_end - time.time() - 0.5)
        if len(pop) >= 2:
            pa, pb = pop[0][1], pop[1][1]
            locks = [-1] * n
            for i in range(n):
                if pa[i * 7 + 6] and pb[i * 7 + 6]                         and pa[i * 7] == pb[i * 7]:
                    locks[i] = pa[i * 7]
            child = anneal(pa, locks, bsec, rs)
        else:
            child = anneal(pop[0][1], None, bsec, rs)
        if child[0] < pop[-1][0]:
            pop[-1] = child
            pop.sort(key=lambda t: t[0])
    eng.set_solution(pop[0][1])
    eng.build_occ(horizon)



# ---------------------------------------------------------- slice refine
_CPSAT = None


def _cpsat():
    """ortools if the environment has it, else None (P3 gains ~5% from the
    slice structure alone, so the fallback is worth having, not just safe)"""
    global _CPSAT
    if _CPSAT is None:
        try:
            from ortools.sat.python import cp_model
            _CPSAT = cp_model
        except Exception:
            _CPSAT = False
    return _CPSAT or None


def _slice_refine(p, eng, seed, until, hor):
    """Spend the tail of the budget on whichever of two arms is paying.

    Arm A re-runs the portfolio from the incumbent; arm P frees K blocks
    seeded on a tardy one and re-places them OPTIMALLY with CP-SAT
    against the frozen rest (candidates from the engine, conflicts from
    Engine::pair_conflicts, so the geometry is exact). Each 6% slice goes
    to whichever earned more per second last time, with a re-probe every
    fifth slice.

    Why two arms and not a fixed split: CP-SAT proved our boards already
    optimal over any 20-30 block rearrangement, so the only improvements
    left need a bigger neighbourhood AND an exact repair -- but paying
    for that with annealing time wins on P6 (-4.4%) and loses on P4, and
    on P3 the polish never even fires because nothing is tardy. Measured
    rates settle that per instance; a constant cannot.
    Measured vs a full-length anneal, 4 seeds: P3 -5.2%, P6 -4.4%.
    """
    B = p["blocks"]; bays = p["bays"]; W = p["weights"]
    n = len(B)
    P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
    D = [int(round(b["due_date"])) for b in B]
    PREF = [[int(round(v)) for v in b["bay_preferences"]] for b in B]
    SMAX = [max(pr) for pr in PREF]
    area_units = [int(round(float(b["width"]) * float(b["height"])))
                  for b in bays]
    area_sum = sum(area_units)
    use_access_cohort = os.environ.get("OGC_ACCESS_COHORT") == "1"
    use_full_slice_obj = os.environ.get("OGC_FULL_SLICE_OBJ") == "1"
    use_anchored_columns = os.environ.get("OGC_ANCHORED_COLUMNS") == "1"
    use_branch_check = os.environ.get("OGC_BRANCH_CHECK") == "1"
    use_stratified_columns = os.environ.get("OGC_STRATIFIED_COLUMNS") == "1"
    use_balance_cohort = os.environ.get("OGC_BALANCE_COHORT") == "1"
    use_shape_contacts = os.environ.get("OGC_SHAPE_CONTACTS") == "1"
    cp = _cpsat()
    rng = random.Random(seed * 7919 + 13)
    cur = eng.get_solution()
    best = eng.objective()[3]
    # Runtime knobs let the evidence harness test genuinely global
    # neighborhoods without changing the shipped K=25 / NCAND=30 defaults.
    K = max(2, min(n, int(os.environ.get("OGC_EXACT_K", "25"))))
    NCAND = max(2, int(os.environ.get("OGC_EXACT_NCAND", "30")))
    exact_only = os.environ.get("OGC_EXACT_SLICE_ONLY") == "1"
    span = until - time.time()
    if span <= 8.0:
        return
    SLICE = max(3.0, span * 0.18)
    rate = {"p": None if cp else -1.0, "a": None}
    rounds = 0
    while time.time() < until - 3.0:
        rounds += 1
        if exact_only:
            arm = "p"
        elif rate["p"] is None:
            arm = "p"
        elif rate["a"] is None:
            arm = "a"
        elif rounds % 5 == 0:
            arm = "a" if rate["p"] >= rate["a"] else "p"
        else:
            arm = "p" if rate["p"] >= rate["a"] else "a"
        end = min(until - 2.0, time.time() + SLICE)
        t1, b0 = time.time(), best
        if arm == "a":
            eng.set_solution(cur); eng.build_occ(hor)
            eng.run_portfolio(_THR, max(1.0, end - t1), 1, 0.05, 0.15,
                              0.002, 0.9995, seed + rounds, 1, [cur])
            v = eng.objective()[3]
            if v < best:
                best, cur = v, eng.get_solution()
            rate["a"] = (b0 - best) / max(1e-9, time.time() - t1)
            continue
        while time.time() < end - 1.0:
            tardy = [i for i in range(n) if cur[i*7+6] and cur[i*7+5] > D[i]]
            if tardy:
                seed_i = rng.choice(tardy)
            elif use_full_slice_obj and use_balance_cohort:
                # Z1-free boards still need exact neighborhoods: seed from the
                # blocks that price the current assignment most heavily through
                # preference regret or the normalized high-load bay.  The old
                # tardy-only gate silently disabled the joint optimizer on P3.
                load = [0.0] * len(bays)
                for i in range(n):
                    if cur[i*7+6]:
                        j = cur[i*7]
                        load[j] += float(B[i]["workload"]) * area_sum / area_units[j]
                high = max(range(len(bays)), key=lambda j: load[j])
                priced = []
                for i in range(n):
                    if not cur[i*7+6]:
                        continue
                    j = cur[i*7]
                    regret = SMAX[i] - PREF[i][j]
                    relief = (float(B[i]["workload"]) * area_sum / area_units[j]
                              if j == high else 0.0)
                    priced.append((W["w3"] * regret + W["w2"] * relief, i))
                priced.sort(reverse=True)
                frontier = [i for _, i in priced[:max(K, min(len(priced), 3*K))]]
                if not frontier:
                    break
                seed_i = rng.choice(frontier)
            else:
                break
            if use_access_cohort:
                free = list(eng.access_cohort(seed_i, K))
            else:
                free = [seed_i]
            pool = [i for i in range(n) if cur[i*7+6] and i not in set(free)]
            if len(free) < K:
                free += rng.sample(pool, min(K - len(free), len(pool)))
            _diag("slice-stage", stage="columns-start", free=len(free),
                  seconds_left=round(end-time.time(), 3))
            part = list(cur)
            for i in free:
                part[i*7+6] = 0
            eng.set_solution(part); eng.build_occ(hor)
            CAND = {}
            for pos, i in enumerate(free):
                budget = max(0.02, min(
                    0.35, (end - time.time() - 2.0)
                    / max(1, len(free) - pos)))
                per_block_cap = max(6, 600 // max(1, len(free)))
                ncols = (min(NCAND, 20, per_block_cap)
                         if use_anchored_columns else min(NCAND, per_block_cap))
                raw = (eng.feasible_positions(
                    i, ncols, 0, budget,
                    3 if use_stratified_columns else 1,
                    math.ceil(ncols / len(bays)) if use_stratified_columns else 0,
                    use_shape_contacts)
                       if time.time() < end - 2.0 else [])
                cs = [tuple(raw[q:q+5]) for q in range(0, len(raw), 5)]
                c0 = tuple(cur[i*7:i*7+5])
                if c0 not in cs:
                    cs.append(c0)
                CAND[i] = cs
            if use_anchored_columns:
                for i in free:
                    if time.time() >= end - 2.0:
                        break
                    anchored = list(cur)
                    anchored[i*7+6] = 0
                    eng.set_solution(anchored)
                    eng.build_occ(hor)
                    raw = eng.feasible_positions(
                        i, 10, 0, 0.12,
                        2 if use_stratified_columns else 1,
                        math.ceil(10 / len(bays)) if use_stratified_columns else 0,
                        use_shape_contacts)
                    for q in range(0, len(raw), 5):
                        c = tuple(raw[q:q+5])
                        if c not in CAND[i] and len(CAND[i]) < NCAND:
                            CAND[i].append(c)
                eng.set_solution(part)
                eng.build_occ(hor)
            _diag("slice-stage", stage="columns-done",
                  columns=sum(map(len, CAND.values())),
                  seconds_left=round(end-time.time(), 3))
            if time.time() >= end - 1.0:
                eng.set_solution(cur)
                eng.build_occ(hor)
                break
            mdl = cp.CpModel()
            V, flatc, idx, incumbent_vars = {}, [], [], []
            for i in free:
                V[i] = [mdl.NewBoolVar("z%d_%d" % (i, q))
                        for q in range(len(CAND[i]))]
                mdl.AddExactlyOne(V[i])
                for q, (j, oi, x, y, e) in enumerate(CAND[i]):
                    flatc += [i, j, oi, x, y, e]
                    idx.append((i, q))
                incumbent = tuple(cur[i*7:i*7+5])
                for q, c in enumerate(CAND[i]):
                    mdl.AddHint(V[i][q], int(c == incumbent))
                    if c == incumbent:
                        incumbent_vars.append(V[i][q])
            mdl.AddDecisionStrategy(
                incumbent_vars, cp.CHOOSE_FIRST, cp.SELECT_MAX_VALUE)
            prs = eng.pair_conflicts(
                flatc, max(0.1, min(2.0, end - time.time() - 1.0)))
            if prs and prs[0] < 0:
                eng.set_solution(cur)
                eng.build_occ(hor)
                break
            _diag("slice-stage", stage="relations-done",
                  conflicts=len(prs)//2, seconds_left=round(end-time.time(), 3))
            for t2 in range(0, len(prs), 2):
                (i, q), (k, r2) = idx[prs[t2]], idx[prs[t2 + 1]]
                mdl.AddBoolOr([V[i][q].Not(), V[k][r2].Not()])
            base_obj = sum(
                (W["w1"] * max(0, c[4] + P[i] - D[i])
                 + W["w3"] * (SMAX[i] - PREF[i][c[0]])) * V[i][q]
                for i in free for q, c in enumerate(CAND[i]))
            if use_full_slice_obj:
                common_area = math.lcm(*area_units)
                z2_denom = len(bays) * common_area
                fixed_load = [0.0] * len(bays)
                free_set = set(free)
                for i in range(n):
                    if cur[i*7+6] and i not in free_set:
                        fixed_load[cur[i*7]] += float(B[i]["workload"])
                load_expr = []
                bound = 0
                for j in range(len(bays)):
                    expr = (area_sum * int(round(fixed_load[j]))
                            * (common_area // area_units[j]))
                    for i in free:
                        coeff = (area_sum * int(round(float(B[i]["workload"])))
                                 * (common_area // area_units[j]))
                        bound += abs(coeff)
                        expr += sum(coeff * V[i][q]
                                    for q, c in enumerate(CAND[i]) if c[0] == j)
                    load_expr.append(expr)
                abs_diffs = []
                bound = max(bound, 1) + max(
                    abs(area_sum * int(round(fixed_load[j]))
                        * (common_area // area_units[j]))
                    for j in range(len(bays)))
                for a in range(len(bays)):
                    for b in range(a + 1, len(bays)):
                        av = mdl.NewIntVar(0, 2 * bound, "load_abs_%d_%d" % (a, b))
                        mdl.AddAbsEquality(av, load_expr[a] - load_expr[b])
                        abs_diffs.append(av)
                if abs_diffs:
                    max_abs = mdl.NewIntVar(0, 2 * bound, "load_max")
                    mdl.AddMaxEquality(max_abs, abs_diffs)
                    z2 = mdl.NewIntVar(0, 2 * bound // z2_denom + 1, "z2_floor")
                    # Exact floor without AddDivisionEquality: the latter's
                    # large rational denominator sent CP-SAT into unbounded
                    # presolve despite a wall limit. These linear bounds encode
                    # the same integer relation and remain interruptible.
                    mdl.Add(z2 * z2_denom <= max_abs)
                    mdl.Add(max_abs < (z2 + 1) * z2_denom)
                    base_obj += W["w2"] * z2
            mdl.Minimize(base_obj)
            new = None
            cuts = 0
            for _ in range(4 if use_branch_check else 1):
                left = end - time.time()
                if left <= 0.3:
                    break
                sv = cp.CpSolver()
                sv.parameters.max_time_in_seconds = max(0.2, min(4.0, left))
                sv.parameters.num_search_workers = _THR
                # Large exact-balance coefficients can make presolve dominate
                # the advertised wall limit. Use bounded direct search instead;
                # branch-and-check enforces geometry outside the relaxation.
                sv.parameters.cp_model_presolve = False
                sv.parameters.stop_after_first_solution = False
                sv.parameters.max_deterministic_time = 0.10
                sv.parameters.random_seed = seed + rounds + cuts
                sv.parameters.search_branching = cp.FIXED_SEARCH
                _diag("slice-stage", stage="solve-start",
                      seconds_left=round(left, 3), cuts=cuts)
                st = sv.Solve(mdl)
                _diag("slice-stage", stage="solve-done",
                      status=int(st), seconds_left=round(end-time.time(), 3))
                if st not in (cp.OPTIMAL, cp.FEASIBLE):
                    break
                candidate = list(part)
                chosen = []
                for i in free:
                    for q, c in enumerate(CAND[i]):
                        if sv.Value(V[i][q]):
                            candidate[i*7:i*7+7] = [
                                c[0], c[1], c[2], c[3], c[4], c[4] + P[i], 1]
                            chosen.append(V[i][q])
                            break
                eng.set_solution(candidate)
                eng.build_occ(hor)
                _diag("slice-stage", stage="audit-start",
                      seconds_left=round(end-time.time(), 3))
                if eng.exact_state_ok():
                    new = candidate
                    break
                if not use_branch_check or not chosen:
                    break
                mdl.AddBoolOr([lit.Not() for lit in chosen])
                cuts += 1
            if new is None:
                eng.set_solution(cur)
                eng.build_occ(hor)
                continue
            if cuts:
                _diag("branch-check", cuts=cuts)
            v = eng.objective()[3]
            if v < best:
                best, cur = v, new
            else:
                eng.set_solution(cur); eng.build_occ(hor)
        rate["p"] = (b0 - best) / max(1e-9, time.time() - t1)
    eng.set_solution(cur)
    eng.build_occ(hor)
    _diag("slice", rounds=rounds, obj=best,
          rate_p=rate["p"], rate_a=rate["a"])


def _arm_free(p, deadline, seed=7, tl=None):
    """diversified seeds -> one continuous portfolio (threads hedged across
    distinct seed basins) -> checker gate."""
    n = len(p["blocks"])
    spec = _spec_rule(p)      # one decision, three effects
    # partial preference lock (spec boards): pin the top-25% preference-
    # spread blocks to their argmax bay for construction AND anneal.
    # Full locks are refuted (tardiness explodes); a PARTIAL lock guides
    # the search into preference-good basins while geometry keeps its
    # freedom elsewhere -- probe 07-30: 60s locked runs reached ~86.0k
    # on P3 where the unlocked control managed 97.3k (official-TL best
    # unlocked: 85,490). Cleared before gate/polish; unlocked fallback.
    asg = None
    if spec and _saturation(p) < 0.6:
        pref = [b["bay_preferences"] for b in p["blocks"]]
        nn = len(pref)
        spread = sorted(range(nn),
                        key=lambda i: -(max(pref[i]) - min(pref[i])))
        asg = [-1] * nn
        for i in spread[:int(nn * 0.4)]:   # K frontier: 40% + mid-anneal
                                     # unlock + NO stacked PREFP reaches
                                     # the deep basins (ledger 07-30)
            asg[i] = max(range(len(pref[i])), key=lambda j: pref[i][j])
    # PREFP and assignment locks are both preference mechanisms. Keep the
    # measured 0.85 override only for unlocked spec boards, but make it
    # Engine-owned so sequential calls cannot inherit another instance's policy.
    pref_p = 0.85 if spec and asg is None else -1.0
    eng, seeds = _seed_free(p, asg=asg, pref_p=pref_p, deadline=deadline)
    if eng is None and asg is not None:
        asg = None
        eng, seeds = _seed_free(
            p, pref_p=0.85 if spec else -1.0, deadline=deadline)
    if eng is None:
        return None, None
    # thread map (4 threads): best seed / fresh construction / 2nd / 3rd.
    # With fewer distinct seeds the pattern cycles, which keeps at least one
    # construction slot and never duplicates a basin needlessly.
    starts = [seeds[0], []] + seeds[1:3] if seeds else []
    # The gate reserve scales with n: its rescue ladder is up to ~6 checker
    # calls, and a checker call grows with instance size. Without the reserve
    # the gate ran in the last ~2.5s before the hard deadline -- on the
    # grading server an overrun is scored as a crash, and a rescue that
    # cannot finish collapses the run to the fallback ladder (measured:
    # prob_29 @T900 -> _trivial, a 1000x objective loss). The reserve also
    # covers the portfolio's post-anneal compaction, which runs OUTSIDE the
    # anneal's wall budget and starved the gate on n>=200 boards (measured:
    # prob_34 raw check -> no ladder stage fit -> fallback -> _trivial).
    # TL-proportional cap: the fixed reserve was 27-55% of a T30-T60 budget
    # on big boards (annealing less than HALF of v0's time). Short runs pack
    # less densely, so exact-touch artifacts -- the gate's whole workload --
    # are rarer there and a smaller reserve is consistent, not risky.
    reserve = min(20.0, 4.0 + 0.05 * n, 0.12 * (tl or 300.0))
    sec = max(2.0, deadline - time.time() - reserve)
    # carve the refine share out of the anneal budget up front --
    # otherwise the portfolio consumes sec entirely and the post-pass
    # never gets its >45s window (measured: TL180 smoke, refine
    # silently skipped).
    # Carve the tail for _slice_refine. Safe to take unconditionally:
    # the two-armed loop hands a slice back to the portfolio whenever
    # annealing is out-earning the CP-SAT polish, so on boards where the
    # polish is worthless (P3 never fires it -- nothing is tardy) the
    # time is simply annealed in bursts, which measured -5.2% there on
    # its own. Measured against a full-length anneal, 4 seeds:
    # P3 -5.2%, P6 -4.4%.
    # Skip the carve where the memetic population path runs: there the
    # budget already buys a stronger search than burst-annealing, and
    # such boards are punctual (P3 has zero tardy blocks) so the CP-SAT
    # arm never fires at all -- the tail would be pure loss. Measured:
    # taking 30% from P3 cost +3.24% while it bought P4 -12.14% and
    # P6 -3.69%. The gate keys on WHICH ALGORITHM runs, not on the
    # instance, so it carries to unseen boards.
    # SCALE-INVARIANT population gate (08-01, 40-instance sweep): the
    # population's edge is the tight regime (~1 s/block, the P3 class);
    # absolute-time windows overfit the train boards. Gate on the ratio.
    _pop_here = sec >= 150.0 and sec / max(1, n) <= 1.6
    _pop_exact = (_pop_here and os.environ.get("OGC_POP_EXACT") == "1")
    # OGC_SLICE=0 is the shipped ablation switch (noslice variant measured
    # +2.1% mean on the 40-pair harness 08-03 -- keep ON; sh45 share and
    # k60 subset variants refuted there too).
    _rfshare = 0.0
    if sec > 60.0 and _pop_exact:
        _rfshare = 0.20 * sec
        sec -= _rfshare
    elif (sec > 60.0 and not _pop_here
          and os.environ.get("OGC_SLICE", "1") == "1"):
        _rfshare = 0.30 * sec
        sec -= _rfshare
    rounds = 1 if sec > 110 else 2
    if asg is not None:
        eng.set_assignment_lock(asg)
    # population vs portfolio: 3-seed A/B (07-31) -- population wins the
    # TL240 class (P3: 3/3 seeds), ties with high variance at TL480, and
    # loses TL600/900 (P5/P6: 3/3 each; burst=sec/8 starves big boards).
    # Default rule: population only in its measured win zone.
    if _pop_here:
        _population_search(p, eng, seed, sec, starts, asg)
        r = None                      # population seats its best in eng;
                                      # the portfolio diag has no row here
    else:
        r = eng.run_portfolio(_THR, sec, rounds, 0.05, 0.15, 0.002,
                              0.9995, seed, 1, starts)
    if asg is not None:
        eng.set_assignment_lock([])
    try:
        # Not on the memetic path at all. Gating only the BUDGET was not
        # enough: the population search returns before its budget is up,
        # and the refinement then burst-anneals on top of a board the
        # memetic path had already polished, costing P3 4.1%. Skip the
        # whole pass there -- those boards are punctual anyway, so the
        # CP-SAT arm has nothing to rescue.
        if _pop_here and not _pop_exact:
            raise StopIteration
        # STOP AT deadline-reserve, not at the deadline. The reserve is
        # what the checker gate and its repair moves run in; eating it
        # cost P5 seed 43 the whole run -- the gate found 3 violations
        # with 1.6s left, could not fix them, and the driver fell back
        # to _trivial (5.5e9). The refinement must never be able to do
        # that, so it hands the reserve back untouched.
        _slice_refine(p, eng, seed, deadline - reserve, eng.horizon())
    except StopIteration:
        pass
    except Exception:
        pass
    if r is not None:
        _st = getattr(eng, "last_stats", lambda: None)()
        if _st:
            _diag("repair", iters=int(_st[0]), failed=int(_st[1]),
                  fail_pct=round(100.0*_st[1]/max(1.0,_st[0]), 1))
        _ops = getattr(eng, "last_ops", lambda: None)()
        if _ops:
            _diag("ops", used=[int(x) for x in _ops[:10]],
                  imp=[int(x) for x in _ops[10:]])
        _ph = getattr(eng, "last_phase", lambda: None)()
        _diag("portfolio", winner=int(r[2]), iters=int(r[0]),
              chunks=eng.last_chunks(),
              phase_us=[round(x / 1000) for x in _ph] if _ph else None,
              thread_best=[round(x) for x in r[3:]])
    else:
        _diag("population", best=round(eng.objective()[3]))
    polish_deadline = deadline - reserve
    if (os.environ.get("OGC_EVENT_SCHEDULE") == "1"
            and time.time() < polish_deadline):
        moved = eng.compact_event_schedule()
        _diag("event-schedule", moved=moved, obj=eng.objective()[3])
    _assign_descent(p, eng, polish_deadline)
    # The gate consumes the portfolio's candidate SET, best first. Whether a
    # layout carries a measure-zero checker artifact is an independent
    # lottery per candidate, so runner-ups are auditioned in two cases:
    #  - the incumbent's gate FAILED outright (historical p22@T60: a clean
    #    743k candidate sat discarded while a fresh-seed 1.47M shipped);
    #  - the incumbent's gate succeeded only via an EXPENSIVE rescue
    #    (measured 08-04, tf-v50-s7 prob_22: move-stage relocation paid
    #    3.9x objective while three sibling candidates at ~2.8k sat
    #    unused). Rescue cost > 2% => audition, keep the CHECKER-measured
    #    cheapest feasible board. Deadline-guarded like every gate call.
    pre = eng.objective()[3]
    sol, res = _gate(p, eng, n, deadline)
    best_obj = res.get("objective", float("inf")) if (sol is not None
                                                      and res) else float("inf")
    if sol is not None and best_obj <= pre * 1.02 + 1e-9:
        return sol, res
    best_sol, best_res = sol, res
    B = p["blocks"]
    hor = (max(int(round(b["due_date"])) for b in B)
           + max(max(1, int(math.ceil(b["processing_time"] - 1e-9)))
                 for b in B) + 40)
    for k, flat in enumerate(eng.get_candidates()[1:4]):
        if time.time() > deadline - 1.0 - 0.01 * n:
            break
        eng.set_solution(flat)
        eng.build_occ(hor)
        if eng.objective()[3] >= best_obj:
            continue                     # cannot beat the incumbent even ungated
        _diag("gate", stage=f"cand{k+1}")
        s2, r2 = _gate(p, eng, n, deadline)
        if s2 is None or r2 is None:
            # A failed checker cannot authorize this candidate. Retain only
            # the previously checker-verified incumbent, if there is one.
            return best_sol, best_res
        if r2.get("objective", float("inf")) < best_obj:
            best_sol, best_res = s2, r2
            best_obj = r2["objective"]
    return best_sol, best_res


def _trivial(p):
    """absolute safety net: one block at a time, alone in its best bay,
    strictly sequential in time -- geometrically and temporally feasible
    by construction. Never used unless everything else failed.
    Picks the (ORIENTATION, bay) pair with the largest containment margin
    over ALL orientations -- the red-team probe showed orientation-0 alone
    can be 0.005 units from not fitting any bay (prob_11 block 144) while
    other orientations carry 12-20 unit margins."""
    B = p["blocks"]; n = len(B)
    bays = p["bays"]
    recs = {}
    t = 0
    for i in sorted(range(n),
                    key=lambda i: int(round(B[i]["due_date"]))):
        best = None                      # (margin, j, oi, x, y)
        for oi, sh in enumerate(B[i]["shape"]):
            xs = [v[0] for lay in sh["layers"] for v in lay]
            ys = [v[1] for lay in sh["layers"] for v in lay]
            w_i = max(xs) - min(xs)
            h_i = max(ys) - min(ys)
            for j in range(len(bays)):
                margin = min(bays[j]["width"] - w_i,
                             bays[j]["height"] - h_i)
                x = int(math.ceil(-min(xs)))
                y = int(math.ceil(-min(ys)))
                fits = (x + max(xs) <= bays[j]["width"] + 1e-9
                        and y + max(ys) <= bays[j]["height"] + 1e-9)
                if fits and (best is None or margin > best[0]):
                    # The checker translates the raw polygon by (x,y).
                    # Integer-aware ceil aligns its minimum to the first
                    # legal integral coordinate without an extra unit shift.
                    best = (margin, j, oi, x, y)
        if best is None:
            raise RuntimeError(f"block {i} has no containing bay/orientation")
        _, j, oi, x, y = best
        R = int(math.ceil(B[i]["release_time"] - 1e-9))
        P = max(1, int(math.ceil(B[i]["processing_time"] - 1e-9)))
        e = max(t, R)
        recs[i] = [j, x, y, oi, e, e + P]
        t = e + P
    return _recs_to_sol(recs)


# -------------------------------------------------------------------- entry
def algorithm(prob_info, timelimit=60):
    try:
        return _algorithm(prob_info, timelimit)
    except Exception:
        fallback = _trivial(prob_info)
        checked = _check(prob_info, fallback)
        if checked is None or not checked.get("feasible", False):
            raise RuntimeError("no checker-certified fallback solution")
        return fallback


def _algorithm(prob_info, timelimit=60):
    t0 = time.time()
    deadline = t0 + float(timelimit) - 2.0
    n = len(prob_info["blocks"])
    span = deadline - t0
    best = (None, None)

    # FREE arm first: its result IS (a) the fallback, (b) the exact
    # habitat signal (Z1==0 is the mathematical precondition for the
    # arm's objective identity obj = w2*Z2 + w3*Z3 -- no threshold),
    # and (c) the instance's own measured delta. Zero constants.
    s_b, r_b = _arm_free(prob_info, deadline, _PSEED, float(timelimit))
    if s_b is not None:
        best = (s_b, r_b)

    if best[0] is not None:
        return best[0]
    _diag("fallback", stage="ladder", left=round(deadline - time.time(), 1))
    # Last resort, STRICTLY budget-aware: everything here runs after the main
    # arm already failed, typically near the deadline -- and the server kills
    # overruns. Measured (v2seed s7 draw): the old unconditional 2s-anneal +
    # unguarded checker calls overran TL by up to +2.8s and still bottomed
    # out at _trivial when the fresh seed itself carried an exact-touch
    # artifact. Ladder: fresh seed -> (if time) short anneal -> gate; else
    # gate the SEED ITSELF (sparse, so nudges/retimes almost always fix it)
    # -> trivial only when nothing checker-feasible fits the clock.
    eng, _ = _seed_free(prob_info, diversify=False, deadline=deadline)
    if eng is not None:
        left = deadline - time.time()
        if left > 8.0:
            eng.run_portfolio(_THR, max(2.0, left - 6.0), 1,
                              0.05, 0.15, 0.002, 0.9995, 5, 1)
        # the gate ladder legalizes whatever state the engine holds (annealed
        # or the bare seed) with deadline-guarded stages. Even the gate's
        # unconditional raw check costs ~n/100 seconds -- skip it entirely
        # when that no longer fits (trivial needs no checker at all).
        if deadline - time.time() > 1.0 + 0.01 * n:
            sol, _ = _gate(prob_info, eng, n, deadline)
            if sol is not None:
                _diag("fallback", stage="rescued")
                return sol
    _diag("fallback", stage="trivial")
    return _trivial(prob_info)
