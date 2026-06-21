"""mcts_polish.py -- per-bay MCTS re-scheduling polish on the FAST compiled decoder.

Same scope/contract as beam_polish (re-schedule each tardy bay's blocks; same assignment -> Z2/Z3
unchanged; bays independent -> safe; best-of -> no regression), but replaces the fixed-width BEAM with
UCT tree search over the entry-order. The decoder is the compiled C bottom-left over-time placer
(placer.c, shared with beam_polish via _load_lib) -> rollouts are cheap (~n incremental place_one calls),
so MCTS gets thousands of iterations per bay (vs ~20 for the old full-re-decode Python MCTS).

Why MCTS over beam: beam commits to its top-K frontier greedily; MCTS adaptively re-allocates rollouts to
the orderings that look promising AND keeps exploring (UCT), so on bays where the good order is not on the
greedy frontier it can still find it. Falls back to beam-equivalent greedy if the C lib can't load.

CRITICAL (shared with beam_polish): the orient index returned by the placer is a LIST-POSITION into the
area-sorted/deduped ORS list, NOT the JSON orient_idx. OI[bid][pos] maps it back; without it -> OOB.
"""
import math, time, random
import numpy as np
from packer import block_meta
import beam_polish  # reuse _load_lib + the C/Python place_one wrapper contract

EXPAND_K = 5          # branching: only the K most-urgent (EDD) unplaced blocks are candidate actions
C_UCT = 1.2


def _placer(W, H, MB, ORS, ORA):
    """Return a place(placed, k) closure: place block k after `placed` -> (x,y,pos,e,f). pos is a LIST
    POSITION into ORS[k] (translate with OI at the boundary). Mirrors beam_polish.beam_bay's place()."""
    lib = beam_polish._load_lib()
    import ctypes
    out = (ctypes.c_double * 4)() if lib else None

    def place(placed, k):
        p = MB[k]["proc"]
        if lib:
            act = np.empty((len(placed), 5), dtype=np.float64)
            for r, (bid, x, y, pos, e, f) in enumerate(placed):
                a = ORS[bid][pos]; act[r] = (x + a[0], y + a[1], x + a[2], y + a[3], f)
            lib.place_one(act.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), len(placed),
                          ORA[k].ctypes.data_as(ctypes.POINTER(ctypes.c_double)), len(ORS[k]),
                          MB[k]["release"], p, W, H, out)
            return out[0], out[1], int(out[2]), out[3], out[3] + p
        else:
            active = [(x + ORS[bid][pos][0], y + ORS[bid][pos][1], x + ORS[bid][pos][2],
                       y + ORS[bid][pos][3], f) for (bid, x, y, pos, e, f) in placed]
            x, y, pos, e = beam_polish._py_place(active, ORS[k], MB[k]["release"], p, W, H)
            return x, y, pos, e, e + p
    return place


class _Node:
    __slots__ = ("placed", "pset", "kids", "n", "q", "untried")
    def __init__(self, placed, pset, untried):
        self.placed = placed; self.pset = pset; self.kids = {}
        self.n = 0; self.q = 0.0; self.untried = list(untried)


