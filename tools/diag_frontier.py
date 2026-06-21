"""diag_frontier.py -- is a frontier instance's residual Z1 a footprint-disjoint LIMIT, or improver slack?

Take the improver's best solution, FIX its assignment, then re-pack each bay with CP-SAT (long budget,
strongest footprint-disjoint solver). If CP-SAT beats the improver's per-bay tardiness -> the improver
still has room WITHIN footprint-disjoint (keep improving, cheaper). If it can't -> we're at/near the
footprint-disjoint density limit -> only interlock/polygon packing can help (the hard lever).

Usage: python3 tools/diag_frontier.py <inst> <imp_tl> <cpsat_tl_per_bay>
"""
import sys, json, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "solution" / "baseline" / "baseline"))
sys.path.insert(0, str(ROOT / "solution" / "dev"))
import improve, utils
from packer import block_meta
from cpsat_packer import pack_bay_cpsat

inst = sys.argv[1]; imp_tl = float(sys.argv[2]); cp_tl = float(sys.argv[3])
prob = json.load(open(ROOT / "data" / "train" / f"{inst}.json"))
bays = prob["bays"]

sol = improve.improve(prob, imp_tl)
# reconstruct assignment + entry/exit from the improver solution
asg = {}; entry = {}; exit_ = {}
for t, ops in sol["operations"].items():
    for o in ops:
        if o["type"] == "ENTRY":
            asg[o["block_id"]] = o["bay_id"]; entry[o["block_id"]] = int(t)
        else:
            exit_[o["block_id"]] = int(t)
imp_z1_bay = [0] * len(bays)
for bid, j in asg.items():
    imp_z1_bay[j] += max(0, exit_[bid] - prob["blocks"][bid]["due_date"])

metas = {i: block_meta(prob["blocks"][i], i) for i in range(len(prob["blocks"]))}
print(f"{inst}: improver Z1 total = {sum(imp_z1_bay)}  per-bay {imp_z1_bay}")
for j, bay in enumerate(bays):
    grp = sorted([metas[i] for i in asg if asg[i] == j], key=lambda m: (m["due"], m["proc"]))
    res = pack_bay_cpsat(bay["width"], bay["height"], grp, prob["blocks"], cp_tl)
    if res is None:
        print(f"  bay{j}: CP-SAT no model"); continue
    cz1 = sum(max(0, f - metas[bid]["due"]) for (bid, x, y, oi, e, f) in res)
    verdict = "CP-SAT BETTER (improver has room)" if cz1 < imp_z1_bay[j] - 1e-6 else \
              ("tie/limit" if cz1 <= imp_z1_bay[j] + 1e-6 else "improver better")
    print(f"  bay{j} ({len(grp)} blk): improver_Z1={imp_z1_bay[j]}  cpsat_Z1={cz1}  -> {verdict}")
