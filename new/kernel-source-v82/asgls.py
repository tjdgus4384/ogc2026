"""Dependency-free assignment optimiser (sec 190).

Z2 and Z3 are functions of the assignment vector alone, so the w2/w3 part of the
objective is a small combinatorial problem -- no solver needed. Starting from the
incumbent assignment (feasible by construction) and accepting only feasible
strict improvements under relocate+swap keeps this exact in spirit and free of
the ortools dependency that once broke a whole submission (sec 69).

Capacity: per-bay area-time demand <= delta * bayArea * H, with delta taken from
what the packer has ALREADY achieved on this instance -- measured, not tuned.
"""
import math

def _pa(l):
    s = 0.0
    for k in range(len(l)):
        x1, y1 = l[k]; x2, y2 = l[(k + 1) % len(l)]
        s += x1 * y2 - x2 * y1
    return abs(s) * 0.5

def prep(p):
    B = p['blocks']; bays = p['bays']; m = len(bays)
    pr = [b['bay_preferences'] for b in B]
    d = {
        'm': m, 'n': len(B), 'pref': pr,
        'Smax': [max(x) for x in pr],
        'wl': [b['workload'] for b in B],
        'area': [bb['width'] * bb['height'] for bb in bays],
        'fa': [_pa(b['shape'][0]['layers'][0]) for b in B],
        'P': [max(1, int(math.ceil(b['processing_time'] - 1e-9))) for b in B],
        'w2': p['weights']['w2'], 'w3': p['weights']['w3'],
    }
    avg = sum(d['area']) / m
    d['u'] = [avg / a for a in d['area']]
    fits = []
    for b in B:
        row = []
        for j in range(m):
            ok = False
            for o in b['shape']:
                L = [l for l in o['layers'] if len(l) >= 3]
                if not L:
                    continue
                xs = [v[0] for l in L for v in l]; ys = [v[1] for l in L for v in l]
                if (max(xs) - min(xs) <= bays[j]['width'] and
                        max(ys) - min(ys) <= bays[j]['height']):
                    ok = True
            row.append(ok)
        fits.append(row)
    d['fits'] = fits
    return d

def cost(d, asg):
    m = d['m']
    L = [0.0] * m; z3 = 0
    for i, j in enumerate(asg):
        L[j] += d['wl'][i]
        z3 += d['Smax'][i] - d['pref'][i][j]
    z2 = 0.0
    for a in range(m):
        for b in range(a + 1, m):
            z2 = max(z2, abs(d['u'][a] * L[a] - d['u'][b] * L[b]))
    return d['w2'] * math.floor(z2) + d['w3'] * z3

def optimise(d, asg0, capE, rounds=6):
    """capE[j] = allowed area-time for bay j"""
    n, m = d['n'], d['m']
    asg = list(asg0)
    E = [0.0] * m
    for i, j in enumerate(asg):
        E[j] += d['fa'][i] * d['P'][i]
    best = cost(d, asg)
    for _ in range(rounds):
        moved = False
        for i in range(n):
            a = asg[i]; e = d['fa'][i] * d['P'][i]
            for j in range(m):
                if j == a or not d['fits'][i][j] or E[j] + e > capE[j]:
                    continue
                asg[i] = j
                c = cost(d, asg)
                if c < best - 1e-9:
                    best = c; E[a] -= e; E[j] += e; a = j; moved = True
                else:
                    asg[i] = a
        for i in range(n):
            for k in range(i + 1, n):
                a, b = asg[i], asg[k]
                if a == b or not d['fits'][i][b] or not d['fits'][k][a]:
                    continue
                ei = d['fa'][i] * d['P'][i]; ek = d['fa'][k] * d['P'][k]
                if E[a] - ei + ek > capE[a] or E[b] - ek + ei > capE[b]:
                    continue
                asg[i] = b; asg[k] = a
                c = cost(d, asg)
                if c < best - 1e-9:
                    best = c
                    E[a] += ek - ei; E[b] += ei - ek; moved = True
                else:
                    asg[i] = a; asg[k] = b
        if not moved:
            break
    return asg, best
