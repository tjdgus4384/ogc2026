"""assign_lns.py -- Bilevel assignment LNS with an INCREMENTAL greedy oracle.

Validated motivation (2026-06-21 full-40 eval): on assignment-dominated instances (Z1 small) the submission
is 1.5-6x worse than the overnight best-known. Z2/Z3 depend ONLY on the bay assignment; w3>>w2 so Z3
(bay-preference penalty) dominates. The low-Z3 assignments (area-ILP / cumulative master) are NOT 2D-packable
(beam confirms Z1 explodes); the packable ones (anytime/improve) carry high Z3. The win is the packable MIDDLE
assignment, which a from-scratch 60s search doesn't reach.

Design:
  obj = w1 * sum_bay tard(bay) + w2*Z2(assign) + w3*Z3(assign)
  - Per-bay ORDER kept; a move is evaluated by GREEDY single-pass re-pack of (order +/- block) using the
    compiled C placer -- reuses the bay's existing good order, so it is FAST (~ms) and never cold-beam-
    inflated (the failure of the previous single-move version: changed bays got pessimistic cold beams ->
    everything rejected, acc=1).
  - Moves: Z3-guided relocate (badly-preferenced block -> preferred bay), Z2 load shift, swap, and EJECTION
    CHAIN (relocate into a bay that would overflow -> eject its lowest-Z3-cost block onward -> make room).
  - FINAL: re-optimize each bay with full BEAM (stronger than greedy) to recover Z1, realize, best-of.
Best-of vs the input -> never regresses.
"""
import time, math, random, ctypes
import numpy as np
import beam_polish
from packer import block_meta


class _Oracle:
    def __init__(self, prob):
        braw = prob["blocks"]
        self.MB = {i: block_meta(braw[i], i) for i in range(len(braw))}
        for i in self.MB:
            self.MB[i]["proc"] = max(1, self.MB[i]["proc"])
        self.ORS = {i: [(o[1], o[2], o[3], o[4]) for o in self.MB[i]["orients"]] for i in self.MB}
        self.ORA = {i: np.asarray(self.ORS[i], dtype=np.float64).reshape(-1) for i in self.MB}
        self.OI = {i: [o[0] for o in self.MB[i]["orients"]] for i in self.MB}
        self.bays = prob["bays"]
        self.due = {i: braw[i]["due_date"] for i in range(len(braw))}
        self.lib = beam_polish._load_lib()
        self.DPTR = ctypes.POINTER(ctypes.c_double)
        self._out = (ctypes.c_double * 4)() if self.lib else None
        self._gcache = {}                      # (j, tuple(order)) -> (td, placed[(bid,x,y,pos,e,f)])

    def greedy(self, j, order):
        """Single-pass over `order`: place each block at its earliest feasible spot. Returns (td, placed)."""
        key = (j, tuple(order))
        hit = self._gcache.get(key)
        if hit is not None:
            return hit
        W, H = int(self.bays[j]["width"]), int(self.bays[j]["height"])
        placed = []; td = 0.0
        lib = self.lib
        for k in order:
            p = self.MB[k]["proc"]
            if lib:
                act = np.empty((len(placed), 6), dtype=np.float64)   # [X0,Y0,X1,Y1,entry,exit] (entry-aware)
                for r, (bid, x, y, pos, e, f) in enumerate(placed):
                    a = self.ORS[bid][pos]; act[r] = (x + a[0], y + a[1], x + a[2], y + a[3], e, f)
                lib.place_one(act.ctypes.data_as(self.DPTR), len(placed),
                              self.ORA[k].ctypes.data_as(self.DPTR), len(self.ORS[k]),
                              self.MB[k]["release"], p, W, H, self._out)
                x, y, pos, e = self._out[0], self._out[1], int(self._out[2]), self._out[3]
            else:
                active = [(x + self.ORS[b][o][0], y + self.ORS[b][o][1], x + self.ORS[b][o][2],
                           y + self.ORS[b][o][3], e, f) for (b, x, y, o, e, f) in placed]
                x, y, pos, e = beam_polish._py_place(active, self.ORS[k], self.MB[k]["release"], p, W, H)
            f = e + p; placed.append((k, x, y, pos, e, f)); td += max(0, f - self.due[k])
        if len(self._gcache) < 200000:
            self._gcache[key] = (td, placed)
        return td, placed

    def beam(self, j, ids, tl=4.0):
        """Full beam re-optimize a bay's set. Returns (td, placed in REAL orient_idx)."""
        W, H = int(self.bays[j]["width"]), int(self.bays[j]["height"])
        packed, td = beam_polish.beam_bay(W, H, list(ids), self.MB, self.ORS, self.ORA, tl=tl)
        real = [(b, x, y, self.OI[b][pos], e, f) for (b, x, y, pos, e, f) in packed]
        return td, real


