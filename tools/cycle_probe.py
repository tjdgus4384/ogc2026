#!/usr/bin/env python3
"""Do PROFITABLE CYCLIC EXCHANGES exist on a finished board?

Every measurement in this repo says the same thing from a different angle:

  * the room is there   -- the energetic bound forces 0 tardy days on P3/P4/P5
    and 261 on P6 against our 4,119, and the Z3 area relaxation bounds P3 at 74
    against our 424;
  * the board is rigid  -- reach_probe brute-forced every day x bay x
    orientation x integer position for P6's worst tardy blocks and found NO
    legal position;
  * single moves are always underwater -- 117/109 exact-delta assignment
    candidates, 0 adopted; the top ones are 14/14 and 25/25 impossible to place
    punctually, and taking them loose costs 60-80x the gain;
  * yet the bays are NOT saturated -- roommake showed that freeing 14-40
    neighbours opens 8-32 punctual slots, CP OPTIMAL.

The one move class that is consistent with all four is a CYCLE: b1 takes b2's
bay, b2 takes b3's, ..., bk takes b1's. No free space is needed, because every
bay gives up a block exactly when it receives one -- which is precisely the
"free the neighbours and slots appear" effect, made self-financing. And each
leg may be individually negative while the cycle is positive, which is what
"single moves are 60-80x underwater" demands.

The kernel has no such move. Ops 8/9 are depth-1 ejections that only fire while
the RUNNING local account stays in profit, and the one swap is random, depth-2,
and requires the two blocks to fit each other's exact (x,y) -- it is also
switched off entirely when nothing is tardy, i.e. on P3.

This probe asks whether the class is worth building in C++ before it is built.
It anneals a real board, then spends the rest of the budget sampling cycles of
length 2-4 against a control of single moves on the SAME board, and reports how
many of each are feasible and improving.

    python3 tools/cycle_probe.py SOLVERDIR prob_3 ANNEAL_SECS PROBE_SECS [seed]
"""
import json
import pathlib
import random
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent


def main():
    solver_dir, prob_id = sys.argv[1], sys.argv[2]
    anneal = float(sys.argv[3])
    probe = float(sys.argv[4])
    seed = int(sys.argv[5]) if len(sys.argv) > 5 else 7
    sys.path.insert(0, str(REPO / solver_dir))
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    B = prob["blocks"]
    n = len(B)
    m = len(prob["bays"])
    eng, D, P = ma._fresh(prob)
    hz = eng.horizon()

    # A real ANNEALED board, not a construction: the question is whether cycles
    # exist where our search actually ends up. run_portfolio keeps its winner in
    # per-thread copies and leaves the engine's own solution untouched, so build
    # here and anneal in place with the single-arm entry point instead.
    order = sorted(range(n), key=lambda i: (D[i], -P[i]))
    if eng.reconstruct_ordered([-1] * n, order, False, seed) != -1:
        print(f"{prob_id}: base order does not construct")
        return
    eng.run_alns(anneal, 0.05, 0.15, 0.002, 0.9995, seed)
    base_state = eng.get_solution()
    base = eng.objective()[3]
    if not any(base_state[i * 7 + 6] for i in range(n)):
        print(f"{prob_id}: engine holds no board after the anneal")
        return
    R = [int(b["release_time"]) for b in B]
    pref = [b["bay_preferences"] for b in B]

    def restore():
        eng.set_solution(base_state)
        eng.build_occ(hz)

    def bay_of(state, i):
        return state[i * 7]

    placed = [i for i in range(n) if base_state[i * 7 + 6]]
    rng = random.Random(seed)

    # THE IMPROVEMENT GRAPH. A cycle is only worth trying if every member is
    # moving somewhere it actually wants: bays it prefers (Z3), plus -- if the
    # block is tardy -- any other bay, since anywhere it can be punctual is a
    # Z1 win. Sampling cycles at random instead tests randomness, not the move
    # class: random single moves and random 3-cycles both score 0/500+.
    wants = {}
    for i in placed:
        bi = bay_of(base_state, i)
        tardy = base_state[i * 7 + 5] > D[i]
        w = [j for j in range(m)
             if j != bi and (tardy or pref[i][j] > pref[i][bi])]
        if w:
            wants[i] = set(w)
    # residents of each bay that want out, indexed by where they want to go
    by_bay = {j: [] for j in range(m)}
    for i in wants:
        by_bay[bay_of(base_state, i)].append(i)

    stats = {k: [0, 0, 0, 0.0] for k in (1, 2, 3, 4)}   # tried, punctual, better, gain

    def attempt(chain, targets):
        state = list(base_state)
        for i in chain:
            state[i * 7 + 6] = 0
        eng.set_solution(state)
        eng.build_occ(hz)
        punctual = True
        for i, j in zip(chain, targets):
            cap = max(R[i], D[i] - P[i])       # latest entry that is still on time
            if not eng.insert_capped(i, cap, j):
                punctual = False
                if not eng.insert_capped(i, -1, j):
                    return None, False
        return eng.objective()[3], punctual

    def sample_cycle(k):
        """A cycle in the wants graph: each member moves into the bay the next
        member is vacating, so no free space is required anywhere."""
        if not wants:
            return None
        a = rng.choice(list(wants))
        chain = [a]
        bays_seen = [bay_of(base_state, a)]
        for _ in range(k - 1):
            need = [j for j in wants[chain[-1]] if j not in bays_seen]
            if not need:
                return None
            j = rng.choice(need)
            pool = [i for i in by_bay[j] if i not in chain]
            if not pool:
                return None
            chain.append(rng.choice(pool))
            bays_seen.append(j)
        # close the cycle: the last member must want the first member's bay
        if bays_seen[0] not in wants[chain[-1]]:
            return None
        targets = bays_seen[1:] + [bays_seen[0]]
        return chain, targets

    t0 = time.time()
    while time.time() - t0 < probe:
        k = rng.choice((1, 2, 2, 3, 3, 4))
        if k == 1:                                   # control: guided single move
            if not wants:
                break
            a = rng.choice(list(wants))
            got = ([a], [rng.choice(list(wants[a]))])
        else:
            got = sample_cycle(k)
        if got is None:
            continue
        chain, targets = got
        st = stats[len(chain)]
        st[0] += 1
        val, punc = attempt(chain, targets)
        if val is not None:
            if punc:
                st[1] += 1
            if val < base - 1e-9:
                st[2] += 1
                st[3] = min(st[3], (val - base) / base * 100)
        restore()

    el = time.time() - t0
    print(f"{solver_dir} {prob_id} seed {seed}: base {base:,.0f} "
          f"({anneal:.0f}s anneal, {el:.0f}s probe); "
          f"{len(wants)}/{len(placed)} blocks want another bay")
    print(f"   {'move':>7} {'tried':>7} {'all punctual':>14} {'improving':>16} {'best':>8}")
    for k in (1, 2, 3, 4):
        tr, pu, im, gn = stats[k]
        if not tr:
            continue
        label = "single" if k == 1 else f"{k}-cycle"
        print(f"   {label:>7} {tr:7d} {pu:7d} ({pu / tr * 100:5.1f}%) "
              f"{im:7d} ({im / tr * 100:5.1f}%) {gn:+7.2f}%")


if __name__ == "__main__":
    main()
