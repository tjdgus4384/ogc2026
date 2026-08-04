"""Shipyard block placement -- solver v3.0 (map: docs/PIPELINE.md).

  algorithm(prob, TL) never raises; worst case returns the always-feasible
  trivial constructor. Pipeline: kernel-native seeding (6 insertion orders,
  top-3 distinct layouts) -> 4-persona portfolio ALNS on iteration-governed
  rounds (trajectory = f(seed, round count); machine speed only decides the
  count, monotone in best-so-far) -> checker gate with a least-destructive
  rescue ladder over the portfolio's candidate set -> deadline-guarded
  fallback ladder.

  Structure-keyed config (_spec_rule, measured 5-seed at official TLs):
  <=3-bay AND under-saturated boards run rounds=2 + pref-first 0.85
  (P3 -2.1% / P4 -8.2%); 4-bay or over-saturated boards keep legacy.

  Env hooks, all inert on the grading server (unset): OGC_SEED (portfolio
  seed, distributional testing),
  OGC_DIAG (stderr diagnostics), OGC_PREFP / screener knobs (judgment instruments; see
  docs/PIPELINE.md knob census).
"""
import json
import math
import os
import random
import re
import sys
import time

# =============================================================== config
# Every tunable in the system, grouped by MECHANISM LAYER (the causal map
# in docs/PIPELINE.md; change-prediction laws L1-L6 live there). C++ hot-
# path knobs read env ONCE via static-init lambdas at their use site --
# consolidating them into plumbing would add complexity, not remove it.
#
#  layer        python (here)             C++ (site, env)
#  ---------    ------------------------  --------------------------------
#  diagnosis    _DIAG                     SMAX_DEBUG (api/alns dumps)
#  seed         _PSEED                    --
#  construction (spec rule -> OGC_PREFP)  PREFP/AREASLK/STRIDE (repair)
#                                         CNOISE (alns constructInitial)
#  destroy mix  --                        DECAY/OP8TOP (alns), BIGQ/HCFRAC
#                                         (api persona splits)
#  gate/rescue  reserve in _arm_free      --
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
def _build_engine(p):
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
    for i in range(n):
        for oi in range(len(B[i]["shape"])):
            L = [[list(v) for v in lay]
                 for lay in B[i]["shape"][oi]["layers"]]
            rx, ry = L[0][0]
            eng.add_orient(i, [[(x - rx, y - ry) for (x, y) in lay]
                               for lay in L])
    return eng


def _fresh(p, hmul=1):
    B = p["blocks"]; n = len(B)
    D = [int(round(b["due_date"])) for b in B]
    P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
    eng = _build_engine(p)
    eng.build_occ((max(D) + max(P) + 40) * hmul)
    return eng, D, P


def _extract(eng, n):
    out = {}
    for i in range(n):
        b = eng.get_block(i)
        if b[6]:
            out[i] = [b[0], b[2], b[3], b[1], b[4], b[5]]
    return out


def _recs_to_sol(recs):
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