def _fits(ORS, i, bay):
    W, H = bay["width"], bay["height"]
    for (lx0, ly0, lx1, ly1) in ORS[i]:
        if math.ceil(-lx0) <= math.floor(W - lx1) and math.ceil(-ly0) <= math.floor(H - ly1):
            return True
    return False


def construct_z3(prob, utils, time_budget=20.0, order="sharp", verbose=False, _oracle=None):
    """Constructive low-Z3 PACKABLE assignment: place blocks (in `order`) into their preferred bay FIRST,
    accepting a bay only while it still greedily packs to ZERO tardiness (greedy==0 => beam==0, so the gate
    is conservative-safe). Builds a genuinely different (low-Z3) basin than anytime's. order in {sharp, due,
    workload, area, release} gives DIFFERENT column-universe samples (no tuning) -> diverse BRKGA seeds.
    Returns a solution dict or None. Pass _oracle to reuse a prebuilt _Oracle across variants (cheaper)."""
    from solve import build_ops
    t0 = time.time()
    braw = prob["blocks"]; bays = prob["bays"]; nb = len(bays)
    orc = _oracle or _Oracle(prob); ORS = orc.ORS; due = orc.due
    smax = {i: max(braw[i]["bay_preferences"]) for i in range(len(braw))}
    pref = {i: braw[i]["bay_preferences"] for i in range(len(braw))}
    fitbays = {i: [j for j in range(nb) if _fits(ORS, i, bays[j])] for i in range(len(braw))}

    def sharp(i):
        ps = sorted((pref[i][j] for j in fitbays[i]), reverse=True)
        return (ps[0] - ps[1]) if len(ps) > 1 else ps[0]
    _area = {i: max((lx1 - lx0) * (ly1 - ly0) for (lx0, ly0, lx1, ly1) in ORS[i]) for i in range(len(braw))}
    keyf = {"sharp": lambda i: -sharp(i),
            "due": lambda i: (due[i], orc.MB[i]["release"]),
            "workload": lambda i: -braw[i]["workload"],
            "area": lambda i: -_area[i],
            "release": lambda i: (orc.MB[i]["release"], due[i])}.get(order, lambda i: -sharp(i))
    blocks = sorted(range(len(braw)), key=keyf)
    bayset = {j: [] for j in range(nb)}

    def edd(ids):
        return sorted(ids, key=lambda i: (due[i], orc.MB[i]["release"]))

    for i in blocks:
        if time.time() - t0 > time_budget:                    # HARD cap: never overrun (would steal from
            return None                                        # the engine that already ran). Abort cleanly.
        prefbays = sorted(fitbays[i], key=lambda j: -pref[i][j])
        placed = False
        for j in prefbays:
            td, _ = orc.greedy(j, edd(bayset[j] + [i]))
            if td <= 1e-9:
                bayset[j].append(i); placed = True; break
        if not placed:                                          # least-tardiness fallback
            best = min(fitbays[i], key=lambda j: orc.greedy(j, edd(bayset[j] + [i]))[0])
            bayset[best].append(i)
    # GREEDY-realize (fast ~1-2s; assignment-dominated bays pack to 0 under greedy too). The downstream
    # beam/polypack polish refines the winner -- no need for the slow beam realize here (it stole anytime time).
    place = {}
    for j in range(nb):
        if not bayset[j]:
            continue
        _, pack = orc.greedy(j, edd(bayset[j]))
        if len(pack) != len(bayset[j]):
            return None
        for (b, x, y, pos, e, f) in pack:
            place[b] = (j, x, y, orc.OI[b][pos], e, f)        # pos -> real orient_idx
    if len(place) != len(braw):
        return None
    ts = build_ops(place); r = utils.check_feasibility(prob, ts)
    if verbose:
        print(f"  construct_z3 obj={r['objective'] if r['feasible'] else 'INF'} feas={r['feasible']} "
              f"Z1={r.get('obj1')} Z3={r.get('obj3')} ({time.time()-t0:.0f}s)")
    return ts if r["feasible"] else None


