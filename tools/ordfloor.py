#!/usr/bin/env python3
"""Does an ENTRY FLOOR make re-realisation faithful? (needs builds/ordf)

ordx proved the (bay, order) encoding always realises -- 537 of 537 -- because
it never names a coordinate. But every realisation lands +104% to +432% above
the annealed parent, and that loss is what makes the operator useless: the
anneal's own descent is bigger than anything the crossover wins.

The loss has a candidate cause. insertGreedy takes the EARLIEST legal day, so
re-realising an annealed schedule lets slack-rich blocks slide forward, hog
prime early space and push the tight ones out -- the exact pathology v6.4's
reservation was built for, here applied to every block at once. If that is the
cause, forbidding a block from entering earlier than its encoded day should
recover most of the gap.

builds/ordf adds Ctx::entryFloor (the mirror of entryCap) and exposes it as
reconstruct_ordered(..., floor=[]). Coordinates are still searched fresh, so
the encoding stays collision-free -- this only stops the forward slide.

Read it as: floor=[] is ordx's number, floor=own is the faithfulness ceiling.
If the gap does not close, the loss is not the greedy's myopia and the whole
(bay, order) route is dead at the seed level.

    python3 tools/ordfloor.py prob_5 600 7 11
"""
import hashlib
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
ORDF = str(REPO / "builds" / "ordf")


def cached(prob_id, tl, seed, solver_dir):
    so = pathlib.Path(solver_dir) / "sscore.cpython-312-x86_64-linux-gnu.so"
    stamp = f"{so.stat().st_mtime_ns}" if so.exists() else "noso"
    h = hashlib.sha256(f"{solver_dir}|{stamp}".encode()).hexdigest()[:12]
    p = REPO / "runs" / "boards" / f"{prob_id}_tl{int(tl)}_s{seed}_{h}.json"
    if not p.exists():
        sys.exit(f"no cached board {p.name} -- warm it with the shipped solver")
    return json.loads(p.read_text())["base"]


def main():
    prob_id, tl = sys.argv[1], float(sys.argv[2])
    sa, sb = int(sys.argv[3]), int(sys.argv[4])
    ds = next(d for d in ("preliminary_test", "train_final", "train")
              if (REPO / "data" / d / f"{prob_id}.json").exists())
    prob = json.load(open(REPO / "data" / ds / f"{prob_id}.json"))
    n = len(prob["blocks"])
    sys.path.insert(0, ORDF)
    import myalgorithm as ma
    eng, D, P = ma._fresh(prob, 4)
    hz = eng.horizon()
    SHIP = str(REPO / "solver")
    A, B = cached(prob_id, tl, sa, SHIP), cached(prob_id, tl, sb, SHIP)

    def enc(f):
        bay = [f[i * 7] if f[i * 7 + 6] else -1 for i in range(n)]
        e = [f[i * 7 + 4] if f[i * 7 + 6] else 10**9 for i in range(n)]
        return bay, e

    def obj(f):
        eng.set_solution(f)
        eng.build_occ(hz)
        return eng.objective()[3]

    def realise(bay, e, floor):
        order = sorted(range(n), key=lambda i: (e[i], i))
        fl = [max(0, e[i]) for i in range(n)] if floor else []
        bad = eng.reconstruct_ordered(list(bay), order, False, 7, fl)
        if bad != -1:
            return None
        if sum(eng.get_block(i)[6] for i in range(n)) < n:
            return None
        return eng.objective()[3]

    oa, ob = obj(A), obj(B)
    ba, ea = enc(A)
    bb, eb = enc(B)
    print(f"{prob_id} s{sa}/s{sb}: annealed {oa:,.0f} / {ob:,.0f}")
    for nm, bay, e, o in (("A", ba, ea, oa), ("B", bb, eb, ob)):
        r0 = realise(bay, e, False)
        r1 = realise(bay, e, True)
        f0 = f"{r0:,.0f} ({(r0-o)/o*100:+.1f}%)" if r0 else "FAILED"
        f1 = f"{r1:,.0f} ({(r1-o)/o*100:+.1f}%)" if r1 else "FAILED"
        print(f"   {nm}: floor=off {f0}    floor=own {f1}")

    best = None
    tried = ok = 0
    T = max(x for x in ea if x < 10**9)
    plans = [(f"time<{t}", [i for i in range(n) if ea[i] < t])
             for t in range(0, T + 1, max(1, T // 30))]
    plans += [(f"bayA={j}", [i for i in range(n) if ba[i] == j])
              for j in range(len(prob["bays"]))]
    for nm, m in plans:
        S = set(m)
        bx = [ba[i] if i in S else bb[i] for i in range(n)]
        ex = [ea[i] if i in S else eb[i] for i in range(n)]
        tried += 1
        r = realise(bx, ex, True)
        if r is None:
            continue
        ok += 1
        if best is None or r < best[0]:
            best = (r, nm)
    print(f"   {tried} crossovers with floor, {ok} realised")
    if best:
        d = (best[0] - min(oa, ob)) / min(oa, ob) * 100
        print(f"   => best child {best[0]:,.0f} ({best[1]})  vs best ANNEALED"
              f" parent {d:+.2f}%"
              + ("   <-- BEATS AN ANNEALED PARENT" if d < 0 else ""))


if __name__ == "__main__":
    main()
