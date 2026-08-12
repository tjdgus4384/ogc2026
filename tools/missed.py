#!/usr/bin/env python3
"""When a block fails to enter on time, was there a legal spot it never tried?

This is the smallest question that separates the two explanations of Z1, and it
has not been asked. Tardiness is exactly the set of blocks that found no legal
placement inside [release, due - processing], so every tardy day traces to one
insertion that failed. That failure has three possible causes:

  (a) no legal position existed on the board AS IT STOOD at that moment;
  (b) one existed and insertGreedy's candidate vocabulary never proposed it
      (it offers residents' bounding-box corners, plus a strided sweep);
  (c) one existed, was proposed, and vp wrongly rejected it.

reach_probe answered (a) -- but on the FINISHED board, long after the block was
placed late. The board at insertion time is a different, emptier board, and
nothing has measured it.

So: replay the construction one block at a time. Whenever the punctual insert
fails, brute-force EVERY (bay, orientation, integer x, y, day) in the block's
punctual window through the kernel's own place_at, which commits only if vp
agrees. A success is a position the vocabulary missed -- (b) or (c), i.e. search
reach. A failure is (a), physics.

Because the rescue is kept, the run also reports what the whole construction's
tardiness would have been with a perfect generator, which bounds everything the
"enrich the vocabulary" family could ever be worth.

    python3 tools/missed.py SOLVERDIR prob_4 [max_failures] [stride]
"""
import json
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent


def main():
    solver_dir, prob_id = sys.argv[1], sys.argv[2]
    cap = int(sys.argv[3]) if len(sys.argv) > 3 else 25
    stride = int(sys.argv[4]) if len(sys.argv) > 4 else 1
    sys.path.insert(0, str(REPO / solver_dir))
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    B = prob["blocks"]
    n = len(B)
    bays = prob["bays"]
    R = [int(b["release_time"]) for b in B]

    eng, D, P = ma._fresh(prob)
    order = sorted(range(n), key=lambda i: (D[i], -P[i]))
    nori = [len(b["shape"]) for b in B]

    failures = scanned = rescued = 0
    rescue_days = 0
    late_after = 0
    t0 = time.time()
    for A in order:
        last = D[A] - P[A]                     # latest punctual entry
        if last >= R[A] and eng.insert_capped(A, last, -1):
            continue                            # entered on time, nothing to ask
        if last < R[A]:                         # cannot be punctual from release
            eng.insert_capped(A, -1, -1)
            continue
        failures += 1
        found = None
        if scanned < cap:
            scanned += 1
            for e in range(R[A], last + 1):
                for j in range(len(bays)):
                    W = int(bays[j]["width"])
                    H = int(bays[j]["height"])
                    for oi in range(nori[A]):
                        for y in range(0, H + 1, stride):
                            for x in range(0, W + 1, stride):
                                if eng.place_at(A, j, oi, x, y, e):
                                    found = (j, oi, x, y, e)
                                    break
                            if found:
                                break
                        if found:
                            break
                    if found:
                        break
                if found:
                    break
        if found:
            rescued += 1
            rescue_days += 0                    # kept, so it costs no tardiness
        else:
            eng.insert_capped(A, -1, -1)
            f = eng.get_block(A)[5]
            late_after += max(0, f - D[A])

    o = eng.objective()
    el = time.time() - t0
    print(f"{prob_id}: {failures} blocks failed a punctual insert; "
          f"{scanned} brute-forced ({el:.0f}s, stride {stride})")
    print(f"   vocabulary MISSED a legal punctual spot: {rescued}/{scanned}"
          f"  -> {'SEARCH REACH' if rescued else 'PHYSICS'}")
    print(f"   construction with the exhaustive fallback: "
          f"Z1={o[0]:,.0f} Z2={o[1]:,.0f} Z3={o[2]:,.0f} obj={o[3]:,.0f}")

    # the same construction with no fallback, for the difference
    eng2, _, _ = ma._fresh(prob)
    if eng2.reconstruct_ordered([-1] * n, order, False, 7) == -1:
        o2 = eng2.objective()
        print(f"   plain greedy construction:                 "
              f"Z1={o2[0]:,.0f} Z2={o2[1]:,.0f} Z3={o2[2]:,.0f} obj={o2[3]:,.0f}")
        if o2[0]:
            print(f"   -> a perfect generator would cut construction Z1 by "
                  f"{(o2[0] - o[0]) / o2[0] * 100:+.1f}%")


if __name__ == "__main__":
    main()