def placement_polish(prob, sol, utils, time_budget=8.0):
    """Per-bay best-of-PLACEMENT-RULE polish: re-pack each bay's blocks (keeping their entry order) with the
    CONTACT-MAX (anti-fragmentation) rule and keep it iff it lowers that bay's tardiness. The placement rule
    is instance-dependent (gauge: contact-max wins prob_4 -15% but loses prob_38 +3%), so best-of per bay is
    a safe way to capture the wins. Uses packer.pack_bay(PLACEMENT='best'); bays independent -> Z2/Z3 fixed."""
    import time as _t
    import packer
    from packer import block_meta
    from solve import build_ops
    t0 = _t.time()
    braw = prob["blocks"]; bays = prob["bays"]
    base = utils.check_feasibility(prob, sol)
    if not base["feasible"]:
        return sol
    MB = {i: block_meta(braw[i], i) for i in range(len(braw))}
    for i in MB:
        MB[i]["proc"] = max(1, MB[i]["proc"])
    due = {i: braw[i]["due_date"] for i in range(len(braw))}
    ext = {}
    for tt, ops in sol["operations"].items():
        for o in ops:
            if o["type"] == "EXIT":
                ext[o["block_id"]] = int(tt)
    cur = {}; byb = {}
    for tt, ops in sol["operations"].items():
        for o in ops:
            if o["type"] == "ENTRY":
                b = o["block_id"]
                cur[b] = (o["bay_id"], o["x"], o["y"], o["orient_idx"], int(tt), ext[b])
                byb.setdefault(o["bay_id"], []).append((int(tt), b))
    changed = False
    saved = packer.PLACEMENT
    try:
        packer.PLACEMENT = "best"
        for j, lst in byb.items():
            if _t.time() - t0 > time_budget:
                break
            # LARGE-BAY GUARD (P3 timeout safety). pack_bay's extreme-point "best" placement scans
            # candidate corners per block with a per-bay-only time-check -> a single huge bay's pack is an
            # un-interruptible call that can blow the wall budget (the P3 -1 mode). Skip oversized bays
            # (keep their current placement); the contact-max win is on normal-sized congested bays anyway.
            if int(bays[j]["width"]) * int(bays[j]["height"]) > 60000:
                continue
            order = [i for _, i in sorted(lst)]            # current entry order
            cur_td = sum(max(0, cur[i][5] - due[i]) for i in order)
            metas = [MB[i] for i in order]
            out = packer.pack_bay(int(bays[j]["width"]), int(bays[j]["height"]), metas)
            new_td = sum(max(0, f - due[bid]) for (bid, x, y, oi, e, f) in out)
            if new_td < cur_td - 1e-9 and len(out) == len(order):
                for (bid, x, y, oi, e, f) in out:
                    cur[bid] = (j, x, y, oi, e, f)
                changed = True
    finally:
        packer.PLACEMENT = saved
    if not changed:
        return sol
    ts = build_ops({i: tuple(cur[i]) for i in cur})
    r = utils.check_feasibility(prob, ts)
    return ts if (r["feasible"] and r["objective"] < base["objective"] - 1e-9) else sol


