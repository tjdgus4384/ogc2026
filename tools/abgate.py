#!/usr/bin/env python3
"""Matched AB/BA gate: judge one candidate against one baseline, decisively.

    abgate.py submit TAG --base DIR [--cand DIR] [--cand-env K=V,...]
                         [--inst 3,4,5,6] [--seeds 7,11,...]
    abgate.py collect TAG

Why this exists. Every campaign between 08-07 and 08-11 judged 2-5% effects
from 1-3 UNPAIRED official runs. The per-draw spread on these boards is
10-12% (measured: P3 baseline drew 78,705 and 87,140 on the same engine), so
those screens could not resolve the effects they were looking for -- promising
arms never reproduced, and five days closed with NO_SURVIVOR. The one campaign
that paired (08-06 population, 24 matched pairs) got a clean verdict at
p=0.000277. This tool makes that method the default: one pair = one
(instance, seed) with both variants run back to back on the same cpuset of an
exclusive node, so the draw cancels and the sign test has something to bite on.

A candidate is either a different solver directory (--cand) or the same
directory under different OGC_* knobs (--cand-env), or both.
"""
import argparse
import json
import math
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
REFS = json.load(open(REPO / "data" / "prelim_refs.json"))
GATES = REPO / "runs" / "abgate"


def parse_env(text):
    if not text:
        return {}
    out = {}
    for item in text.split(","):
        key, _, value = item.partition("=")
        out[key.strip()] = value.strip()
    return out


