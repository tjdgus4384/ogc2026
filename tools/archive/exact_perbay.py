"""exact_perbay.py -- N3: exact footprint-disjoint per-bay optimum via CP-SAT.

Single bay, block-set S, minimize total tardiness under footprint-disjoint placement (AABB non-overlap
when time-overlapping => crane+collision auto-satisfied, matches utils). Block as bottom-left corner
(X0,Y0)+chosen-orient size (w,h); pairwise: time-sep OR x-sep OR y-sep. This is the EXACT optimum of the
footprint-disjoint subclass (an upper-bound class on the true problem, and the class our heuristic packer
lives in) -> tells us (1) how near-optimal our improver is WITHIN footprint-disjoint, (2) the size at which
CP-SAT stops converging.

Usage: python3 tools/exact_perbay.py <inst> <bay_id> <k> [tl]
"""
import sys, json, pathlib, math
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "solution" / "dev"))
from ortools.sat.python import cp_model
import packer


def block_sizes(blk):
    """Deduped footprint sizes (w,h) as integers (ceil) per orientation, for the exact rectangle model."""
    out = []
    for (oi, lx0, ly0, lx1, ly1) in packer.precompute_orients(blk):
        out.append((oi, math.ceil(lx1 - lx0), math.ceil(ly1 - ly0)))
    # dedupe by (w,h)
    seen = {}
    for (oi, w, h) in out:
        seen.setdefault((w, h), oi)
    return [(oi, w, h) for (w, h), oi in seen.items()]


def exact(prob, bay_id, metas, tl):
    bay = prob["bays"][bay_id]; W, H = bay["width"], bay["height"]
    maxR = max(m["release"] for m in metas); sumP = sum(m["proc"] for m in metas)
    Hor = maxR + sumP + 1
    cm = cp_model.CpModel()
    X0 = {}; Y0 = {}; W_ = {}; Hgt = {}; S = {}; E = {}; T = []
    fit = {}
    for m in metas:
        i = m["id"]; sizes = [(oi, w, h) for (oi, w, h) in block_sizes(prob["blocks"][i]) if w <= W and h <= H]
        fit[i] = sizes
        sel = [cm.NewBoolVar(f"sel{i}_{oi}") for (oi, w, h) in sizes]
        cm.AddExactlyOne(sel)
        wv = cm.NewIntVar(min(w for _, w, _ in sizes), max(w for _, w, _ in sizes), f"w{i}")
        hv = cm.NewIntVar(min(h for _, _, h in sizes), max(h for _, _, h in sizes), f"h{i}")
        cm.Add(wv == sum(b * s for (s, (_, b, _)) in zip(sel, sizes)))
        cm.Add(hv == sum(b * s for (s, (_, _, b)) in zip(sel, sizes)))
        x0 = cm.NewIntVar(0, W, f"x{i}"); y0 = cm.NewIntVar(0, H, f"y{i}")
        cm.Add(x0 + wv <= W); cm.Add(y0 + hv <= H)
        s = cm.NewIntVar(m["release"], Hor, f"s{i}"); e = cm.NewIntVar(m["release"] + m["proc"], Hor, f"e{i}")
        cm.Add(e == s + m["proc"])
        t = cm.NewIntVar(0, Hor, f"t{i}"); cm.Add(t >= e - m["due"]); T.append(t)
        X0[i], Y0[i], W_[i], Hgt[i], S[i], E[i] = x0, y0, wv, hv, s, e
    ids = [m["id"] for m in metas]
    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            i, k = ids[a], ids[b]
            lx = cm.NewBoolVar(""); rx = cm.NewBoolVar(""); ly = cm.NewBoolVar(""); ry = cm.NewBoolVar("")
            bt = cm.NewBoolVar(""); bt2 = cm.NewBoolVar("")
            cm.Add(X0[i] + W_[i] <= X0[k]).OnlyEnforceIf(lx)
            cm.Add(X0[k] + W_[k] <= X0[i]).OnlyEnforceIf(rx)
            cm.Add(Y0[i] + Hgt[i] <= Y0[k]).OnlyEnforceIf(ly)
            cm.Add(Y0[k] + Hgt[k] <= Y0[i]).OnlyEnforceIf(ry)
            cm.Add(E[i] <= S[k]).OnlyEnforceIf(bt)
            cm.Add(E[k] <= S[i]).OnlyEnforceIf(bt2)
            cm.AddBoolOr([lx, rx, ly, ry, bt, bt2])
    cm.Minimize(sum(T))
    solver = cp_model.CpSolver(); solver.parameters.max_time_in_seconds = tl
    solver.parameters.num_search_workers = 4
    st = solver.Solve(cm)
    has = st in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    return (solver.ObjectiveValue() if has else None, solver.BestObjectiveBound(),
            st == cp_model.OPTIMAL)


if __name__ == "__main__":
    inst = sys.argv[1]; bay_id = int(sys.argv[2]); k = int(sys.argv[3])
    tl = float(sys.argv[4]) if len(sys.argv) > 4 else 30.0
    prob = json.load(open(ROOT / "data" / "train" / f"{inst}.json"))
    metas = [packer.block_meta(prob["blocks"][i], i) for i in range(len(prob["blocks"]))]
    # pick k blocks that FIT this bay, earliest due first
    bay = prob["bays"][bay_id]
    def fits(m):
        return any(math.ceil(lx1 - lx0) <= bay["width"] and math.ceil(ly1 - ly0) <= bay["height"]
                   for (_, lx0, ly0, lx1, ly1) in m["orients"])
    sub = sorted([m for m in metas if fits(m)], key=lambda m: (m["due"], m["proc"]))[:k]
    # heuristic packer tardiness on the same subset+bay
    heur = sum(max(0, f - next(m["due"] for m in sub if m["id"] == bid))
               for (bid, x, y, oi, e, f) in packer.pack_bay(bay["width"], bay["height"], sub))
    obj, bound, proven = exact(prob, bay_id, sub, tl)
    print(f"{inst} bay{bay_id} k={k}: heuristic_Z1={heur}  exact_obj={obj} bound={bound:.0f} "
          f"proven_opt={proven}")