def _seed_free(p, diversify=True, asg=None):
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
    # adaptive-horizon ladder (sec 103): on congested giants the loose
    # insert overflows the base occupancy horizon (P6 board: total seed
    # failure -> _trivial's 4.2e9). Doubling the horizon is sound (a
    # superset of feasible times) and bounded (two retries).
    for hmul in (1, 2, 4):
        eng, _, _ = _fresh(p, hmul)
        cands = []                       # (objective, flat solution)
        best = None
        lockv = asg if asg is not None else [-1] * n
        for od in orders:
            if eng.reconstruct_ordered(lockv, od, False, 7) == -1:
                ob = eng.objective()[3]      # [z1,z2,z3,total] -> total
                cands.append((ob, eng.get_solution()))
                if best is None or ob < best[0]:
                    best = (ob, od)
        # ORDER SEARCH (env OGC_ORDSEARCH = seconds, 0 = off): the
        # decoder is exact and fast (60ms at n=150) and the objective
        # spread across EDD-neighbourhood orders is 44% -- yet the
        # classic seeding samples only the 6 handmade orders. Spend a
        # slice of the budget hill-climbing the ORDER instead.
        _ords = float(os.environ.get("OGC_ORDSEARCH", "0"))
        if _ords > 0 and best is not None:
            _t0 = time.time()
            _cur = list(best[1])
            _curv = best[0]
            _rng = random.Random(9)
            _tried = 0
            while time.time() - _t0 < _ords:
                _cand = list(_cur)
                for _ in range(1 + _rng.randrange(3)):
                    _a = _rng.randrange(n)
                    _b = min(n - 1, max(0, _a + _rng.randint(-12, 12)))
                    _cand[_a], _cand[_b] = _cand[_b], _cand[_a]
                if eng.reconstruct_ordered(lockv, _cand, False, 7) != -1:
                    continue
                _tried += 1
                _v = eng.objective()[3]
                if _v < _curv:
                    _curv, _cur = _v, _cand
                    cands.append((_v, eng.get_solution()))
            _diag("ordsearch", tried=_tried, start=round(best[0]),
                  best=round(_curv))
            if _curv < best[0]:
                best = (_curv, _cur)
        if best is not None:
            if best[1] is not orders[-1]:
                eng.reconstruct_ordered([-1] * n, best[1], False, 7)
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
    """A checker call that cannot take the run down (sec 158).

    `algorithm()` guards its body with try/except, but a checker that differs
    in signature or return shape in the grading environment throws inside the
    gate and collapses the whole run to `_trivial` -- measured: p2 3690 ->
    9.6e8, a feasible but worthless answer. If the checker is unusable we
    report `None` and the caller keeps the kernel's own solution, which
    `vpConservative` has already validated against the same physics (resting
    overlap + crane entry/exit); sec 133 records only an EPS-level gap between
    the two, so this is a graceful degradation instead of a collapse.
    """
    if utils is None:
        return None
    try:
        res = utils.check_feasibility(p, sol)
    except Exception:
        return None
    return res if isinstance(res, dict) else None


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
    sol = _recs_to_sol(_extract(eng, n))
    t0 = time.time()
    res = _check(p, sol)
    ck = max(0.3, time.time() - t0)          # measured checker cost (max seen)
    if res is None:
        return sol, None                     # checker unusable -> trust kernel
    if res.get("feasible"):
        return sol, res

    def room():
        # 1.3x on the WORST observed checker cost: the first call is often the
        # cheapest, and a stage that starts a check it cannot finish overruns
        # the hard TL (server = kill). Measured: +0.1-0.3s overruns on the
        # n>=200 boards with the naive first-call estimate.
        return deadline is None or time.time() + 1.3 * ck + 0.5 < deadline

    def recheck(stage):
        nonlocal sol, res, ck
        sol = _recs_to_sol(_extract(eng, n))
        t1 = time.time()
        res = _check(p, sol)
        ck = max(ck, time.time() - t1)       # keep the worst-case estimate
        if res is not None:
            _diag("gate", stage=stage, feasible=bool(res.get("feasible")),
                  nviol=len(res.get("violations", [])))
        return res

    _diag("gate", stage="raw", feasible=False,
          nviol=len(res.get("violations", [])),
          first=str(res.get("violations", ["?"])[0])[:120])

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
        return sol, None
    if r.get("feasible"):
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
            return sol, None
        if r.get("feasible"):
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
            return sol, None
        if r.get("feasible"):
            return sol, r
    return None, res


