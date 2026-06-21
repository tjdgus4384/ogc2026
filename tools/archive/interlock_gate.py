"""interlock_gate.py -- GATE: does TRUE per-layer interlock (footprints may overlap; only same-level
layer polygons must be disjoint + crane clearance) beat cuboid on a not-area-forced instance?

Per-level energetic analysis says prob_26/prob_39 are NOT layer-area-forced (<1) yet our cuboid leaves
big Z1 (cuboid footprint peak >1 forces it). True interlock could pack at layer density -> near-0 forced
tardiness. BUT crane rigidity (C5': an obstructer must time-nest inside the obstructed block, freezing its
exit) may cancel the gain under tight slack. Measure it.

Greedy interlock per bay (EDD): place each block at the earliest entry + a position (allowing footprint
overlap) that is (a) contained, (b) same-level collision-free with present, (c) crane-clear to ENTER,
validated with the provided utils. Exit = entry+P. Validate the full result with utils.check_feasibility.
Candidate positions = NFP-vertex set over LAYER-0 polygons (so blocks can nest). Compare Z1 vs cuboid.
"""
import sys, json, pathlib, math
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "solution" / "baseline" / "baseline"))
sys.path.insert(0, str(ROOT / "solution" / "dev"))
import utils, solve
from shapely.geometry import Polygon


def layer0_verts(blk, oi):
    return blk["shape"][oi]["layers"][0]


def interlock_pack_bay(prob, bay_id, ids, tl=60):
    import time
    t0 = time.time()
    bay = prob["bays"][bay_id]; W, H = bay["width"], bay["height"]
    Bdata = prob["blocks"]
    metas = {i: solve.block_meta(Bdata[i], i) for i in ids}
    order = sorted(ids, key=lambda i: (Bdata[i]["due_date"], Bdata[i]["processing_time"]))
    present = []   # list of utils.Block currently placed (with .entry/.exit tracked separately)
    info = {}      # id -> (entry, exit, oi, x, y)
    for i in order:
        if time.time() - t0 > tl:
            return None
        b = Bdata[i]; R, P, due = b["release_time"], b["processing_time"], b["due_date"]
        best = None
        for (oi, lx0, ly0, lx1, ly1) in metas[i]["orients"]:
            if math.ceil(-lx0) > math.floor(W - lx1) or math.ceil(-ly0) > math.floor(H - ly1):
                continue
            l0 = layer0_verts(b, oi)
            # candidate entries = release + present exits
            for e in sorted({R} | {info[k][1] for k in info if info[k][1] > R}):
                if best is not None and e >= best[0]:
                    break
                pres_ids = [k for k in info if info[k][0] <= e < info[k][1]]  # present at entry e
                # NFP-vertex candidate positions over layer-0 (allow footprint overlap)
                cand = {(max(0, math.ceil(-lx0)), max(0, math.ceil(-ly0)))}
                for k in pres_ids:
                    kb = Bdata[k]; kl0 = kb["shape"][info[k][2]]["layers"][0]
                    kx, ky = info[k][3], info[k][4]
                    for (ax, ay) in kl0:
                        for (mx, my) in l0:
                            px, py = math.ceil(kx + ax - mx), math.ceil(ky + ay - my)
                            cand.add((px, py))
                cand = [(px, py) for (px, py) in cand
                        if px + lx0 >= -1e-9 and px + lx1 <= W + 1e-9 and py + ly0 >= -1e-9 and py + ly1 <= H + 1e-9]
                cand.sort(key=lambda c: (c[1], c[0]))
                for (px, py) in cand[:40]:
                    blk_i = utils.Block(block_id=i, block_data=b, x=int(px), y=int(py), orient_idx=oi)
                    pres_blocks = [utils.Block(block_id=k, block_data=Bdata[k], x=int(info[k][3]),
                                               y=int(info[k][4]), orient_idx=info[k][2]) for k in pres_ids]
                    if not bay_obj.contains_block(blk_i):
                        continue
                    if utils.check_collisions(bay_obj, pres_blocks + [blk_i]):
                        continue
                    if utils.check_entry(bay_obj, pres_blocks, blk_i, fast=True):
                        continue
                    best = (e, px, py, oi); break
                if best and best[0] == e:
                    break
            if best and best[0] == R:
                break
        if best is None:
            e = max(R, max((info[k][1] for k in info), default=0))
            oi, lx0, ly0, lx1, ly1 = metas[i]["orients"][0]
            best = (e, max(0, math.ceil(-lx0)), max(0, math.ceil(-ly0)), oi)
        e, px, py, oi = best
        info[i] = (e, e + P, oi, px, py)
    return {i: (bay_id, info[i][3], info[i][4], info[i][2], info[i][0], info[i][1]) for i in ids}


if __name__ == "__main__":
    nm = sys.argv[1] if len(sys.argv) > 1 else "prob_26"
    prob = json.load(open(ROOT / "data" / "train" / f"{nm}.json"))
    metas = [solve.block_meta(prob["blocks"][i], i) for i in range(len(prob["blocks"]))]
    asg = solve._assign(prob, metas, "bal")
    place = {}
    import time; t0 = time.time()
    ok = True
    for j in range(len(prob["bays"])):
        global bay_obj
        bay_obj = utils.Bay.from_dict(prob["bays"][j], j)
        ids = [i for i in asg if asg[i] == j]
        r = interlock_pack_bay(prob, j, ids, tl=120)
        if r is None:
            ok = False; break
        place.update(r)
    if not ok:
        print(nm, "interlock: TIMEOUT/FAIL")
    else:
        sol = solve.build_ops(place); res = utils.check_feasibility(prob, sol)
        # cuboid baseline (same assignment)
        cub = utils.check_feasibility(prob, solve.build_ops(solve._pack_all(prob, metas, asg)))
        print("%s interlock: feas=%s Z1=%s obj=%s  | cuboid: Z1=%s obj=%s  (%.0fs)" % (
            nm, res["feasible"], res.get("obj1"), res.get("objective"),
            cub.get("obj1"), cub.get("objective"), time.time() - t0))
