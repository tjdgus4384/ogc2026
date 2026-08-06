#!/usr/bin/env python3
"""Final-extract unit/API verifier (Stage 2 U-DRIVER / I-PREBUILT minimum).

Imports ONLY the release final-extract in an isolated Python 3.12 subprocess
and verifies: import isolation (module paths under the extract), the public
`algorithm(prob_info, timelimit=60)` signature, the full canonical pybind
export inventory on the prebuilt cp312 `.so`, checker loadability + hash, the
fail-closed malformed-input path, and the no-search `_trivial` fallback being
official-checker feasible on preliminary fixtures.

All arguments are required so the harness can never silently validate the
dirty repo tree instead of the release extract.

    python3 tools/release_unit.py --solver EXTRACT_DIR \
        --checker EXTRACT_DIR/utils.py --data-root data \
        --receipt runs/.../unit-driver.json [--python PY312]
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import release_schema as rs

DEFAULT_PY = "/data_fast/home/tjdgus4384/priv/ogc2026/.miniforge/envs/ogc2026/bin/python"

PRODUCTION_EXPORTS = [
    "init_problem", "add_orient", "build_occ", "reconstruct_ordered",
    "run_portfolio", "get_block", "get_candidates", "objective",
    "nudge_block", "retime_block", "move_block", "swap_blocks",
]
CANONICAL_PROBE_EXPORTS = [
    "run_alns", "reconstruct_assigned", "insert_capped", "feasible_positions",
    "place_at", "last_chunks", "last_phase", "last_ops", "last_stats",
    "horizon", "pair_conflicts", "set_assignment_lock", "set_entry_lock",
    "compact_left", "set_block", "finalize_load", "set_solution",
    "get_solution", "vp_ok", "audit_state",
]

INNER = r'''
import importlib.util
import inspect
import json
import pathlib
import sys

solver, checker_path, data_root = sys.argv[1], sys.argv[2], sys.argv[3]
production = json.loads(sys.argv[4])
probes = json.loads(sys.argv[5])
out = {"pythonVersion": "%d.%d.%d" % sys.version_info[:3], "assertions": []}

def A(aid, ok, detail=""):
    out["assertions"].append(
        {"id": aid, "status": "PASS" if ok else "FAIL", "detail": str(detail)[:400]})

sys.path.insert(0, solver)
try:
    import myalgorithm
    A("import-myalgorithm", True, myalgorithm.__file__)
    A("myalgorithm-path-isolated",
      pathlib.Path(myalgorithm.__file__).resolve().is_relative_to(
          pathlib.Path(solver).resolve()), myalgorithm.__file__)
except Exception as exc:
    A("import-myalgorithm", False, repr(exc))
    print(json.dumps(out)); sys.exit(0)

try:
    import sscore
    A("import-sscore", True, sscore.__file__)
    A("sscore-path-isolated",
      pathlib.Path(sscore.__file__).resolve().is_relative_to(
          pathlib.Path(solver).resolve()), sscore.__file__)
except Exception as exc:
    A("import-sscore", False, repr(exc))
    print(json.dumps(out)); sys.exit(0)

names = set(dir(sscore))
for cls in [getattr(sscore, n) for n in dir(sscore) if isinstance(getattr(sscore, n), type)]:
    names |= set(dir(cls))
missing_prod = [n for n in production if n not in names]
missing_probe = [n for n in probes if n not in names]
A("production-exports", not missing_prod, f"missing={missing_prod}")
A("canonical-probe-exports", not missing_probe, f"missing={missing_probe}")

try:
    sig = inspect.signature(myalgorithm.algorithm)
    params = list(sig.parameters.values())
    ok = (len(params) == 2 and params[0].name == "prob_info"
          and params[1].name == "timelimit" and params[1].default == 60)
    A("public-signature", ok, str(sig))
except Exception as exc:
    A("public-signature", False, repr(exc))

spec = importlib.util.spec_from_file_location("utils", checker_path)
checker = importlib.util.module_from_spec(spec)
sys.modules["utils"] = checker
try:
    spec.loader.exec_module(checker)
    A("checker-loads", hasattr(checker, "check_feasibility"),
      "check_feasibility present")
except Exception as exc:
    A("checker-loads", False, repr(exc))

try:
    res = myalgorithm.algorithm({}, 1)
    A("malformed-fail-closed", res == {"operations": {}}, repr(res)[:120])
except Exception as exc:
    A("malformed-fail-closed", False, f"raised {exc!r}")

for pid in ("1", "6"):
    fx = pathlib.Path(data_root) / "preliminary_test" / f"prob_{pid}.json"
    try:
        prob = json.loads(fx.read_text())
        sol = myalgorithm._trivial(json.loads(fx.read_text()))
        res = checker.check_feasibility(prob, json.loads(json.dumps(sol)))
        A(f"trivial-feasible-P{pid}", bool(res.get("feasible")),
          f"objective={res.get('objective')}")
    except Exception as exc:
        A(f"trivial-feasible-P{pid}", False, repr(exc))

print(json.dumps(out))
'''


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--solver", required=True)
    ap.add_argument("--checker", required=True)
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--receipt", required=True)
    ap.add_argument("--python", default=DEFAULT_PY)
    ap.add_argument("--gate-id", default="U-DRIVER")
    args = ap.parse_args(argv)

    solver = pathlib.Path(args.solver).resolve()
    checker = pathlib.Path(args.checker).resolve()
    data_root = pathlib.Path(args.data_root).resolve()
    for p, label in ((solver, "solver"), (checker, "checker"), (data_root, "data-root")):
        if not p.exists():
            print(f"UNIT FAIL: missing {label}: {p}", file=sys.stderr)
            return 2

    tree_sha, _ = rs.tree_sha256(solver)
    checker_sha = rs.sha256_file(checker)

    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "OMP_NUM_THREADS": "4",
    }
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tf:
        tf.write(INNER)
        inner_path = tf.name
    try:
        proc = subprocess.run(
            [args.python, inner_path, str(solver), str(checker), str(data_root),
             json.dumps(PRODUCTION_EXPORTS), json.dumps(CANONICAL_PROBE_EXPORTS)],
            capture_output=True, text=True, timeout=600, env=env)
    finally:
        os.unlink(inner_path)

    if proc.returncode != 0 or not proc.stdout.strip():
        print(f"UNIT FAIL: inner rc={proc.returncode} stderr={proc.stderr[-2000:]}",
              file=sys.stderr)
        return 2
    inner = json.loads(proc.stdout.strip().splitlines()[-1])
    assertions = inner["assertions"]
    assertions.insert(0, {
        "id": "python-312", "status":
        "PASS" if inner["pythonVersion"].startswith("3.12") else "FAIL",
        "detail": inner["pythonVersion"]})
    status = "PASS" if all(a["status"] == "PASS" for a in assertions) else "FAIL"

    receipt = {
        "schemaVersion": rs.SCHEMA_VERSION,
        "kind": "unit",
        "status": status,
        "createdAt": rs.now_iso(),
        "gateId": args.gate_id,
        "tool": {"path": "tools/release_unit.py", "sha256": rs.sha256_file(__file__)},
        "python": {"executable": args.python, "version": inner["pythonVersion"]},
        "solver": {"path": str(solver), "treeSha256": tree_sha},
        "checker": {"path": str(checker), "sha256": checker_sha},
        "dataRoot": {"path": str(data_root)},
        "assertions": assertions,
    }
    receipt_sha = rs.write_receipt(args.receipt, receipt)
    print(json.dumps({"gateId": args.gate_id, "status": status,
                      "receipt": args.receipt, "receiptSha256": receipt_sha}))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
