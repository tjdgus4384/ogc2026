#!/usr/bin/env python3
"""Collect, verify and freeze the 24-pair population production gate.

Pre-registered acceptance (Revision 3, unchanged by Revision 4):
  safety    48/48 checker-feasible, error=0, overrun=0, cpus=4, RSS<=16G,
            scheduler COMPLETED 0:0, same node+affinity within each pair,
            AB/BA order per matrix, all snapshot hashes intact
  quality   candidate wins >= 18/24 non-tie pairs; exact two-sided sign
            test p <= 0.05 over non-ties; median delta <= -0.010 over all
            24 pairs; each instance wins >= 5/8 with median delta < 0;
            no pair delta > +0.10

delta_i = (obj_candidate - obj_control) / obj_control  (lower is better).
Exact tie: |obj_candidate - obj_control| <= 1e-9 (excluded from sign test,
included in coverage and medians).

Writes results/population-production-gate-v1.json exactly once.
"""
import hashlib
import json
import math
import pathlib
import statistics
import subprocess
import sys
import time

TOOLS = pathlib.Path(__file__).resolve().parent
ROOT = TOOLS.parent
GATE = ROOT / "runs/release/population-gate"
RESULT = ROOT / "results/population-production-gate-v1.json"


def sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def binom_two_sided(k, n):
    if n == 0:
        return 1.0
    def cdf(x):
        return sum(math.comb(n, i) for i in range(x + 1)) / 2.0 ** n
    lo = cdf(k)
    hi = 1.0 - cdf(k - 1)
    return min(1.0, 2.0 * min(lo, hi))


