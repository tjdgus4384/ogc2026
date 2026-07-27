"""anytime.py -- Approach A: budget-scaling ALNS + CP repair (OGC 2026).

Rebuild of solve_lns into an ANYTIME solver that exploits the full (up to 30-min) budget instead of
quitting on staleness. Footprint-disjoint cuboid model (EXIT = ENTRY + P). Minimizes the true weighted
objective w1*Z1 + w2*Z2 + w3*Z3, dominated by Z1 (total tardiness).

Differences vs solve.solve_lns:
  - NO stale-stop: runs until the time budget; staleness triggers DIVERSIFICATION, not termination.
  - Destroy portfolio (roulette by recent success): worst-tardy subset, spatial cluster, time-window,
    cross-bay MIGRATE (the new lever -- repairs two bays so a bad assignment can be fixed), random kick.
  - K and repair time-limit GROW with budget (CP-SAT is ~5x our packer at k<=30 -- feed it bigger windows).
  - SA acceptance in polish (escape local optima); global best (by true objective) tracked and returned.

Public: solve_anytime(prob, total_budget, seed=0, cp_workers=4, t_construct=None, verbose=False) -> sol dict.
"""
import time, math, random
from packer import block_meta, _best_pos
from cpsat_packer import pack_bay_cpsat
from solve import build_ops, _orient_off
import improve


def _improve_seed(args):
    prob, ts, s = args
    try:
        import improve, utils
        sol = improve.improve(prob, ts, seed=s)
        r = utils.check_feasibility(prob, sol)
        return (r["objective"], sol) if r["feasible"] else (None, None)
    except Exception:
        return (None, None)


def _parse(sol):
    place = {}
    for t, ops in sol["operations"].items():
        for o in ops:
            if o["type"] == "ENTRY":
                place[o["block_id"]] = [o["bay_id"], o["x"], o["y"], o["orient_idx"], int(t), None]
    for t, ops in sol["operations"].items():
        for o in ops:
            if o["type"] == "EXIT":
                place[o["block_id"]][5] = int(t)
    return place


class _Scorer:
    """Closed-form true objective from a `place` dict, matching improve.State.objective()."""
    def __init__(self, prob):
        w = prob.get("weights", {})
        self.w1, self.w2, self.w3 = w.get("w1", 1), w.get("w2", 1), w.get("w3", 1)
        self.nb = len(prob["bays"])
        self.due = {i: prob["blocks"][i]["due_date"] for i in range(len(prob["blocks"]))}
        self.workload = {i: prob["blocks"][i]["workload"] for i in range(len(prob["blocks"]))}
        self.smax = {i: max(prob["blocks"][i]["bay_preferences"]) for i in range(len(prob["blocks"]))}
        self.pref = {i: prob["blocks"][i]["bay_preferences"] for i in range(len(prob["blocks"]))}
        areas = [b["width"] * b["height"] for b in prob["bays"]]
        avg = sum(areas) / self.nb
        self.u = [avg / a for a in areas]

    def z1(self, place):
        return sum(max(0, place[i][5] - self.due[i]) for i in place)

    def z2(self, place):
        if self.nb < 2:
            return 0.0
        loads = [0.0] * self.nb
        for i in place:
            loads[place[i][0]] += self.workload[i]
        wl = [self.u[j] * loads[j] for j in range(self.nb)]
        return math.floor(max(wl) - min(wl))

    def z3(self, place):
        return sum(self.smax[i] - self.pref[i][place[i][0]] for i in place)

    def obj(self, place):
        return self.w1 * self.z1(place) + self.w2 * self.z2(place) + self.w3 * self.z3(place)


