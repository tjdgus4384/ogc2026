#!/usr/bin/env python3
"""With days pinned, the insertion ORDER is a free variable. Which one realises?

realize.py isolated the bottleneck. Pin every block to its planned bay and its
planned entry day -- a schedule the annealed board proves realisable -- and the
shipped greedy strands 8 to 51 blocks. The misses start ~20-25% into the
sequence and they are always the LONGEST-residency blocks (P=22-30). Long
blocks need the same footprint held for many days; the short ones inserted
first fragment exactly that.

But entry order is not required. Once the days are pinned, occupancy is a
function of the final placement alone, so blocks may be inserted in ANY order.
That makes the order a free parameter of the realiser -- and the diagnosis says
the residency-blind one we use is the worst possible choice.

Orders tried (all with days pinned to plan):
  entry      what realize.py measured, the shipped choice
  longP      longest processing first -- most-constraining-first
  area       largest footprint first
  longP-day  by entry day, longest first within the day
  span       by (entry day, -P) reversed: latest-leaving first

Score is blocks seated on their planned day. 100% means the realiser can
rebuild a packing it was shown, which is what three refuted directions needed.

    python3 tools/rorder.py prob_5 600 7
"""
import hashlib, json, pathlib, sys
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
    A   = [b["width"]*b["height"] if "width" in b else 0 for b in prob["blocks"]]
    eng.set_solution(base); eng.build_occ(hz)
    tot = eng.objective()[3]
    print(f"{pid} s{seed}: annealed {tot:,.0f}", flush=True)

    ORDERS = {
        "entry":     sorted(range(n), key=lambda i: (e[i], i)),
        "longP":     sorted(range(n), key=lambda i: (-P[i], e[i], i)),
        "area":      sorted(range(n), key=lambda i: (-A[i], e[i], i)),
        "longP-day": sorted(range(n), key=lambda i: (e[i], -P[i], i)),
        "exit":      sorted(range(n), key=lambda i: (-(e[i]+P[i]), e[i], i)),
    }
    # PIN the day: insert_capped bounds the entry at <= e but the greedy still
    # takes the earliest legal day, so without a floor blocks land BEFORE plan
    # and hog the space later blocks need. set_entry_floor makes floor == cap,
    # which pins exactly. (The first version of this sweep lacked it and was
    # measuring day drift, not order.)
    eng.set_entry_floor([max(0, x) for x in e])
    best = None
    for nm, od in ORDERS.items():
        eng.set_solution([0]*(n*7)); eng.build_occ(hz)
        seated = onplan = 0
        for i in od:
            if eng.insert_capped(i, e[i], bay[i]) and eng.get_block(i)[4] == e[i]:
                onplan += 1
        seated = sum(eng.get_block(i)[6] for i in range(n))
        o = eng.objective()
        full = (onplan == n)
        print(f"   {nm:<10} on-plan {onplan:>3}/{n}  seated {seated:>3}"
              f"  obj {o[3]:>14,.0f}"
              + (f" ({(o[3]-tot)/tot*100:+.1f}%)" if full else "  [INCOMPLETE]")
              + ("   <-- REALISES THE SCHEDULE" if full else ""), flush=True)
        if full and (best is None or o[3] < best[0]):
            best = (o[3], nm)
    if best:
        print(f"   => best complete realisation {best[0]:,.0f} ({best[1]})"
              f"  {(best[0]-tot)/tot*100:+.2f}% vs annealed", flush=True)


if __name__ == "__main__":
    main()
