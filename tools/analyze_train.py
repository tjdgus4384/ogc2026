"""Extract the REAL training distribution. No assumptions — measure everything."""
import json, glob, pathlib, statistics as st

ROOT = pathlib.Path(__file__).resolve().parent.parent
files = sorted(glob.glob(str(ROOT / "data" / "train" / "*.json")),
               key=lambda p: int(pathlib.Path(p).stem.split("_")[1]))


def bb(layer):
    xs = [v[0] for v in layer]; ys = [v[1] for v in layer]
    return min(xs), min(ys), max(xs), max(ys)


print(f"{'inst':10s} {'n':>4s} {'m':>2s} {'bays(WxH)':28s} {'w1':>8s} {'w2':>6s} {'w3':>6s}")
allstats = []
for f in files:
    name = pathlib.Path(f).stem
    try:
        p = json.loads(pathlib.Path(f).read_text())
    except Exception as e:
        print(f"{name:10s} !! UNREADABLE: {type(e).__name__} {e}")
        continue
    n = len(p["blocks"]); m = len(p["bays"])
    bays = [(b["width"], b["height"]) for b in p["bays"]]
    bstr = " ".join(f"{w}x{h}" for w, h in bays[:4]) + (".." if m > 4 else "")
    w = p["weights"]
    print(f"{name:10s} {n:>4d} {m:>2d} {bstr:28s} {w['w1']:>8} {w['w2']:>6} {w['w3']:>6}")
    # per-block stats
    Ks, Os, Ps, slacks, lds, prefsum, aspects = [], [], [], [], [], [], []
    overhang = 0; nonconv = 0
    for blk in p["blocks"]:
        o0 = blk["shape"][0]
        Ks.append(len(o0["layers"])); Os.append(len(blk["shape"]))
        Ps.append(blk["processing_time"])
        slacks.append(blk["due_date"] - blk["release_time"] - blk["processing_time"])
        lds.append(blk["workload"]); prefsum.append(sum(blk["bay_preferences"]))
        # overhang: any orientation, an upper layer bbox exceeds layer0 bbox
        for o in blk["shape"]:
            ls = o["layers"]
            if len(ls) >= 2:
                w0 = bb(ls[0]); a0 = (w0[2]-w0[0], w0[3]-w0[1])
                for up in ls[1:]:
                    wu = bb(up)
                    if (wu[2]-wu[0]) > a0[0]+1e-6 or (wu[3]-wu[1]) > a0[1]+1e-6:
                        overhang += 1; break
                else:
                    continue
                break
        # non-convex layer0 (vertex count > 4 as crude proxy)
        if len(o0["layers"][0]) > 4:
            nonconv += 1
    bayareas = [w*h for w, h in bays]
    aspect = [round(w/h, 1) for w, h in bays]
    allstats.append({
        "name": name, "n": n, "m": m, "w": w,
        "K": (min(Ks), int(st.median(Ks)), max(Ks)),
        "O": (min(Os), int(st.median(Os)), max(Os)),
        "P": (min(Ps), int(st.median(Ps)), max(Ps)),
        "slack": (min(slacks), int(st.median(slacks)), max(slacks)),
        "neg_slack": sum(1 for s in slacks if s < 0),
        "load": (min(lds), int(st.median(lds)), max(lds)),
        "prefsum_uniq": sorted(set(prefsum)),
        "overhang_frac": round(overhang/n, 2),
        "nonconv_frac": round(nonconv/n, 2),
        "bay_aspect": aspect, "bay_area": bayareas,
    })

print("\n=== per-instance detail (min/median/max) ===")
for s in allstats:
    print(f"\n{s['name']}  n={s['n']} m={s['m']}  weights={s['w']}")
    print(f"  layers K={s['K']}  orient O={s['O']}  proc P={s['P']}  workload={s['load']}")
    print(f"  slack(D-R-P)={s['slack']}  blocks_with_neg_slack(forced-tardy)={s['neg_slack']}")
    print(f"  pref_sums_seen={s['prefsum_uniq']}  overhang_frac={s['overhang_frac']}  nonconvex_frac={s['nonconv_frac']}")
    print(f"  bay_aspect(W/H)={s['bay_aspect']}  bay_areas={s['bay_area']}")

print("\n=== AGGREGATE across all train ===")
print("  n:", sorted(s['n'] for s in allstats))
print("  m:", sorted(s['m'] for s in allstats))
print("  w1:", sorted(s['w']['w1'] for s in allstats))
print("  w2:", sorted(s['w']['w2'] for s in allstats))
print("  w3:", sorted(s['w']['w3'] for s in allstats))
print("  overhang_frac:", sorted(s['overhang_frac'] for s in allstats))
print("  nonconvex_frac:", sorted(s['nonconv_frac'] for s in allstats))
print("  any neg-slack (forced tardy):", [s['name'] for s in allstats if s['neg_slack']>0])
