#!/usr/bin/env bash
#SBATCH -J fastcheck
#SBATCH -N 1
#SBATCH -c 8
#SBATCH --mem=24G
#SBATCH -t 00:25:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
OGC_SEED=7 "$PY" -u - <<'EOF'
"""fast-build acceptance: (1) BYTE-IDENTITY of deterministic reconstructions
against the shipped kernel (same candidates, same order, same first acceptance
-- any divergence is a correctness bug in buildForbiddenX); (2) SPEED as
iterations completed in a fixed wall budget."""
import json, sys, time, pathlib
REPO = pathlib.Path("/data_fast/home/tjdgus4384/priv/ogc2026")

def load(build):
    import importlib
    for m in list(sys.modules):
        if m in ("myalgorithm", "sscore", "utils"):
            del sys.modules[m]
    sys.path.insert(0, str(REPO / build))
    import myalgorithm as ma
    sys.path.pop(0)
    return ma

def recon_sig(ma, prob, order_key, seed):
    B = prob["blocks"]; n = len(B)
    D = [int(round(b["due_date"])) for b in B]
    P = [max(1, -(-int(b["processing_time"]) // 1)) for b in B]
    eng, _, _ = ma._fresh(prob, 4)
    order = sorted(range(n), key=order_key(D, P))
    bad = eng.reconstruct_ordered([-1]*n, order, False, seed)
    return bad, tuple(eng.get_solution())

orders = {
    "due":   lambda D, P: (lambda i: (D[i], i)),
    "slack": lambda D, P: (lambda i: (D[i]-P[i], -P[i], i)),
    "longP": lambda D, P: (lambda i: (-P[i], D[i], i)),
}
probs = {p: json.load(open(REPO/"data"/"preliminary_test"/f"prob_{p}.json"))
         for p in (3, 4, 5, 6)}

ma_ship = load("solver")
sig_ship = {}
for p, pr in probs.items():
    for nm, ok in orders.items():
        for sd in (7, 13):
            sig_ship[(p, nm, sd)] = recon_sig(ma_ship, pr, ok, sd)
print("shipped reconstructions collected", flush=True)

ma_fast = load("builds/fast")
mism = 0
for key, want in sig_ship.items():
    got = recon_sig(ma_fast, probs[key[0]], orders[key[1]], key[2])
    if got != want:
        mism += 1
        print(f"  MISMATCH {key}: bad {want[0]} vs {got[0]},"
              f" sol equal={got[1]==want[1]}", flush=True)
print(f"byte-identity: {len(sig_ship)-mism}/{len(sig_ship)} reconstructions"
      f" identical", flush=True)
if mism:
    sys.exit(1)

# speed: fixed 60s anneal, same seed/starts; metric = kernel iterations
for p in (4, 6):
    pr = probs[p]
    row = {}
    for tag, ma in (("ship", ma_ship), ("fast", ma_fast)):
        eng, seeds = ma._seed_free(pr)
        starts = [seeds[0], []] + seeds[1:3]
        eng.run_portfolio(4, 60.0, 1, 0.05, 0.15, 0.002, 0.9995, 7, 1, starts)
        st = eng.last_stats()
        row[tag] = (st[0] if st else -1, eng.objective()[3])
    r = row["fast"][0] / max(1, row["ship"][0])
    print(f"P{p}: iters ship {row['ship'][0]:,} fast {row['fast'][0]:,}"
          f"  RATIO {r:.3f}x   obj {row['ship'][1]:,.0f} vs {row['fast'][1]:,.0f}",
          flush=True)
EOF
