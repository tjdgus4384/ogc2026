#!/usr/bin/env python3
"""Does an earlier legal entry exist for the tardy blocks of a finished board?

    python3 tools/rigidity_probe.py SOLVERDIR prob_4 runs/base53-s41/prob_4.sol.json

The 2026-07-30 probe asked this and answered no for 8/8 top offenders, which
became the project's "final boards are exactly rigid" diagnosis and closed the
earliest-entry axis. That probe called insert_capped, i.e. it asked the kernel's
vpConservative -- which treats same-day ENTRY events as simultaneous and so
rejects placements the official checker accepts (see docs/CAMPAIGN_LEDGER.md).
Re-running it against a corrected kernel says how much of that closed axis was
an artifact of the oracle rather than of the instance.

Reports, per tardy block, the earliest entry day the kernel will grant with the
REST OF THE BOARD FIXED -- the same question, the same fixed rest, only the
feasibility test differs between solver directories.
"""
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def load(solver_dir, prob_path, sol_path):
    sys.path.insert(0, str(solver_dir))
    import myalgorithm as ma
    prob = json.load(open(prob_path))
    eng, D, P = ma._fresh(prob)
    ops = json.load(open(sol_path))["operations"]
    n = len(prob["blocks"])
    rec = {}
    for day, evs in ops.items():
        for e in evs:
            if e["type"] == "ENTRY":
                rec[e["block_id"]] = (e["bay_id"], e["orient_idx"],
                                      e["x"], e["y"], int(day))
    flat = []
    for i in range(n):
        if i in rec:
            bay, ori, x, y, ent = rec[i]
            flat += [bay, ori, x, y, ent, ent + P[i], 1]
        else:
            flat += [0, 0, 0, 0, 0, 0, 0]
    eng.set_solution(flat)
    return ma, prob, eng, D, P, rec


def main():
    solver_dir, prob_id, sol_path = sys.argv[1], sys.argv[2], sys.argv[3]
    prob_path = REPO / "data" / "preliminary_test" / f"{prob_id}.json"
    ma, prob, eng, D, P, rec = load(REPO / solver_dir, prob_path, sol_path)
    if not eng.audit_state():
        print(f"{solver_dir}: loaded board is NOT self-consistent under this "
              f"kernel's rules -- cannot probe")
        return
    tardy = sorted(((rec[i][4] + P[i] - D[i], i) for i in rec
                    if rec[i][4] + P[i] > D[i]), reverse=True)
    print(f"{solver_dir} {prob_id}: {len(tardy)} tardy blocks, "
          f"Z1={sum(t for t, _ in tardy)} days")
    freed, gained = 0, 0
    for late, i in tardy:
        bay, ori, x, y, ent = rec[i]
        eng.set_block(i, bay, ori, x, y, ent, ent + P[i], False)
        eng.finalize_load()
        # insert_capped returns the EARLIEST feasible entry <= cap, so one call
        # answers the question. cap <= 0 means "no cap" to the kernel, so a
        # block already entering on day 0 or 1 is skipped rather than probed
        # with an accidental uncapped call.
        best = ent
        if ent - 1 > 0 and eng.insert_capped(i, ent - 1, -1):
            best = eng.get_block(i)[4]
        if best < ent:
            freed += 1
            gained += min(late, ent - best)
        eng.set_block(i, bay, ori, x, y, ent, ent + P[i], True)
        eng.finalize_load()
    print(f"  earlier entry exists for {freed}/{len(tardy)} tardy blocks; "
          f"single-block tardiness recoverable = {gained} days")


if __name__ == "__main__":
    main()
