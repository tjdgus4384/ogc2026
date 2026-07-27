"""Shipyard block placement -- pipeline v5 (lean, 4 files).

  FREE   kernel-native best-of-5 seeds -> one continuous 4-thread
         portfolio on a wall-clock-normalized SA schedule (size-
         invariant temperature curve, sec 114) -> checker-gated tails:
         windowed-repack sweep (sec 109) + tardy rescue (sec 110),
         both adopt-improvements-only with snapshot rollback.
  SAFETY adaptive occupancy horizon (sec 103), pair-nudge + replace
         gate (secs 81/46), trivial always-feasible constructor and a
         never-raise contract (sec 99).
  The exact-assignment (delta) arm is dormant. sec 157: its module was also
  IMPORTED here but never called, and that import pulled in ortools CP-SAT --
  0.83s of startup on every run and, worse, a module-level single point of
  failure (a missing/mismatched ortools in the grading environment would kill
  the whole submission before algorithm() could catch anything). Import and
  file both dropped from the shipped bundle; the mechanism stays documented in
  FAST_KERNEL_SPEC secs 90-98/106 and the source is kept in the kernel-source
  archive. Formerly: assign.py keeps the
  windowed-energetic capability for re-enable on its habitat.
"""
import math
import re
import time

import os as _os

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

_THR = min(4, _os.cpu_count() or 1)   # sec 100: hard-coded 4 on a 1-core
                                      # sandbox oversubscribed every local
                                      # bench; the server has 4 cores.


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
def _seed_free(p, diversify=True):
    """kernel-native seeding, best-of-5 insertion orders (sec 87)."""
    B = p["blocks"]; n = len(B)
    D = [int(round(b["due_date"])) for b in B]
    P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
    R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
    A = [b["workload"] for b in B]
    orders = [sorted(range(n), key=lambda i: D[i])]
    if diversify:
        import random as _rnd
        _rnd.seed(5)
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
            sorted(range(n), key=lambda i: D[i] + _rnd.uniform(-8, 8)),
            sorted(range(n), key=lambda i: D[i] + _rnd.uniform(-8, 8)),
        ]
    # adaptive-horizon ladder (sec 103): on congested giants the loose
    # insert overflows the base occupancy horizon (P6 board: total seed
    # failure -> _trivial's 4.2e9). Doubling the horizon is sound (a
    # superset of feasible times) and bounded (two retries).
    eng = None
    for hmul in (1, 2, 4):
        eng, _, _ = _fresh(p, hmul)
        best = None
        for od in orders:
            if eng.reconstruct_ordered([-1] * n, od, False, 7) == -1:
                ob = eng.objective()[3]      # [z1,z2,z3,total] -> total
                if best is None or ob < best[0]:
                    best = (ob, od)
        if best is not None:
            if best[1] is not orders[-1]:
                eng.reconstruct_ordered([-1] * n, best[1], False, 7)
            return eng
    return None


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


def _gate(p, eng, n):
    """checker + pair-nudge (sec 81) + replace rounds (sec 46)."""
    sol = _recs_to_sol(_extract(eng, n))
    res = _check(p, sol)
    if res is None:
        return sol, None            # checker unusable -> trust the kernel
    if res.get("feasible"):
        return sol, res
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
    sol = _recs_to_sol(_extract(eng, n))
    res = _check(p, sol)
    if res is None:
        return sol, None
    if res.get("feasible"):
        return sol, res
    for sd in (21, 22, 23, 24):
        ids = []
        for v in res.get("violations", [])[:6]:
            for x in re.findall(r"block (\d+)", str(v)):
                if int(x) not in ids:
                    ids.append(int(x))
        moved = 0
        for bi in ids:
            b = eng.get_block(bi)
            if b[6] and eng.move_block(bi, b[0], False, sd):
                moved += 1
        if not moved:
            break
        sol = _recs_to_sol(_extract(eng, n))
        res = _check(p, sol)
        if res is None:
            return sol, None
        if res.get("feasible"):
            return sol, res
    return None, res


def _arm_free(p, deadline, seed=7):
    """ARM B: diversified seed + one continuous portfolio + gate; then
    keep continuing the same engine while time remains."""
    n = len(p["blocks"])
    eng = _seed_free(p)
    if eng is None:
        return None, None
    # one continuous leg on the wall-clock-normalized schedule (sec 114:
    # the schedule is size-invariant, which removed the small-instance
    # trajectory fragility that once required a polish leg), then the
    # checker-gated tails (sweep + rescue: adopt-improvements-only).
    sec = max(2.0, deadline - time.time() - 2.5)
    rounds = 1 if sec > 110 else 2
    eng.run_portfolio(_THR, sec, rounds, 0.05, 0.15, 0.002, 0.9995, seed, 1)
    # sec 156: two checker-gated tails (a window-LNS sweep and a tardy rescue)
    # used to hang here behind `>16s` / `>5s` guards that could never fire --
    # the anneal consumed the whole budget, so both executed 0 times (measured
    # at T20 and T60). Making them reachable (reserve derived from the measured
    # gate cost) was tried and REJECTED: Set B mean +3.19% at T12, a wash at
    # T60, +0.03% at T90 -- the anneal is more productive per second than
    # either tail, both of which date from a weaker-anneal era. Removed.
    return _gate(p, eng, n)


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
    s_b, r_b = _arm_free(prob_info, deadline)
    if s_b is not None:
        best = (s_b, r_b)

    if best[0] is not None:
        return best[0]
    # last resort ladder: short anneal -> gate -> trivial constructor
    eng = _seed_free(prob_info, diversify=False)
    if eng is not None:
        eng.run_portfolio(_THR, max(2.0, deadline - time.time() - 2.0), 1,
                          0.05, 0.15, 0.002, 0.9995, 5, 1)
        sol, _ = _gate(prob_info, eng, n)
        if sol is not None:
            return sol
    return _trivial(prob_info)
