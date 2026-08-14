#!/usr/bin/env python3
"""CANDFIX stage 0(c): the decisive model replay -- V0..V3 on identical windows.

candaud confirmed two of the three suspected defects at the CP arm's own
state: EVERY (block, bay) offers exactly ONE entry day (15,000/15,000 -- the
model cannot trade days, in a z1-dominated objective where the day is the whole
cost), and the uniform window frees the tardy seed's same-bay blockers only
9.6-17.1% of the time. The bay quota is mild (43/34/23), not the collapse
bayscratch saw.

This replay copies the arm's model verbatim (myalgorithm.py:657-695) and runs
the SAME 200 windows per board under:
  V0 shipped: uniform window, feasible_positions(i, 30, 0)
  V1 blocker-seeded window, shipped vocabulary
  V2 uniform window, MULTI-DAY vocabulary: union of feasible_positions over
     eMin in {0, punctual, incumbent day}, bay-balanced dedupe to 30
  V3 both
Metrics: improving-window count, median/best dObj, vp-fail rate.

PRE-REGISTERED (from the architecture review):
  KILL  V3 improving-count <= V0's AND V3 best dObj >= V0's best  -> the
        vocabulary is not binding; close CANDFIX, extend reseat_price to P6
  GO    V3 improves >= 1.5x as many windows as V0, or best dObj >= 30% deeper

    python3 tools/candreplay.py prob_6 900 7 200
"""
import hashlib, json, pathlib, random, sys, time
REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "builds" / "ordf"))


def cached(pid, tl, seed):
    sd = str(REPO / "solver")
    so = pathlib.Path(sd) / "sscore.cpython-312-x86_64-linux-gnu.so"
    h = hashlib.sha256(f"{sd}|{so.stat().st_mtime_ns}".encode()).hexdigest()[:12]
    p = REPO / "runs" / "boards" / f"{pid}_tl{int(tl)}_s{seed}_{h}.json"
    if not p.exists():
        sys.exit(f"no cached board {p.name}")
    return json.loads(p.read_text())["base"]


