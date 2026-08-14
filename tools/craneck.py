#!/usr/bin/env python3
"""The last unverified link: do contact packings survive the CRANE rules?

repack proved contact packing fills +13-19pp more area at ground truth --
raster disjointness only. The official rules add crane access: entry descent
at e, exit sweep at f, and rules 4/5 for others' events. Deep interlock could
block those paths and void the density gain.

Test through the kernel's exact judge: rebuild each peak (bay, day) scene
inside the engine -- residents seated at their annealed poses via place_at
(control: they must all pass, they came from a feasible board), then seat the
extra tardy blocks at CONTACT positions (feasible_positions_slide, which
vp-checks every candidate). Report how many extras the kernel certifies at the
peak day vs the corner vocabulary.

    python3 tools/craneck.py prob_6 900 7
"""
import hashlib, json, pathlib, sys
REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "builds" / "ordf"))


def cached(pid, tl, seed):
    sd = str(REPO / "solver")
    so = pathlib.Path(sd) / "sscore.cpython-312-x86_64-linux-gnu.so"
    h = hashlib.sha256(f"{sd}|{so.stat().st_mtime_ns}".encode()).hexdigest()[:12]
    p = REPO / "runs" / "boards" / f"{pid}_tl{int(tl)}_s{seed}_{h}.json"
    return json.loads(p.read_text())["base"]


def main():
    pid, tl = sys.argv[1], float(sys.argv[2])
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    ds = next(d for d in ("preliminary_test","train_final","train")
              if (REPO/"data"/d/f"{pid}.json").exists())
    prob = json.load(open(REPO/"data"/ds/f"{pid}.json"))
    n = len(prob["blocks"]); m = len(prob["bays"])
    import myalgorithm as ma
    eng, D, P = ma._fresh(prob, 4)
    hz = eng.horizon()
    cur = cached(pid, tl, seed)
    tardy = [i for i in range(n) if cur[i*7+6] and cur[i*7+5] > D[i]]
    for j in range(m):
        days = {}
        for i in range(n):
            if cur[i*7+6] and cur[i*7] == j:
                for d in range(cur[i*7+4], cur[i*7+5]):
                    days.setdefault(d, []).append(i)
        if not days:
            continue
        d, live = max(days.items(), key=lambda kv: len(kv[1]))
        # scene: ONLY the same-bay residents, at their annealed poses/times
        part = [0]*(n*7)
        for i in live:
            part[i*7:i*7+7] = cur[i*7:i*7+7]
        eng.set_solution(part); eng.build_occ(hz)
        # control: every resident must pass the exact judge
        bad = sum(1 for i in live if not eng.vp_ok(i))
        extras = [i for i in tardy if i not in set(live)][:30]
        got = {}
        for tag, fn in (("corner", eng.feasible_positions),
                        ("slide", eng.feasible_positions_slide)):
            eng.set_solution(list(part)); eng.build_occ(hz)
            cnt = 0
            for i in extras:
                rows = fn(i, 8, d, j, d)      # exactly day d, bay j
                seated = False
                for q in range(0, len(rows), 5):
                    b2, oi, x, y, e2 = rows[q:q+5]
                    if eng.place_at(i, b2, oi, x, y, e2):
                        seated = True
                        break
                if seated:
                    cnt += 1
            got[tag] = cnt
        print(f"{pid} bay{j} day {d}: residents {len(live)}"
              f" (vp control fail {bad})  extras seated at day-{d}:"
              f" corner {got['corner']}  slide {got['slide']}"
              + (f"   <-- +{got['slide']-got['corner']} KERNEL-CERTIFIED"
                 if got['slide'] > got['corner'] else ""), flush=True)


if __name__ == "__main__":
    main()
