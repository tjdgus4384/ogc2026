#!/usr/bin/env python3
"""Two composed defects in repair::insertGreedy, measured on the pinned bench.

DEFECT A  the area screen (repair.hpp areaRoom) compares a SUM of per-resident
          max-over-layer polygon areas against the FLAT bay area. Blocks stack
          (crane.hpp: layer l conflicts only with resident layer l), so a legal
          packing can exceed that sum; when it does, phases 1-2 never scan the
          bay.  Knob OGC_NOSCREEN=1 makes areaRoom always true.
DEFECT B  phase 3, the unconditional "last resort", pre-filters with occ::hits,
          which tests candidate layer l against resident layers j >= l over the
          whole stay -- STRICTER than the same-layer rule vpConservative
          enforces exactly, and stricter than the hitsSnap the fast phases use.
          Knob OGC_PH3SNAP=1 swaps in buildSnap/hitsSnap.

Bench = the pinned-schedule realiser (tools/rorder.py "entry" row): take an
annealed board, keep only (bay, entry), pin floor == cap == e, insert one block
at a time in entry order, never abort. The board itself proves the schedule
realisable, so every miss is a false rejection by the insertion kernel.

Arm 0 must reproduce the shipped numbers exactly (P4 94, P5 135, P6 205);
one arm per PROCESS because both knobs are read into function-local statics.

    OGC_PH3SNAP=1 python3 tools/ph3probe.py <build> prob_4 480 7
"""
import hashlib
import json
import os
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent


def cached(pid, tl, seed):
    # NOTE: the board cache key hashes the SHIPPED solver's .so, not the probe
    # build -- the board is an input, produced by solver/, so every build reads
    # the same cached board.
    sd = str(REPO / "solver")
    so = pathlib.Path(sd) / "sscore.cpython-312-x86_64-linux-gnu.so"
    h = hashlib.sha256(f"{sd}|{so.stat().st_mtime_ns}".encode()).hexdigest()[:12]
    p = REPO / "runs" / "boards" / f"{pid}_tl{int(tl)}_s{seed}_{h}.json"
    if not p.exists():
        sys.exit(f"no cached board {p.name}")
    return json.loads(p.read_text())["base"]


def main():
    build, pid, tl = sys.argv[1], sys.argv[2], float(sys.argv[3])
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 7
    sys.path.insert(0, str(REPO / "builds" / build))
    ds = next(d for d in ("preliminary_test", "train_final", "train")
              if (REPO / "data" / d / f"{pid}.json").exists())
    prob = json.load(open(REPO / "data" / ds / f"{pid}.json"))
    n = len(prob["blocks"])
    import myalgorithm as ma
    eng, D, P = ma._fresh(prob, 4)
    hz = eng.horizon()
    base = cached(pid, tl, seed)
    bay = [base[i * 7] for i in range(n)]
    e = [base[i * 7 + 4] for i in range(n)]
    eng.set_solution(base)
    eng.build_occ(hz)
    az1, az2, az3, atot = eng.objective()
    arm = f"PH3SNAP={os.environ.get('OGC_PH3SNAP', '0')}" \
          f" NOSCREEN={os.environ.get('OGC_NOSCREEN', '0')}" \
          f" NORSV={os.environ.get('OGC_NORSV', '0')}"
    print(f"BOARD {pid} s{seed} build={build} {arm}", flush=True)
    print(f"  annealed tot {atot:,.0f}  z1 {az1:,.0f}  n={n}", flush=True)

    order = sorted(range(n), key=lambda i: (e[i], i))

    # ---- FREE reconstruction (realize.py "free" row): no floor, no cap ----
    t0 = time.perf_counter()
    bad = eng.reconstruct_ordered(list(bay), order, False, 7, [], [])
    tfree = time.perf_counter() - t0
    if bad != -1:
        print(f"  FREE   aborted at block {bad}  ({tfree:.1f}s)", flush=True)
    else:
        f1, f2, f3, ftot = eng.objective()
        print(f"  FREE   tot {ftot:,.0f} ({(ftot-atot)/atot*100:+.1f}%)"
              f"  z1 {f1:,.0f}  ({tfree:.1f}s)", flush=True)

    # ---- PINNED one-by-one (rorder.py "entry" row): floor == cap == e ----
    eng.set_entry_floor([max(0, x) for x in e])
    eng.set_solution([0] * (n * 7))
    eng.build_occ(hz)
    eng.reset_counters()
    # Per-failure attribution. When insert_capped refuses block i we ask, on
    # the IDENTICAL state and without placing anything, whether the enumerator
    # (feasible_positions = the same buildSnap/rbCollect/cornerSetsRB/hitsSnap/
    # vpConservative universe as scanBayAt) finds a seat at the pinned day and
    # bay. A seat found there is a false rejection, witnessed exactly.
    t0 = time.perf_counter()
    onplan = 0
    nrsv = ncap = np3 = 0        # how the refusal was reached
    wit_rsv = wit_p3 = wit_cap = 0   # ...and of those, how many had a seat
    twit = 0.0
    for i in order:
        c0 = eng.probe_counters()
        ok = eng.insert_capped(i, e[i], bay[i])
        c1 = eng.probe_counters()
        if ok and eng.get_block(i)[4] == e[i]:
            onplan += 1
            continue
        d = [int(b - a) for a, b in zip(c0, c1)]
        tw = time.perf_counter()
        seat = 0
        if not eng.get_block(i)[6]:
            seat = 1 if eng.feasible_positions(i, 1, max(0, e[i]), bay[i],
                                               e[i]) else 0
        twit += time.perf_counter() - tw
        if d[2]:
            nrsv += 1; wit_rsv += seat
        elif d[1]:
            ncap += 1; wit_cap += seat
        elif d[6]:
            np3 += 1; wit_p3 += seat
    tpin = time.perf_counter() - t0 - twit
    cts = [int(x) for x in eng.probe_counters()]
    print(f"  WHY    refused-by-RESERVE {nrsv} (seat existed for {wit_rsv})"
          f"  by-cap {ncap} (seat {wit_cap})"
          f"  reached-phase3-and-failed {np3} (seat {wit_p3})", flush=True)
    print(f"  CTRS   calls {cts[0]} capRef {cts[1]} rsvRef {cts[2]}"
          f" rsvAdv {cts[3]} p1hit {cts[4]} p2hit {cts[5]}"
          f" p3enter {cts[6]} p3hit {cts[7]} scrRej {cts[8]}"
          f" fail {cts[9]}", flush=True)
    seated = sum(eng.get_block(i)[6] for i in range(n))
    p1, p2, p3, ptot = eng.objective()
    print(f"  PINNED on-plan {onplan}/{n}  seated {seated}"
          f"  tot {ptot:,.0f}  z1 {p1:,.0f}  ({tpin:.1f}s)", flush=True)
    print(f"RESULT {pid} {arm} onplan={onplan} n={n} seated={seated}"
          f" pin_s={tpin:.2f} free_tot={ftot if bad == -1 else -1:.0f}"
          f" free_z1={f1 if bad == -1 else -1:.0f} free_s={tfree:.2f}"
          f" ann_tot={atot:.0f} ann_z1={az1:.0f}", flush=True)


if __name__ == "__main__":
    main()
