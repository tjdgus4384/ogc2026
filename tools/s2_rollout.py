#!/usr/bin/env python3
"""NEXT_SOLVER stages S1+S2: does rollout evaluation see what greedy cannot?

S1: the rollout evaluator -- complete a partial construction with the plain
greedy (EDD order, insert_capped) and score the true objective. Deterministic;
its cost is the architecture's currency.

S2, the kill test: replay the baseline construction to each of the first K
decision points where a block comes out tardy (the sacrifices greedy commits
implicitly). At each point, evaluate VARIANTS of that one decision by rollout:
   (a) greedy as-is                    (baseline)
   (b) defer this block by 5 positions
   (c) defer this block by 15 positions
   (d) force it to its 2nd-preference bay
If any variant's rollout beats the baseline's final at that point, rollout
evaluation has an edge no existing operator has (every repair in the driver
judges by immediate objective only). No edge at any point on any board kills
the architecture, and the spec gains the record.

    python3 tools/s2_rollout.py prob_4 [seed]
"""
import json, math, os, pathlib, sys, time
REPO = pathlib.Path(__file__).resolve().parent.parent
SOLVER = "builds/pc2"

def main():
    prob_id = sys.argv[1]
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 7
    sys.path.insert(0, str(REPO / SOLVER))
    os.environ["OGC_SEED"] = str(seed)
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    B = prob["blocks"]; n = len(B)
    PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
    eng, D, P = ma._fresh(prob, 4)
    hz = eng.horizon()
    order = sorted(range(n), key=lambda i: (D[i], -P[i]))     # plain EDD

    def construct(prefix_actions):
        """DETERMINISTIC construction: reconstruct_ordered is a pure function
        of the order (fixed internal seed 7). The first S2/S3 ran on
        insert_capped, whose per-call RNG consumption made every build a fresh
        draw -- the measured "edges" were draw noise, and S3's monotone loop
        came out WORSE than its own baseline, which is what exposed it. The
        instrument discipline again: the evaluator must be deterministic
        before its comparisons mean anything. Bay-forcing variants are
        dropped (not expressible under reconstruct_ordered; defers carry the
        debt-free signal anyway)."""
        seq = list(order)
        for pos in sorted(prefix_actions, reverse=True):
            act = prefix_actions[pos]
            if act[0] == 'defer':
                b = seq.pop(pos); seq.insert(min(len(seq), pos + act[1]), b)
        if eng.reconstruct_ordered([-1] * n, seq, False, 7) != -1:
            return None
        return eng.objective()

    t0 = time.time()
    base = construct({})
    t1 = time.time() - t0
    print(f"{prob_id} seed {seed}: S1 rollout cost {t1:.2f}s,"
          f" baseline {base[3]:,.0f}  Z1={base[0]:.0f}")

    # sacrifice positions from the deterministic baseline board
    construct({})
    pos_of = {i: k for k, i in enumerate(order)}
    tardy_pos = sorted(pos_of[i] for i in range(n)
                       if eng.get_block(i)[6]
                       and eng.get_block(i)[5] > D[i])
    print(f"   {len(tardy_pos)} sacrifice positions; probing first 8")

    wins = 0
    for pos in tardy_pos[:8]:
        i = order[pos]
        variants = {
            'defer5':  {pos: ('defer', 5)},
            'defer15': {pos: ('defer', 15)},
            'defer40': {pos: ('defer', 40)},
        }
        row = []
        for name, acts in variants.items():
            o = construct(acts)
            if o is not None:
                d = (o[3] - base[3]) / base[3] * 100
                row.append((name, o[3], d))
        best = min(row, key=lambda r: r[1]) if row else None
        mark = ""
        if best and best[1] < base[3] - 1e-9:
            wins += 1
            mark = "   <- ROLLOUT EDGE"
        print(f"   pos {pos:3d} blk {i:3d}: " +
              "  ".join(f"{nm} {dd:+.2f}%" for nm, _, dd in row) + mark)
    print(f"   S2 verdict: rollout edge at {wins} of {min(8,len(tardy_pos))}"
          f" decision points"
          + ("   -> S2 PASSES" if wins else "   -> architecture refuted here"))

if __name__ == "__main__":
    main()
