"""improve.py -- anytime local search (LNS/SA) over (assignment, per-bay order); the Z1 engine.

Genotype = (assignment: block->bay, order: per-bay priority list). The packer (packer.pack_bay) is the
deterministic decoder: it turns a bay's ordered block list into footprint-disjoint placements with
EXIT=ENTRY+P. Because footprint-disjoint guarantees feasibility, the objective is computed DIRECTLY
from placements (no Shapely) -- this is what lets us use the full time budget:
  Z1 = sum max(0, exit-due)  (separable per bay), Z2 = floor(range of u_j*load_j), Z3 = sum(smax-pref).

Moves (biased toward tardy blocks, since Z1 = minimize ENTRY): promote a tardy block earlier in its
bay order; reassign a block to another fitting bay; swap two blocks; reverse a short segment. Only the
1-2 affected bays are re-packed per move. Simulated-annealing acceptance; best feasible kept.
"""
import time, random, math
from packer import block_meta, pack_bay


class State:
    def __init__(self, prob, seed=0):
        self.prob = prob
        self.bays = prob["bays"]
        self.blocks = prob["blocks"]
        self.nb = len(self.bays)
        w = prob.get("weights", {})
        self.w1, self.w2, self.w3 = w.get("w1", 1), w.get("w2", 1), w.get("w3", 1)
        self.metas = {i: block_meta(prob["blocks"][i], i) for i in range(len(prob["blocks"]))}
        self.workload = {i: prob["blocks"][i]["workload"] for i in self.metas}
        self.smax = {i: max(prob["blocks"][i]["bay_preferences"]) for i in self.metas}
        self.pref = {i: prob["blocks"][i]["bay_preferences"] for i in self.metas}
        # bay weights u_j for Z2
        areas = [b["width"] * b["height"] for b in self.bays]
        avg = sum(areas) / self.nb
        self.u = [avg / a for a in areas]
        self.fit = {i: self._fitting(self.metas[i]) for i in self.metas}
        self.order = [[] for _ in range(self.nb)]      # per-bay ordered ids
        self.assign = {}                                # id -> bay
        self.place = {}                                 # id -> (bay,x,y,oi,e,f)
        self.bz1 = [0.0] * self.nb                       # per-bay Z1
        self.rng = random.Random(seed)

    def _fitting(self, mt):
        out = []
        for j, bay in enumerate(self.bays):
            for (_, lx0, ly0, lx1, ly1) in mt["orients"]:
                if math.ceil(-lx0) <= math.floor(bay["width"] - lx1) and \
                   math.ceil(-ly0) <= math.floor(bay["height"] - ly1):
                    out.append(j); break
        return out or list(range(self.nb))

    def _edd_key(self, i):
        m = self.metas[i]; return (m["due"], m["proc"])

    def repack(self, j):
        bay = self.bays[j]
        metas = [self.metas[i] for i in self.order[j]]
        z1 = 0.0
        for (bid, x, y, oi, e, f) in pack_bay(bay["width"], bay["height"], metas):
            self.place[bid] = (j, x, y, oi, e, f)
            z1 += max(0.0, f - self.metas[bid]["due"])
        self.bz1[j] = z1

    def z2(self):
        if self.nb < 2:
            return 0.0
        loads = [0.0] * self.nb
        for i, j in self.assign.items():
            loads[j] += self.workload[i]
        wl = [self.u[j] * loads[j] for j in range(self.nb)]
        return math.floor(max(wl) - min(wl))

    def z3(self):
        return sum(self.smax[i] - self.pref[i][self.assign[i]] for i in self.assign)

    def objective(self):
        return self.w1 * sum(self.bz1) + self.w2 * self.z2() + self.w3 * self.z3()

    def construct(self, policy):
        timeload = [0] * self.nb
        self.order = [[] for _ in range(self.nb)]; self.assign = {}
        for i in sorted(self.metas, key=self._edd_key):
            fit = self.fit[i]
            smax = self.smax[i]
            if policy == "pref":
                j = max(fit, key=lambda j: self.pref[i][j])
            elif policy == "bal":
                j = min(fit, key=lambda j: (timeload[j], -self.pref[i][j]))
            else:  # "obj": objective-aware — weigh Z1 contention (area-weighted load) and Z3 jointly,
                   # auto-adapting to the instance via w1,w3 (units rough; only the argmin matters)
                j = min(fit, key=lambda j: self.w1 * timeload[j] * self.u[j]
                        + self.w3 * (smax - self.pref[i][j]))
            self.assign[i] = j; self.order[j].append(i); timeload[j] += self.metas[i]["proc"]
        for j in range(self.nb):
            self.repack(j)

    def construct_from(self, assign):
        """Build from a given {block_id: bay} assignment (e.g. the cumulative master's), EDD per bay."""
        self.order = [[] for _ in range(self.nb)]; self.assign = {}
        for i in sorted(self.metas, key=self._edd_key):
            j = assign.get(i, self.fit[i][0])
            if j not in self.fit[i]:
                j = self.fit[i][0]
            self.assign[i] = j; self.order[j].append(i)
        for j in range(self.nb):
            self.repack(j)

    # -- moves: return (undo_fn) ; caller computes delta via objective() ----------
    def _box(self, i):
        j, x, y, oi, e, f = self.place[i]
        for (o, lx0, ly0, lx1, ly1) in self.metas[i]["orients"]:
            if o == oi:
                return (math.floor(x + lx0), math.floor(y + ly0),
                        math.ceil(x + lx1), math.ceil(y + ly1), e, f)
        return (x, y, x + 1, y + 1, e, f)

    def repack_cp(self, j, tl=3.0, K=24):
        """STRONG per-bay decode: CP-SAT re-optimizes a HOT SUBSET (the K most-tardy blocks of bay j) with
        the rest fixed as obstacles -- exactly the scalable granularity (CP-SAT can't solve a full congested
        bay, only <=~30-block windows). The assignment-centric frame uses cheap greedy decode everywhere and
        escalates to this only on the bottleneck bay -> improve's speed + CP's per-bay power where it counts."""
        try:
            from cpsat_packer import pack_bay_cpsat
        except Exception:
            return
        ids = self.order[j]
        if len(ids) < 2:
            return
        tardy = sorted((i for i in ids if self.place[i][5] > self.metas[i]["due"]),
                       key=lambda i: self.place[i][5] - self.metas[i]["due"], reverse=True)
        hot = tardy[:K]
        if len(hot) < K:                                  # pad with neighbours for room
            hot += [i for i in ids if i not in set(hot)][:K - len(hot)]
        hotset = set(hot)
        fixed = [self._box(i) for i in ids if i not in hotset]
        free = [self.metas[i] for i in hot]
        hint = {i: (self.place[i][1], self.place[i][2], self.place[i][3], self.place[i][4]) for i in hot}
        bay = self.bays[j]
        res = pack_bay_cpsat(bay["width"], bay["height"], free, self.blocks, tl, hint=hint, fixed=fixed)
        if not res:
            return
        for (bid, x, y, oi, e, f) in res:
            self.place[bid] = (j, x, y, oi, e, f)
        self.bz1[j] = sum(max(0.0, self.place[i][5] - self.metas[i]["due"]) for i in ids)

    def snapshot_bay(self, j):
        return (list(self.order[j]), self.bz1[j],
                {i: self.place[i] for i in self.order[j]})

    def restore_bay(self, j, snap):
        self.order[j], self.bz1[j], pl = snap
        self.place.update(pl)