def main():
    pid, tl = sys.argv[1], float(sys.argv[2])
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    NW = int(sys.argv[4]) if len(sys.argv) > 4 else 200
    K, NCAND = 25, 30
    ds = next(d for d in ("preliminary_test","train_final","train")
              if (REPO/"data"/d/f"{pid}.json").exists())
    prob = json.load(open(REPO/"data"/ds/f"{pid}.json"))
    n = len(prob["blocks"]); m = len(prob["bays"])
    W = prob["weights"]
    PREF = [[float(v) for v in b["bay_preferences"]] for b in prob["blocks"]]
    SMAX = [max(p) for p in PREF]
    import myalgorithm as ma
    from ortools.sat.python import cp_model as cp
    eng, D, P = ma._fresh(prob, 4)
    hz = eng.horizon()
    cur = cached(pid, tl, seed)
    eng.set_solution(cur); eng.build_occ(hz)
    base_obj = eng.objective()[3]
    tardy = [i for i in range(n) if cur[i*7+6] and cur[i*7+5] > D[i]]
    if not tardy:
        print(f"{pid}: no tardy -- arm inactive"); return

    def vocab(i, mode):
        if mode == 0:
            raw = eng.feasible_positions(i, NCAND, 0)
            return [tuple(raw[q:q+5]) for q in range(0, len(raw), 5)]
        # multi-day: union over eMin anchors, bay-balanced round-robin to 30
        punct = max(0, D[i] - P[i])
        inc = cur[i*7+4]
        seen, per = set(), {}
        for em in (0, punct, inc):
            raw = eng.feasible_positions(i, NCAND, em)
            for q in range(0, len(raw), 5):
                c = tuple(raw[q:q+5])
                if c in seen:
                    continue
                seen.add(c)
                per.setdefault(c[0], []).append(c)
        out, rr = [], 0
        while len(out) < NCAND and any(per.values()):
            for b in sorted(per):
                if per[b]:
                    out.append(per[b].pop(0))
                    if len(out) >= NCAND:
                        break
        return out

    rows = {v: [] for v in range(4)}
    t00 = time.time()
    for w in range(NW):
        rng = random.Random(9000 + w)          # identical windows per variant
        seed_i = rng.choice(tardy)
        sb = [j for j in range(n) if j != seed_i and cur[j*7+6]
              and cur[j*7] == cur[seed_i*7]
              and cur[j*7+4] < cur[seed_i*7+5]
              and cur[seed_i*7+4] < cur[j*7+5]]
        pool = [i for i in range(n) if cur[i*7+6] and i != seed_i]
        uni = rng.sample(pool, min(K-1, len(pool)))
        blk = sb[:K-1]
        if len(blk) < K-1:
            blk += [i for i in uni if i not in set(blk)][:K-1-len(blk)]
        for var in range(4):
            free = [seed_i] + (blk if var in (1, 3) else uni)
            vmode = 1 if var in (2, 3) else 0
            part = list(cur)
            for i in free:
                part[i*7+6] = 0
            eng.set_solution(part); eng.build_occ(hz)
            CAND = {}
            for i in free:
                cs = vocab(i, vmode)
                c0 = tuple(cur[i*7:i*7+5])
                if c0 not in cs:
                    cs.append(c0)
                CAND[i] = cs
            mdl = cp.CpModel()
            V, flatc, idx = {}, [], []
            for i in free:
                V[i] = [mdl.NewBoolVar(f"z{i}_{q}")
                        for q in range(len(CAND[i]))]
                mdl.AddExactlyOne(V[i])
                for q, c in enumerate(CAND[i]):
                    flatc += [i, c[0], c[1], c[2], c[3], c[4]] if False else \
                             [i, *c]
                    idx.append((i, q))
            prs = eng.pair_conflicts(flatc)
            for t2 in range(0, len(prs), 2):
                (i, q), (k2, r2) = idx[prs[t2]], idx[prs[t2+1]]
                mdl.AddBoolOr([V[i][q].Not(), V[k2][r2].Not()])
            mdl.Minimize(sum((W["w1"] * max(0, c[4] + P[i] - D[i])
                              + W["w3"] * (SMAX[i] - PREF[i][c[0]])) * V[i][q]
                             for i in free for q, c in enumerate(CAND[i])))
            sv = cp.CpSolver()
            sv.parameters.max_time_in_seconds = 6.0
            sv.parameters.num_search_workers = 4
            st = sv.Solve(mdl)
            if st not in (cp.OPTIMAL, cp.FEASIBLE):
                rows[var].append(("nosolve", 0.0)); continue
            new = list(part)
            for i in free:
                for q, c in enumerate(CAND[i]):
                    if sv.Value(V[i][q]):
                        new[i*7:i*7+7] = [*c, c[4] + P[i], 1]
                        break
            eng.set_solution(new); eng.build_occ(hz)
            if any(new[i*7+6] and not eng.vp_ok(i) for i in range(n)):
                rows[var].append(("vpfail", 0.0)); continue
            d = eng.objective()[3] - base_obj
            rows[var].append(("ok", d))
    names = ["V0 shipped", "V1 blockers", "V2 multiday", "V3 both"]
    print(f"{pid} s{seed}: base {base_obj:,.0f}  {NW} windows"
          f"  [{time.time()-t00:.0f}s]", flush=True)
    for var in range(4):
        oks = [d for s2, d in rows[var] if s2 == "ok"]
        imp = [d for d in oks if d < -0.5]
        vpf = sum(1 for s2, _ in rows[var] if s2 == "vpfail")
        med = sorted(imp)[len(imp)//2] if imp else 0.0
        best = min(imp) if imp else 0.0
        print(f"   {names[var]:<12} improving {len(imp):>3}/{NW}"
              f"  median {med:>12,.0f}  best {best:>12,.0f}  vpfail {vpf}",
              flush=True)


if __name__ == "__main__":
    main()
