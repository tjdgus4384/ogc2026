#!/usr/bin/env python3
"""The joint-witness experiment: catch both insertGreedy defects in one shot.

Pin every block to the bay AND the exact entry day of an annealed board -- a
schedule the board itself proves realisable -- then seat them one at a time
with the shipped greedy. With floor == cap == e the window collapses to a
single day (eBot == eTop == e), and insertGreedy reduces to exactly two scans
of one (bay, day):

  phase 1   areaRoom screen, then scanBayAt = buildSnap + rbCollect +
            cornerSetsRB + hitsSnap + vpConservative
  phase 2   vacuous: no resident exit lies strictly inside an empty interval
  phase 3   the same day, same bay, same cornerSetsRB corners -- but the
            candidate test is occ::hits (crane rule j >= l over every day of
            the stay) instead of hitsSnap (same-layer, union over days)

Engine::feasiblePositions(A, k, e, bay, e) scans the IDENTICAL universe as
scanBayAt. So if it returns a seat where insert_capped failed, BOTH defects
fired at once: the area screen must have rejected the bay (else phase 1's
identical scan would have found that seat), AND phase 3's occ::hits must have
rejected a placement that hitsSnap + vpConservative accepts.

That is a deduction, and deductions about a kernel deserve a kernel witness.
builds/wit is builds/ordf plus a counter vector filled in by insertGreedy
itself (`insert_diag`), so the mechanism is MEASURED, not inferred:

  * dg[3]  the reservation guard (knobs::reserve() == 1) pushed eBot past
           eTop and the call returned with NO scan at all -- a third failure
           mode that would otherwise be miscounted as a joint witness
  * dg[4]  the phase-1 area screen rejected the bay          <- DEFECT A
  * dg[10] phase-3 candidates killed by occ::hits            <- DEFECT B
  * dg[11] phase-3 candidates that survived occ::hits

Run one build per process (two sscore extensions cannot share an interpreter):

    python3 tools/witness.py ordf prob_4 480 7
    python3 tools/witness.py wit  prob_4 480 7
"""
import hashlib
import json
import math
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def cached_board(prob_id, tl, seed):
    """The annealed board, keyed the way tools/board.py keys it."""
    solver_dir = str(REPO / "solver")
    so = pathlib.Path(solver_dir) / "sscore.cpython-312-x86_64-linux-gnu.so"
    stamp = f"{so.stat().st_mtime_ns}" if so.exists() else "noso"
    h = hashlib.sha256(f"{solver_dir}|{stamp}".encode()).hexdigest()[:12]
    p = REPO / "runs" / "boards" / f"{prob_id}_tl{int(tl)}_s{seed}_{h}.json"
    if not p.exists():
        sys.exit(f"no cached board {p.name}")
    return json.loads(p.read_text())


def shoelace(poly):
    s2 = 0.0
    for k in range(len(poly)):
        x1, y1 = poly[k]
        x2, y2 = poly[(k + 1) % len(poly)]
        s2 += x1 * y2 - x2 * y1
    return abs(s2) * 0.5


def parea_of(block, oi):
    """crane::OrientGeom::parea -- max polygon area over layers (they stack)."""
    return max(shoelace(lay) for lay in block["shape"][oi]["layers"])


def bbox_area_of(block, oi):
    xs = [v[0] for lay in block["shape"][oi]["layers"] for v in lay]
    ys = [v[1] for lay in block["shape"][oi]["layers"] for v in lay]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


