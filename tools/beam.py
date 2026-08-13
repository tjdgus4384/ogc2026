#!/usr/bin/env python3
"""Beam search over PLACEMENTS -- the stronger construction the diagnosis asks for.

realize.py + rorder.py located the architectural bottleneck precisely. Pin every
block to the bay and the exact entry day of an annealed board -- a schedule that
board proves realisable -- and greedy insertion seats only

    P3 192/200   P4 99/150   P5 141/200   P6 207/250

and the insertion ORDER barely moves it (five orders within 5-10% of each
other). So the failure is not which block goes next; it is WHERE inside the bay
each one lands. Greedy commits to the first vp-feasible position and never
reconsiders, and by ~25% of the way in the space is fragmented past use.

That is exactly what a beam fixes: keep B partial packings instead of one, and
let each block's position choice be revisited by the states that survive.
B=1 reproduces greedy exactly, so the sweep is a strict generalisation.

Primitives (builds/ordf): feasible_positions(A, k, eMin, bayOnly, eMax)
enumerates vp-feasible placements filtered to the planned bay and day; place_at
seats one exactly. Ground truth: 100% seated means the realiser can rebuild a
packing it was shown, which is what unlocks the three refuted directions.

    python3 tools/beam.py prob_5 600 7 8 6
"""
import hashlib, json, pathlib, sys, time
REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "builds" / "ordf"))


def cached(pid, tl, seed):
    sd = str(REPO / "solver")
    so = pathlib.Path(sd) / "sscore.cpython-312-x86_64-linux-gnu.so"
    h = hashlib.sha256(f"{sd}|{so.stat().st_mtime_ns}".encode()).hexdigest()[:12]
    p = REPO / "runs" / "boards" / f"{pid}_tl{int(tl)}_s{seed}_{h}.json"
    if not p.exists():
        sys.exit(f"no cached board {p.name}")
    return json.loads(p.read_text())["base"]


def main():
    pid, tl = sys.argv[1], float(sys.argv[2])
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    B    = int(sys.argv[4]) if len(sys.argv) > 4 else 8
    K    = int(sys.argv[5]) if len(sys.argv) > 5 else 6
    ds = next(d for d in ("preliminary_test","train_final","train")
              if (REPO/"data"/d/f"{pid}.json").exists())
    prob = json.load(open(REPO/"data"/ds/f"{pid}.json"))
    n = len(prob["blocks"])
    import myalgorithm as ma
    eng, D, P = ma._fresh(prob, 4)
    hz = eng.horizon()
    base = cached(pid, tl, seed)
    bay = [base[i*7] for i in range(n)]
    e   = [base[i*7+4] for i in range(n)]
    eng.set_solution(base); eng.build_occ(hz)
    tot = eng.objective()[3]
    eng.set_entry_floor([max(0, x) for x in e])
    order = sorted(range(n), key=lambda i: (e[i], i))

    t0 = time.time()
    # a beam state is (blocks_seated, flat_solution)
    beam = [(0, [0]*(n*7))]
    for step, i in enumerate(order):
        nxt = []
        for seated, flat in beam:
            eng.set_solution(list(flat)); eng.build_occ(hz)
            rows = eng.feasible_positions(i, K, max(0, e[i]), bay[i], e[i])
            if not rows:
                nxt.append((seated, flat))          # stranded, carry on
                continue
            for q in range(0, len(rows), 5):
                j, oi, x, y, ee = rows[q:q+5]
                eng.set_solution(list(flat)); eng.build_occ(hz)
                if eng.place_at(i, j, oi, x, y, ee):
                    nxt.append((seated+1, eng.get_solution()))
        if not nxt:
            break
        # keep the B best: most seated first, then lowest objective
        scored = []
        seen = set()
        for seated, flat in nxt:
            key = (seated, tuple(flat[::7]), tuple(flat[2::7]))
            if key in seen:
                continue
            seen.add(key)
            eng.set_solution(list(flat)); eng.build_occ(hz)
            scored.append((-seated, eng.objective()[3], flat))
        scored.sort(key=lambda t: (t[0], t[1]))
        beam = [(-a, f) for a, _, f in scored[:B]]
        if step % 40 == 0:
            print(f"      step {step:>3}/{n}  best seated {beam[0][0]}"
                  f"  beam {len(beam)}  [{time.time()-t0:.0f}s]", flush=True)
    best = max(beam, key=lambda t: t[0])
    eng.set_solution(list(best[1])); eng.build_occ(hz)
    o = eng.objective()
    full = best[0] == n
    print(f"{pid} s{seed} B={B} K={K}: seated {best[0]}/{n}"
          f"  obj {o[3]:,.0f}"
          + (f" ({(o[3]-tot)/tot*100:+.2f}% vs annealed {tot:,.0f})" if full
             else f"  [INCOMPLETE, annealed {tot:,.0f}]")
          + f"  [{time.time()-t0:.0f}s]"
          + ("   <-- REALISES THE SCHEDULE" if full else ""), flush=True)


if __name__ == "__main__":
    main()