def cmd_submit(args):
    gate = GATES / args.tag
    if gate.exists():
        sys.exit(f"{gate} exists -- gates are one-shot; pick a new tag")
    insts = [int(x) for x in args.inst.split(",")]
    seeds = [int(x) for x in args.seeds.split(",")]
    base = pathlib.Path(args.base).resolve()
    cand = pathlib.Path(args.cand).resolve() if args.cand else base
    data = REPO / "data" / args.data
    pairs = []
    for si, seed in enumerate(seeds):
        for ii, inst in enumerate(insts):
            if args.tl:
                tl = args.tl
            elif f"P{inst}" in REFS["hidden_specs"]:
                tl = REFS["hidden_specs"][f"P{inst}"]["tl"]
            else:
                sys.exit(f"no official TL for instance {inst}; pass --tl")
            pairs.append({
                "idx": len(pairs), "instance": inst, "seed": seed, "tl": tl,
                "probPath": str(data / f"prob_{inst}.json"),
                # Counterbalance on seed+instance parity so each arm runs first
                # about half the time, in every cell: a systematic first-run or
                # second-run effect then cannot masquerade as a treatment one.
                "order": "AB" if (si + ii) % 2 == 0 else "BA",
            })
    # A cell is one node-job; its pairs run concurrently on 4 cores each, so
    # keep it under a node's slot count. Longest pair sets the cell's wall.
    for i, pair in enumerate(pairs):
        pair["cell"] = i // args.per_cell
    ncells = (len(pairs) + args.per_cell - 1) // args.per_cell
    manifest = {
        "tag": args.tag, "baseDir": str(base), "candDir": str(cand),
        "baseEnv": parse_env(args.base_env), "candEnv": parse_env(args.cand_env),
        # One checker for both arms, pinned independently of either solver dir:
        # the two utils.py in this repo differ only by input-validation guards,
        # and the stricter one must judge every arm.
        "checkerDir": str(pathlib.Path(args.checker).resolve()),
        "dataset": args.data,
        "instances": insts, "seeds": seeds, "pairs": pairs,
    }
    if args.dry:
        print(json.dumps({k: v for k, v in manifest.items() if k != "pairs"},
                         indent=2, sort_keys=True))
        print(f"would submit {len(pairs)} pairs in {ncells} cells "
              f"({args.per_cell}/cell, longest TL {max(p['tl'] for p in pairs)}s "
              f"-> cell wall about {2 * max(p['tl'] for p in pairs) // 60} min)")
        return
    gate.mkdir(parents=True)
    (gate / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    out = subprocess.run(
        ["sbatch", "-p", args.partition,
         f"--array=0-{ncells - 1}%{args.concurrent}",
         str(REPO / "tools" / "abgate.sbatch")],
        cwd=REPO, capture_output=True, text=True,
        env={**os.environ, "OGC_ABGATE_DIR": str(gate)})
    if out.returncode != 0:
        sys.exit(f"sbatch failed: {out.stderr.strip()}")
    print(out.stdout.strip())
    print(f"{len(pairs)} pairs / {ncells} cells -> {gate}")
    print(f"collect with: python3 tools/abgate.py collect {args.tag}")


def sign_test(wins, losses):
    """Two-sided exact binomial p for wins:losses under p=0.5 (ties dropped)."""
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def cmd_collect(args):
    gate = GATES / args.tag
    manifest = json.loads((gate / "manifest.json").read_text())
    rows, bad = [], []
    for pair in manifest["pairs"]:
        path = gate / f"pair-{pair['idx']:03d}.json"
        if not path.exists():
            continue
        r = json.loads(path.read_text())
        a, b = r["A"], r["B"]
        if not (a.get("feasible") and b.get("feasible")):
            bad.append((pair["instance"], pair["seed"],
                        a.get("error") or b.get("error") or "infeasible"))
            continue
        rows.append({"instance": pair["instance"], "seed": pair["seed"],
                     "a": a["objective"], "b": b["objective"],
                     "delta": (b["objective"] - a["objective"]) / a["objective"] * 100})
    total = len(manifest["pairs"])
    print(f"== {args.tag}  {len(rows)}/{total} valid pairs"
          + (f", {len(bad)} unusable" if bad else ""))
    for inst, seed, why in bad:
        print(f"   !! P{inst} s{seed}: {str(why)[:80]}")
    if not rows:
        return
    amin = REFS["alltime_min"]

    def block(label, sel):
        sel = [r for r in sel if r]
        if not sel:
            return
        deltas = sorted(r["delta"] for r in sel)
        med = deltas[len(deltas) // 2] if len(deltas) % 2 else \
            (deltas[len(deltas) // 2 - 1] + deltas[len(deltas) // 2]) / 2
        wins = sum(1 for d in deltas if d < 0)
        losses = sum(1 for d in deltas if d > 0)
        p = sign_test(wins, losses)
        print(f"   {label:10s} {wins:2d}W/{losses:2d}L  median {med:+6.2f}%  "
              f"sign p={p:.4f}")

    print("   -- B vs A (negative = candidate better) --")
    block("ALL", rows)
    for inst in manifest["instances"]:
        block(f"P{inst}", [r for r in rows if r["instance"] == inst])
    print("   -- best draw vs all-time minimum --")
    for inst in manifest["instances"]:
        sel = [r for r in rows if r["instance"] == inst]
        if not sel:
            continue
        tgt = amin[f"P{inst}"]["objective"]
        ba, bb = min(r["a"] for r in sel), min(r["b"] for r in sel)
        print(f"   P{inst}  A {ba:12,.0f} ({(ba - tgt) / tgt * 100:+6.1f}%)   "
              f"B {bb:12,.0f} ({(bb - tgt) / tgt * 100:+6.1f}%)   target {tgt:,}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("submit")
    s.add_argument("tag")
    s.add_argument("--base", required=True)
    s.add_argument("--cand")
    s.add_argument("--base-env", default="")
    s.add_argument("--cand-env", default="")
    s.add_argument("--inst", default="3,4,5,6")
    s.add_argument("--seeds", default="7,11,23,41,59,73,89,101")
    s.add_argument("--data", default="preliminary_test")
    s.add_argument("--checker", default=str(REPO / "solver"))
    s.add_argument("--partition", default="titan")
    s.add_argument("--concurrent", type=int, default=8)
    s.add_argument("--per-cell", type=int, default=4)
    s.add_argument("--tl", type=int, help="TL override for non-prelim datasets")
    s.add_argument("--dry", action="store_true")
    s.set_defaults(func=cmd_submit)
    c = sub.add_parser("collect")
    c.add_argument("tag")
    c.set_defaults(func=cmd_collect)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