def solve_anytime(prob, total_budget, seed=0, cp_workers=4, t_construct=None, verbose=False):
    rng = random.Random(seed)
    t0 = time.time()
    bays = prob["bays"]; nb = len(bays)
    metas = {i: block_meta(prob["blocks"][i], i) for i in range(len(prob["blocks"]))}
    sc = _Scorer(prob)

    # -- MULTI-SEED construct: improve() is CP-free, cheap, and EXTREMELY seed-sensitive on the
    # assignment/Z3 (prob_1: seed1 -> bk, seed0 -> 39x worse). Sample several seeds in parallel and
    # keep the best start; the single deep tardiness ALNS then runs from it. -----------------------
    # construct gets ~30% of the total budget. n_seeds in parallel waves of 4 (cpu cap); ts is sized so
    # waves*ts stays inside that 30% -> NEVER overruns the total budget (server treats overrun as -1).
    construct_wall = min(180.0, total_budget * 0.30)
    n_seeds = 8 if total_budget >= 60 else 4
    waves = (n_seeds + 3) // 4
    ts = max(4.0, min(90.0, construct_wall / waves))
    sol = None; best_co = float("inf")
    try:
        import multiprocessing as mp
        nproc = min(4, mp.cpu_count() or 1)
        with mp.Pool(nproc) as pool:
            for o, s in pool.map(_improve_seed, [(prob, ts, seed * 100 + k) for k in range(n_seeds)]):
                if o is not None and o < best_co:
                    best_co, sol = o, s
    except Exception:
        sol = None
    if sol is None:                                       # mp blocked -> a few sequential seeds
        import utils as _u
        for k in range(3):
            c = improve.improve(prob, ts, seed=seed * 100 + k)
            r = _u.check_feasibility(prob, c)
            if r["feasible"] and r["objective"] < best_co:
                best_co, sol = r["objective"], c
    if sol is None:
        sol = improve.improve(prob, ts, seed=seed)
    place = _parse(sol)

    def box(i):
        j, x, y, oi, e, f = place[i]
        lx0, ly0, lx1, ly1 = _orient_off(metas[i], oi)
        return (math.floor(x + lx0), math.floor(y + ly0), math.ceil(x + lx1), math.ceil(y + ly1), e, f)

    def tard(i):
        return max(0, place[i][5] - metas[i]["due"])

    def bay_ids(j):
        return [i for i in place if place[i][0] == j]

    def bay_tard(j):
        return sum(tard(i) for i in bay_ids(j))

    # fitting bays per block (for migrate)
    fit = {}
    for i in metas:
        fb = []
        for j, bay in enumerate(bays):
            for (_, lx0, ly0, lx1, ly1) in metas[i]["orients"]:
                if math.ceil(-lx0) <= math.floor(bay["width"] - lx1) and \
                   math.ceil(-ly0) <= math.floor(bay["height"] - ly1):
                    fb.append(j); break
        fit[i] = fb or list(range(nb))

    best_place = {i: list(place[i]) for i in place}
    best_obj = sc.obj(place)
    cur_obj = best_obj

    # NOTE: a construct-Z1 ADAPTIVE BRANCH (low Z1 -> commit to CP-free improve) was tried and REMOVED --
    # broader testing showed construct-Z1 does NOT predict improve-vs-anytime: prob_4 (Z1=14, "low") is
    # strongly ANYTIME-favored (anytime 82609 vs improve 273247, +231%) because anytime optimizes Z2/Z3 far
    # better. Committing to improve without a best-of safety net regresses such instances catastrophically.
    # Keep the proven single deep CP-ALNS engine for all instances.

    # -- repair one bay's hot subset with CP-SAT; returns (new_place_for_subset, dtard) or None ---
    def repair_bay(j, K, rep_tl, force_ids=None, workers=None):
        ids = bay_ids(j)
        if not ids:
            return None
        tardy = [i for i in ids if tard(i) > 0]
        tardy.sort(key=tard, reverse=True)
        hot = list(force_ids) if force_ids else []
        for i in tardy:
            if i not in hot and len(hot) < K:
                hot.append(i)
        if len(hot) < K:                       # add movable neighbours for room
            others = [i for i in ids if i not in hot]
            rng.shuffle(others)
            hot += others[:max(0, (K - len(hot)) // 2)]
        if not hot:
            return None
        hotset = set(hot)
        fixed = [box(i) for i in ids if i not in hotset]
        free = [metas[i] for i in hot]
        hint = {i: (place[i][1], place[i][2], place[i][3], place[i][4]) for i in hot}
        res = pack_bay_cpsat(bays[j]["width"], bays[j]["height"], free, prob["blocks"],
                             rep_tl, hint=hint, fixed=fixed, workers=cp_workers if workers is None else workers)
        if not res:
            return None
        old = sum(tard(i) for i in hot)
        new = sum(max(0, f - metas[bid]["due"]) for (bid, x, y, oi, e, f) in res)
        return res, old - new

    # -- destroy operators: each returns a list of candidate bay repairs -----------------
    def op_worst(K, rep_tl):                   # D1: most-tardy subset of worst bay
        bt = sorted(((bay_tard(j), j) for j in range(nb)), reverse=True)
        bt = [(s, j) for s, j in bt if s > 0]
        if not bt:
            return None
        j = bt[0][1] if rng.random() < 0.6 else rng.choice(bt)[1]
        r = repair_bay(j, K, rep_tl)
        return [(j, r[0])] if r and r[1] > 1e-9 else None

    def op_cluster(K, rep_tl):                 # D2: spatial cluster around a random tardy block
        tardy_all = [i for i in place if tard(i) > 0]
        if not tardy_all:
            return None
        seed_i = rng.choice(tardy_all)
        j = place[seed_i][0]
        sx0, sy0, sx1, sy1, _, _ = box(seed_i)
        cx, cy = (sx0 + sx1) / 2, (sy0 + sy1) / 2
        near = sorted(bay_ids(j), key=lambda i: ((box(i)[0] + box(i)[2]) / 2 - cx) ** 2
                      + ((box(i)[1] + box(i)[3]) / 2 - cy) ** 2)[:K]
        r = repair_bay(j, K, rep_tl, force_ids=near)
        return [(j, r[0])] if r and r[1] > 1e-9 else None

    def op_timewin(K, rep_tl):                 # D3: time-overlap -- the blocks actually contending
        tardy_all = [i for i in place if tard(i) > 0]
        if not tardy_all:
            return None
        b = rng.choice(tardy_all)
        j = place[b][0]
        be, bf = place[b][4], place[b][5]
        # blocks in j whose [entry,exit] overlaps b's interval = its space-time competitors
        comp = [i for i in bay_ids(j) if place[i][5] > be and place[i][4] < bf]
        comp.sort(key=tard, reverse=True)
        r = repair_bay(j, K, rep_tl, force_ids=comp[:K])
        return [(j, r[0])] if r and r[1] > 1e-9 else None

    def op_random(K, rep_tl):                  # D5: random kick on a random non-empty bay
        nonempty = [j for j in range(nb) if bay_ids(j)]
        if not nonempty:
            return None
        j = rng.choice(nonempty)
        ids = bay_ids(j); rng.shuffle(ids)
        r = repair_bay(j, K, rep_tl, force_ids=ids[:K])
        return [(j, r[0])] if r else None       # accept even non-improving (SA gate decides)

    OPS = [op_worst, op_cluster, op_timewin, op_random]
    w_op = [3.0, 2.0, 2.0, 1.0]                 # roulette weights, adapt by success

    # -- cross-bay MIGRATE core: move block b to bay jp, CP-repair both, return (snap,delta) --
    def _do_migrate(b, jp, K, rep_tl, workers=None):
        j = place[b][0]
        snap = {i: list(place[i]) for i in place}
        old_local = sc.obj(place)
        place[b][0] = jp                                  # tentative default placement in jp
        place[b][1] = max(0, math.ceil(-metas[b]["orients"][0][1]))
        place[b][2] = max(0, math.ceil(-metas[b]["orients"][0][2]))
        place[b][3] = metas[b]["orients"][0][0]
        ok = True
        for bay_j, force in ((jp, [b]), (j, None)):
            r = repair_bay(bay_j, K, rep_tl, force_ids=force, workers=workers)
            if r is None:
                if bay_j == jp:
                    ok = False; break
                continue
            for (bid, x, y, oi, e, f) in r[0]:
                place[bid] = [bay_j, x, y, oi, e, f]
        if not ok:
            for i in snap:
                place[i] = snap[i]
            return None
        return ("MIGRATE", snap, sc.obj(place) - old_local)

    def migrate(K, rep_tl):                    # tardiness lever: worst-tardy block -> least-loaded alt
        tardy_all = [i for i in place if tard(i) > 0]
        if not tardy_all:
            return None
        tardy_all.sort(key=tard, reverse=True)
        b = tardy_all[0] if rng.random() < 0.5 else rng.choice(tardy_all)
        alts = [jp for jp in fit[b] if jp != place[b][0]]
        if not alts:
            return None
        load = [sum(metas[i]["proc"] for i in bay_ids(jp)) for jp in range(nb)]
        jp = min(alts, key=lambda x: load[x])
        return _do_migrate(b, jp, K, rep_tl)

    def pref_migrate():                        # Z3 lever: move a block toward its preferred bay
        # Uses the CP migrate with workers=1 -> DETERMINISTIC (single-worker CP-SAT is reproducible,
        # unlike workers>1) and able to REARRANGE the target bay to fit the block without tardiness
        # (a single-block insert cannot, so it gets stuck). Greedy on the full objective.
        cands = [i for i in place if sc.pref[i][place[i][0]] < sc.smax[i]
                 and any(jp != place[i][0] and sc.pref[i][jp] > sc.pref[i][place[i][0]] for jp in fit[i])]
        if not cands:
            return None
        b = rng.choice(cands)
        jp = max((jp for jp in fit[b] if jp != place[b][0]), key=lambda x: sc.pref[b][x], default=None)
        if jp is None or sc.pref[b][jp] <= sc.pref[b][place[b][0]]:
            return None
        m = _do_migrate(b, jp, K=20, rep_tl=2.0, workers=1)
        if m is None:
            return None
        _, snap, delta = m
        return ("PREF", snap, delta)

    # -- anytime ALNS loop: single trajectory, FIXED cooling horizon ---------------------
    # Cooling (temperature high->low, windows small->large) completes by H seconds REGARDLESS of
    # total budget, so a short budget behaves like the strong single-cool run and a long budget keeps
    # intensifying at low temperature (monotone-ish; best retained) instead of re-tuning its schedule
    # to the budget (which made a longer run land WORSE on prob_39). Diverse destroy keeps the long
    # low-temp tail productive; periodic kick-from-best escapes deep plateaus.
    H = min(total_budget, 400.0)
    it = 0; stale = 0; accepts = 0
    while True:
        el = time.time() - t0
        budget_left = total_budget - el
        if budget_left < 5.0:                            # reserve a tail (pref does 2 CP repairs ~2s ea);
            break                                        # rep_tl is also budget-capped -> never overruns
        frac = min(1.0, el / H)                          # 0 -> 1 by H, then pinned (intensify)
        rem = 1.0 - frac
        # deep plateau: occasional dive back to the global best to re-focus the low-temp tail
        if stale > 60:
            place = {i: list(best_place[i]) for i in best_place}; cur_obj = best_obj; stale = 0
        K = int(12 + 16 * frac) + (4 if stale > 8 else 0)
        K = max(8, min(30, K))
        rep_tl = min(2.0 + 4.0 * frac, max(1.0, budget_left * 0.25))   # last repair can't blow the budget
        tau = 6.0 * rem + 0.15                            # SA temp in tardiness units
        it += 1

        n_tardy = sum(1 for i in place if tard(i) > 0)
        # preference (Z3) lever: greedy pref-migrate -- the DOMINANT move once tardiness ~ 0 (else every
        # destroy op idles and Z3 stays at the construct value), and an occasional move otherwise. Greedy
        # (accept only full-obj improvement) since Z3 deltas are far below the Z1 temperature scale.
        if nb >= 2 and (n_tardy == 0 or rng.random() < 0.12):
            m = pref_migrate()
            if m is not None:
                _, snap, delta = m
                if delta < -1e-9:
                    cur_obj = sc.obj(place); accepts += 1; stale = 0
                else:
                    for i in snap:
                        place[i] = snap[i]                # revert (greedy: keep only improvements)
                    stale += 1
                if cur_obj < best_obj - 1e-9:
                    best_obj = cur_obj; best_place = {i: list(place[i]) for i in place}
                continue
            if n_tardy == 0:                              # Z1=0 and no pref gain -> converged
                stale += 1
                if stale > 150:
                    break
                continue

        do_migrate = nb >= 2 and (rng.random() < 0.18 or stale > 12)
        if do_migrate:
            m = migrate(K, rep_tl)
            if m is None:
                stale += 1; continue
            _, snap, delta = m                            # delta in objective units (>0 worse)
            accept = delta < 0 or rng.random() < math.exp(-(delta / sc.w1) / tau) if sc.w1 > 0 else delta < 0
            if accept:
                cur_obj = sc.obj(place); accepts += 1
                stale = 0 if delta < 0 else stale + 1
            else:
                for i in snap:
                    place[i] = snap[i]
                stale += 1
        else:
            oi = rng.choices(range(len(OPS)), weights=w_op)[0]
            res = OPS[oi](K, rep_tl)
            if not res:
                stale += 1; continue
            # apply tentatively, measure objective delta, SA-accept
            snap = {}
            for (j, repl) in res:
                for (bid, x, y, oi2, e, f) in repl:
                    snap[bid] = list(place[bid])
            old_obj = cur_obj
            for (j, repl) in res:
                for (bid, x, y, oi2, e, f) in repl:
                    place[bid] = [j, x, y, oi2, e, f]
            new_obj = sc.obj(place)
            delta = new_obj - old_obj
            accept = delta < 0 or (sc.w1 > 0 and rng.random() < math.exp(-(delta / sc.w1) / tau))
            if accept:
                cur_obj = new_obj
                if delta < 0:
                    w_op[oi] += 0.5; stale = 0; accepts += 1
                else:
                    stale += 1
            else:
                for bid in snap:
                    place[bid] = snap[bid]
                stale += 1

        if cur_obj < best_obj - 1e-9:
            best_obj = cur_obj
            best_place = {i: list(place[i]) for i in place}
        # decay roulette weights toward base
        if it % 20 == 0:
            w_op = [0.9 * x + 0.1 * b for x, b in zip(w_op, [3.0, 2.0, 2.0, 1.0])]
            if verbose:
                print(f"[anytime] it={it} t={el:.0f}s best={best_obj:.0f} "
                      f"cur={cur_obj:.0f} acc={accepts} stale={stale} K={K}", flush=True)

    return build_ops({i: tuple(best_place[i]) for i in best_place})


def _seed_worker(args):
    prob, budget, seed, cpw = args
    try:
        import utils
        sol = solve_anytime(prob, budget, seed=seed, cp_workers=cpw)
        r = utils.check_feasibility(prob, sol)
        return (r["objective"], sol) if r["feasible"] else (None, None)
    except Exception:
        return (None, None)


def solve_anytime_multi(prob, budget, n_seeds=4, cp_workers=1, verbose=False):
    """Run n_seeds independent anytime trajectories in parallel; return the best FEASIBLE.
    Seed diversity reduces the single-trajectory variance / basin-sticking seen on prob_38/39.
    Falls back to one deep run (all cores) if multiprocessing is unavailable (firejail)."""
    import time
    t0 = time.time()
    results = []
    try:
        import multiprocessing as mp
        n = min(n_seeds, mp.cpu_count() or 1)
        args = [(prob, budget, s, cp_workers) for s in range(n)]
        with mp.Pool(processes=n) as pool:
            results = pool.map(_seed_worker, args)
    except Exception:
        results = []
    feas = [(o, s) for o, s in results if o is not None]
    if not feas:                                          # mp blocked -> one deep run, all cores
        return solve_anytime(prob, budget, seed=0, cp_workers=max(4, cp_workers))
    feas.sort(key=lambda x: x[0])
    if verbose:
        print(f"[multi] {len(feas)} feasible objs={[round(o) for o, _ in feas]} "
              f"wall={time.time()-t0:.0f}s", flush=True)
    return feas[0][1]
