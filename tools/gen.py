"""
gen.py -- Synthetic OGC2026 instance generator (spec-faithful PROXY).

NOT the hidden distribution. A controllable stand-in to (a) stress the feasibility
floor, (b) measure baseline failure modes & objective gap, (c) test the
wide-short-bay -> 1-D DSA hypothesis and the interlock (Psi\\Phi) value, so the
committed design's free constants can be set now and recalibrated when the real
training instances arrive.

Spec rules honored:
 - bays: width x height integers, indexed 0..m-1.
 - blocks: layered polygons; layer 0 first vertex == reference point (0,0) (re-anchored).
 - orientations: rigid rotations (0/90/180/270), each re-anchored so layer0[0]==(0,0).
 - release/due/proc integers; D-R >= P (slack); workload integer; prefs integers summing 100.
 - weights w1,w2,w3 nonnegative integers (regime-controlled).
 - every block fits in >=1 bay at >=1 orientation (generator enforces).
"""
import json, random, math


def _rot(verts, k):
    """Rotate vertex list by k*90 deg CCW."""
    out = []
    for x, y in verts:
        for _ in range(k % 4):
            x, y = -y, x
        out.append([x, y])
    return out


def _anchor(layers):
    """Translate all layers so layer0's FIRST vertex is exactly (0,0) (the reference point)."""
    rx, ry = layers[0][0]
    return [[[round(x - rx, 4), round(y - ry, 4)] for x, y in l] for l in layers]


def _bbox(layers):
    vs = [v for l in layers for v in l]
    xs = [v[0] for v in vs]; ys = [v[1] for v in vs]
    return min(xs), min(ys), max(xs), max(ys)


def _base_shape(rng, w, h, overhang):
    """One orientation's layers in local coords. overhang=True -> wider/extra upper layer."""
    nl = rng.choice([1, 1, 2, 2, 3])
    rect = [[0, 0], [w, 0], [w, h], [0, h]]
    shape = rng.random()
    if shape < 0.45:                      # rectangle
        l0 = rect
    elif shape < 0.8:                     # L-shape (non-convex)
        cw, ch = w * rng.uniform(0.4, 0.7), h * rng.uniform(0.4, 0.7)
        l0 = [[0, 0], [w, 0], [w, h - ch], [cw, h - ch], [cw, h], [0, h]]
    else:                                 # T / notch (non-convex)
        nx = w * rng.uniform(0.3, 0.5)
        l0 = [[0, 0], [w, 0], [w, h], [w - nx, h], [w - nx, h * 0.5], [nx, h * 0.5], [nx, h], [0, h]]
    layers = [l0]
    for li in range(1, nl):
        if overhang and li == nl - 1:     # widen the top layer to create a true overhang
            ow = min(w * rng.uniform(1.3, 1.8), w + 6)
            layers.append([[0, 0], [ow, 0], [ow, h], [0, h]])
        else:
            layers.append([[round(p[0], 4), round(p[1], 4)] for p in l0])
    return layers


def gen_instance(seed, n, m, tightness, regime, overhang_frac, bay_aspect="wide_short"):
    rng = random.Random(seed)
    # --- bays ---
    bays = []
    for j in range(m):
        if bay_aspect == "wide_short":
            W = rng.randint(70, 150); H = rng.randint(12, 28)
        elif bay_aspect == "square":
            s = rng.randint(30, 60); W, H = s, rng.randint(s - 5, s + 5)
        else:
            W = rng.randint(40, 90); H = rng.randint(40, 90)
        bays.append({"width": W, "height": H})
    minW = min(b["width"] for b in bays); minH = min(b["height"] for b in bays)
    horizon = max(10, int(n * tightness[0]))
    blocks = []
    for i in range(n):
        # block footprint must fit smallest bay comfortably (leave room for overhang growth)
        bw = rng.uniform(0.12, 0.55) * minW
        bh = rng.uniform(0.45, 0.95) * minH       # wide-short -> blocks span most of H
        bw = max(2.0, min(bw, minW - 8)); bh = max(2.0, min(bh, minH - 1))
        ov = rng.random() < overhang_frac
        base = _base_shape(rng, bw, bh, ov)
        Oi = rng.choice([1, 2, 4])
        shape = []
        for o in range(Oi):
            rotk = o if Oi == 4 else (0 if o == 0 else 2)
            lay = _anchor([_rot(l, rotk) for l in base])
            # keep only orientations that fit some bay
            mnx, mny, mxx, mxy = _bbox(lay)
            if (mxx - mnx) <= minW and (mxy - mny) <= minH:
                shape.append({"orientation": o, "layers": lay})
        if not shape:
            lay = _anchor(base)
            shape = [{"orientation": 0, "layers": lay}]
        P = rng.randint(3, 10)
        R = rng.randint(0, horizon)
        slack = rng.randint(tightness[1], tightness[2])     # D = R + P + slack
        D = R + P + slack
        # prefs: random 3-split summing 100 (pad/truncate to m)
        cuts = sorted(rng.sample(range(1, 100), max(1, m - 1))) if m > 1 else []
        parts = [a - b for a, b in zip(cuts + [100], [0] + cuts)] if m > 1 else [100]
        prefs = (parts + [0] * m)[:m]
        prefs[rng.randrange(m)] += 100 - sum(prefs)          # fix rounding to sum 100
        blocks.append({
            "release_time": R, "due_date": D, "processing_time": P,
            "workload": rng.randint(5, 30), "bay_preferences": prefs, "shape": shape,
        })
    # --- weights regime ---
    if regime == "z1":      w = {"w1": rng.randint(15000, 30000), "w2": rng.randint(5, 20), "w3": rng.randint(100, 400)}
    elif regime == "bal":   w = {"w1": rng.randint(50, 200), "w2": rng.randint(50, 200), "w3": rng.randint(50, 200)}
    elif regime == "pref":  w = {"w1": rng.randint(10, 100), "w2": rng.randint(5, 30), "w3": rng.randint(500, 2000)}
    else:                   w = {"w1": 1, "w2": 1, "w3": 1}
    return {"name": f"synth_s{seed}_n{n}_m{m}_{regime}", "bays": bays, "blocks": blocks, "weights": w}


if __name__ == "__main__":
    import pathlib
    out = pathlib.Path(__file__).resolve().parent.parent / "data" / "synth"
    out.mkdir(parents=True, exist_ok=True)
    grid = []
    seed = 0
    for n in (10, 25, 60, 120):
        for m in (2, 3):
            for regime in ("z1", "bal", "pref"):
                for tight in [(1.0, 0, 4), (1.2, 2, 12)]:   # tight vs loose
                    for ovf in (0.0, 0.5):
                        seed += 1
                        inst = gen_instance(seed, n, m, tight, regime, ovf)
                        f = out / f"{inst['name']}_t{int(tight[1])}_o{int(ovf*10)}.json"
                        f.write_text(json.dumps(inst))
                        grid.append(str(f.name))
    print(f"wrote {len(grid)} instances to {out}")
