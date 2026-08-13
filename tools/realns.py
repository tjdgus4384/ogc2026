#!/usr/bin/env python3
"""Realiser as ruin-and-recreate: the only variant that can UNDO a placement.

Benchmark (tools/realize.py): pin every block to the bay and the exact entry day
of an annealed board -- a schedule that board proves realisable -- and greedy
seats 94/150 (P4), 135/200 (P5). Three variants that do NOT touch already-seated
blocks all failed: insertion order (5 orders within 5-10%), beam width (B=1 126
vs B=8 127 on P4), density mode (indistinguishable). Enumerating positions
instead of first-fit reached 126/150 and stalled.

That is the signature of a joint problem. A block's legal corner positions are
generated from the blocks already resting beside it (repair.hpp rbCollect /
cornerSetsRB), so the target position only exists once its neighbours are right.
Nothing that leaves seated blocks alone can converge to it.

So: ruin and recreate, with SEATING as the objective and the schedule frozen.

This is NOT the shipped ALNS. That one optimises the true objective with bay and
entry FREE; here bay and entry are pinned and the only thing being maximised is
how many blocks have a seat. Feasibility is decided by the kernel throughout --
place_at runs vpConservative, so vp and exact polygon overlap are honoured and
no raster approximation enters.

    python3 tools/realns.py prob_4 480 7 120
"""
import hashlib, json, pathlib, random, sys, time
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
    BUD  = float(sys.argv[4]) if len(sys.argv) > 4 else 120.0
    K    = int(sys.argv[5]) if len(sys.argv) > 5 else 12
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
    eng.set_entry_floor([max(0, x) for x in e])   # floor == cap => day pinned
    rng = random.Random(20260814)

    def seat(i):
        """try to give block i a seat on its planned day, kernel-verified."""
        rows = eng.feasible_positions(i, K, max(0, e[i]), bay[i], e[i])
        for q in range(0, len(rows), 5):
            j, oi, x, y, ee = rows[q:q+5]
            if eng.place_at(i, j, oi, x, y, ee):
                return True
        return False

    def rebuild(sol, todo, order):
        eng.set_solution(list(sol)); eng.build_occ(hz)
        got = 0
        for i in order:
            if todo[i] and seat(i):
                got += 1
        return got

    # baseline: plain sweep in entry order
    order0 = sorted(range(n), key=lambda i: (e[i], i))
    eng.set_solution([0]*(n*7)); eng.build_occ(hz)
    for i in order0:
        seat(i)
    cur = eng.get_solution()
    best = sum(cur[i*7+6] for i in range(n))
    print(f"{pid} s{seed}: annealed {tot:,.0f}   constructive seating"
          f" {best}/{n}   budget {BUD:.0f}s", flush=True)

    t0 = time.time(); it = 0; acc = 0
    while time.time() - t0 < BUD and best < n:
        it += 1
        seated = [i for i in range(n) if cur[i*7+6]]
        miss   = [i for i in range(n) if not cur[i*7+6]]
        if not miss:
            break
        # RUIN: drop a few seated blocks that share time and bay with a
        # stranded one -- the blocks whose positions generate its candidates.
        tgt = rng.choice(miss)
        nb = [i for i in seated if bay[i] == bay[tgt]
              and e[i] < e[tgt] + P[tgt] and e[tgt] < e[i] + P[i]]
        r = min(len(nb), rng.randint(2, 8))
        drop = set(rng.sample(nb, r)) if r else set()
        trial = list(cur)
        for i in drop:
            trial[i*7:i*7+7] = [0]*7
        # RECREATE: the stranded block first, then everything unseated
        todo = [False]*n
        for i in drop:
            todo[i] = True
        for i in miss:
            todo[i] = True
        od = [tgt] + [i for i in (list(drop) + miss) if i != tgt]
        rng.shuffle(od)
        od = [tgt] + [i for i in od if i != tgt]
        rebuild(trial, todo, od)
        got = sum(eng.get_block(i)[6] for i in range(n))
        if got >= best:
            if got > best:
                acc += 1
            best = got
            cur = eng.get_solution()
        if it % 200 == 0:
            print(f"      it {it:>5}  seated {best}/{n}  accepted {acc}"
                  f"  [{time.time()-t0:.0f}s]", flush=True)
    eng.set_solution(list(cur)); eng.build_occ(hz)
    o = eng.objective()
    full = best == n
    chk = None
    if full:
        res = ma._check(prob, ma._recs_to_sol(ma._extract(eng, n),
                                              eng.entry_ranks()))
        chk = res.get("feasible") if res else None
    print(f"   ruin-and-recreate: {best}/{n} seated after {it} iterations"
          + (f"  obj {o[3]:,.0f} ({(o[3]-tot)/tot*100:+.2f}% vs annealed)"
             f"  checker={chk}   <-- REALISES THE SCHEDULE" if full
             else f"  [still {n-best} stranded]")
          + f"  [{time.time()-t0:.0f}s]", flush=True)


if __name__ == "__main__":
    main()
