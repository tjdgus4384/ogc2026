"""beam_polish.py -- per-bay BEAM SEARCH re-scheduling polish (the research-grade congested lever).

Beam search over entry-order with incremental bottom-left placement BEATS anytime's CP-repair per bay
(measured -12 to -15% on congested bays prob_27/38). The hot placement loop is a COMPILED C extension
(placer.c, ~34x faster than pure Python -> full beam viable). On a solution, re-schedule each bay's blocks
(same assignment -> Z2/Z3 unchanged, bays independent -> safe) and keep per-bay Z1 improvements.

Robust: if the C extension can't be built/loaded (gcc missing / firejail), falls back to the Python
placement -> still correct, just slower (fewer beam nodes). Never worsens (keeps the input solution if no
bay improves)."""
import os, math, time, ctypes
import numpy as np
from packer import block_meta, _best_pos

_LIB = None
_TRIED = False


def _load_lib():
    global _LIB, _TRIED
    if _TRIED:
        return _LIB
    _TRIED = True
    d = os.path.dirname(os.path.abspath(__file__))
    so = os.path.join(d, "placer.so")
    src = os.path.join(d, "placer.c")
    try:
        # (re)compile if the .so is missing OR is a stale build without place_many (added 2026-06-21)
        need = not os.path.exists(so)
        if not need and os.path.exists(so):
            try:
                if not hasattr(ctypes.CDLL(so), "place_many"):
                    need = True
            except Exception:
                need = True
        if need and os.path.exists(src):                       # runtime-compile (gcc available on server)
            os.system(f"gcc -O3 -shared -fPIC -o '{so}' '{src}' 2>/dev/null")
        if os.path.exists(so):
            lib = ctypes.CDLL(so)
            D = ctypes.POINTER(ctypes.c_double); I = ctypes.POINTER(ctypes.c_int)
            lib.place_one.restype = ctypes.c_int
            lib.place_one.argtypes = [D, ctypes.c_int, D, ctypes.c_int,
                                      ctypes.c_double, ctypes.c_double, ctypes.c_int, ctypes.c_int, D]
            if hasattr(lib, "place_many"):
                lib.place_many.restype = None
                lib.place_many.argtypes = [D, ctypes.c_int, D, I, I, D, D, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_int, D]
            _LIB = lib
    except Exception:
        _LIB = None
    return _LIB


def _py_place(active, orients, release, proc, W, H, max_ent=48):
    # active rows = (X0,Y0,X1,Y1,entry,exit); a resident constrains the new block's window [e,e+proc) iff
    # their windows overlap (exit>e && entry<e+proc), not merely exit>e (the old, over-constraining test).
    ent = sorted({release} | {a[5] for a in active if a[5] > release})[:max_ent]
    for e in ent:
        present = [(a[0], a[1], a[2], a[3], a[5]) for a in active if a[5] > e and a[4] < e + proc]
        for mi, (lx0, ly0, lx1, ly1) in enumerate(orients):
            if math.ceil(-lx0) > math.floor(W - lx1) or math.ceil(-ly0) > math.floor(H - ly1):
                continue
            pos = _best_pos(W, H, lx0, ly0, lx1, ly1, present)
            if pos is not None:
                return (pos[0], pos[1], mi, e)
    mx = max((a[5] for a in active), default=0)
    o = orients[0]
    return (max(0, math.ceil(-o[1])), max(0, math.ceil(-o[2])), 0, max(release, mx))


