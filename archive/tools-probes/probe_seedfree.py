import json, sys
sys.path.insert(0, "solver")
import myalgorithm as M
for pnum in [3, 4, 6]:
    p = json.load(open(f"data/preliminary_test/prob_{pnum}.json"))
    eng, seeds = M._seed_free(p)
    dup = len(seeds) >= 4 and seeds[3] in seeds[:3]
    print(json.dumps({"prob": pnum, "n_seeds": len(seeds), "dup4": dup}))
