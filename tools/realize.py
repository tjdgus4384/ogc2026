#!/usr/bin/env python3
"""THE bottleneck, isolated: can we rebuild a packing we KNOW exists?

Three refuted directions all died in the same place -- encoding-level
crossover (children +104% above annealed parents), relax-then-realise (F5:
29-48 of 200 blocks realised on P3), and assignment optimisation (19 of 200
blocks have no seat at the proven optimum). Each found a good (bay, entry)
and could not turn it into geometry. So one faithful realiser reopens all
three at once, and that makes the realiser the architectural keystone.

It is also the rare question with GROUND TRUTH. Take an annealed board, keep
only its (bay, entry) -- the board itself proves that schedule is realisable --
and rebuild the geometry from empty. A perfect realiser returns the same
objective. The shipped greedy returns +102% to +171%.

This harness separates the two failure modes that were tangled together:

  DRIFT    greedy takes the earliest legal day, so blocks land before plan,
           occupancy diverges and later blocks are pushed out
  SPATIAL  even on the exact planned day, no position is found

builds/ordf wires Ctx::entryFloor and Ctx::entryCap through
reconstruct_ordered, so setting floor = cap = e PINS every block to its planned
day and leaves a pure spatial question. The counts below are the measurement
the architecture decision needs:

  free      floor=[], cap=[]        the shipped realiser
  floor     floor=e                 no earlier than plan
  pinned    floor=e, cap=e          exactly on plan -- pure spatial failure

If `pinned` seats nearly every block, greedy's weakness is day drift and a
cheap fix exists. If it strands many, position choice itself is the wall and a
real search (beam / backtracking) is required.

    python3 tools/realize.py prob_5 600 7
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
        sys.exit(f"no cached board {p.name}")
    return json.loads(p.read_text())["base"]


def main():
    pid, tl = sys.argv[1], float(sys.argv[2])
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    ds = next(d for d in ("preliminary_test", "train_final", "train")
              if (REPO / "data" / d / f"{pid}.json").exists())
    prob = json.load(open(REPO / "data" / ds / f"{pid}.json"))
    n = len(prob["blocks"])
    sys.path.insert(0, ORDF)
    import myalgorithm as ma
    eng, D, P = ma._fresh(prob, 4)
    hz = eng.horizon()
    base = cached(pid, tl, seed, str(REPO / "solver"))

    bay = [base[i * 7] for i in range(n)]
    e = [base[i * 7 + 4] for i in range(n)]
    placed0 = sum(base[i * 7 + 6] for i in range(n))
    eng.set_solution(base)
    eng.build_occ(hz)
    z1, z2, z3, tot = eng.objective()
    print(f"{pid} s{seed}: annealed {tot:,.0f}  (z1 {z1:,.0f})"
          f"  {placed0}/{n} placed", flush=True)

    order = sorted(range(n), key=lambda i: (e[i], i))

    def run(tag, floor, cap):
        args = [list(bay), order, False, 7]
        bad = eng.reconstruct_ordered(*args, floor or [], cap or [])
        seated = sum(eng.get_block(i)[6] for i in range(n))
        if bad != -1:
            # reconstruct_ordered restores the backup on failure, so count how
            # far it got by replaying without the aborting block
            print(f"   {tag:<8} FAILED at block {bad}"
                  f" (rank {order.index(bad)}/{n})", flush=True)
            return None
        o = eng.objective()
        onday = sum(1 for i in range(n)
                    if eng.get_block(i)[6] and eng.get_block(i)[4] == e[i])
        early = sum(1 for i in range(n)
                    if eng.get_block(i)[6] and eng.get_block(i)[4] < e[i])
        late = sum(1 for i in range(n)
                   if eng.get_block(i)[6] and eng.get_block(i)[4] > e[i])
        print(f"   {tag:<8} {o[3]:>14,.0f} ({(o[3]-tot)/tot*100:+7.1f}%)"
              f"  seated {seated}/{n}  on-plan {onday}"
              f"  early {early}  late {late}", flush=True)
        return o[3]

    run("free", None, None)
    run("floor", [max(0, x) for x in e], None)
    run("pinned", [max(0, x) for x in e], [max(0, x) for x in e])

    # pure spatial diagnosis: pin the days and place blocks ONE AT A TIME,
    # never aborting, so we count exactly how many have no seat on their day.
    eng.set_solution([0] * (n * 7))
    eng.build_occ(hz)
    miss = []
    for i in order:
        if not eng.insert_capped(i, e[i], bay[i]):
            miss.append(i)
            continue
        if eng.get_block(i)[4] != e[i]:
            miss.append(i)
    seated = sum(eng.get_block(i)[6] for i in range(n))
    o = eng.objective()
    print(f"   one-by-one, days pinned: {seated}/{n} seated,"
          f" {len(miss)} off-plan or unseated  obj {o[3]:,.0f}"
          f" ({(o[3]-tot)/tot*100:+.1f}%)", flush=True)
    if miss:
        big = sorted(miss, key=lambda i: -P[i])[:8]
        print(f"      first misses by entry rank:"
              f" {[order.index(i) for i in miss[:8]]}", flush=True)
        print(f"      longest missed: P={[P[i] for i in big]}"
              f" bays={[bay[i] for i in big]}", flush=True)


if __name__ == "__main__":
    main()
