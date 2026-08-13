#!/usr/bin/env python3
"""Is the construction path's density screen falsely rejecting seats?

The realiser benchmark (realize.py) showed greedy stranding 16-34% of blocks on
a schedule an annealed board proves realisable, and beam.py showed that merely
ENUMERATING vp-feasible positions and placing one seats far more (P4 99 -> 126,
P5 141 -> 165) while widening the beam to 8 adds nothing (126 -> 127). With the
days pinned the objective does not depend on x,y at all, so "picking a better
position" cannot be the explanation. The difference has to be a filter the
greedy applies and the enumerator does not.

repair.hpp:99-107 is that filter. It caps a bay at fillRatio (< 1) of its true
area for every day of the stay, and the kernel's own comment says the two modes
differ exactly here -- dm=0 "never falsely rejects", dm=1 "filters" -- which is
why the anneal splits its threads between them (densMode = tid % 2).

But Ctx defaults densMode to -1, which maps to dm=1. So every reconstruction
and every insert_capped runs the rejecting mode, including _seed_free's, which
builds every seed the whole run is grown from.

This measures the realiser under both modes. If dm=0 recovers the enumerator's
seating, the shipped construction has been throwing away legal seats.

    python3 tools/densmode.py prob_4 480 7
"""
import hashlib, json, pathlib, sys
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


pid, tl = sys.argv[1], float(sys.argv[2])
seed = int(sys.argv[3]) if len(sys.argv) > 3 else 7
ds = next(d for d in ("preliminary_test","train_final","train")
          if (REPO/"data"/d/f"{pid}.json").exists())
prob = json.load(open(REPO/"data"/ds/f"{pid}.json"))
n = len(prob["blocks"])
import myalgorithm as ma
eng, D, P = ma._fresh(prob, 4)
hz = eng.horizon()
base = cached(pid, tl, seed)
bay = [base[i*7] for i in range(n)]
e   = [base[i*7+4] for i in range(n)]
eng.set_solution(base); eng.build_occ(hz)
tot = eng.objective()[3]
print(f"{pid} s{seed}: annealed {tot:,.0f}  fill-screen cap = fillRatio", flush=True)
eng.set_entry_floor([max(0, x) for x in e])
order = sorted(range(n), key=lambda i: (e[i], i))

for dm, tag in ((-1, "dm=-1 (shipped default -> filters)"),
                (1,  "dm=1  (filters, explicit)"),
                (0,  "dm=0  (never falsely rejects)")):
    eng.set_dens_mode(dm)
    eng.set_solution([0]*(n*7)); eng.build_occ(hz)
    on = 0
    for i in order:
        if eng.insert_capped(i, e[i], bay[i]) and eng.get_block(i)[4] == e[i]:
            on += 1
    o = eng.objective()
    full = on == n
    print(f"   {tag:<36} on-plan {on:>3}/{n}"
          + (f"  obj {o[3]:,.0f} ({(o[3]-tot)/tot*100:+.2f}%)" if full
             else "  [INCOMPLETE]")
          + ("   <-- REALISES THE SCHEDULE" if full else ""), flush=True)

# and the same through reconstruct_ordered, which is what _seed_free uses
for dm in (1, 0):
    eng.set_dens_mode(dm)
    fl = [max(0, x) for x in e]
    bad = eng.reconstruct_ordered(list(bay), order, False, 7, fl, fl)
    print(f"   reconstruct_ordered dm={dm}: "
          + ("all blocks seated" if bad == -1 else f"aborted at block {bad}"),
          flush=True)