def construct_seeds(prob, utils, time_budget=12.0, orders=("sharp", "due", "workload"), verbose=False):
    """Run construct_z3 under several block-orderings (shared oracle) -> list of diverse feasible seed
    solutions. No tuning -> low overfit risk; each ordering samples a different column universe."""
    t0 = time.time()
    orc = _Oracle(prob)
    out = []
    for od in orders:
        if time.time() - t0 > time_budget:
            break
        try:
            s = construct_z3(prob, utils, time_budget=max(2.0, time_budget - (time.time() - t0)),
                             order=od, _oracle=orc)
        except Exception:
            s = None
        if s is not None:
            r = utils.check_feasibility(prob, s)
            if r["feasible"]:
                out.append(s)
                if verbose:
                    print(f"  seed[{od}] obj={r['objective']:.0f} Z1={r.get('obj1')}")
    return out


def assign_lns(prob, sol, utils, time_budget=30.0, seed=0, verbose=False):
    from solve import build_ops
    t0 = time.time(); rng = random.Random(seed)
    braw = prob["blocks"]; bays = prob["bays"]; nb = len(bays)
    w = prob.get("weights", {}); w1, w2, w3 = w.get("w1", 1), w.get("w2", 1), w.get("w3", 1)
    base = utils.check_feasibility(prob, sol)
    if not base["feasible"]:
        return sol
    orc = _Oracle(prob); ORS, MB = orc.ORS, orc.MB

    # parse input -> assignment + per-bay ORDER (entry-time order) ------------------------------------
    ent = {}; ext = {}
    for t, ops in sol["operations"].items():
        for o in ops:
            if o["type"] == "ENTRY":
                ent[o["block_id"]] = (o["bay_id"], int(t))
            else:
                ext[o["block_id"]] = int(t)
    asg = {i: ent[i][0] for i in ent}
    order = {j: [] for j in range(nb)}
    for i in sorted(ent, key=lambda i: ent[i][1]):
        order[asg[i]].append(i)

    workload = {i: braw[i]["workload"] for i in range(len(braw))}
    smax = {i: max(braw[i]["bay_preferences"]) for i in range(len(braw))}
    pref = {i: braw[i]["bay_preferences"] for i in range(len(braw))}
    due = orc.due
    areas = [b["width"] * b["height"] for b in bays]; avg = sum(areas) / nb
    U = [avg / a for a in areas]
    fitbays = {i: [j for j in range(nb) if _fits(ORS, i, bays[j])] for i in range(len(braw))}

    load = [sum(workload[i] for i in order[j]) for j in range(nb)]
    bay_td = [orc.greedy(j, order[j])[0] for j in range(nb)]

    def Z2():
        wl = [U[j] * load[j] for j in range(nb)]
        return math.floor(max(wl) - min(wl)) if nb >= 2 else 0

    Z3sum = sum(smax[i] - pref[i][asg[i]] for i in asg)

    def cur_obj():
        return w1 * sum(bay_td) + w2 * Z2() + w3 * Z3sum

    def insert_pos(j, i):                       # EDD insertion index into order[j]
        d = due[i]; o = order[j]
        for k, b in enumerate(o):
            if due[b] > d:
                return k
        return len(o)

    cur = cur_obj()
    base_obj = base["objective"]
    if verbose:
        print(f"  assign_lns start greedy_obj={cur:.0f} base={base_obj:.0f}")

    def try_relocate(i, j2):
        """move block i to bay j2 (greedy-evaluated). apply iff obj improves. returns True if applied."""
        nonlocal cur, Z3sum
        j1 = asg[i]
        if j1 == j2:
            return False
        o1 = [b for b in order[j1] if b != i]
        pos = insert_pos(j2, i); o2 = order[j2][:pos] + [i] + order[j2][pos:]
        td1, _ = orc.greedy(j1, o1); td2, _ = orc.greedy(j2, o2)
        dZ3 = (smax[i] - pref[i][j2]) - (smax[i] - pref[i][j1])
        nload = list(load); nload[j1] -= workload[i]; nload[j2] += workload[i]
        nZ2 = math.floor(max(U[j] * nload[j] for j in range(nb)) - min(U[j] * nload[j] for j in range(nb))) if nb >= 2 else 0
        new = w1 * (sum(bay_td) - bay_td[j1] - bay_td[j2] + td1 + td2) + w2 * nZ2 + w3 * (Z3sum + dZ3)
        if new < cur - 1e-9:
            order[j1] = o1; order[j2] = o2; asg[i] = j2
            bay_td[j1] = td1; bay_td[j2] = td2; load[j1] -= workload[i]; load[j2] += workload[i]
            Z3sum += dZ3; cur = new
            return True
        return False

    bay_pack = [orc.greedy(j, order[j])[1] for j in range(nb)]   # per-bay placements (cached)

    def tardy_list():
        """blocks finishing after due, by current greedy packings, heaviest-tardy first."""
        out = []
        for j in range(nb):
            for (b, x, y, pos, e, f) in bay_pack[j]:
                if f > due[b]:
                    out.append((w1 * (f - due[b]), b))
        out.sort(reverse=True)
        return [b for _, b in out]

    def refresh_packs():
        for j in range(nb):
            bay_pack[j] = orc.greedy(j, order[j])[1]

    blocks = list(asg); it = 0; acc = 0
    while time.time() - t0 < time_budget:
        it += 1
        r = rng.random()
        if r < 0.45:                                          # TARDINESS-guided: move a tardy block to the
            refresh_packs()                                   # bay that most lowers the TRUE objective
            tl = tardy_list()
            if tl:
                i = tl[rng.randrange(min(5, len(tl)))]        # bias to the worst few
                for j2 in fitbays[i]:
                    if j2 != asg[i] and try_relocate(i, j2):
                        acc += 1; break
                continue
        if r < 0.7:                                           # Z3-guided: bad-pref block -> better bay
            i = rng.choice(blocks); j1 = asg[i]
            # bays ranked by preference (high pref first), among fitting
            cand = sorted([j for j in fitbays[i] if j != j1], key=lambda j: -pref[i][j])
            moved = False
            for j2 in cand[:2]:
                if try_relocate(i, j2):
                    acc += 1; moved = True; break
            if not moved and cand:                            # EJECTION: make room in top-pref bay
                j2 = cand[0]
                # eject the block in j2 whose move-out costs least Z3 (lowest pref-loss to its next bay)
                if order[j2]:
                    e = min(order[j2], key=lambda b: min((pref[b][jj] for jj in fitbays[b] if jj != j2), default=-1e9) - pref[b][j2])
                    ecand = sorted([jj for jj in fitbays[e] if jj != j2], key=lambda jj: -pref[e][jj])
                    if ecand and try_relocate(e, ecand[0]):
                        acc += 1
                        if try_relocate(i, j2): acc += 1
        else:                                                 # random relocate (diversify)
            i = rng.choice(blocks); cand = [j for j in fitbays[i] if j != asg[i]]
            if cand and try_relocate(i, rng.choice(cand)):
                acc += 1

    # FINAL: beam-reoptimize each bay (stronger than greedy), realize, validate, best-of --------------
    if cur < base_obj - 1e-9 or True:                         # always try realize (greedy obj is a proxy)
        place = {}; ok = True
        for j in range(nb):
            if not order[j]:
                continue
            _, pack = orc.beam(j, order[j], tl=min(5.0, 2.0 + len(order[j]) / 40.0))
            if len(pack) != len(order[j]):
                ok = False; break
            for (b, x, y, oi, e, f) in pack:
                place[b] = (j, x, y, oi, e, f)
        if ok and len(place) == len(braw):
            ts = build_ops(place); r = utils.check_feasibility(prob, ts)
            if r["feasible"] and r["objective"] < base_obj - 1e-9:
                if verbose:
                    print(f"  assign_lns -> {r['objective']:.0f} (was {base_obj:.0f}) it={it} acc={acc}")
                return ts
    return sol
