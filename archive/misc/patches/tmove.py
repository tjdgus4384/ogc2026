"""Time-indexed assignment repair (sec 191).

The global area-time model was too coarse: a bay saturated ON AVERAGE can still
have idle windows, which is exactly why the sec-187 ejection chain worked and a
global-capacity IP did not. So keep the SCHEDULE fixed -- then Z1 cannot worsen
by construction -- and screen every candidate relocation against the per-instant
area capacity it would actually need:

    for all t in [e_i, f_i):  load_j(t) + area_i <= delta_j * bayArea_j

delta_j is the peak instantaneous utilisation the packer has ALREADY achieved in
bay j on this instance, so the screen admits only moves the packer has
demonstrated it can build. Moves are then realised with a forced on-time
reinsertion and kept only if the true objective improves.
"""
import math

def _pa(l):
    s = 0.0
    for k in range(len(l)):
        x1, y1 = l[k]; x2, y2 = l[(k + 1) % len(l)]
        s += x1 * y2 - x2 * y1
    return abs(s) * 0.5

def repair_assignment(p, eng, n, passes=8, verbose=False):
    B = p['blocks']; bays = p['bays']; m = len(bays)
    pref = [b['bay_preferences'] for b in B]
    Smax = [max(x) for x in pref]
    wl = [b['workload'] for b in B]
    fa = [_pa(b['shape'][0]['layers'][0]) for b in B]
    bA = [bb['width'] * bb['height'] for bb in bays]
    avg = sum(bA) / m
    u = [avg / a for a in bA]
    w1, w2, w3 = p['weights']['w1'], p['weights']['w2'], p['weights']['w3']
    kept = 0
    for _ in range(passes):
        blk = [eng.get_block(i) for i in range(n)]
        if not all(b[6] for b in blk):
            break
        T = max(b[5] for b in blk) + 1
        # per-instant area load and the peak utilisation actually achieved
        occ = [[0.0] * T for _ in range(m)]
        for i in range(n):
            for t in range(blk[i][4], blk[i][5]):
                occ[blk[i][0]][t] += fa[i]
        cap = [max(occ[j]) for j in range(m)]        # demonstrated peak, per bay
        L = [0.0] * m
        z3 = 0
        for i in range(n):
            L[blk[i][0]] += wl[i]
            z3 += Smax[i] - pref[i][blk[i][0]]
        def z2of(Lv):
            z = 0.0
            for a in range(m):
                for b in range(a + 1, m):
                    z = max(z, abs(u[a] * Lv[a] - u[b] * Lv[b]))
            return math.floor(z)
        z2 = z2of(L)
        cands = []
        for i in range(n):
            a = blk[i][0]
            for j in range(m):
                if j == a:
                    continue
                room = all(occ[j][t] + fa[i] <= cap[j] + 1e-9
                           for t in range(blk[i][4], blk[i][5]))
                if not room:
                    continue
                L2 = list(L); L2[a] -= wl[i]; L2[j] += wl[i]
                d = w2 * (z2of(L2) - z2) + w3 * (pref[i][a] - pref[i][j])
                if d < 0:
                    cands.append((d, i, j))
        if not cands:
            break
        cands.sort()
        got = False
        for d, i, j in cands[:16]:
            snap = [v for k in range(n) for v in eng.get_block(k)]
            base = eng.objective()[3]
            if eng.move_block(i, j, True, 7 + kept) and \
                    eng.objective()[3] < base - 1e-9:
                kept += 1; got = True; break
            eng.set_solution(snap)
        if not got:
            break
    return kept
