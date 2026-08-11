#!/usr/bin/env python3
"""One-shot 24-pair matched AB/BA gate for the sealed population candidate
(ralplan Revision 4; pre-registered in tools/population_closure_policy.json).

    submit    verify seal (gateEligible + exact treatment hashes), then submit
              ONE sbatch array (24 tasks, max 8 concurrent). Each task runs
              variant A (canonical v5.3 extract) and variant B (sealed
              candidate) SEQUENTIALLY on the same node/cpuset, in the
              pre-registered AB/BA order for its seed. Refuses to run twice.
    collect   validate the 48 run receipts against the seal and the safety
              contract, apply the pre-registered quality thresholds, and
              freeze evidence/pair-gate.json with the automatic selection
              (population | v53-fallback).

Safety contract: 48/48 checker-feasible, 0 errors, 0 overruns, seal-hash
match on every B-side receipt. Quality: wins>=18/24, two-sided non-tie
sign-test p<=0.05, median delta<=-1%, per-instance wins>=5/8 with median
delta<0, no pair delta>+10%.
"""
import argparse
import json
import math
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import release_schema as rs

REPO = pathlib.Path(__file__).resolve().parent.parent
WORK = REPO / "runs" / "release" / "population-candidate"
EVID = WORK / "evidence"
GATE = WORK / "pair-gate"
POLICY_PATH = REPO / "tools" / "population_closure_policy.json"
SO = "sscore.cpython-312-x86_64-linux-gnu.so"


def _policy():
    return json.loads(POLICY_PATH.read_text())


def _seal():
    seal = json.loads((EVID / "candidate-seal.json").read_text())
    if not seal["approval"]["gateEligible"]:
        raise SystemExit("seal is not gateEligible -> v53-fallback")
    return seal


def _pairs(policy):
    g = policy["pairGate"]
    ab = set(g["orderBalance"]["AB"])
    out = []
    for inst, tl in sorted(g["instances"].items(), key=lambda kv: int(kv[0])):
        for seed in g["seeds"]:
            out.append({"instance": int(inst), "tl": int(tl), "seed": seed,
                        "order": "AB" if seed in ab else "BA"})
    return out