def beam_bay(W, H, ids, MB, ORS, ORA, K=48, C=4, tl=20.0):
    """Beam search a single bay's blocks (entry-order + placement) -> [(bid,x,y,oi,e,f)], min tardiness."""
    lib = _load_lib()
    t0 = time.time()
    edd = sorted(ids, key=lambda i: (MB[i]["due"], MB[i]["release"]))
    out = (ctypes.c_double * 4)() if lib else None
    _MASK = (1 << 64) - 1                                      # order-independent XOR hash of the placed SET
    KH = {i: ((i + 1) * 0x9E3779B97F4A7C15) & _MASK for i in ids}   # -> O(1) beam dedup key (no frozenset copy)

    DPTR = ctypes.POINTER(ctypes.c_double)
    IPTR = ctypes.POINTER(ctypes.c_int)
    has_many = bool(lib) and hasattr(lib, "place_many")
    if has_many:                          # one global orient buffer -> one place_many call per beam STATE
        gori = []; OOFF = {}; OCNT = {}; off = 0
        for i in ids:
            OOFF[i] = off; OCNT[i] = len(ORS[i]); gori.append(ORA[i]); off += len(ORS[i])
        GORI = np.concatenate(gori) if gori else np.zeros(0, dtype=np.float64)
        GORIp = GORI.ctypes.data_as(DPTR)
        RELd = {i: MB[i]["release"] for i in ids}; PROCd = {i: MB[i]["proc"] for i in ids}

    def build_act(placed):
        """active rectangles for a beam state -> numpy (N,6) [X0,Y0,X1,Y1,entry,exit], built ONCE per state."""
        act = np.empty((len(placed), 6), dtype=np.float64)
        for r, (bid, x, y, oi, e, f) in enumerate(placed):
            a = ORS[bid][oi]; act[r] = (x + a[0], y + a[1], x + a[2], y + a[3], e, f)
        return act

    def place_lib(actptr, n, k):
        p = MB[k]["proc"]
        lib.place_one(actptr, n, ORA[k].ctypes.data_as(DPTR), len(ORS[k]),
                      MB[k]["release"], p, W, H, out)
        return out[0], out[1], int(out[2]), out[3], p

    def place_py(placed, k):
        p = MB[k]["proc"]
        active = [(x + ORS[bid][oi][0], y + ORS[bid][oi][1], x + ORS[bid][oi][2],
                   y + ORS[bid][oi][3], e, f) for (bid, x, y, oi, e, f) in placed]
        x, y, oi, e = _py_place(active, ORS[k], MB[k]["release"], p, W, H)
        return x, y, oi, e, p

    def place(placed, k):                                      # used only by the greedy time-out finish
        if lib:
            act = build_act(placed)
            return place_lib(act.ctypes.data_as(DPTR), len(placed), k)
        return place_py(placed, k)

    # state = [td, placed, ps, setkey]. Candidates store only (td, parent_state, entry, setkey) -- NO copy of
    # `placed`/`ps`; the O(depth) copy is DEFERRED to the <=K survivors of each depth (profiled: the per-
    # candidate placed+[...] / ps|{k} copies were 95% of beam_bay's runtime). Dedup uses the XOR setkey (O(1)).
    beam = [[0.0, [], frozenset(), 0]]
    for _ in range(len(ids)):
        cands = []
        for st in beam:
            td, placed, ps, sk = st
            kids = [i for i in edd if i not in ps][:C]
            if not kids:
                continue
            n = len(placed)
            if has_many:                                       # ONE C call places all C candidates
                act = build_act(placed)                        # keep ref alive (GC) through the call
                nk = len(kids)
                ocnt = np.fromiter((OCNT[k] for k in kids), dtype=np.int32, count=nk)
                ooff = np.fromiter((OOFF[k] for k in kids), dtype=np.int32, count=nk)
                rel = np.fromiter((RELd[k] for k in kids), dtype=np.float64, count=nk)
                prc = np.fromiter((PROCd[k] for k in kids), dtype=np.float64, count=nk)
                om = np.empty(nk * 4, dtype=np.float64)
                lib.place_many(act.ctypes.data_as(DPTR), n, GORIp,
                               ocnt.ctypes.data_as(IPTR), ooff.ctypes.data_as(IPTR),
                               rel.ctypes.data_as(DPTR), prc.ctypes.data_as(DPTR), nk, W, H,
                               om.ctypes.data_as(DPTR))
                for idx, k in enumerate(kids):
                    b = idx * 4
                    x, y, oi, e = om[b], om[b + 1], int(om[b + 2]), om[b + 3]
                    f = e + PROCd[k]
                    cands.append((td + max(0, f - MB[k]["due"]), st, (k, x, y, oi, e, f), sk ^ KH[k]))
            else:
                act = build_act(placed) if lib else None       # built ONCE per state; keep ref alive (GC)
                actptr = act.ctypes.data_as(DPTR) if lib else None
                for k in kids:
                    x, y, oi, e, p = place_lib(actptr, n, k) if lib else place_py(placed, k)
                    cands.append((td + max(0, e + p - MB[k]["due"]), st, (k, x, y, oi, e, e + p), sk ^ KH[k]))
        cands.sort(key=lambda s: s[0])
        seen = set(); nb = []
        for (td2, st, entry, skn) in cands:
            if skn in seen:
                continue
            seen.add(skn)
            nb.append([td2, st[1] + [entry], st[2] | {entry[0]}, skn])   # copy only the K survivors
            if len(nb) >= K:
                break
        beam = nb
        if time.time() - t0 > tl:                              # finish greedily from current best
            td, placed, ps, sk = beam[0]
            for k in edd:
                if k in ps:
                    continue
                x, y, oi, e, p = place(placed, k)
                placed.append((k, x, y, oi, e, e + p)); td += max(0, e + p - MB[k]["due"])
            return placed, td
    return beam[0][1], beam[0][0]


