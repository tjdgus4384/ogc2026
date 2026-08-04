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
            if not eng.move_block(i, tb, True, 7):
                continue
            if eng.objective()[3] < before:
                improved = True
            else:
                eng.move_block(i, ob, True, 7)
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
    rounds = 1 if sec > 110 else 2
    if asg is not None:
        eng.set_assignment_lock(asg)
    if sec >= 150.0:
        _population_search(p, eng, seed, sec, starts, asg)
    else:
        r = eng.run_portfolio(_THR, sec, rounds, 0.05, 0.15, 0.002,
                              0.9995, seed, 1, starts, 0)
    if asg is not None:
        eng.set_assignment_lock([])
    _diag("portfolio", winner=int(r[2]), chunks=eng.last_chunks(),
          thread_best=[round(x) for x in r[3:]])
    _pref_polish(p, eng, deadline)
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
