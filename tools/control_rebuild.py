#!/usr/bin/env python3
"""The missing control: can the realizer rebuild a schedule KNOWN to be feasible?

Every realization verdict so far (realize2 greedy 599, realize3 area-capped
525, cohort exact 1065 -- all against a promise of 49) assumed the rebuild
procedure itself is sound: insert from empty in day order, forced bay, entry
capped at the scheduled day. That assumption was never tested, and there is a
schedule for which the ground truth is known absolutely -- OUR OWN BOARD's.
Its geometry witness exists (the board itself), so a sound realizer must
rebuild it with every block on schedule.

    control succeeds   the realizers are sound, the rescheduled day profiles
                       are genuinely geometry-infeasible, and the scheduling
                       axis closes on clean evidence;
    control fails      every realization verdict is an artifact of first-fit
                       corner choice, and schedule feasibility can only be
                       judged with witness-carrying methods.

    python3 tools/control_rebuild.py SOLVERDIR prob_4 TL [seed]
"""
import json, os, pathlib, sys
REPO = pathlib.Path(__file__).resolve().parent.parent

def main():
    solver_dir, prob_id = sys.argv[1], sys.argv[2]
    tl = float(sys.argv[3]); seed = int(sys.argv[4]) if len(sys.argv) > 4 else 7
    sys.path.insert(0, str(REPO / solver_dir)); os.environ["OGC_SEED"] = str(seed)
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    n = len(prob["blocks"])
    sol = ma.algorithm(prob, timelimit=tl)
    eng, D, P = ma._fresh(prob, 4)
    st = [0] * (n * 7)
    for day, ops in sol["operations"].items():
        for op in ops:
            if op["type"] == "ENTRY":
                i = op["block_id"]
                st[i*7:i*7+7] = [op["bay_id"], op["orient_idx"], op["x"], op["y"],
                                 int(day), int(day) + P[i], 1]
    hz = eng.horizon(); eng.set_solution(st); eng.build_occ(hz)
    base = eng.objective()
    print(f"{prob_id}: board {base[3]:,.0f}  Z1={base[0]:.0f}  (TL {tl:.0f}s, seed {seed})")
    # rebuild the board's OWN schedule from empty, same procedure as realize2
    eng.set_solution([0] * (n * 7)); eng.build_occ(hz)
    order = sorted((i for i in range(n) if st[i*7+6]),
                   key=lambda i: (st[i*7+4], -P[i]))
    exact = early = inbay = free = lost = 0
    for i in order:
        d, j = st[i*7+4], st[i*7]
        if eng.insert_capped(i, d, j):
            b = eng.get_block(i)
            if b[4] < d and not eng.retime_block(i, d - b[4], d - b[4]):
                early += 1
            else:
                exact += 1
        elif eng.insert_capped(i, -1, j): inbay += 1
        elif eng.insert_capped(i, -1, -1): free += 1
        else: lost += 1
    o = eng.objective()
    print(f"   rebuilt: {o[3]:,.0f}  Z1={o[0]:.0f}"
          f"   locks: {exact} on schedule, {early} early, {inbay} late-in-bay,"
          f" {free} anywhere, {lost} unplaced"
          f"   vs board {(o[3]-base[3])/base[3]*100:+.2f}%")

if __name__ == "__main__":
    main()
