#!/usr/bin/env python3
"""Are the corner-universe dead-ends VOCABULARY holes or JOINT entanglement?

The instrumented refutation round left exactly one live completeness hole: on
P6 (the class carrying 73% of the finals total) all 45 pinned-schedule
failures scanned phase 1 and phase 3 and found nothing, and feasible_positions
confirmed no seat exists in the corner universe. Two explanations remain:

  VOCABULARY  the seat exists but is a polygon-contact position -- the kind
              slideLeftX generates and NO insertion phase can produce
              (occ.hpp:120: "the corner scan never produces"; sole caller is
              spatialCompact, alns.hpp:762)
  ENTANGLED   the seat does not exist on the partial state at all -- the
              annealed board's neighbours sit elsewhere, so the block's seat
              only exists after joint repositioning

The discriminator is nearly free: replay the one-by-one pinned pass, and at
every corner-universe failure try place_at with the ANNEALED BOARD'S OWN
(ori, x, y) for that block. place_at runs the exact vpConservative, so
  success  => the seat exists on this partial state and is simply non-corner
              => VOCABULARY hole, countable
  failure  => the seat needs neighbours moved first => ENTANGLED

Guard note (instrument defect 12): insert_capped would activate the
reservation guard under pinning, so seating uses feasible_positions+place_at,
the guard-free path, exactly as realns.py does.

    python3 tools/deadend.py prob_6 900 7
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
    K = 12
    ds = next(d for d in ("preliminary_test","train_final","train")
              if (REPO/"data"/d/f"{pid}.json").exists())
    prob = json.load(open(REPO/"data"/ds/f"{pid}.json"))
    n = len(prob["blocks"])
    import myalgorithm as ma
    eng, D, P = ma._fresh(prob, 4)
    hz = eng.horizon()
    base = cached(pid, tl, seed)
    bay = [base[i*7] for i in range(n)]
    ori = [base[i*7+1] for i in range(n)]
    X   = [base[i*7+2] for i in range(n)]
    Y   = [base[i*7+3] for i in range(n)]
    e   = [base[i*7+4] for i in range(n)]
    eng.set_entry_floor([max(0, x) for x in e])
    order = sorted(range(n), key=lambda i: (e[i], i))

    eng.set_solution([0]*(n*7)); eng.build_occ(hz)
    corner = vocab = tangled = 0
    vocab_ids = []
    for i in order:
        rows = eng.feasible_positions(i, K, max(0, e[i]), bay[i], e[i])
        placed = False
        for q in range(0, len(rows), 5):
            j, oi, x, y, ee = rows[q:q+5]
            if eng.place_at(i, j, oi, x, y, ee):
                placed = True
                corner += 1
                break
        if placed:
            continue
        # corner universe empty: does the annealed board's own seat fit HERE?
        if eng.place_at(i, bay[i], ori[i], X[i], Y[i], e[i]):
            vocab += 1
            vocab_ids.append(i)
        else:
            tangled += 1
    tot_fail = vocab + tangled
    print(f"{pid} s{seed}: corner-seated {corner}/{n}  dead-ends {tot_fail}"
          f"  -> VOCABULARY {vocab}  ENTANGLED {tangled}"
          + (f"   ({100*vocab/max(1,tot_fail):.0f}% of dead-ends are non-corner"
             f" seats insertion cannot produce)" if tot_fail else ""),
          flush=True)
    if vocab_ids:
        print(f"   vocabulary blocks (first 10): {vocab_ids[:10]}", flush=True)
    # sanity note: seating the annealed coordinate keeps later blocks aligned
    # with the plan, so this pass can seat MORE than the pure corner pass.


if __name__ == "__main__":
    main()
