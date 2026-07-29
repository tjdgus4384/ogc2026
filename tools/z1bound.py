"""Z1 lower bound: area-relaxed scheduling via CP-SAT.

Relaxation: geometry and crane rules dropped; what remains is that ground-
layer (layer-0) polygons of co-resident blocks in a bay cannot overlap, so
their areas sum to at most the bay area -- a per-bay cumulative resource.
Any real solution satisfies this, so the CP bound is a TRUE lower bound on
Z1 (total tardiness days). The incumbent shows what scheduling alone could
reach if packing never binds beyond area.

Usage: python z1bound.py <prob.json> [time_limit_s]
"""
import json
import math
import sys

from ortools.sat.python import cp_model


def poly_area(pts):
    s = 0.0
    for k in range(len(pts)):
        x1, y1 = pts[k]; x2, y2 = pts[(k + 1) % len(pts)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


def main():
    prob = json.load(open(sys.argv[1]))
    tl = float(sys.argv[2]) if len(sys.argv) > 2 else 120.0
    B, bays = prob["blocks"], prob["bays"]
    n, m = len(B), len(bays)

    cap = [int(round(b["width"] * b["height"])) for b in bays]
    R = [int(b["release_time"]) for b in B]
    P = [int(b["processing_time"]) for b in B]
    D = [int(b["due_date"]) for b in B]
    ground = [int(math.ceil(poly_area(b["shape"][0]["layers"][0]))) for b in B]

    # bays a block can enter at all: some orientation bbox fits
    def fits(b, bay):
        for o in b["shape"]:
            vs = [v for L in o["layers"] for v in L]
            w = max(v[0] for v in vs) - min(v[0] for v in vs)
            h = max(v[1] for v in vs) - min(v[1] for v in vs)
            if w <= bay["width"] + 1e-9 and h <= bay["height"] + 1e-9:
                return True
        return False

    H = max(D) + max(P) + 60
    md = cp_model.CpModel()
    tardy = []
    for i, b in enumerate(B):
        e = md.NewIntVar(R[i], H - P[i], f"e{i}")
        end = md.NewIntVar(0, H, f"f{i}")
        md.Add(end == e + P[i])
        t = md.NewIntVar(0, H, f"t{i}")
        md.Add(t >= end - D[i])
        tardy.append(t)
        pres = []
        for j, bay in enumerate(bays):
            if not fits(b, bay):
                continue
            y = md.NewBoolVar(f"y{i}_{j}")
            iv = md.NewOptionalIntervalVar(e, P[i], end, y, f"iv{i}_{j}")
            pres.append((j, y, iv))
        md.AddExactlyOne(y for _, y, _ in pres)
        b.setdefault("_pres", 0)
        B[i]["_iv"] = pres
    for j in range(m):
        ivs = [iv for i in range(n) for (jj, y, iv) in B[i]["_iv"] if jj == j]
        dem = [ground[i] for i in range(n)
               for (jj, y, iv) in B[i]["_iv"] if jj == j]
        md.AddCumulative(ivs, dem, cap[j])
    md.Minimize(sum(tardy))

    sv = cp_model.CpSolver()
    sv.parameters.max_time_in_seconds = tl
    sv.parameters.num_search_workers = 4
    st = sv.Solve(md)
    print(json.dumps({
        "inst": prob.get("name"),
        "status": sv.StatusName(st),
        "z1_lower_bound": sv.BestObjectiveBound(),
        "z1_incumbent": sv.ObjectiveValue()
        if st in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
        "wall": round(sv.WallTime(), 1),
    }))


if __name__ == "__main__":
    main()