def mcts_bay(W, H, ids, MB, ORS, ORA, tl=10.0, seed=0):
    """UCT over entry-order for one bay. Returns ([(bid,x,y,pos,e,f)], total_tardiness)."""
    t0 = time.time(); rng = random.Random(seed)
    place = _placer(W, H, MB, ORS, ORA)
    edd = sorted(ids, key=lambda i: (MB[i]["due"], MB[i]["release"]))

    def actions(pset):
        return [i for i in edd if i not in pset][:EXPAND_K]

    def extend(placed, k):
        x, y, pos, e, f = place(placed, k)
        return placed + [(k, x, y, pos, e, f)], max(0.0, f - MB[k]["due"])

    def rollout(placed, pset):
        """greedy EDD completion -> total tardiness of the full bay sequence."""
        td = sum(max(0.0, f - MB[bid]["due"]) for (bid, x, y, pos, e, f) in placed)
        cur = placed
        for k in edd:
            if k in pset:
                continue
            cur, dt = extend(cur, k)
            td += dt
        return cur, td

    root = _Node([], set(), actions(set()))
    best_seq, best_td = None, math.inf
    ref = None                                   # tardiness scale for reward normalization (first rollout)
    while time.time() - t0 < tl:
        node = root; path = [root]
        # selection
        while not node.untried and node.kids:
            ln = math.log(node.n + 1)
            node = max(node.kids.values(),
                       key=lambda c: c.q / max(c.n, 1) + C_UCT * math.sqrt(ln / max(c.n, 1)))
            path.append(node)
        # expansion
        if node.untried:
            a = node.untried.pop(rng.randrange(len(node.untried)))
            placed2, _ = extend(node.placed, a)
            pset2 = node.pset | {a}
            child = _Node(placed2, pset2, actions(pset2))
            node.kids[a] = child; path.append(child); node = child
        # simulation
        seq, td = rollout(node.placed, node.pset)
        if td < best_td:
            best_td, best_seq = td, seq
        if ref is None:
            ref = td if td > 1e-9 else 1.0
        r = ref / (ref + td)                     # in (0,1], higher = lower tardiness
        for nd in path:
            nd.n += 1; nd.q += r
    if best_seq is None:                         # tl too small for even one rollout
        best_seq, best_td = rollout([], set())
    return best_seq, best_td


def mcts_polish(prob, sol, utils, time_budget=10.0, seed=0):
    """Re-schedule each tardy bay via per-bay MCTS; keep per-bay improvements (best-of). Returns sol."""
    from solve import build_ops
    t0 = time.time()
    braw = prob["blocks"]; bays = prob["bays"]
    MB = {i: block_meta(braw[i], i) for i in range(len(braw))}
    for i in MB:
        MB[i]["proc"] = max(1, MB[i]["proc"])
    ORS = {i: [(o[1], o[2], o[3], o[4]) for o in MB[i]["orients"]] for i in MB}
    ORA = {i: np.asarray(ORS[i], dtype=np.float64).reshape(-1) for i in MB}
    OI = {i: [o[0] for o in MB[i]["orients"]] for i in MB}   # list-position -> JSON orient_idx
    base = utils.check_feasibility(prob, sol)
    if not base["feasible"]:
        return sol
    place = {}
    for t, ops in sol["operations"].items():
        for o in ops:
            if o["type"] == "ENTRY":
                place[o["block_id"]] = [o["bay_id"], o["x"], o["y"], o["orient_idx"], int(t), None]
    for t, ops in sol["operations"].items():
        for o in ops:
            if o["type"] == "EXIT":
                place[o["block_id"]][5] = int(t)
    by_bay = {}
    for i, p in place.items():
        by_bay.setdefault(p[0], []).append(i)

    def bay_tard(ids):
        return sum(max(0, place[i][5] - MB[i]["due"]) for i in ids)
    order = sorted(by_bay, key=lambda j: -bay_tard(by_bay[j]))
    best_sol = sol; best_obj = base["objective"]
    cur = {i: list(place[i]) for i in place}
    for j in order:
        if time.time() - t0 > time_budget:
            break
        ids = by_bay[j]
        if bay_tard(ids) <= 0 or len(ids) < 2:
            continue
        bl = min(25.0, time_budget - (time.time() - t0))
        packed, _ = mcts_bay(int(bays[j]["width"]), int(bays[j]["height"]), ids, MB, ORS, ORA, tl=bl, seed=seed)
        trial = {i: list(cur[i]) for i in cur}
        for (bid, x, y, pos, e, f) in packed:
            trial[bid] = [j, x, y, OI[bid][pos], e, f]      # pos -> real orient_idx
        ts = build_ops({i: tuple(trial[i]) for i in trial})
        r = utils.check_feasibility(prob, ts)
        if r["feasible"] and r["objective"] < best_obj - 1e-9:
            best_obj = r["objective"]; best_sol = ts; cur = trial
    return best_sol
