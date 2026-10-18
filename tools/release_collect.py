#!/usr/bin/env python3
"""Collect + freeze the final-extract shipcheck (Stage 2 O-RECEIPT).

Verifies against the run manifest: exact matrix coverage, per-case PASS,
lineage hash equality across every receipt, canary/noort exact reference
objectives, scheduler COMPLETED 0:0 and cpus=4 for search cases, and input
snapshot drift 0. Output is create-only (refuses to overwrite).

    python3 tools/release_collect.py --run-root runs/release/e2e-v53 \
        --output results/release-v53-final-extract.json
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys

TOOLS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import release_schema as rs


def sacct_rows(job_ids):
    out = subprocess.run(
        ["sacct", "-P", "-n", "-j", ",".join(job_ids),
         "--format=JobIDRaw,State,ExitCode,ElapsedRaw,AllocCPUS,Partition,NodeList"],
        capture_output=True, text=True)
    rows = {}
    for line in out.stdout.splitlines():
        f = line.split("|")
        if len(f) >= 7 and "." not in f[0]:
            rows[f[0]] = {"state": f[1], "exitCode": f[2], "elapsed": f[3],
                          "cpus": f[4], "partition": f[5], "node": f[6]}
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-root", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args(argv)

    run_root = pathlib.Path(args.run_root).resolve()
    output = pathlib.Path(args.output)
    manifest = json.loads((run_root / "manifest.json").read_text())
    matrix = manifest["matrix"]
    lineage = {"releaseZipSha256": manifest["archive"]["sha256"],
               "solverTreeSha256": manifest["extract"]["treeSha256"],
               "canonicalSoSha256": manifest["extract"]["canonicalSoSha256"],
               "checkerSha256": manifest["extract"]["checkerSha256"]}
    assertions = []

    def A(aid, ok, detail=""):
        assertions.append({"id": aid, "status": "PASS" if ok else "FAIL",
                           "detail": str(detail)[:400]})

    # matrix coverage + per-case receipts
    cases = []
    missing = []
    for case in matrix["cases"]:
        rp = run_root / "cases" / f"{case['caseId']}.json"
        if not rp.is_file():
            missing.append(case["caseId"])
            continue
        receipt = json.loads(rp.read_text())
        rs.validate(receipt)
        cases.append(receipt)
    A("matrix-coverage", not missing, f"missing={missing}")

    # lineage equality across all receipts
    drift = [c["caseId"] for c in cases if c["lineage"] != lineage]
    A("lineage-equality", not drift, f"drift={drift}")

    # input snapshot drift
    bad_inputs = []
    for c in cases:
        key = f"{c['case']['dataset']}/prob_{c['case']['instance']}.json"
        if manifest["dataRoot"]["inputs"].get(key) != c["case"]["inputSha256"]:
            bad_inputs.append(c["caseId"])
    A("input-snapshot", not bad_inputs, f"drift={bad_inputs}")

    # per-case status / feasibility / error / overrun
    failed = [c["caseId"] for c in cases if c["status"] != "PASS"]
    A("all-cases-pass", not failed, f"failed={failed}")

    # exact references (canary / noort)
    expected = {c["caseId"]: c["expectedObjective"] for c in matrix["cases"]
                if c["expectedObjective"] is not None}
    wrong = []
    for c in cases:
        want = expected.get(c["caseId"])
        if want is not None and c["result"]["objective"] != want:
            wrong.append(f"{c['caseId']}:{c['result']['objective']}!={want}")
    A("exact-references", not wrong, f"wrong={wrong}")

    # scheduler receipts: COMPLETED 0:0, cpus=4 for tl-bounded cases
    job_ids = [j["jobId"] for j in manifest["jobs"] if j["jobId"]]
    rows = sacct_rows(job_ids) if job_ids else {}
    sched_bad = []
    cpu_bad = []
    by_case = {j["caseId"]: j["jobId"] for j in manifest["jobs"]}
    for c in cases:
        row = rows.get(str(by_case.get(c["caseId"])))
        if not row or row["state"] != "COMPLETED" or row["exitCode"] != "0:0":
            sched_bad.append(f"{c['caseId']}:{row['state'] if row else 'missing'}")
        if c["case"]["tl"] is not None and c["result"]["cpus"] != 4:
            cpu_bad.append(f"{c['caseId']}:{c['result']['cpus']}")
    A("scheduler-completed", not sched_bad, f"bad={sched_bad}")
    A("cpus-4-search", not cpu_bad, f"bad={cpu_bad}")

    status = "PASS" if all(a["status"] == "PASS" for a in assertions) else "FAIL"
    gates = {}
    for c in cases:
        g = gates.setdefault(c["gateId"], {"cases": 0, "pass": 0})
        g["cases"] += 1
        g["pass"] += c["status"] == "PASS"

    aggregate = {
        "schemaVersion": rs.SCHEMA_VERSION,
        "kind": "aggregate",
        "status": status,
        "createdAt": rs.now_iso(),
        "runRoot": str(run_root),
        "tool": {"path": "tools/release_collect.py",
                 "sha256": rs.sha256_file(__file__)},
        "lineage": lineage,
        "matrixSha256": manifest["matrixSha256"],
        "gates": gates,
        "cases": [{"caseId": c["caseId"], "gateId": c["gateId"],
                   "status": c["status"],
                   "objective": c["result"]["objective"],
                   "z1": c["result"]["z1"], "z2": c["result"]["z2"],
                   "z3": c["result"]["z3"],
                   "wall": c["result"]["wall"],
                   "maxRssMb": c["result"]["maxRssMb"],
                   "feasible": c["result"]["feasible"],
                   "overrun": c["result"]["overrun"],
                   "error": c["result"]["error"],
                   "solutionSha256": c["result"]["solutionSha256"],
                   "receiptSha256": rs.sha256_file(
                       run_root / "cases" / f"{c['caseId']}.json")}
                  for c in cases],
        "assertions": assertions,
    }
    rs.validate(aggregate)
    body = json.dumps(aggregate, indent=2, sort_keys=True) + "\n"
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(fd, "w") as f:
        f.write(body)
    print(json.dumps({"output": str(output), "status": status,
                      "sha256": rs.sha256_file(output),
                      "gates": gates}))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
