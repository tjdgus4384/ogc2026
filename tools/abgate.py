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
import collections
import hashlib
import json
import math
import os
import pathlib
import shutil
import subprocess
import sys

from abgate_seal import file_sha256, seal_id, tree_seal

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
    checker = pathlib.Path(args.checker).resolve()
    data = REPO / "data" / args.data
    # Instance-major so a cell's pairs share a time limit. A cell ends only
    # when its slowest pair does, so mixing P3 (2x240s) with P6 (2x900s) parks
    # the P3 cores for twenty minutes.
    pairs = []
    prob_hashes = {}
    for ii, inst in enumerate(insts):
        for si, seed in enumerate(seeds):
            if args.tl:
                tl = args.tl
            elif f"P{inst}" in REFS["hidden_specs"]:
                tl = REFS["hidden_specs"][f"P{inst}"]["tl"]
            else:
                sys.exit(f"no official TL for instance {inst}; pass --tl")
            prob_path = data / f"prob_{inst}.json"
            prob_hashes.setdefault(prob_path, file_sha256(prob_path))
            pairs.append({
                "idx": len(pairs), "instance": inst, "seed": seed, "tl": tl,
                "probPath": str(prob_path), "probSha256": prob_hashes[prob_path],
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
    if args.dry:
        seals = {"base": tree_seal(base), "candidate": tree_seal(cand),
                 "checker": tree_seal(checker)}
        print(json.dumps({
            "tag": args.tag, "baseDir": str(base), "candDir": str(cand),
            "checkerDir": str(checker), "baseEnv": parse_env(args.base_env),
            "candEnv": parse_env(args.cand_env), "dataset": args.data,
            "instances": insts, "seeds": seeds,
            "sealIds": {name: seal_id(value) for name, value in seals.items()},
            "sealFiles": {name: len(value) for name, value in seals.items()},
        }, indent=2, sort_keys=True))
        print(f"would submit {len(pairs)} pairs in {ncells} cells "
              f"({args.per_cell}/cell, longest TL {max(p['tl'] for p in pairs)}s "
              f"-> cell wall about {2 * max(p['tl'] for p in pairs) // 60} min)")
        return
    gate.mkdir(parents=True)
    snapshots = gate / "snapshots"
    snapshots.mkdir()
    materialized = {}

    def snapshot(role, source):
        key = str(source)
        if key in materialized:
            return materialized[key]
        before = tree_seal(source)
        dest = snapshots / role
        shutil.copytree(source, dest,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        after = tree_seal(dest)
        if after != before:
            sys.exit(f"{source} changed while its gate snapshot was copied")
        materialized[key] = dest.resolve()
        return materialized[key]

    base_snapshot = snapshot("base", base)
    cand_snapshot = snapshot("candidate", cand)
    checker_snapshot = snapshot("checker", checker)
    harness = gate / "harness"
    harness.mkdir()
    for name in ("abgate.sbatch", "abgate_cell.py", "abgate_seal.py"):
        shutil.copy2(REPO / "tools" / name, harness / name)
    manifest = {
        "tag": args.tag,
        "baseDir": str(base_snapshot), "candDir": str(cand_snapshot),
        "checkerDir": str(checker_snapshot),
        "originalDirs": {"base": str(base), "candidate": str(cand),
                         "checker": str(checker)},
        "harnessDir": str(harness.resolve()),
        "harnessSeal": tree_seal(harness),
        "baseEnv": parse_env(args.base_env), "candEnv": parse_env(args.cand_env),
        # One checker for both arms, pinned independently of either solver dir.
        "dataset": args.data,
        "instances": insts, "seeds": seeds, "pairs": pairs,
        # Jobs run from immutable, gate-local snapshots.  Seals make accidental
        # mutation fail before import and preserve exact ignored-build bytes.
        "seals": {
            "base": tree_seal(base_snapshot),
            "candidate": tree_seal(cand_snapshot),
            "checker": tree_seal(checker_snapshot),
        },
    }
    (gate / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    out = subprocess.run(
        ["sbatch", "-p", args.partition,
         f"--array=0-{ncells - 1}%{args.concurrent}",
         str(harness / "abgate.sbatch")],
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
    missing = collections.Counter(
        p["instance"] for p in manifest["pairs"]
        if not (gate / f"pair-{p['idx']:03d}.json").exists())
    print(f"== {args.tag}  {len(rows)}/{total} valid pairs"
          + (f", {len(bad)} unusable" if bad else ""))
    # A cell that dies takes a whole instance with it, and a verdict read
    # without noticing that is a verdict on a different experiment.
    if missing:
        print("   !! MISSING cells: "
              + ", ".join(f"P{i}x{c}" for i, c in sorted(missing.items())))
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
    # MEDIAN, not best-of-N. The grading server runs each instance once, so the
    # median draw is our position; the minimum over a dozen seeds is a lottery
    # ticket and quoting it makes the gap to the target look far smaller than it
    # is (P4: best draw +25% against a median of +59%).
    # ...and only for the preliminary boards. `alltime_min` is keyed P3..P6 on
    # the PRELIMINARY instances; a train/train_final gate has its own prob_3
    # that shares the number and nothing else. Applying the table across
    # datasets printed silent nonsense (train_final prob_3 read as "+6890.8%
    # vs target" on 08-13) before it crashed on the first id with no entry.
    if manifest.get("dataset") != "preliminary_test":
        return
    print("   -- median draw vs all-time minimum (best-of-N in parens) --")
    for inst in manifest["instances"]:
        sel = sorted((r["b"] for r in rows if r["instance"] == inst))
        if not sel or f"P{inst}" not in amin:
            continue
        tgt = amin[f"P{inst}"]["objective"]
        med = sel[len(sel) // 2] if len(sel) % 2 else \
            (sel[len(sel) // 2 - 1] + sel[len(sel) // 2]) / 2
        print(f"   P{inst}  median {med:13,.0f} ({(med - tgt) / tgt * 100:+6.1f}%)"
              f"   best {sel[0]:13,.0f} ({(sel[0] - tgt) / tgt * 100:+6.1f}%)"
              f"   target {tgt:,}")


def cmd_freeze(args):
    """Freeze a complete gate's compact raw evidence outside ignored runs/."""
    gate = GATES / args.tag
    manifest = json.loads((gate / "manifest.json").read_text())
    receipts = []
    for pair in manifest["pairs"]:
        path = gate / f"pair-{pair['idx']:03d}.json"
        if not path.exists():
            sys.exit(f"gate incomplete: missing {path.name}")
        receipt = json.loads(path.read_text())
        for key in ("idx", "instance", "seed", "order", "tl", "probPath"):
            if receipt.get(key) != pair.get(key):
                sys.exit(f"{path.name}: manifest mismatch in {key}")
        receipts.append(receipt)
    observed = {}
    roots = manifest.get("originalDirs", {
        "base": manifest["baseDir"], "candidate": manifest["candDir"],
        "checker": manifest["checkerDir"],
    })
    for name, root in roots.items():
        path = pathlib.Path(root)
        if path.is_dir():
            seal = tree_seal(path)
            observed[name] = {"id": seal_id(seal), "files": seal}
    payload = {
        "schemaVersion": 1, "tag": args.tag, "manifest": manifest,
        "receipts": receipts, "freezeObservedSeals": observed,
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["evidenceSha256"] = hashlib.sha256(body).hexdigest()
    output = pathlib.Path(args.output) if args.output else \
        REPO / "results" / "abgate" / f"{args.tag}.json"
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if output.exists():
        if output.read_text() != encoded:
            sys.exit(f"refusing to overwrite different evidence: {output}")
        print(f"already frozen: {output}")
        return
    output.write_text(encoded)
    print(f"froze {len(receipts)} pairs -> {output}")
    print(f"evidence sha256 {payload['evidenceSha256']}")


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
    # A titan node has 48 cores and a pair is pinned to 4, so twelve pairs
    # fill one exclusive node exactly.
    s.add_argument("--per-cell", type=int, default=12)
    s.add_argument("--tl", type=int, help="TL override for non-prelim datasets")
    s.add_argument("--dry", action="store_true")
    s.set_defaults(func=cmd_submit)
    c = sub.add_parser("collect")
    c.add_argument("tag")
    c.set_defaults(func=cmd_collect)
    f = sub.add_parser("freeze")
    f.add_argument("tag")
    f.add_argument("--output")
    f.set_defaults(func=cmd_freeze)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