def fill_ratio(prob):
    """Re-derivation of Engine::computeFill's pb.fillRatio, from JSON alone.

    Cross-checked against the kernel's own value (area_screen slot 4) so the
    reimplementation cannot quietly drift.
    """
    B, bays = prob["blocks"], prob["bays"]
    n = len(B)
    R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
    D = [int(round(b["due_date"])) for b in B]
    P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
    sp = sb = 0.0
    amin = []
    for i in range(n):
        best_bb, best_pa = 1e18, 1e18
        a = 1e18
        for oi in range(len(B[i]["shape"])):
            bb = bbox_area_of(B[i], oi)
            pa = parea_of(B[i], oi)
            if bb < best_bb:
                best_bb, best_pa = bb, pa
            a = min(a, pa if pa > 0 else bb)
        amin.append(a)
        if best_bb < 1e17:
            sp += best_pa
            sb += best_bb
    fill = min(1.0, sp / sb) if sb > 0 else 1.0
    cap_area = sum(b["width"] * b["height"] for b in bays)
    tmax = max(D)
    need = [0.0] * (tmax + 2)
    for i in range(n):
        lo, hi = R[i], max(R[i], D[i] - P[i])
        for t in range(lo, min(hi, tmax) + 1):
            need[t] += amin[i]
    day_load = [need[t] / max(1e-9, cap_area) for t in range(tmax + 1)]
    req = max(day_load) if day_load else 0.0
    return min(1.0, max(fill, req)), day_load