def _pref_polish(p, eng, deadline):
    """Engine-verified preference polish on the merged best board. The
    anneal's entry-times are exactly rigid at convergence (probe 07-30),
    but ASSIGNMENT moves are not: a tight move_block into a better-
    preferred bay kept 88570 -> 85490 on P3 (-3.5%) in one verified
    move. Greedy chain, objective-guarded (revert on worse), deadline-
    guarded; the checker gate still judges the result afterwards."""
    pref = [b["bay_preferences"] for b in p["blocks"]]
    n = len(pref)
    improved = True
    while improved and time.time() < deadline - 3.0:
        improved = False
        cands = []
        B = p["blocks"]
        for i in range(n):
            b = eng.get_block(i)
            if not b[6]:
                continue
            dcap = (int(round(B[i]["due_date"]))
                    - max(1, int(math.ceil(B[i]["processing_time"] - 1e-9))))
            if b[4] > dcap:
                continue          # tardy block: a tight revert could fail
            order = sorted(range(len(pref[i])), key=lambda j: -pref[i][j])
            for tb in order:
                if tb == b[0]:
                    break                 # already at best remaining bay
                cands.append((pref[i][tb] - pref[i][b[0]], i, tb))
        cands.sort(reverse=True)
        for gain, i, tb in cands:
            if gain <= 0 or time.time() > deadline - 3.0:
                break
            ob = eng.get_block(i)[0]
            before = eng.objective()[3]
            # A preference SWAP was tried here (trade places with a
            # resident of the wanted bay, which needs no free space).
            # Measured worse, mean +1.6%, and P3's regret barely moved
            # (424 -> 421): the partner loses exactly what the mover
            # gains, so the trade rarely pays. Plain moves only.
            if not eng.move_block(i, tb, True, 7):
                continue
            if eng.objective()[3] < before:
                improved = True
            else:
                eng.move_block(i, ob, True, 7)
    # PREFERENCE CYCLES (env OGC_PREFCYC): a 2-way trade fails because
    # the partner loses what the mover gains. A CYCLE does not: i takes
    # j's bay, j takes k's, k takes i's, and every one of them can move
    # closer to its own favourite. Cycles are the standard fix for
    # assignment problems where pairwise exchange is blocked, and on a
    # z1-free board (P3: tardiness 0, regret 424 vs rank-1's ~321)
    # assignment is the entire remaining objective.
    if os.environ.get("OGC_PREFCYC", "0") == "1" and \
            hasattr(eng, "swap_blocks"):
        rng = random.Random(11)
        tries = 0
        while time.time() < deadline - 3.0 and tries < 4000:
            tries += 1
            cand = [i for i in range(n) if eng.get_block(i)[6]]
            if len(cand) < 3:
                break
            i = rng.choice(cand)
            bi = eng.get_block(i)[0]
            want = max(range(len(pref[i])), key=lambda j: pref[i][j])
            if want == bi:
                continue
            # j sits in the bay i wants; k sits in the bay j wants
            inbay = [q for q in cand if eng.get_block(q)[0] == want]
            if not inbay:
                continue
            j = rng.choice(inbay)
            wj = max(range(len(pref[j])), key=lambda q: pref[j][q])
            if wj == want:
                continue
            third = [q for q in cand
                     if eng.get_block(q)[0] == wj and q not in (i, j)]
            if not third:
                continue
            k = rng.choice(third)
            before = eng.objective()[3]
            if not eng.swap_blocks(i, j):
                continue
            if not eng.swap_blocks(j, k):
                eng.swap_blocks(i, j)
                continue
            if eng.objective()[3] >= before:
                eng.swap_blocks(j, k)
                eng.swap_blocks(i, j)
    _diag("prefpolish", obj=eng.objective()[3])


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

    def anneal(flat, locks, bsec, rseed):
        eng.set_solution(flat)
        B = p["blocks"]
        eng.build_occ(max(int(round(b["due_date"])) for b in B)
                      + max(max(1, int(math.ceil(b["processing_time"]
                                                 - 1e-9))) for b in B) + 40)
        eng.set_assignment_lock(locks if locks is not None else
                                (base_asg or []))
        eng.run_portfolio(_THR, bsec, 1, 0.05, 0.15, 0.002, 0.9995,
                          rseed, 1, [eng.get_solution()], 0)
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
    # evolve: consensus-lock children replace the worst
    while time.time() + burst <= t_end:
        rs += 1
        if len(pop) >= 2:
            pa, pb = pop[0][1], pop[1][1]
            locks = [-1] * n
            for i in range(n):
                if pa[i * 7 + 6] and pb[i * 7 + 6]                         and pa[i * 7] == pb[i * 7]:
                    locks[i] = pa[i * 7]
            child = anneal(pa, locks, burst, rs)
        else:
            child = anneal(pop[0][1], None, burst, rs)
        if child[0] < pop[-1][0]:
            pop[-1] = child
            pop.sort(key=lambda t: t[0])
    eng.set_solution(pop[0][1])
    B = p["blocks"]
    eng.build_occ(max(int(round(b["due_date"])) for b in B)
                  + max(max(1, int(math.ceil(b["processing_time"] - 1e-9)))
                        for b in B) + 40)



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
    PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
    SMAX = [max(pr) for pr in PREF]
    cp = _cpsat()
    rng = random.Random(seed * 7919 + 13)
    cur = eng.get_solution()
    # sibling-thread boards as extra vocabulary (env OGC_SLPEER). The
    # portfolio's four threads end ~25% apart, i.e. in different basins;
    # a freed block's position in a sibling board is a placement another
    # basin certified as part of a full packing. Verification against
    # the frozen rest costs ~0.03s/iteration at 4 boards -- nothing --
    # and the offline probe showed exactly this vocabulary keeps earning
    # after the corner channel saturates (P5, time-fair -1.35% vs -0.86%).
    peers = []
    if os.environ.get("OGC_SLPEER", "0") == "1":
        try:
            peers = [list(c) for c in eng.get_candidates()[:4]
                     if len(c) >= n * 7]
        except Exception:
            peers = []
    best = eng.objective()[3]
    # subset size / candidate count: standalone sweeps preferred K=60
    # (P6 -4.24% vs -4.06% at K=25; P5 -2.09%), affordable only because
    # pair_conflicts is C++. Env-tunable for measurement, fixed defaults.
    K = int(os.environ.get("OGC_SLK", "25"))
    NCAND = int(os.environ.get("OGC_SLNC", "30"))
    # Per-iteration randomization of (K, ncand): the per-instance best
    # setting is real and seed-stable (fresh-seed selector: 17W-7L,
    # p=0.03, -4.6% on switched boards) but does NOT generalize by
    # features (LOIO 8/40, predicted config -3.2%). Prediction being
    # impossible, DIVERSITY harvests part of the same spread: hundreds
    # of re-solves per run, each drawing its own scale.
    _rand_kn = os.environ.get("OGC_SLRAND", "0") == "1"
    span = until - time.time()
    if span <= 8.0:
        return
    SLICE = max(3.0, span * 0.18)
    rate = {"p": None if cp else -1.0, "a": None}
    rounds = 0
    while time.time() < until - 3.0:
        rounds += 1
        if rate["p"] is None:
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
                              0.002, 0.9995, seed + rounds, 1, [cur], 0)
            v = eng.objective()[3]
            if v < best:
                best, cur = v, eng.get_solution()
            rate["a"] = (b0 - best) / max(1e-9, time.time() - t1)
            continue
        while time.time() < end - 1.0:
            tardy = [i for i in range(n) if cur[i*7+6] and cur[i*7+5] > D[i]]
            if not tardy:
                break
            if _rand_kn:
                K = rng.choice((15, 25, 40, 60))
                NCAND = rng.choice((15, 30, 40))
            seed_i = rng.choice(tardy)
            free = [seed_i]
            if os.environ.get("OGC_SLCOH", "0") == "1":
                # coherent subset: the seed's bay-window neighbours are
                # the blocks it can actually trade space-time with; a
                # uniform sample mostly frees blocks that cannot interact
                # with the seed at all.
                jb, e0, f0 = cur[seed_i*7], cur[seed_i*7+4], cur[seed_i*7+5]
                Pi = f0 - e0
                nb = [i for i in range(n)
                      if i != seed_i and cur[i*7+6] and cur[i*7] == jb
                      and cur[i*7+4] < f0 + Pi and cur[i*7+5] > e0 - Pi]
                nb.sort(key=lambda i: abs(cur[i*7+4] - e0))
                free += nb[:K - 1]
            pool = [i for i in range(n) if cur[i*7+6] and i not in set(free)]
            if len(free) < K:
                free += rng.sample(pool, min(K - len(free), len(pool)))
            part = list(cur)
            for i in free:
                part[i*7+6] = 0
            eng.set_solution(part); eng.build_occ(hor)
            CAND = {}
            for i in free:
                raw = eng.feasible_positions(i, NCAND, 0)
                cs = [tuple(raw[q:q+5]) for q in range(0, len(raw), 5)]
                if peers:
                    seen = set(cs)
                    for pbd in peers:
                        c = tuple(pbd[i*7:i*7+5])
                        if c in seen: continue
                        seen.add(c)
                        if eng.place_at(i, c[0], c[1], c[2], c[3], c[4]):
                            cs.append(c)
                            eng.set_solution(part)
                            eng.build_occ(hor)
                c0 = tuple(cur[i*7:i*7+5])
                if c0 not in cs:
                    cs.append(c0)
                CAND[i] = cs
            mdl = cp.CpModel()
            V, flatc, idx = {}, [], []
            for i in free:
                V[i] = [mdl.NewBoolVar("z%d_%d" % (i, q))
                        for q in range(len(CAND[i]))]
                mdl.AddExactlyOne(V[i])
                for q, (j, oi, x, y, e) in enumerate(CAND[i]):
                    flatc += [i, j, oi, x, y, e]
                    idx.append((i, q))
            prs = eng.pair_conflicts(flatc)
            for t2 in range(0, len(prs), 2):
                (i, q), (k, r2) = idx[prs[t2]], idx[prs[t2 + 1]]
                mdl.AddBoolOr([V[i][q].Not(), V[k][r2].Not()])
            mdl.Minimize(sum((W["w1"] * max(0, c[4] + P[i] - D[i])
                              + W["w3"] * (SMAX[i] - PREF[i][c[0]])) * V[i][q]
                             for i in free
                             for q, c in enumerate(CAND[i])))
            sv = cp.CpSolver()
            sv.parameters.max_time_in_seconds = max(
                0.5, min(6.0, end - time.time()))
            sv.parameters.num_search_workers = _THR
            st = sv.Solve(mdl)
            if st not in (cp.OPTIMAL, cp.FEASIBLE):
                eng.set_solution(cur); eng.build_occ(hor); continue
            new = list(part)
            for i in free:
                for q, c in enumerate(CAND[i]):
                    if sv.Value(V[i][q]):
                        new[i*7:i*7+7] = [c[0], c[1], c[2], c[3], c[4],
                                          c[4] + P[i], 1]
                        break
            eng.set_solution(new); eng.build_occ(hor)
            if any(new[i*7+6] and not eng.vp_ok(i) for i in range(n)):
                eng.set_solution(cur); eng.build_occ(hor); continue
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
    # BALANCE ATTACK (env OGC_LOCKK = fraction, 0 = classic): the lock
    # only ever fired on spec boards, so tardiness-dominated instances
    # (P4-P6) run with NO regret control -- measured composition: obj1
    # 190 x w1 = 76% of objective while obj3 4,573 x w3 = 21%, whereas
    # rank-1's P4 total is only reachable with BOTH low (obj1 ~130 and
    # obj3 ~2,500). Locking the top preference-spread blocks to their
    # argmax bay makes the anneal buy that regret back with geometry.
    _lk = float(os.environ.get("OGC_LOCKK", "0"))
    if _lk > 0:
        pref = [b["bay_preferences"] for b in p["blocks"]]
        nn = len(pref)
        spread = sorted(range(nn),
                        key=lambda i: -(max(pref[i]) - min(pref[i])))
        asg = [-1] * nn
        for i in spread[:int(nn * _lk)]:
            asg[i] = max(range(len(pref[i])), key=lambda j: pref[i][j])
    elif spec and _saturation(p) < 0.6:
        pref = [b["bay_preferences"] for b in p["blocks"]]
        nn = len(pref)
        spread = sorted(range(nn),
                        key=lambda i: -(max(pref[i]) - min(pref[i])))
        asg = [-1] * nn
        for i in spread[:int(nn * 0.4)]:   # K frontier: 40% + mid-anneal
                                     # unlock + NO stacked PREFP reaches
                                     # the deep basins (ledger 07-30)
            asg[i] = max(range(len(pref[i])), key=lambda j: pref[i][j])
    # PREFP boost and locks are BOTH preference mechanisms -- stacked
    # they push locked boards over the tardiness edge (the production-
    # only cliff: probe without PREFP was stable). Locks replace the
    # boost where they apply; unlocked spec boards keep 0.85.
    if spec and asg is None:
        os.environ["OGC_PREFP"] = os.environ.get("OGC_PREFP", "0.85")
    eng, seeds = _seed_free(p, asg=asg)
    if eng is None and asg is not None:
        asg = None
        eng, seeds = _seed_free(p)
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
    _spb0 = sec / max(1, n)
    _pop_here = (os.environ.get("OGC_POP", "") == "1"
                 or (os.environ.get("OGC_POP", "") == ""
                     and sec >= 150.0 and _spb0 <= 1.6))
    # NB: OGC_SLICE, deliberately NOT OGC_REFINE -- that name already
    # gates the old certified-CP post-pass further down, and sharing it
    # would silently switch on a refuted module.
    _rfshare = 0.0
    if (sec > 60.0 and not _pop_here
            and os.environ.get("OGC_SLICE", "1") == "1"):
        _rfshare = float(os.environ.get("OGC_SLSH", "0.30")) * sec
        sec -= _rfshare
    # bay-split budget carve, gated on BAY SIZE: measured 08-01 at a
    # 22% tail -- P6 (83 blocks/bay) -3.4%, P5 (50/bay) flat, P4
    # (50/bay) +5.3%. Big bays are where the joint anneal spreads too
    # thin for its per-bay work to be deep; small bays it handles.
    _blc = float(os.environ.get("OGC_BOXLNS", "0"))
    if _blc > 0 and sec > _blc + 60.0:
        sec -= _blc                      # carve the LNS slice up front
    _bpb = n / max(1, len(p["bays"]))
    _bs_on = (os.environ.get("OGC_BAYSPLIT", "0") == "1"
              and _bpb >= float(os.environ.get("OGC_BSMIN", "65"))
              and sec > 90.0)
    if _bs_on:
        sec = max(60.0, float(os.environ.get("OGC_BSJOINT", "0.78"))
                  * sec)
    # MORPH ARM (env OGC_MARM = w3 multiplier, default off): spend
    # ~35% of the budget annealing under a preference-boosted
    # objective, then inject that board as a start for the true-weight
    # portfolio. Offline discovery (08-01): objective bias steers the
    # anneal into different valleys whose boards win under TRUE
    # weights (morph-a x long: P4 -8.5%). In-TL transfer candidate.
    _ma = float(os.environ.get("OGC_MARM", "0"))
    if _ma > 0 and sec > 60.0:
        _sec1 = min(0.35 * sec, 300.0)
        _pm = dict(p)
        _pm["weights"] = {"w1": p["weights"]["w1"],
                          "w2": p["weights"]["w2"],
                          "w3": p["weights"]["w3"] * _ma}
        _e2, _s2 = _seed_free(_pm)
        if _e2 is not None:
            _st2 = [_s2[0], []] + _s2[1:3] if _s2 else []
            _e2.run_portfolio(_THR, _sec1, 1, 0.05, 0.15, 0.002,
                              0.9995, seed, 1, _st2, 0)
            starts = [_e2.get_solution()] + starts[:3]
        sec = max(2.0, deadline - time.time() - reserve)
    rounds = 1 if sec > 110 else 2
    if asg is not None:
        eng.set_assignment_lock(asg)
    # population vs portfolio: 3-seed A/B (07-31) -- population wins the
    # TL240 class (P3: 3/3 seeds), ties with high variance at TL480, and
    # loses TL600/900 (P5/P6: 3/3 each; burst=sec/8 starves big boards).
    # Default rule: population only in its measured win zone.
    _pe = os.environ.get("OGC_POP", "")
    # SCALE-INVARIANT population gate (08-01, 40-instance sweep): the
    # old absolute window (150 <= sec <= 250) was an artifact of P3's
    # budget-per-block, and on the train set it cost 1.3pp of regret
    # vs always-portfolio (3.77 vs 2.49) by firing on loose-budget
    # n=100 boards where the portfolio wins (prob_24: port -49.8%).
    # The population's edge is the TIGHT regime (P3: 1.05 s/block);
    # gate on that ratio so hidden instances of any size land right.
    # LAHC gate (measured 08-02): canonical late-acceptance beats SA
    # on the BIG boards -- P6 (n=250, 83 blocks/bay, 900s) improved in
    # 3/3 independent runs (-2.7%, -3.5%, -5.0%) with P3 unchanged,
    # while P4/P5-class boards lose. Those are the instances with the
    # most iterations per block, where a long quality memory pays and
    # a decaying temperature does not. Gate on that structure, not on
    # the instance id.
    # LAHC with a SELF-SCALED history (L = -1 asks the kernel for
    # n * lahcMul slots, so the memory always spans the same fraction
    # of the walk regardless of instance size) plus the slack
    # surrogate, whose weight is 1/n inside the kernel -- neither
    # carries a hand-tuned constant, which is what makes them safe on
    # hidden instances.
    # LAHC was a real win over the OLD repair order, but once repair
    # inserts hardest-first (OGC_RORDER=2) the SA schedule is better
    # on 3/4 boards (08-02 head-to-head, seeds 9-11): the acceptance
    # rule was compensating for a weak repair, not adding search
    # power. Kept in the kernel behind OGC_LAHC, default off.
    # slack surrogate REFUTED by its own ablation (08-02): turning it
    # OFF gave the best P5 of the whole sweep (10,212,943 vs 10.7-11.6M
    # with it on). The earlier "P5 gains" were seed variance, not the
    # term. Left in the kernel behind OGC_SLACK, default off.
    _spb = sec / max(1, n)
    _use_pop = _pe == "1" or (_pe == "" and sec >= 150.0
                              and _spb <= 1.6)
    # learned persona-fleet selector (LOIO-validated 08-01: fixed
    # fleet wastes 7.09% avg per thread; predicted [best x2, 2nd x2]
    # fleet 4.37%, oracle 3.18%; model beat fixed on 27/40 held-out
    # instances). Ridge weights baked; features are instance meta.
    # REFUTED at official TLs (fleet-s7/s8: P4 +14%, P5 +6%): the
    # judgment metric was mean per-thread loss but the outcome is the
    # MIN over correlated draws -- min-simulation shows even ORACLE
    # persona pairs gain <=1%p while duplicate-persona correlation
    # erases it. Fixed diverse fleet is min-optimal. Default OFF.
    if os.environ.get("OGC_FLEET", "0") == "1":
        _PW = [[0.075931, 0.153667, -0.137218, 0.075835, 0.006136, 0.06412, -0.002169, -0.071218], [-0.033278, -0.012892, 0.028401, -0.001825, 0.016969, 0.021952, 0.000774, 0.042794], [0.022798, 0.148489, -0.14064, 0.070772, 0.001814, 0.078943, -0.000355, -0.029713], [-0.044941, -0.012549, 0.039284, -0.011994, 0.002369, -0.020951, -0.000155, 0.080311]]
        _bl = p["blocks"]
        _by = p["bays"]
        _wts = p["weights"]
        _f = [len(_bl) / 250.0, len(_by) / 5.0,
              len(_bl) / len(_by) / 80.0,
              math.log10(max(_wts["w1"], 1)) / 5.0,
              _wts["w2"] / 10.0,
              math.log10(max(_wts["w3"], 1)) / 3.0,
              _wts["w3"] / (_wts["w1"] + 1e-9) * 100.0, 1.0]
        _pl = [sum(a * b for a, b in zip(_f, _PW[k])) for k in range(4)]
        _rank = sorted(range(4), key=lambda k: _pl[k])
        os.environ["OGC_PERSONA"] = ",".join(
            map(str, [_rank[0], _rank[0], _rank[1], _rank[1]]))
        _diag("fleet", persona=os.environ["OGC_PERSONA"])
    if _use_pop and sec >= 150.0:
        _population_search(p, eng, seed, sec, starts, asg)
        r = None                      # population seats its best in eng;
                                      # the portfolio diag has no row here
    else:
        r = eng.run_portfolio(_THR, sec, rounds, 0.05, 0.15, 0.002,
                              0.9995, seed, 1, starts, 0)
    if asg is not None:
        eng.set_assignment_lock([])
    try:
        # Not on the memetic path at all. Gating only the BUDGET was not
        # enough: the population search returns before its budget is up,
        # and the refinement then burst-anneals on top of a board the
        # memetic path had already polished, costing P3 4.1%. Skip the
        # whole pass there -- those boards are punctual anyway, so the
        # CP-SAT arm has nothing to rescue.
        if _pop_here:
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
    # BAY-SPLIT main search (env OGC_BAYSPLIT): the objective
    # decomposes exactly once the assignment is fixed (Z2/Z3 are
    # assignment-only, Z1 is a per-block sum, every checker rule is
    # within-bay), so after the joint phase settles the assignment we
    # anneal each bay as its own instance, in parallel, one core each.
    # Measured motivation (08-01): a 50-block bay sub-anneal recovers
    # 77% of a deliberate perturbation -- the per-bay space is where
    # the search still has room.
    if _bs_on:
        try:
            import baysplit as _bsm
            _orig = list(eng.get_solution())
            _ob = eng.objective()
            _bo = _ob[3]
            if _ob[0] <= 1e-9:
                _left = deadline - time.time() - 2.0 - 0.01 * n
                if _left > 10.0:
                    eng.run_portfolio(_THR, _left, 1, 0.05, 0.15,
                                      0.002, 0.9995, seed + 5, 1,
                                      [eng.get_solution()], 0)
                    _diag("baysplit", skipped="z1free",
                          gave_back=round(_left, 1))
                raise RuntimeError("z1-free")
            B_ = p["blocks"]
            _H3 = (max(int(round(b["due_date"])) for b in B_)
                   + max(max(1, int(math.ceil(b["processing_time"]
                                              - 1e-9)))
                         for b in B_) + 40)
            _new = _bsm.optimize(
                p, _orig, deadline - 2.0 - 0.01 * n, seed=seed,
                diag=_diag,
                solver_dir=os.path.dirname(os.path.abspath(__file__)))
            if _new is not None:
                eng.set_solution(_new)
                eng.build_occ(_H3)
                if eng.objective()[3] >= _bo:
                    eng.set_solution(_orig)
                    eng.build_occ(_H3)
        except Exception:
            _diag("baysplit", error="exception")
    # CP-SAT EXACT-REPAIR LNS (env OGC_BOXLNS = seconds): the engine's
    # repair is greedy first-fit over corner candidates; this hands a
    # ~18-block slice (one bay, one time window) to CP-SAT with x/y/
    # entry CONTINUOUS and the rest as fixed obstacles, taking the
    # proven-best arrangement. Bbox-conservative, so accepted moves are
    # checker-legal by construction.
    _bl = float(os.environ.get("OGC_BOXLNS", "0"))
    if _bl > 0:
        try:
            import boxlns as _bxm
            _o0 = list(eng.get_solution())
            _v0 = eng.objective()[3]
            B_ = p["blocks"]
            _H4 = (max(int(round(b["due_date"])) for b in B_)
                   + max(max(1, int(math.ceil(b["processing_time"]
                                              - 1e-9))) for b in B_) + 40)
            _nf = _bxm.optimize(p, _o0,
                                min(time.time() + _bl, deadline - 3.0),
                                seed=seed, diag=_diag)
            if _nf is not None:
                eng.set_solution(_nf)
                eng.build_occ(_H4)
                if eng.objective()[3] >= _v0:
                    eng.set_solution(_o0); eng.build_occ(_H4)
                else:
                    _diag("boxlns", gain=round(_v0 - eng.objective()[3]))
        except Exception:
            _diag("boxlns", error="exception")
    _pref_polish(p, eng, deadline)
    # in-TL certified CP refinement post-pass (env OGC_REFINE):
    # strictly additive -- adopts the CP board only when its EXACT
    # model objective beats the anneal's, and falls back to the anneal
    # board if the gate rejects it. ortools is in the grading env
    # (problem_statement.md sec 3.2).
    if os.environ.get("OGC_REFINE", "0") == "1":
        _rleft = deadline - time.time()
        if _rleft <= 25.0:
            _diag("refine", skipped="budget", left=round(_rleft, 1))
    if os.environ.get("OGC_REFINE", "0") == "1" and \
            deadline - time.time() > 25.0:
        try:
            import refine as _rfm
            _orig = list(eng.get_solution())
            _cands = [list(c) for c in eng.get_candidates()[:6]]
            if _orig not in _cands:
                _cands = [_orig] + _cands
            _e2 = _build_engine(p)
            B_ = p["blocks"]
            _H2 = (max(int(round(b["due_date"])) for b in B_)
                   + max(max(1, int(math.ceil(b["processing_time"]
                                              - 1e-9))) for b in B_)
                   + 40)
            _e2.build_occ(_H2)
            _rfl = _rfm.refine(
                p, _e2, _cands, _orig,
                deadline - 1.5 - 0.01 * n,
                best_obj=eng.objective()[3], diag=_diag)
            if _rfl is not None:
                eng.set_solution(_rfl)
                eng.build_occ(_H2)
                _sol2, _res2 = _gate(p, eng, n, deadline)
                if _sol2 is not None:
                    return _sol2, _res2
                eng.set_solution(_orig)
                eng.build_occ(_H2)
        except Exception:
            _diag("refine", error="exception")
            _dp = os.environ.get("OGC_REFDBG", "")
            if _dp:
                try:
                    import traceback
                    with open(_dp, "a") as _fh:
                        _fh.write(traceback.format_exc())
                except Exception:
                    pass
    # The gate consumes the portfolio's candidate SET, best first. Whether a
    # layout carries a measure-zero checker artifact is an independent
    # lottery per candidate, so trying the runner-ups on gate failure drops
    # the tail multiplicatively and rescues at runner-up quality (measured:
    # p22@T60 one draw returned a fresh-seed 1.47M while a clean 743k
    # candidate sat discarded).
    sol, res = _gate(p, eng, n, deadline)
    if sol is not None:
        return sol, res
    for k, flat in enumerate(eng.get_candidates()[1:4]):
        if time.time() > deadline - 1.0 - 0.01 * n:
            break
        eng.set_solution(flat)
        B = p["blocks"]
        eng.build_occ(max(int(round(b["due_date"])) for b in B)
                      + max(max(1, int(math.ceil(b["processing_time"] - 1e-9)))
                            for b in B) + 40)
        _diag("gate", stage=f"cand{k+1}")
        sol, res = _gate(p, eng, n, deadline)
        if sol is not None:
            return sol, res
    return None, res


