#!/usr/bin/env python3
"""Does construction-only best-fit change the board at all? Deterministically.

Before spending a 36-pair gate, ask the cheap question the campaign keeps
skipping: does the mechanism DO anything, and does it do the thing it was
built for? `reconstruct_ordered(lockv, order, tight, k)` is a pure function of
the order -- its internal seed is fixed -- so first-fit and best-fit can be
compared with no lottery at all. Same build, same order, one env var apart.

Three questions, in the order that can kill the candidate fastest:

  1  does the board change?          identical objective on every order means
                                     the contact ranking never separates the
                                     candidates and bf1 is a no-op;
  2  does Z1 move, and which way?    the mechanism exists to cut tardiness;
  3  does fragmentation drop?        the DIAGNOSIS was 78-83% of tardy days
                                     from fragmentation, so if bf1 packs
                                     tighter without cutting Z1, the diagnosis
                                     is what is wrong, not the rule.

Fragmentation proxy = summed free-space perimeter... which we cannot get from
the kernel, so use what blocked.py used: peak bbox occupancy per bay-day.
Tighter packing raises polygon-occupancy per occupied bbox area.

    python3 tools/bfprobe.py prob_4 [n_orders]
"""
import json
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
BUILD = "builds/bf1"

CHILD = r'''
import json, os, sys, pathlib
sys.path.insert(0, sys.argv[1])
import myalgorithm as ma
prob = json.load(open(sys.argv[2]))
K = int(sys.argv[3])
n = len(prob["blocks"])
eng, _ = ma._fresh(prob, 4)
D = [int(round(b["due_date"])) for b in prob["blocks"]]
P = [max(1, int(-(-b["processing_time"] // 1))) for b in prob["blocks"]]
out = []
for k in range(K):
    if k == 0:
        order = sorted(range(n), key=lambda i: (D[i], -P[i]))
    elif k == 1:
        order = sorted(range(n), key=lambda i: (D[i] - P[i], -P[i]))
    elif k == 2:
        order = sorted(range(n), key=lambda i: (-P[i], D[i]))
    else:
        order = sorted(range(n), key=lambda i: ((D[i] * (k + 1)) % 9973, i))
    bad = eng.reconstruct_ordered([-1] * n, order, False, 7)
    if bad >= 0:
        out.append(None)
        continue
    o = eng.objective()
    placed = sum(1 for i in range(n) if eng.get_block(i)[6])
    out.append({"obj": o[3], "z1": o[0], "z2": o[1], "z3": o[2],
                "placed": placed})
print("@@" + json.dumps(out))
'''


def run(prob_path, k, bestfit):
    env = dict(os.environ, OGC_BESTFIT="1" if bestfit else "0")
    env.pop("OGC_DIAG", None)
    r = subprocess.run([sys.executable, "-c", CHILD, str(REPO / BUILD),
                        str(prob_path), str(k)],
                       capture_output=True, text=True, env=env, timeout=1800)
    for line in r.stdout.splitlines():
        if line.startswith("@@"):
            return json.loads(line[2:])
    sys.exit(f"child failed:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")


def main():
    prob_id = sys.argv[1]
    K = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    for ds in ("preliminary_test", "train_final", "train"):
        p = REPO / "data" / ds / f"{prob_id}.json"
        if p.exists():
            break
    else:
        sys.exit(f"no such instance: {prob_id}")
    a = run(p, K, False)
    b = run(p, K, True)
    print(f"{prob_id} ({p.parent.name})  {K} construction orders, "
          f"reconstruct_ordered (deterministic)")
    print(f"  {'order':>5}  {'first-fit obj':>15}  {'best-fit obj':>15}  "
          f"{'delta':>8}   {'Z1 ff':>8} {'Z1 bf':>8}  placed")
    same = 0
    wins = losses = 0
    for k, (x, y) in enumerate(zip(a, b)):
        if x is None or y is None:
            print(f"  {k:>5}  reconstruction failed "
                  f"(ff={x is None}, bf={y is None})")
            continue
        d = (y["obj"] - x["obj"]) / x["obj"] * 100
        if y["obj"] == x["obj"]:
            same += 1
        elif y["obj"] < x["obj"]:
            wins += 1
        else:
            losses += 1
        flag = "" if x["placed"] == y["placed"] else "  !! placed differs"
        print(f"  {k:>5}  {x['obj']:>15,.0f}  {y['obj']:>15,.0f}  "
              f"{d:>+7.2f}%   {x['z1']:>8.0f} {y['z1']:>8.0f}  "
              f"{x['placed']}/{y['placed']}{flag}")
    print(f"  -> identical {same}/{K}, best-fit better {wins}, worse {losses}")
    if same == K:
        print("  VERDICT: no-op on this board -- the contact ranking never "
              "separates candidates. Do not gate.")


if __name__ == "__main__":
    main()