def main():
    build, pid, tl = sys.argv[1], sys.argv[2], float(sys.argv[3])
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 7
    bdir = str(REPO / "builds" / build)
    ds = next(d for d in ("preliminary_test", "train_final", "train")
              if (REPO / "data" / d / f"{pid}.json").exists())
    prob = json.load(open(REPO / "data" / ds / f"{pid}.json"))
    B, bays = prob["blocks"], prob["bays"]
    n = len(B)

    sys.path.insert(0, bdir)
    import myalgorithm as ma
    eng, D, P = ma._fresh(prob, 4)
    hz = eng.horizon()
    has_diag = hasattr(eng, "insert_diag")

    rec = cached_board(pid, tl, seed)
    base = rec["base"]
    bay = [base[i * 7] for i in range(n)]
    ori = [base[i * 7 + 1] for i in range(n)]
    e = [base[i * 7 + 4] for i in range(n)]
    f = [base[i * 7 + 5] for i in range(n)]
    pl = [base[i * 7 + 6] for i in range(n)]

    eng.set_solution(base)
    eng.build_occ(hz)
    z1, z2, z3, tot = eng.objective()
    print(f"## {pid} s{seed} build={build}  annealed {tot:,.0f} (z1 {z1:,.0f})"
          f"  {sum(pl)}/{n} placed  checker={rec.get('feasible')}", flush=True)

    fr_py, _ = fill_ratio(prob)

    # ---- replay: days pinned, one block at a time, never aborting ----
    eng.set_solution([0] * (n * 7))
    eng.build_occ(hz)
    eng.set_entry_floor([max(0, x) for x in e])
    # insertGreedy draws from ctx.rng (bay sort, orientation order), so two
    # replays in one process are NOT paired unless the stream is reset. The
    # first guard-off run moved P6 by -7 seats with the guard provably inert
    # -- that was the unpaired stream, not the guard.
    if hasattr(eng, "set_rng_seed"):
        eng.set_rng_seed(seed)
    order = sorted(range(n), key=lambda i: (e[i], i))

    seated_onplan, seated_offplan = 0, 0
    fails, witnesses, offplan_ids = [], [], []
    mech = {"guard": 0, "screenA": 0, "scanned": 0}
    for i in order:
        pre = eng.area_screen(i, bay[i], e[i], e[i] + P[i]) if has_diag else None
        if has_diag:
            dg = eng.insert_diag(i, e[i], bay[i])
            ok = bool(dg[16])
        else:
            dg = None
            ok = eng.insert_capped(i, e[i], bay[i])
        if ok:
            if eng.get_block(i)[4] == e[i]:
                seated_onplan += 1
            else:
                seated_offplan += 1
                offplan_ids.append(i)
            continue
        # FAILURE. Same engine state, identical scan universe.
        fp = eng.feasible_positions(i, 8, e[i], bay[i], e[i])
        row = {"i": i, "bay": bay[i], "e": e[i], "P": P[i],
               "rank": order.index(i), "fp": len(fp) // 5,
               "dg": list(dg) if dg else None,
               "scr": list(pre) if pre else None}
        fails.append(row)
        if fp:
            witnesses.append(row)
        if dg:
            if dg[3]:
                mech["guard"] += 1
            if dg[4]:
                mech["screenA"] += 1
            if dg[5]:
                mech["scanned"] += 1

    print(f"seated {seated_onplan + seated_offplan}/{n}"
          f"  on-plan {seated_onplan}  off-plan {seated_offplan}"
          f"  failed {len(fails)}", flush=True)
    print(f"JOINT WITNESSES (insert_capped failed, feasible_positions found a"
          f" seat on the same state): {len(witnesses)} / {len(fails)} failures",
          flush=True)
    if offplan_ids:
        print(f"  off-plan seats (only possible when e==0, where eCap=0 means"
              f" 'no cap'): {offplan_ids}", flush=True)

    if has_diag:
        print(f"MECHANISM (kernel counters, all {len(fails)} failures):"
              f" reservation-guard killed (no scan at all) {mech['guard']};"
              f" phase-1 AREA SCREEN rejected the bay {mech['screenA']};"
              f" phase-1 actually scanned {mech['scanned']}", flush=True)
        w_guard = sum(1 for r in witnesses if r["dg"][3])
        w_screen = sum(1 for r in witnesses if r["dg"][4])
        w_scan = sum(1 for r in witnesses if r["dg"][5])
        w_h = sum(1 for r in witnesses if r["dg"][9] > 0 and r["dg"][11] == 0)
        w_any = sum(1 for r in witnesses if r["dg"][11] > 0)
        w_p3 = sum(1 for r in witnesses if r["dg"][9] > 0)
        print(f"  of the {len(witnesses)} witnesses: guard {w_guard};"
              f" DEFECT A (screen fired) {w_screen}; phase-1 scanned anyway"
              f" {w_scan}; reached phase 3 {w_p3};"
              f" DEFECT B (every phase-3 candidate killed by occ::hits)"
              f" {w_h}; some candidate survived hits but vp rejected {w_any}",
              flush=True)
        # THE INSTRUMENT'S OWN FALSIFIER. Phase 1's scanBayAt and
        # feasible_positions scan the identical universe, so a witness whose
        # phase 1 actually RAN (dg[5] > 0) is impossible unless that identity
        # claim is wrong. Print the ids, loudly.
        bad = [r["i"] for r in witnesses if r["dg"][5] > 0]
        print(f"  CONTRADICTION CHECK (witness whose phase-1 scan ran and"
              f" still failed, which the identical-universe claim forbids):"
              f" {len(bad)} {bad[:12]}", flush=True)
        # full mechanism census over ALL failures, witness or not
        cen = {}
        for r in fails:
            d = r["dg"]
            key = ("guard" if d[3] else
                   "screen-fired->phase3" if d[4] else
                   "phase1-scanned->phase3")
            key += "/witness" if r["fp"] else "/no-seat"
            cen[key] = cen.get(key, 0) + 1
        print(f"  census over all {len(fails)} failures: "
              + "  ".join(f"{k}={v}" for k, v in sorted(cen.items())),
              flush=True)
        tot_c = sum(r["dg"][9] for r in witnesses)
        tot_k = sum(r["dg"][10] for r in witnesses)
        print(f"  phase-3 candidates over all witnesses: {tot_c} tested,"
              f" {tot_k} killed by occ::hits"
              f" ({100.0 * tot_k / max(1, tot_c):.1f}%)", flush=True)
        if witnesses:
            fr_k = witnesses[0]["scr"][4]
            print(f"  fillRatio: kernel {fr_k:.6f}  python re-derivation"
                  f" {fr_py:.6f}  {'MATCH' if abs(fr_k-fr_py) < 1e-9 else 'MISMATCH'}",
                  flush=True)

    # ---- DEFECT A from the JSON alone: does the stacked parea sum on the
    # witness's own (bay, days) exceed the flat-area cap the screen uses? ----
    # residents = the annealed board's own occupants of that bay on that day.
    over_flat = over_slk = over_kernel = under = 0
    detail = []
    for r in witnesses:
        i, j = r["i"], r["bay"]
        W, H = bays[j]["width"], bays[j]["height"]
        a0 = parea_of(B[i], 0)          # the screen uses ORIENT 0's parea
        mx = 0.0
        for t in range(e[i], e[i] + P[i]):
            s = sum(parea_of(B[k], ori[k]) for k in range(n)
                    if pl[k] and bay[k] == j and e[k] <= t < f[k] and k != i)
            mx = max(mx, s)
        flat = W * H
        cap_slk = flat * 1.02                       # knobs::areaSlk()
        cap_ker = flat * fr_py * 1.02               # densMode -1 -> dm=1
        if mx + a0 > cap_ker:
            over_kernel += 1
        if mx + a0 > cap_slk:
            over_slk += 1
        if mx + a0 > flat:
            over_flat += 1
        else:
            under += 1
        detail.append((i, j, round(mx, 1), round(a0, 1), round(flat, 1),
                       round(cap_ker, 1)))
    print(f"DEFECT A, from the annealed JSON alone (stacked parea sum of the"
          f" board's own residents + orient-0 parea of the witness, max over"
          f" the stay), {len(witnesses)} witnesses:", flush=True)
    print(f"  exceeds bayW*bayH                    : {over_flat}", flush=True)
    print(f"  exceeds bayW*bayH*areaSlk(1.02)      : {over_slk}", flush=True)
    print(f"  exceeds bayW*bayH*fillRatio*areaSlk  : {over_kernel}"
          f"   (the cap the screen ACTUALLY uses, densMode=-1 -> dm=1)",
          flush=True)
    if under:
        print(f"  !! {under} witness(es) sit BELOW the flat bay area -- the"
              f" flat-overflow story does not explain those", flush=True)
    if witnesses:
        ratios = sorted((d[2] + d[3]) / d[4] for d in detail)
        print(f"  (resident_parea_sum + orient0_parea) / (bayW*bayH):"
              f" min {ratios[0]:.3f}  median {ratios[len(ratios)//2]:.3f}"
              f"  max {ratios[-1]:.3f}   -- fillRatio is {fr_py:.3f},"
              f" so the cap sits at {fr_py * 1.02:.3f} of flat area",
              flush=True)
    print(f"  sample (i, bay, resident_parea_sum, orient0_parea, W*H, kernel"
          f" cap): {detail[:10]}", flush=True)

    if has_diag and witnesses:
        # kernel's own screen arithmetic at the failing moment (replay state,
        # not the annealed board): areaA, cap, max areaUsed over the stay
        k_over = sum(1 for r in witnesses if r["scr"][2] + r["scr"][0] > r["scr"][1])
        print(f"  kernel area_screen at the moment of failure (replay"
              f" occupancy): {k_over}/{len(witnesses)} have"
              f" areaUsed+areaA > cap, i.e. the screen provably fired",
              flush=True)

    # ---- outcome test: same replay with the reservation guard switched off.
    # If the guard really is what strands the witnesses, the seat count must
    # rise by about the witness count -- a mechanism claim turned into an
    # outcome claim.
    seat_norsv = None
    if hasattr(eng, "set_no_reserve"):
        eng.set_solution([0] * (n * 7))
        eng.build_occ(hz)
        eng.set_entry_floor([max(0, x) for x in e])
        eng.set_rng_seed(seed)          # paired with the replay above
        eng.set_no_reserve(True)
        on2 = off2 = 0
        for i in order:
            if eng.insert_capped(i, e[i], bay[i]):
                if eng.get_block(i)[4] == e[i]:
                    on2 += 1
                else:
                    off2 += 1
        eng.set_no_reserve(False)
        seat_norsv = on2 + off2
        obj = eng.objective()
        print(f"GUARD OFF (same replay, knobs::reserve bypassed):"
              f" seated {seat_norsv}/{n} (on-plan {on2}, off-plan {off2})"
              f"  vs {seated_onplan + seated_offplan} with the guard"
              f"  -> {seat_norsv - seated_onplan - seated_offplan:+d} seats,"
              f" witnesses were {len(witnesses)}", flush=True)

    print(f"RAW {json.dumps({'prob': pid, 'build': build, 'n': n, 'seated': seated_onplan + seated_offplan, 'onplan': seated_onplan, 'offplan': seated_offplan, 'fails': len(fails), 'witnesses': len(witnesses), 'mech': mech, 'seated_noreserve': seat_norsv})}",
          flush=True)


if __name__ == "__main__":
    main()
