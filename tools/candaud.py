#!/usr/bin/env python3
"""CANDFIX stage 0: audit the CP arm's candidate enumerator on its own windows.

The slice CP arm is what measurably carries the P6-like class (rate_p 2983-3253
where alive), and its vocabulary is Engine::feasiblePositions(i, NCAND=30, 0)
-- a function whose own comment calls it a probe primitive. Suspected defects
(architecture review, 08-14): the k-quota is consumed by low-index bays before
later bays are visited, and `if (any) break` caps each bay at its EARLIEST
feasible day only. Suspected window defect: the 25-block window is 1 tardy seed
+ 24 blocks sampled UNIFORMLY, so the seed's actual blockers are almost never
freed together with it.

This replays the arm's own loop (myalgorithm.py:640-656) verbatim on cached
annealed boards: 200 windows per board, free 25, enumerate, and measure
  per freed block: candidates by bay index, distinct entry days per bay
  per window:      how many same-bay time-overlapping residents of the tardy
                   seed are inside the freed set

PRE-REGISTERED READ:
  KILL the premise  candidates spread across bays AND >1 day per bay
  CONFIRM           monotone bay-index collapse and ~1 day per bay, windows
                    hold ~10% of the seed's relevant residents

    python3 tools/candaud.py prob_6 900 7 200
"""
import hashlib, json, pathlib, random, sys
from collections import Counter
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
    NW = int(sys.argv[4]) if len(sys.argv) > 4 else 200
    K, NCAND = 25, 30
    ds = next(d for d in ("preliminary_test","train_final","train")
              if (REPO/"data"/d/f"{pid}.json").exists())
    prob = json.load(open(REPO/"data"/ds/f"{pid}.json"))
    n = len(prob["blocks"])
    m = len(prob["bays"])
    import myalgorithm as ma
    eng, D, P = ma._fresh(prob, 4)
    hz = eng.horizon()
    cur = cached(pid, tl, seed)
    rng = random.Random(4242)

    tardy = [i for i in range(n) if cur[i*7+6] and cur[i*7+5] > D[i]]
    if not tardy:
        print(f"{pid}: no tardy blocks -- arm inactive here"); return
    baycnt = Counter()          # candidate rows by bay index
    daysper = Counter()         # distinct entry days per (block, bay)
    onebay = 0; blocks = 0      # blocks whose entire set is one bay
    oneday = 0
    relin = reltot = 0
    for w in range(NW):
        seed_i = rng.choice(tardy)
        free = [seed_i]
        pool = [i for i in range(n) if cur[i*7+6] and i != seed_i]
        free += rng.sample(pool, min(K-1, len(pool)))
        part = list(cur)
        for i in free:
            part[i*7+6] = 0
        eng.set_solution(part); eng.build_occ(hz)
        # window audit: seed's same-bay time-overlapping residents freed?
        sb = [j for j in range(n) if j != seed_i and cur[j*7+6]
              and cur[j*7] == cur[seed_i*7]
              and cur[j*7+4] < cur[seed_i*7+5]
              and cur[seed_i*7+4] < cur[j*7+5]]
        relin += len(set(sb) & set(free)); reltot += len(sb)
        for i in free:
            raw = eng.feasible_positions(i, NCAND, 0)
            rows = [raw[q:q+5] for q in range(0, len(raw), 5)]
            if not rows:
                continue
            blocks += 1
            bys = Counter(r[0] for r in rows)
            for b, c in bys.items():
                baycnt[b] += c
            if len(bys) == 1:
                onebay += 1
            alld = {b: len({r[4] for r in rows if r[0] == b}) for b in bys}
            for b, dcnt in alld.items():
                daysper[dcnt] += 1
            if all(v == 1 for v in alld.values()):
                oneday += 1
    tot = sum(baycnt.values())
    print(f"{pid} s{seed}: {NW} windows, {blocks} block-enumerations,"
          f" tardy pool {len(tardy)}", flush=True)
    print("   candidates by bay index: "
          + "  ".join(f"bay{b} {100*baycnt[b]/max(1,tot):.0f}%"
                      for b in range(m)), flush=True)
    print(f"   blocks with a SINGLE-bay candidate set: {onebay}/{blocks}"
          f" ({100*onebay/max(1,blocks):.0f}%)", flush=True)
    print(f"   distinct-days-per-bay distribution: "
          + "  ".join(f"{d}d x{c}" for d, c in sorted(daysper.items())[:6]),
          flush=True)
    print(f"   blocks with EXACTLY ONE day in every bay: {oneday}/{blocks}"
          f" ({100*oneday/max(1,blocks):.0f}%)", flush=True)
    print(f"   window audit: seed's same-bay overlapping residents freed"
          f" {relin}/{reltot} ({100*relin/max(1,reltot):.1f}%)"
          f"  avg residents {reltot/NW:.1f}", flush=True)


if __name__ == "__main__":
    main()
