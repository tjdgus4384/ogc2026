"""Conflict-model soundness/tightness test (v5.3 acceptance).

Two properties the pair model must have, checked on real boards:

  TIGHTNESS  the incumbent board is feasible, so feeding each placed
             block's OWN placement as its single candidate must yield
             ZERO conflicts. A conflict here means the model rejects
             legal arrangements (over-constrained) -- it would blind the
             CP arm instead of guiding it.
  SOUNDNESS  every pair the model calls conflict-free must really be
             jointly placeable: sample candidate pairs it accepts,
             place both, and ask the vp oracle.

Usage: conflict_soundness.py <prob.json> <sol.json> [samples]
"""
import json, math, os, random, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "solver")); os.chdir(ROOT)
import myalgorithm as M

prob_path, sol_path = sys.argv[1], sys.argv[2]
NS = int(sys.argv[3]) if len(sys.argv) > 3 else 400
p = json.load(open(prob_path)); ops = json.load(open(sol_path))["operations"]
B = p["blocks"]; n = len(B)
D = [int(round(b["due_date"])) for b in B]
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
flat = [0] * (n * 7)
for t, day in ops.items():
    for o in day:
        i = o["block_id"]
        if o["type"] == "ENTRY":
            flat[i*7:i*7+5] = [o["bay_id"], o["orient_idx"], o["x"], o["y"], int(t)]
            flat[i*7+6] = 1
        else:
            flat[i*7+5] = int(t)
eng = M._build_engine(p); hor = max(D) + max(P) + 40
eng.build_occ(hor); eng.set_solution(flat); eng.build_occ(hor)

# TIGHTNESS: incumbent placements as the only candidates
placed = [i for i in range(n) if flat[i*7+6]]
flatc = []
for i in placed:
    flatc += [i, flat[i*7], flat[i*7+1], flat[i*7+2], flat[i*7+3], flat[i*7+4]]
prs = eng.pair_conflicts(flatc)
tight_bad = len(prs) // 2

# SOUNDNESS: pairs the model accepts must be jointly placeable
rng = random.Random(7)
free = rng.sample(placed, min(24, len(placed)))
part = list(flat)
for q in free:
    part[q*7+6] = 0
eng.set_solution(part); eng.build_occ(hor)
CAND = {}
for q in free:
    raw = eng.feasible_positions(q, 60, 0)
    CAND[q] = [tuple(raw[t:t+5]) for t in range(0, len(raw), 5)]
flatc, idx = [], []
for q in free:
    for r, c in enumerate(CAND[q]):
        flatc += [q, c[0], c[1], c[2], c[3], c[4]]; idx.append((q, r))
prs2 = set()
raw2 = eng.pair_conflicts(flatc)
for t in range(0, len(raw2), 2):
    prs2.add((raw2[t], raw2[t+1])); prs2.add((raw2[t+1], raw2[t]))
checked = viol = 0
for _ in range(NS):
    a, b = rng.randrange(len(idx)), rng.randrange(len(idx))
    if a == b or (a, b) in prs2:
        continue
    (qa, ra), (qb, rb) = idx[a], idx[b]
    if qa == qb:
        continue
    trial = list(part)
    for (q, r) in ((qa, ra), (qb, rb)):
        c = CAND[q][r]
        trial[q*7:q*7+7] = [c[0], c[1], c[2], c[3], c[4], c[4] + P[q], 1]
    eng.set_solution(trial); eng.build_occ(hor)
    checked += 1
    if not (eng.vp_ok(qa) and eng.vp_ok(qb)):
        viol += 1
    eng.set_solution(part); eng.build_occ(hor)
print(json.dumps({"board": os.path.basename(sol_path),
                  "tightness_conflicts_on_incumbent": tight_bad,
                  "soundness_pairs_checked": checked,
                  "soundness_violations": viol}))
