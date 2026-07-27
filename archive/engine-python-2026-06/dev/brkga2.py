"""brkga2.py -- the UNIFIED near-optimal engine: BRKGA over (assignment, per-bay order) with a FAST compiled
decoder, SEEDED by every other method, elite-polished by beam.

Why this is the unified frame (validated reasoning, 2026-06-21 session):
  - The problem is set-partitioning: assignment fixes Z2/Z3 exactly + bounds Z1; per-bay packing fixes Z1.
  - Single-basin search (anytime/assign_lns) gets STUCK; the win came from a DIFFERENT basin (construct_z3).
    Population + recombination is the principled basin-explorer -> escapes exactly that trap.
  - It SUBSUMES the others: construct_z3 / anytime / ilp assignments are SEED chromosomes; beam is the elite
    polish; the per-bay packer is the decoder. One frame, all components.
  - Feasible in 60s only with a FAST decoder: the compiled C placer (placer.c place_one) does the per-bay
    bottom-left greedy in a single pass (~ms/bay) instead of pack_bay's multi-entry Python probe.

Chromosome = 2n keys: keys[:n] = bay choice (scaled into the block's FITTING bays), keys[n:] = per-bay order.
Decoder = C greedy single-pass per bay. Fitness = true weighted objective. Parallel decode across workers.
solve_brkga2(prob, budget, seeds=[{block:bay}...], workers, seed, verbose) -> sol dict.
"""
import time, math, ctypes
import numpy as np
from packer import block_meta
from solve import build_ops
from anytime import _Scorer
import beam_polish

_G = {}


def _init_worker(prob):
    _G["prob"] = prob
    bays = prob["bays"]; braw = prob["blocks"]
    _G["bays"] = bays; _G["nb"] = len(bays); _G["n"] = len(braw)
    MB = {i: block_meta(braw[i], i) for i in range(len(braw))}
    for i in MB:
        MB[i]["proc"] = max(1, MB[i]["proc"])
    _G["MB"] = MB
    _G["ORS"] = {i: [(o[1], o[2], o[3], o[4]) for o in MB[i]["orients"]] for i in MB}
    _G["ORA"] = {i: np.asarray(_G["ORS"][i], dtype=np.float64).reshape(-1) for i in MB}
    _G["OI"] = {i: [o[0] for o in MB[i]["orients"]] for i in MB}
    _G["due"] = {i: braw[i]["due_date"] for i in range(len(braw))}
    fit = {}
    for i in MB:
        fb = [j for j, bay in enumerate(bays)
              if any(math.ceil(-lx0) <= math.floor(bay["width"] - lx1) and
                     math.ceil(-ly0) <= math.floor(bay["height"] - ly1)
                     for (lx0, ly0, lx1, ly1) in _G["ORS"][i])]
        fit[i] = fb or list(range(_G["nb"]))
    _G["fit"] = fit
    _G["sc"] = _Scorer(prob)
    _G["lib"] = beam_polish._load_lib()
    _G["DPTR"] = ctypes.POINTER(ctypes.c_double)
    _G["out"] = (ctypes.c_double * 4)()


def _greedy_bay(j, order):
    """C single-pass bottom-left pack of `order` in bay j -> placed [(bid,x,y,REAL_oi,e,f)]."""
    bays = _G["bays"]; ORS = _G["ORS"]; ORA = _G["ORA"]; MB = _G["MB"]; OI = _G["OI"]
    lib = _G["lib"]; DPTR = _G["DPTR"]; out = _G["out"]
    W, H = int(bays[j]["width"]), int(bays[j]["height"])
    placed = []
    for k in order:
        p = MB[k]["proc"]
        if lib:
            act = np.empty((len(placed), 6), dtype=np.float64)
            for r, (bid, x, y, pos, e, f) in enumerate(placed):
                a = ORS[bid][pos]; act[r] = (x + a[0], y + a[1], x + a[2], y + a[3], e, f)
            lib.place_one(act.ctypes.data_as(DPTR), len(placed),
                          ORA[k].ctypes.data_as(DPTR), len(ORS[k]),
                          MB[k]["release"], p, W, H, out)
            x, y, pos, e = out[0], out[1], int(out[2]), out[3]
        else:
            active = [(x + ORS[b][o][0], y + ORS[b][o][1], x + ORS[b][o][2], y + ORS[b][o][3], e, f)
                      for (b, x, y, o, e, f) in placed]
            x, y, pos, e = beam_polish._py_place(active, ORS[k], MB[k]["release"], p, W, H)
        placed.append((k, x, y, pos, e, e + p))
    return [(b, x, y, pos, e, f) for (b, x, y, pos, e, f) in placed]


def _decode(chrom, real_oi=False):
    n = _G["n"]; nb = _G["nb"]; fit = _G["fit"]; OI = _G["OI"]
    by_bay = {j: [] for j in range(nb)}
    for i in range(n):
        fb = fit[i]; j = fb[min(len(fb) - 1, int(chrom[i] * len(fb)))]
        by_bay[j].append(i)
    place = {}
    for j in range(nb):
        order = sorted(by_bay[j], key=lambda i: chrom[n + i])
        for (b, x, y, pos, e, f) in _greedy_bay(j, order):
            place[b] = [j, x, y, (OI[b][pos] if real_oi else pos), e, f]
    return place