def cmd_submit(args):
    policy = _policy()
    seal = _seal()
    tree_sha, _ = rs.tree_sha256(WORK / "candidate")
    if tree_sha != seal["treatment"]["treeSha256"]:
        raise SystemExit("candidate tree != sealed treatment -> refuse")
    so_sha = rs.sha256_file(WORK / "candidate" / SO)
    if so_sha != seal["treatment"]["soSha256"]:
        raise SystemExit("candidate .so != sealed binary -> refuse")
    if (GATE / "submitted.json").exists():
        raise SystemExit("pair gate already submitted (one-shot rule)")
    pairs = _pairs(policy)
    GATE.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schemaVersion": 1, "createdAt": rs.now_iso(),
        "pairs": pairs,
        "dataset": policy["pairGate"]["dataset"],
        "candidateSealSha256": rs.sha256_file(EVID / "candidate-seal.json"),
        "closureManifestSha256": rs.sha256_file(EVID / "closure-manifest.json"),
        "testedTreeSha256": tree_sha, "testedSoSha256": so_sha,
    }
    (GATE / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    n = len(pairs)
    conc = policy["pairGate"]["maxConcurrent"]
    r = subprocess.run(
        ["sbatch", f"--array=0-{n - 1}%{conc}",
         str(REPO / "tools" / "population_pair_gate.sbatch")],
        cwd=REPO, capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode != 0:
        raise SystemExit("sbatch failed")
    job_id = r.stdout.strip().split()[-1]
    (GATE / "submitted.json").write_text(json.dumps(
        {"jobId": job_id, "submittedAt": rs.now_iso(),
         "manifestSha256": rs.sha256_file(GATE / "manifest.json")},
        indent=2) + "\n")
    return 0


def _sign_test_p(wins, losses):
    """two-sided exact binomial sign test over non-ties"""
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    lower_tail = sum(math.comb(n, i) for i in range(k + 1))
    return min(1.0, 2.0 * lower_tail / (2.0 ** n))


def cmd_collect(args):
    policy = _policy()
    seal = _seal()
    manifest = json.loads((GATE / "manifest.json").read_text())
    submitted = json.loads((GATE / "submitted.json").read_text())
    if rs.sha256_file(GATE / "manifest.json") != submitted["manifestSha256"]:
        raise SystemExit("manifest changed after submission -> refuse collection")
    if manifest["pairs"] != _pairs(policy):
        raise SystemExit("manifest pairs differ from pre-registered policy")
    if manifest["candidateSealSha256"] != rs.sha256_file(
            EVID / "candidate-seal.json"):
        raise SystemExit("manifest references a different candidate seal")
    if (manifest["testedTreeSha256"] != seal["treatment"]["treeSha256"]
            or manifest["testedSoSha256"] != seal["treatment"]["soSha256"]):
        raise SystemExit("manifest treatment differs from sealed candidate")
    pairs = manifest["pairs"]
    q = policy["pairGate"]["quality"]
    rows, safety = [], []

    def S(sid, ok, detail=""):
        safety.append({"id": sid, "status": "PASS" if ok else "FAIL",
                       "detail": str(detail)[:300]})

    for idx, pr in enumerate(pairs):
        rec_path = GATE / f"pair-{idx:02d}.json"
        if not rec_path.exists():
            S(f"pair-{idx:02d}-present", False, "missing receipt")
            continue
        rec = json.loads(rec_path.read_text())
        metadata_ok = (
            rec.get("idx") == idx
            and rec.get("instance") == pr["instance"]
            and rec.get("tl") == pr["tl"]
            and rec.get("seed") == pr["seed"]
            and rec.get("order") == pr["order"])
        S(f"pair-{idx:02d}-metadata", metadata_ok)
        ok_seal = (rec.get("candidateSealSha256")
                   == manifest["candidateSealSha256"]
                   and rec.get("testedTreeSha256")
                   == manifest["testedTreeSha256"]
                   and rec.get("testedSoSha256") == manifest["testedSoSha256"])
        S(f"pair-{idx:02d}-seal", ok_seal)
        a, b = rec.get("A", {}), rec.get("B", {})
        feas = a.get("feasible") is True and b.get("feasible") is True
        S(f"pair-{idx:02d}-feasible", feas,
          f"A={a.get('feasible')} B={b.get('feasible')}")
        tl = pr["tl"]
        walls = (a.get("wall"), b.get("wall"))
        wall_ok = all(isinstance(x, (int, float)) and math.isfinite(x)
                      for x in walls)
        over = not wall_ok or any(x > tl + 1.0 for x in walls if wall_ok)
        S(f"pair-{idx:02d}-overrun", not over,
          f"A={walls[0]} B={walls[1]} tl={tl}")
        objective_ok = False
        if feas:
            try:
                oa, ob = float(a["objective"]), float(b["objective"])
                objective_ok = (math.isfinite(oa) and math.isfinite(ob)
                                and oa > 0.0)
            except (KeyError, TypeError, ValueError):
                objective_ok = False
        S(f"pair-{idx:02d}-objective", objective_ok,
          f"A={a.get('objective')} B={b.get('objective')}")
        if feas and objective_ok:
            delta = (ob - oa) / oa
            rows.append({"idx": idx, **pr, "objA": oa, "objB": ob,
                         "delta": delta})

    safety_ok = (len(rows) == len(pairs)
                 and all(s["status"] == "PASS" for s in safety))
    wins = sum(1 for r in rows if r["objB"] < r["objA"])
    losses = sum(1 for r in rows if r["objB"] > r["objA"])
    deltas = sorted(r["delta"] for r in rows)
    med = deltas[len(deltas) // 2] if len(deltas) % 2 else (
        (deltas[len(deltas) // 2 - 1] + deltas[len(deltas) // 2]) / 2
    ) if deltas else 0.0
    p = _sign_test_p(wins, losses)
    per_inst = {}
    for r in rows:
        per_inst.setdefault(r["instance"], []).append(r)
    inst_ok = {}
    for inst, rr in per_inst.items():
        iw = sum(1 for x in rr if x["objB"] < x["objA"])
        dd = sorted(x["delta"] for x in rr)
        imed = dd[len(dd) // 2] if len(dd) % 2 else (
            dd[len(dd) // 2 - 1] + dd[len(dd) // 2]) / 2
        inst_ok[inst] = {"wins": iw, "median": imed,
                         "pass": iw >= q["perInstanceMinWins"] and imed < 0}
    catastrophic = [r for r in rows if r["delta"] > q["catastrophicDelta"]]
    quality_ok = (wins >= q["minWins"] and p <= q["signTestP"]
                  and med <= q["medianDelta"]
                  and all(v["pass"] for v in inst_ok.values())
                  and not catastrophic)
    selected = "population" if (safety_ok and quality_ok) else "v53-fallback"
    receipt = {
        "schemaVersion": 1, "kind": "pair-gate",
        "status": "PASS" if safety_ok else "FAIL",
        "createdAt": rs.now_iso(),
        "candidateSealSha256": manifest["candidateSealSha256"],
        "closureManifestSha256": manifest["closureManifestSha256"],
        "testedTreeSha256": manifest["testedTreeSha256"],
        "testedSoSha256": manifest["testedSoSha256"],
        "pairs": rows, "safety": safety,
        "summary": {"pairs": len(rows), "wins": wins, "losses": losses,
                    "ties": len(rows) - wins - losses,
                    "medianDelta": med, "signTestP": p,
                    "perInstance": {str(k): v for k, v in inst_ok.items()},
                    "catastrophic": [r["idx"] for r in catastrophic]},
        "thresholds": q,
        "safetyOk": safety_ok, "qualityOk": quality_ok,
        "selectedVariant": selected,
    }
    out = EVID / "pair-gate.json"
    out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"selectedVariant": selected, "safetyOk": safety_ok,
                      "qualityOk": quality_ok, "wins": wins,
                      "losses": losses, "medianDelta": round(med, 5),
                      "signTestP": round(p, 5),
                      "perInstance": {str(k): v for k, v in inst_ok.items()},
                      "receipt": str(out)}))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("submit")
    sub.add_parser("collect")
    args = ap.parse_args(argv)
    return {"submit": cmd_submit, "collect": cmd_collect}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