def _trivial(p):
    """absolute safety net: one block at a time, alone in its best bay,
    strictly sequential in time -- geometrically and temporally feasible
    by construction. Never used unless everything else failed."""
    B = p["blocks"]; n = len(B)
    bays = p["bays"]
    recs = {}
    t = 0
    for i in sorted(range(n),
                    key=lambda i: int(round(B[i]["due_date"]))):
        xs = [v[0] for lay in B[i]["shape"][0]["layers"] for v in lay]
        ys = [v[1] for lay in B[i]["shape"][0]["layers"] for v in lay]
        w_i = max(xs) - min(xs)
        h_i = max(ys) - min(ys)
        # checker translates the RAW polygon by (x,y): align the raw
        # min-corner to the bay origin, in the bay with the most room.
        j = max(range(len(bays)),
                key=lambda q: min(bays[q]["width"] - w_i,
                                  bays[q]["height"] - h_i))
        x = int(math.ceil(-min(xs) + 1e-9))
        y = int(math.ceil(-min(ys) + 1e-9))
        R = int(math.ceil(B[i]["release_time"] - 1e-9))
        P = max(1, int(math.ceil(B[i]["processing_time"] - 1e-9)))
        e = max(t, R)
        recs[i] = [j, x, y, 0, e, e + P]
        t = e + P
    return _recs_to_sol(recs)


# -------------------------------------------------------------------- entry
def algorithm(prob_info, timelimit=60):
    try:
        return _algorithm(prob_info, timelimit)
    except Exception:
        try:
            return _trivial(prob_info)
        except Exception:
            return {"operations": {}}


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
    eng, _ = _seed_free(prob_info, diversify=False)
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
