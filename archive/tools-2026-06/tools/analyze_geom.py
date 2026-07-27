"""Test the 1-D-DSA hypothesis and measure real concurrency pressure on train data."""
import json, glob, pathlib, statistics as st

ROOT = pathlib.Path(__file__).resolve().parent.parent
files = sorted(glob.glob(str(ROOT / "data" / "train" / "*.json")),
               key=lambda p: int(pathlib.Path(p).stem.split("_")[1]))


def bb(layer):
    xs = [v[0] for v in layer]; ys = [v[1] for v in layer]
    return max(xs)-min(xs), max(ys)-min(ys)


for f in files:
    name = pathlib.Path(f).stem
    try:
        p = json.loads(pathlib.Path(f).read_text())
    except Exception:
        print(f"{name}: UNREADABLE (skip)"); continue
    bays = [(b["width"], b["height"]) for b in p["bays"]]
    Hs = [h for _, h in bays]; medH = st.median(Hs); maxH = max(Hs)
    # For each block: min achievable bbox height over orientations (best "lay flat") and min width.
    minheights = []; can_short = 0  # can_short = block fits under <=0.5*maxH in some orientation
    for blk in p["blocks"]:
        hopts = []
        for o in blk["shape"]:
            # footprint bbox over all layers
            xs=[]; ys=[]
            for l in o["layers"]:
                for x,y in l: xs.append(x); ys.append(y)
            hopts.append(max(ys)-min(ys))
        mh = min(hopts); minheights.append(mh)
        if mh <= 0.5*maxH: can_short += 1
    n = len(p["blocks"])
    # fraction of blocks whose MINIMAL height still exceeds half the largest bay height
    forced_tall = sum(1 for h in minheights if h > 0.5*maxH)/n
    med_minh = st.median(minheights)
    # concurrency pressure: peak overlap of [R, D] windows (space-free upper bound on demand), /m
    evs=[]
    horizon=0
    for blk in p["blocks"]:
        R=blk["release_time"]; D=blk["due_date"]; horizon=max(horizon,D)
        evs.append((R,1)); evs.append((D,-1))
    evs.sort(key=lambda x:(x[0],x[1]))
    cur=peak=0
    for _,d in evs:
        cur+=d; peak=max(peak,cur)
    m=len(bays)
    print(f"{name:9s} n={n:3d} m={m} bayH={Hs} | med_min_blockH={med_minh:.1f} "
          f"forced_tall(minH>0.5*maxH)={forced_tall:.0%} | horizon={horizon} "
          f"peak_window_overlap={peak} ~/m={peak/m:.0f}")
