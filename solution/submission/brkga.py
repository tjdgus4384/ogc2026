"""brkga.py -- Approach B: Biased Random-Key Genetic Algorithm (OGC 2026).

Global search over (bay-assignment, per-bay insertion order) via random keys. Decoder = the deterministic
footprint-disjoint over-time packer (packer.pack_bay) placing each bay's blocks in decoded order, each at
its earliest-fitting (entry, position). Fitness = true weighted objective (Z1-dominated). BRKGA recombines
whole assignments, so it escapes the local optima the 5 SA operators cannot leave; it is the literature
SOTA decoder pattern for irregular/temporal packing. Anytime; parallel decode across the 4 cores.

Chromosome = 2n floats in [0,1): keys[0:n] = assignment, keys[n:2n] = per-bay ordering priority.

Public: solve_brkga(prob, budget, seed=0, pop=None, workers=4, verbose=False, warm=None) -> sol dict.
  warm (optional) = a {block_id:(bay,order_rank)} seed to inject one elite chromosome (e.g. ALNS best).
"""
import time, math, random
from packer import block_meta, pack_bay
from solve import build_ops
from anytime import _Scorer

_G = {}


def _init_worker(prob):
    _G["prob"] = prob
    _G["bays"] = prob["bays"]
    _G["nb"] = len(prob["bays"])
    _G["metas"] = {i: block_meta(prob["blocks"][i], i) for i in range(len(prob["blocks"]))}
    fit = {}
    for i, mt in _G["metas"].items():
        fb = []
        for j, bay in enumerate(_G["bays"]):
            for (_, lx0, ly0, lx1, ly1) in mt["orients"]:
                if math.ceil(-lx0) <= math.floor(bay["width"] - lx1) and \
                   math.ceil(-ly0) <= math.floor(bay["height"] - ly1):
                    fb.append(j); break
        fit[i] = fb or list(range(_G["nb"]))
    _G["fit"] = fit
    _G["sc"] = _Scorer(prob)
    _G["n"] = len(prob["blocks"])


def _decode_place(chrom):
    """keys -> place dict {id:[bay,x,y,oi,e,f]}."""
    n = _G["n"]; nb = _G["nb"]; fit = _G["fit"]; metas = _G["metas"]; bays = _G["bays"]
    by_bay = {j: [] for j in range(nb)}
    for i in range(n):
        fb = fit[i]
        j = fb[min(len(fb) - 1, int(chrom[i] * len(fb)))]
        by_bay[j].append(i)
    place = {}
    for j in range(nb):
        ids = sorted(by_bay[j], key=lambda i: chrom[n + i])
        order_metas = [metas[i] for i in ids]
        for (bid, x, y, oi, e, f) in pack_bay(bays[j]["width"], bays[j]["height"], order_metas):
            place[bid] = [j, x, y, oi, e, f]
    return place


def _fitness(chrom):
    return _G["sc"].obj(_decode_place(chrom))


def solve_brkga(prob, budget, seed=0, pop=None, workers=4, verbose=False, warm=None):
    import random as _r
    rng = _r.Random(seed)
    t0 = time.time()
    _init_worker(prob)                                   # also primes the main process
    n = _G["n"]
    L = 2 * n
    if pop is None:
        pop = max(20, min(60, 1500 // max(1, n) * 4))    # smaller pop for big n
    pe = max(1, int(0.20 * pop))                         # elites
    pm = max(1, int(0.15 * pop))                         # mutants
    rho = 0.70                                           # elite allele inheritance prob

    def rand_chrom():
        return [rng.random() for _ in range(L)]

    population = [rand_chrom() for _ in range(pop)]
    # warm-start one chromosome from a known assignment+order
    if warm is not None:
        c = rand_chrom()
        ranks = {}
        for i, (bay, rank) in warm.items():
            fb = _G["fit"][i]
            c[i] = (fb.index(bay) + 0.5) / len(fb) if bay in fb else c[i]
            ranks.setdefault(bay, []).append((rank, i))
        for bay, lst in ranks.items():
            lst.sort()
            for pos, (_, i) in enumerate(lst):
                c[n + i] = (pos + 0.5) / max(1, len(lst))
        population[0] = c

    # parallel pool (optional)
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
    ranked = sorted(zip(fits, range(pop)), key=lambda x: x[0])
    population = [population[i] for _, i in ranked]
    fits = [f for f, _ in ranked]
    best_f = fits[0]; best_c = list(population[0])
    gen = 0

    while time.time() - t0 < budget:
        gen += 1
        elites = population[:pe]
        new = [list(e) for e in elites]                  # elites carry over
        new += [rand_chrom() for _ in range(pm)]         # mutants
        while len(new) < pop:                            # biased crossover
            ea = elites[rng.randrange(pe)]
            eb = population[pe + rng.randrange(pop - pe)] if pop > pe else population[rng.randrange(pop)]
            child = [ea[k] if rng.random() < rho else eb[k] for k in range(L)]
            new.append(child)
        population = new
        newfits = evaluate(population[pe:])              # elites' fitness already known
        fits = [f for f in fits[:pe]] + list(newfits)
        ranked = sorted(zip(fits, range(pop)), key=lambda x: x[0])
        population = [population[i] for _, i in ranked]
        fits = [f for f, _ in ranked]
        if fits[0] < best_f - 1e-9:
            best_f = fits[0]; best_c = list(population[0])
        if verbose and gen % 5 == 0:
            print(f"[brkga] gen={gen} t={time.time()-t0:.0f}s best={best_f:.0f} pop={pop}", flush=True)

    if pool is not None:
        pool.close(); pool.join()
    place = _decode_place(best_c)
    return build_ops({i: tuple(place[i]) for i in place})