def tardy_ids(st):
    return [i for i, (j, x, y, oi, e, f) in st.place.items() if f > st.metas[i]["due"]]


def improve(prob, timelimit, seed=0, verbose=False, master_assign=None, fix_assign=False):
    t0 = time.time()
    # construct candidates: pref/bal/obj heuristics + (if given) the cumulative-master assignment.
    # fix_assign=True: keep the GIVEN assignment fixed (only order/packing moves) -- used to pack an
    # exact-ILP Z2/Z3-optimal assignment to Z1=0 without the search drifting off it.
    cand_states = []
    if fix_assign and master_assign is not None:
        s = State(prob, seed); s.construct_from(master_assign)
        cand_states.append((s.objective(), "master", s))
    else:
        for pol in ("pref", "bal", "obj"):
            s = State(prob, seed); s.construct(pol)
            cand_states.append((s.objective(), pol, s))
        if master_assign is not None:
            s = State(prob, seed); s.construct_from(master_assign)
            cand_states.append((s.objective(), "master", s))
    cand_states.sort(key=lambda x: x[0])
    construct_obj, start_pol, st = cand_states[0]
    cur = st.objective(); best = cur
    best_place = dict(st.place); best_assign = dict(st.assign)
    rng = st.rng

    def edd_insert(j, i):
        ins = 0
        while ins < len(st.order[j]) and st._edd_key(st.order[j][ins]) < st._edd_key(i):
            ins += 1
        st.order[j].insert(ins, i)

    # -- operators: each returns (touched_bays, apply_fn) or None --------------
    def op_promote():  # order: move a tardy block earlier in its bay (cuts its ENTRY -> Z1)
        tids = tardy_ids(st)
        if not tids:
            return None
        i = rng.choice(tids); j = st.assign[i]; pos = st.order[j].index(i)
        if pos == 0:
            return None
        np_ = rng.randint(0, pos - 1)
        def ap():
            st.order[j].pop(pos); st.order[j].insert(np_, i); st.repack(j)
        return [j], ap

    def op_swap():  # order: swap two within a bay
        j = rng.randrange(st.nb)
        if len(st.order[j]) < 2:
            return None
        a, b = rng.randrange(len(st.order[j])), rng.randrange(len(st.order[j]))
        if a == b:
            return None
        def ap():
            st.order[j][a], st.order[j][b] = st.order[j][b], st.order[j][a]; st.repack(j)
        return [j], ap

    def op_reassign():  # ASSIGNMENT (dominant lever): tardy block -> least time-loaded fitting alt bay
        if st.nb < 2:
            return None
        tids = tardy_ids(st) or list(st.assign)
        i = rng.choice(tids); j0 = st.assign[i]
        alts = [j for j in st.fit[i] if j != j0]
        if not alts:
            return None
        load = {j: sum(st.metas[b]["proc"] for b in st.order[j]) for j in alts}
        j1 = min(alts, key=lambda j: load[j]) if rng.random() < 0.7 else rng.choice(alts)
        def ap():
            st.order[j0].remove(i); st.assign[i] = j1; edd_insert(j1, i)
            st.repack(j0); st.repack(j1)
        return [j0, j1], ap

    def op_bay_swap():  # ASSIGNMENT: swap the bays of two blocks (rebalance without changing counts)
        if st.nb < 2:
            return None
        i = rng.choice(list(st.assign)); j0 = st.assign[i]
        alts = [j for j in st.fit[i] if j != j0]
        if not alts:
            return None
        j1 = rng.choice(alts)
        cands = [k for k in st.order[j1] if j0 in st.fit[k]]
        if not cands:
            return None
        k = rng.choice(cands)
        def ap():
            st.order[j0].remove(i); st.order[j1].remove(k)
            st.assign[i] = j1; st.assign[k] = j0
            edd_insert(j1, i); edd_insert(j0, k)
            st.repack(j0); st.repack(j1)
        return [j0, j1], ap

    def op_pref():  # Z3 (preference): move a block to a higher-preference fitting bay; SA gates on full
                    # objective so it's taken only when w3·ΔZ3 gain isn't outweighed by w1·ΔZ1 rise.
        if st.nb < 2:
            return None
        cands = [i for i in st.assign
                 if any(st.pref[i][j] > st.pref[i][st.assign[i]] for j in st.fit[i] if j != st.assign[i])]
        if not cands:
            return None
        i = rng.choice(cands); j0 = st.assign[i]
        better = [j for j in st.fit[i] if j != j0 and st.pref[i][j] > st.pref[i][j0]]
        j1 = max(better, key=lambda j: st.pref[i][j])
        def ap():
            st.order[j0].remove(i); st.assign[i] = j1; edd_insert(j1, i)
            st.repack(j0); st.repack(j1)
        return [j0, j1], ap

    OPS = ([op_promote, op_swap] if fix_assign
           else [op_promote, op_swap, op_reassign, op_bay_swap, op_pref])
    w = [1.0] * len(OPS)          # adaptive weights (reward recent improvement)

    T0 = max(1.0, cur * 0.003); it = 0
    while time.time() - t0 < timelimit:
        it += 1
        now = time.time()
        T = T0 * (1 - (now - t0) / timelimit) + 1e-6
        # weighted operator choice
        tot = sum(w); r = rng.random() * tot; oi = 0; acc = 0.0
        for oi in range(len(OPS)):
            acc += w[oi]
            if r <= acc:
                break
        plan = OPS[oi]()
        if plan is None:
            continue
        touched, apply_fn = plan
        snaps = [(j, st.snapshot_bay(j)) for j in touched]
        apply_fn()
        new = st.objective(); d = new - cur
        if d <= 0 or rng.random() < math.exp(-min(50.0, d / T)):
            cur = new
            w[oi] = min(50.0, w[oi] * (1.02 if d < 0 else 1.0) + (0.3 if d < 0 else 0.0))
            if cur < best - 1e-9:
                best = cur; best_place = dict(st.place); best_assign = dict(st.assign)
        else:
            for (j, snap) in snaps:
                st.restore_bay(j, snap)
            for (j, snap) in snaps:
                for bid in snap[0]:
                    st.assign[bid] = j
            w[oi] = max(0.2, w[oi] * 0.999)

    if verbose:
        print(f"  iters={it} construct={construct_obj:.0f} final_best={best:.0f} "
              f"w={[round(x, 1) for x in w]}")
    # build solution from best_place
    ops = {}
    for bid, (j, x, y, oi, e, f) in best_place.items():
        ops.setdefault(str(int(f)), []).append({"type": "EXIT", "block_id": bid, "bay_id": j})
    for bid, (j, x, y, oi, e, f) in best_place.items():
        ops.setdefault(str(int(e)), []).append({"type": "ENTRY", "block_id": bid, "bay_id": j,
                                                 "x": int(x), "y": int(y), "orient_idx": oi})
    for t in ops:
        ops[t].sort(key=lambda o: (0 if o["type"] == "EXIT" else 1, o["block_id"]))
    sol = {"operations": {t: ops[t] for t in sorted(ops, key=int)}}
    return sol
