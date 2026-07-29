"""Structural stats of the real preliminary P1-P6 vs known training families.

Saturation = on-time area demand / bay capacity over the due-date span: the
single number that separated z1-bound boards from z3-open ones in training
(KERNEL_SPEC sec 106/189: > ~1 means tardiness is capacity-forced).
"""
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def stats(path):
    p = json.loads(path.read_text())
    B, bays = p["blocks"], p["bays"]
    n, m = len(B), len(bays)
    W = p["weights"]
    w1, w2, w3 = W["w1"], W["w2"], W["w3"]
    cap = sum(b["width"] * b["height"] for b in bays)
    D = [b["due_date"] for b in B]
    R = [b["release_time"] for b in B]
    P = [b["processing_time"] for b in B]
    A = [b["workload"] for b in B]
    # bbox area of orientation 0 (all layers' vertices)
    def bbox(b):
        vs = [v for layer in b["shape"][0]["layers"] for v in layer]
        xs = [v[0] for v in vs]; ys = [v[1] for v in vs]
        return (max(xs) - min(xs)) * (max(ys) - min(ys))
    area = [bbox(b) for b in B]
    span = max(D) - min(R)
    demand = sum(a * p_ for a, p_ in zip(area, P))          # area-days if on time
    supply = cap * span
    slack = [d - r - p_ for d, r, p_ in zip(D, R, P)]
    pref = [b.get("bay_preferences", []) for b in B]
    npref = sum(1 for q in pref if q)
    return {
        "n": n, "m": m, "cap": cap,
        "w1": w1, "w2": w2, "w3": w3,
        "span": round(span, 1),
        "saturation": round(demand / supply, 3),
        "avgP": round(sum(P) / n, 1), "maxP": max(P),
        "avg_slack": round(sum(slack) / n, 1),
        "neg_slack": sum(1 for s_ in slack if s_ < 0),
        "tight10": sum(1 for s_ in slack if s_ < 10),
        "avg_area": round(sum(area) / n, 1),
        "sumW": round(sum(A), 1),
        "npref": npref,
    }


def main():
    d = ROOT / "data" / (sys.argv[1] if len(sys.argv) > 1 else "preliminary_test")
    for i in range(1, 7):
        f = d / f"prob_{i}.json"
        if f.exists():
            print(f"P{i}", json.dumps(stats(f)))


if __name__ == "__main__":
    main()
