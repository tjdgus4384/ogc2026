#!/usr/bin/env python3
"""If the neighbours only WAITED, could the tardy block enter on time?

The entry day is the one quantity that decides the objective on P4/P5/P6 --
tardiness is 78-96% of it, and tardiness is a pure function of entry days --
and it is the one quantity our search never decides. `insertGreedy` takes the
earliest legal day (skipping only days the energetic reservation proves
over-subscribed), and no operator ever proposes "enter later so someone else
can enter now". slackW() is 0.0: the slack surrogate was a penalty term, and
blanket slack-deferral was a fixed rule; neither searched the dimension.

Two probes have already bracketed this from the wrong sides:

  reach_probe  holds every neighbour FIXED and asks whether a tardy block has
               any legal position -> none, on P4/P5/P6.
  roommake     REMOVES neighbours and finds punctual slots do open (0 -> 8-32,
               CP optimal) -- but relocating them costs 60-80x the gain.

This asks the question in between, and it is the cheap one: keep every
neighbour, in its bay, at its position, and only let it enter LATER, never
past its own punctual deadline. A neighbour delayed inside its own slack adds
exactly ZERO tardiness, so if this opens the window the gain is free.

For each of the worst tardy blocks it finds, over the block's punctual entry
days, the set of residents that occupy the window and asks how much of that
set could simply wait -- and whether the block then fits.

    python3 tools/delay_probe.py SOLVERDIR prob_6 runs/base53-s41/prob_6.sol.json [n]
"""
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def main():
    solver_dir, prob_id, sol_path = sys.argv[1], sys.argv[2], sys.argv[3]
    top = int(sys.argv[4]) if len(sys.argv) > 4 else 4
    sys.path.insert(0, str(REPO / solver_dir))
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    sol = json.load(open(REPO / sol_path))
    B = prob["blocks"]
    n = len(B)
    eng, D, P = ma._fresh(prob)
    hz = eng.horizon()
    R = [int(b["release_time"]) for b in B]

    entry, exit_, bay, xy = {}, {}, {}, {}
    for day, ops in sol["operations"].items():
        for op in ops:
            if op["type"] == "ENTRY":
                entry[op["block_id"]] = int(day)
                bay[op["block_id"]] = op["bay_id"]
                xy[op["block_id"]] = (op["x"], op["y"], op["orient_idx"])
            else:
                exit_[op["block_id"]] = int(day)

    state = [0] * (n * 7)
    for i in range(n):
        if i in entry:
            x, y, o = xy[i]
            state[i * 7:i * 7 + 7] = [bay[i], o, x, y, entry[i],
                                      exit_.get(i, entry[i] + P[i]), 1]
    eng.set_solution(state)
    eng.build_occ(hz)
    base = eng.objective()[3]

    tardy = sorted((i for i in entry
                    if exit_.get(i, entry[i] + P[i]) > D[i]),
                   key=lambda i: entry[i] + P[i] - D[i], reverse=True)
    print(f"{prob_id}: base {base:,.0f}, {len(tardy)} tardy; worst {top}")

    for A in tardy[:top]:
        late = exit_.get(A, entry[A] + P[A]) - D[A]
        last = D[A] - P[A]                 # latest punctual entry
        if last < R[A]:
            print(f"  blk {A}: cannot be punctual from release -- forced")
            continue
        # residents sharing A's punctual window, and how far each could wait
        # without becoming tardy itself
        movable, stuck = [], []
        for k in range(n):
            if k == A or k not in entry:
                continue
            if not (entry[k] < last + P[A] and R[A] < exit_.get(k, entry[k] + P[k])):
                continue
            room = (D[k] - P[k]) - entry[k]      # free days of delay
            (movable if room > 0 else stuck).append((k, room))
        print(f"  blk {A}: late {late}d, punctual entry <= {last}, "
              f"{len(movable)} window residents could wait "
              f"(median {sorted(r for _, r in movable)[len(movable) // 2] if movable else 0}d), "
              f"{len(stuck)} cannot")
        if not movable:
            continue
        # push every movable resident as late as its own slack allows, then ask
        # the kernel -- with its own vp oracle -- whether A now has a punctual spot
        trial = list(state)
        for k, room in movable:
            trial[k * 7 + 4] = entry[k] + room
            trial[k * 7 + 5] = entry[k] + room + P[k]
        trial[A * 7 + 6] = 0
        eng.set_solution(trial)
        eng.build_occ(hz)
        ok_all = eng.insert_capped(A, last, -1)
        # and the cheaper half-measure: only the residents with the most room
        movable.sort(key=lambda t: -t[1])
        half = movable[:max(1, len(movable) // 2)]
        trial2 = list(state)
        for k, room in half:
            trial2[k * 7 + 4] = entry[k] + room
            trial2[k * 7 + 5] = entry[k] + room + P[k]
        trial2[A * 7 + 6] = 0
        eng.set_solution(trial2)
        eng.build_occ(hz)
        ok_half = eng.insert_capped(A, last, -1)
        print(f"      delay ALL movable -> punctual: {ok_all}; "
              f"delay top half -> punctual: {ok_half}")

    eng.set_solution(state)
    eng.build_occ(hz)


if __name__ == "__main__":
    main()