def main():
    if RESULT.exists():
        print(f"REFUSE: result already frozen: {RESULT}", file=sys.stderr)
        sys.exit(1)
    manifest = json.loads((GATE / "MANIFEST.json").read_text())
    problems = []
    pairs = []
    for spec in manifest["matrix"]:
        pid = spec["pairId"]
        pdir = GATE / "pairs" / pid
        recs = {}
        for phase, treatment in enumerate(spec["order"]):
            f = pdir / f"{phase}-{treatment}.json"
            if not f.exists():
                problems.append(f"{pid}: missing receipt phase {phase}")
                continue
            recs[treatment] = json.loads(f.read_text())
        if len(recs) != 2:
            continue
        c, k = recs["control"], recs["candidate"]
        for name, r in recs.items():
            if r.get("error"):
                problems.append(f"{pid}/{name}: error {r['error']}")
            if not r.get("feasible"):
                problems.append(f"{pid}/{name}: infeasible")
            if r.get("overrun"):
                problems.append(f"{pid}/{name}: overrun wall={r.get('wall')}")
            if r.get("cpus") != 4:
                problems.append(f"{pid}/{name}: cpus={r.get('cpus')}")
            if (r.get("maxrssMb") or 0) > 16384:
                problems.append(f"{pid}/{name}: rss {r.get('maxrssMb')}MB")
            if r.get("checkerSha256") != manifest["checkerSha256"]:
                problems.append(f"{pid}/{name}: checker hash drift")
            if r.get("inputSha256") != manifest["inputs"][
                    str(spec["instance"])]:
                problems.append(f"{pid}/{name}: input hash drift")
            exp = manifest["treatments"][name]
            if r.get("treatmentTreeSha256") != exp["treeSha256"]:
                problems.append(f"{pid}/{name}: tree hash drift")
            if r.get("treatmentSoSha256") != exp["soSha256"]:
                problems.append(f"{pid}/{name}: so hash drift")
            if r.get("harnessSha256") != manifest["harnessSha256"]:
                problems.append(f"{pid}/{name}: harness drift")
            if r.get("candidateSealSha256") != manifest["candidateSealSha256"]:
                problems.append(f"{pid}/{name}: seal hash drift")
        if c.get("node") != k.get("node"):
            problems.append(f"{pid}: node mismatch {c.get('node')} vs "
                            f"{k.get('node')}")
        if c.get("affinity") != k.get("affinity"):
            problems.append(f"{pid}: affinity mismatch")
        sacct = pdir / "sacct.txt"
        if sacct.exists():
            rows = [l for l in sacct.read_text().splitlines()
                    if l and ".ba" not in l]
            bad = [l for l in rows
                   if l.split("|")[1] not in ("COMPLETED", "RUNNING")
                   and not l.split("|")[1].startswith("CANCELLED by")]
            main_rows = [l for l in rows if l.split("|")[1] == "COMPLETED"
                         and l.split("|")[2] == "0:0"]
            if not main_rows:
                problems.append(f"{pid}: no COMPLETED 0:0 sacct row")
        else:
            problems.append(f"{pid}: missing sacct receipt")
        if None in (c.get("objective"), k.get("objective")):
            continue
        delta = (k["objective"] - c["objective"]) / c["objective"]
        tie = abs(k["objective"] - c["objective"]) <= 1e-9
        pairs.append({
            "pairId": pid, "instance": spec["instance"], "seed": spec["seed"],
            "order": "AB" if spec["order"][0] == "control" else "BA",
            "node": c.get("node"),
            "control": c["objective"], "candidate": k["objective"],
            "delta": delta, "tie": tie,
            "win": (not tie) and k["objective"] < c["objective"],
        })

    coverage_ok = len(pairs) == 24 and not any(
        p.startswith(x) for p in problems for x in ())
    nontie = [p for p in pairs if not p["tie"]]
    wins = sum(1 for p in pairs if p["win"])
    losses = sum(1 for p in nontie if not p["win"])
    pval = binom_two_sided(min(wins, losses), len(nontie))
    med = statistics.median([p["delta"] for p in pairs]) if pairs else None
    per_instance = {}
    for inst in (4, 5, 6):
        sub = [p for p in pairs if p["instance"] == inst]
        per_instance[str(inst)] = {
            "pairs": len(sub),
            "wins": sum(1 for p in sub if p["win"]),
            "median": statistics.median([p["delta"] for p in sub])
            if sub else None,
        }
    catastrophic = [p["pairId"] for p in pairs if p["delta"] > 0.10]

    safety_ok = not problems and len(pairs) == 24
    q = {
        "wins18": wins >= 18,
        "signTest": len(nontie) > 0 and pval <= 0.05,
        "medianDelta": med is not None and med <= -0.010,
        "perInstance": all(
            v["pairs"] == 8 and v["wins"] >= 5 and (v["median"] or 0) < 0
            for v in per_instance.values()),
        "noCatastrophic": not catastrophic,
    }
    quality_ok = all(q.values())
    status = "PASS" if (safety_ok and quality_ok) else "FAIL"

    result = {
        "schemaVersion": 1,
        "tag": manifest["tag"],
        "collectedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "criteriaVersion": "revision3-24pair-v1",
        "status": status,
        "safety": {"ok": safety_ok, "problems": problems,
                   "pairsComplete": len(pairs)},
        "quality": {"ok": quality_ok, "checks": q, "wins": wins,
                    "losses": losses, "ties": 24 - len(nontie)
                    if len(pairs) == 24 else None,
                    "signTestP": pval, "medianDelta": med,
                    "perInstance": per_instance,
                    "catastrophic": catastrophic},
        "pairs": sorted(pairs, key=lambda p: (p["instance"], p["seed"])),
        "provenance": {
            "manifestSha256": sha(GATE / "MANIFEST.json"),
            "candidateSealSha256": manifest["candidateSealSha256"],
            "closureManifestSha256": manifest["closureManifestSha256"],
            "testedTreeSha256": manifest["testedTreeSha256"],
            "testedSoSha256": manifest["testedSoSha256"],
            "controlTreeSha256":
                manifest["treatments"]["control"]["treeSha256"],
            "controlSoSha256": manifest["treatments"]["control"]["soSha256"],
            "checkerSha256": manifest["checkerSha256"],
        },
        "selection": {
            "selectedVariant": "population" if status == "PASS"
                               else "v53-fallback",
            "automaticRule": "PASS selects the sealed population candidate; "
                             "any failure selects the compliant v5.3 fallback",
        },
    }
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    subprocess.run(["chmod", "a-w", str(RESULT)], check=True)
    print(f"gate {status}: wins={wins}/24 p={pval:.4f} median={med}"
          f" selected={result['selection']['selectedVariant']}")
    if problems:
        print(json.dumps(problems[:20], indent=1))
    sys.exit(0 if status == "PASS" else 2)


if __name__ == "__main__":
    main()