def _fitness(chrom):
    # decode with POSITION indices; _Scorer.obj only needs bay/timing, orient index irrelevant to Z1/Z2/Z3
    return _G["sc"].obj({i: tuple(v) for i, v in _decode(chrom).items()})


def _seed_chrom(assign, rng_vals, n):
    """encode a {block:bay} assignment (+ EDD order) as a chromosome. EDD tie-break MUST match the decoder's
    greedy (due, release) or the seed decodes to a worse packing (greedy is order-sensitive)."""
    fit = _G["fit"]; due = _G["due"]; MB = _G["MB"]
    c = list(rng_vals)
    by_bay = {}
    for i, bay in assign.items():
        fb = fit[i]
        c[i] = (fb.index(bay) + 0.5) / len(fb) if bay in fb else c[i]
        by_bay.setdefault(bay, []).append(i)
    for bay, ids in by_bay.items():
        ids.sort(key=lambda i: (due[i], MB[i]["release"]))
        for pos, i in enumerate(ids):
            c[n + i] = (pos + 0.5) / max(1, len(ids))
    return c


def solve_brkga2(prob, budget, seeds=None, seed_sols=None, workers=4, seed=0, verbose=False, beam_final=True,
                 rho=0.70, pm_frac=0.15):
    # rho (elite-gene inheritance prob) and pm_frac (mutant fraction) shape the explore<->exploit balance.
    # The 4 PARALLEL islands pass DISTINCT (rho, pm_frac) so they converge to COMPLEMENTARY assignment basins
    # (sharing only RNG seed made them correlated -> all 4 could miss the good basin in a given run, the
    # prob_20 inter-run variance). Diverse lenses make best-of-4 reliably cover the good basin.
    import random as _r
    rng = _r.Random(seed); t0 = time.time()
    _init_worker(prob)
    n = _G["n"]; L = 2 * n
    pop = max(16, min(48, 2000 // max(1, n) * 4))
    pe = max(1, int(0.20 * pop)); pm = max(1, int(pm_frac * pop))

    def rand_chrom():
        return [rng.random() for _ in range(L)]

    population = [rand_chrom() for _ in range(pop)]
    seeds = seeds or []
    for s, asg in enumerate(seeds[:pe]):                  # inject seeds as elite chromosomes
        if asg:
            population[s] = _seed_chrom(asg, rand_chrom(), n)

    pool = None
    if workers > 1:
        try:
            import multiprocessing as mp
            pool = mp.Pool(processes=workers, initializer=_init_worker, initargs=(prob,))
        except Exception:
            pool = None

    def evaluate(chroms):
        if pool is not None:
            try:
                return pool.map(_fitness, chroms)
            except Exception:
                pass
        return [_fitness(c) for c in chroms]

    fits = evaluate(population)
    order = sorted(range(pop), key=lambda i: fits[i])
    population = [population[i] for i in order]; fits = [fits[i] for i in order]
    best_f = fits[0]; best_c = list(population[0]); gen = 0
    if verbose:
        print(f"[brkga2] seeded best={best_f:.0f} pop={pop} (t={time.time()-t0:.0f}s)", flush=True)

    reserve = 6.0 if beam_final else 0.0
    while time.time() - t0 < budget - reserve:           # plain BRKGA: 15% mutants/gen keep it improving on
        gen += 1                                          # long budgets (validated scaling); explicit restart
        elites = population[:pe]                           # was net-negative (disrupted congested basins + added
        new = [list(e) for e in elites] + [rand_chrom() for _ in range(pm)]   # run-variance) so removed.
        while len(new) < pop:
            ea = elites[rng.randrange(pe)]
            eb = population[pe + rng.randrange(pop - pe)] if pop > pe else population[rng.randrange(pop)]
            new.append([ea[k] if rng.random() < rho else eb[k] for k in range(L)])
        population = new
        nf = evaluate(population[pe:]); fits = fits[:pe] + list(nf)
        order = sorted(range(pop), key=lambda i: fits[i])
        population = [population[i] for i in order]; fits = [fits[i] for i in order]
        if fits[0] < best_f - 1e-9:
            best_f = fits[0]; best_c = list(population[0])
        if verbose and gen % 10 == 0:
            print(f"[brkga2] gen={gen} t={time.time()-t0:.0f}s best={best_f:.0f}", flush=True)
    if pool is not None:
        pool.close(); pool.join()

    place = _decode(best_c, real_oi=True)
    sol = build_ops({i: tuple(place[i]) for i in place})
    import utils
    # SAFETY best-of vs the original seed solutions: a seed chromosome can decode slightly worse than the
    # solution it came from (greedy is order-sensitive), so never let the GA lose a seed.
    bestobj = utils.check_feasibility(prob, sol)["objective"]
    for ss in (seed_sols or []):
        try:
            r = utils.check_feasibility(prob, ss)
            if r["feasible"] and r["objective"] < bestobj - 1e-9:
                sol, bestobj = ss, r["objective"]
        except Exception:
            pass
    if beam_final:                                        # elite polish: beam re-pack each bay
        try:
            pol = beam_polish.beam_polish(prob, sol, utils, time_budget=max(2.0, budget - (time.time() - t0)))
            r = utils.check_feasibility(prob, pol)
            if r["feasible"] and r["objective"] < bestobj - 1e-9:
                sol = pol
        except Exception:
            pass
    return sol