def beam_polish(prob, sol, utils, time_budget=60.0, K=48, C=4):
    """Re-schedule each bay's blocks via beam search; keep per-bay improvements. Returns improved sol.
    K=48: a narrow K=8 reached the same Z1 ~10x faster on 100+ block bays, but on ~50-block bays the wider
    K=48 beam packs better (and still finishes in ~19s < the per-bay budget), so it's the safer default."""
    from solve import build_ops
    t0 = time.time()
    braw = prob["blocks"]; bays = prob["bays"]
    MB = {i: block_meta(braw[i], i) for i in range(len(braw))}
    for i in MB:
        MB[i]["proc"] = max(1, MB[i]["proc"])
    ORS = {i: [(o[1], o[2], o[3], o[4]) for o in MB[i]["orients"]] for i in MB}
    ORA = {i: np.asarray(ORS[i], dtype=np.float64).reshape(-1) for i in MB}
    # orients is area-SORTED & AABB-deduped, so its list position != the real orient_idx that utils wants.
    # OI[i][m] maps a beam/placer position m back to the JSON orient_idx (shape index). CRITICAL: without
    # this, blocks whose fitting orientation isn't at list-position==orient_idx get placed OOB.
    OI = {i: [o[0] for o in MB[i]["orients"]] for i in MB}
    base = utils.check_feasibility(prob, sol)
    if not base["feasible"]:
        return sol
    place = {}
    for t, ops in sol["operations"].items():
        for o in ops:
            if o["type"] == "ENTRY":
                place[o["block_id"]] = [o["bay_id"], o["x"], o["y"], o["orient_idx"], int(t), None]
    for t, ops in sol["operations"].items():
        for o in ops:
            if o["type"] == "EXIT":
                place[o["block_id"]][5] = int(t)
    by_bay = {}
    for i, p in place.items():
        by_bay.setdefault(p[0], []).append(i)

    def bay_tard(ids):
        return sum(max(0, place[i][5] - MB[i]["due"]) for i in ids)
    order = sorted(by_bay, key=lambda j: -bay_tard(by_bay[j]))   # worst-tardy bay first
    best_sol = sol; best_obj = base["objective"]
    cur = {i: list(place[i]) for i in place}
    for j in order:
        if time.time() - t0 > time_budget:
            break
        ids = by_bay[j]
        if bay_tard(ids) <= 0 or len(ids) < 2:
            continue
        bl = min(25.0, time_budget - (time.time() - t0))
        packed, _ = beam_bay(int(bays[j]["width"]), int(bays[j]["height"]), ids, MB, ORS, ORA, K=K, C=C, tl=bl)
        trial = {i: list(cur[i]) for i in cur}
        for (bid, x, y, oi, e, f) in packed:
            trial[bid] = [j, x, y, OI[bid][oi], e, f]   # oi is a list-position -> map to real orient_idx
        ts = build_ops({i: tuple(trial[i]) for i in trial})
        r = utils.check_feasibility(prob, ts)
        if r["feasible"] and r["objective"] < best_obj - 1e-9:
            best_obj = r["objective"]; best_sol = ts; cur = trial
    return best_sol
