"""scaling.py -- does the improver keep gaining with more wall-clock, or plateau?
Runs improve.improve at several time budgets on a representative set and prints the objective curve.
Same seed each call => longer budget = strictly more iterations from the identical start."""
import sys, json, time, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "solution" / "baseline" / "baseline"))
sys.path.insert(0, str(ROOT / "solution" / "dev"))
import improve, utils

BUDGETS = [15, 45, 120]
INSTS = ["prob_8", "prob_20", "prob_27", "prob_38", "prob_40"]

hdr = f"{'inst':8s} " + " ".join(f"{str(b)+'s':>14s}" for b in BUDGETS)
print(hdr)
for nm in INSTS:
    prob = json.load(open(ROOT / "data" / "train" / f"{nm}.json"))
    row = []
    for b in BUDGETS:
        sol = improve.improve(prob, b)
        r = utils.check_feasibility(prob, sol)
        row.append(r["objective"] if r["feasible"] else None)
    cells = " ".join(f"{(v if v is not None else -1):14.0f}" for v in row)
    drop = (1 - row[-1] / row[0]) * 100 if row[0] else 0
    print(f"{nm:8s} {cells}   ({BUDGETS[0]}->{BUDGETS[-1]}s: -{drop:.1f}%)")
