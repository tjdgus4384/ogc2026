#!/usr/bin/env bash
#SBATCH -J popcand-build
#SBATCH -p titan
#SBATCH -c 4
#SBATCH --mem=16G
#SBATCH -t 01:00:00
#
# Population-candidate sealed build + unit smoke (ralplan Revision 4 Batch 1).
# Compiles ONLY the seated candidate sources (copied from the read-only
# candidate tree into an isolated work dir), records the compiler depfile and
# verifies its local closure equals the seated member set, imports the built
# .so in isolation, installs it INTO the candidate tree as the tested
# treatment binary, then runs tools/population_candidate_unit.py.
#
# Usage: sbatch tools/population_candidate_build.sbatch
set -euo pipefail

REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="${OGC_PY312:-$REPO/.miniforge/envs/ogc2026/bin/python}"
CAND="$REPO/runs/release/population-candidate/candidate"
BUILD_ROOT="$REPO/runs/release/population-candidate/build"
EVID="$REPO/runs/release/population-candidate/evidence"
SO="sscore.cpython-312-x86_64-linux-gnu.so"

rm -rf "$BUILD_ROOT"
mkdir -p "$BUILD_ROOT/src" "$EVID"

"$PY" - "$CAND" "$BUILD_ROOT" "$EVID/candidate-build.json" "$PY" "$REPO" <<'PYEOF'
import json, os, pathlib, shutil, stat, subprocess, sys
cand, build_root, receipt_path, py, repo = (pathlib.Path(sys.argv[1]),
                                            pathlib.Path(sys.argv[2]),
                                            sys.argv[3], sys.argv[4],
                                            pathlib.Path(sys.argv[5]))
sys.path.insert(0, str(repo / "tools"))
import release_schema as rs

SO = "sscore.cpython-312-x86_64-linux-gnu.so"
assertions = []
def A(aid, ok, detail=""):
    assertions.append({"id": aid, "status": "PASS" if ok else "FAIL",
                       "detail": str(detail)[:400]})

# isolated source copy: seated src members only
members = sorted(p.name for p in (cand / "src").glob("*"))
for name in members:
    shutil.copy2(cand / "src" / name, build_root / "src" / name)

includes = subprocess.run([py, "-m", "pybind11", "--includes"],
                          capture_output=True, text=True, check=True).stdout.split()
ext = subprocess.run([py, "-c",
                      "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))"],
                     capture_output=True, text=True, check=True).stdout.strip()
out_so = build_root / f"sscore{ext}"
dep = build_root / "api.d"
cmd = (["g++", "-O3", "-std=c++17", "-funroll-loops", "-fopenmp",
        "-march=x86-64-v3", "-ffp-contract=off", "-flto", "-shared", "-fPIC",
        "-MMD", "-MF", str(dep)] + includes
       + ["src/api.cpp", "-o", str(out_so)])
proc = subprocess.run(cmd, cwd=build_root, capture_output=True, text=True)
A("candidate-compile", proc.returncode == 0, proc.stderr[-400:])

dep_local, dep_sha = [], None
if dep.exists():
    dep_sha = rs.sha256_file(dep)
    toks = dep.read_text().replace("\\\n", " ").split()
    for t in toks[1:]:
        tp = pathlib.Path(t)
        if not tp.is_absolute():
            tp = build_root / tp
        tp = tp.resolve()
        try:
            rel = tp.relative_to(build_root.resolve())
            dep_local.append(str(rel))
        except ValueError:
            pass  # system/pybind header
    dep_srcs = sorted({pathlib.Path(d).name for d in dep_local})
    A("depfile-closure-equals-members", dep_srcs == members,
      f"depfile={dep_srcs} members={members}")
else:
    A("depfile-closure-equals-members", False, "no depfile")

out_so_sha = rs.sha256_file(out_so) if proc.returncode == 0 else None

INV = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import sscore
names = set(dir(sscore))
for cls in [getattr(sscore, n) for n in dir(sscore)
            if isinstance(getattr(sscore, n), type)]:
    names |= set(dir(cls))
print(json.dumps(sorted(n for n in names if not n.startswith("_"))))
'''
imp = subprocess.run([py, "-c", INV, str(build_root)], capture_output=True,
                     text=True,
                     env={"PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
                          "OMP_NUM_THREADS": "1", "HOME": "/tmp",
                          "PATH": "/usr/bin:/bin"})
exports = json.loads(imp.stdout) if imp.returncode == 0 else []
A("candidate-import", imp.returncode == 0, imp.stderr[-400:] or f"{len(exports)} exports")
A("export-run-kernel-parallel-bandit", "run_kernel_parallel_bandit" in exports)
A("export-set-pref-p", "set_pref_p" in exports)
A("no-forbidden-run-kernel-bandit", "run_kernel_bandit" not in exports)
A("no-forbidden-set-exact-events", "set_exact_events" not in exports)

# install the tested binary into the candidate tree (this IS the treatment)
if out_so_sha and imp.returncode == 0:
    dest = cand / SO
    if dest.exists():
        dest.chmod(0o644)
    shutil.copy2(out_so, dest)
    dest.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    A("installed-into-candidate", rs.sha256_file(dest) == out_so_sha)
else:
    A("installed-into-candidate", False, "no importable build output")

gxx = subprocess.run(["g++", "--version"], capture_output=True,
                     text=True).stdout.splitlines()[0]
pyver = subprocess.run([py, "--version"], capture_output=True,
                       text=True).stdout.strip()
pb = subprocess.run([py, "-c", "import pybind11; print(pybind11.__version__)"],
                    capture_output=True, text=True).stdout.strip()
status = "PASS" if all(a["status"] == "PASS" for a in assertions) else "FAIL"
receipt = {
    "schemaVersion": 1, "kind": "candidate-build", "status": status,
    "createdAt": rs.now_iso(),
    "jobId": os.environ.get("SLURM_JOB_ID", "local"),
    "candidateTree": str(cand),
    "command": " ".join(cmd), "commandSha256": rs.canonical_hash(cmd),
    "toolchain": {"gxx": gxx, "pybind11": pb, "python": pyver},
    "depfileSha256": dep_sha, "depfileLocal": sorted(dep_local),
    "outputSoSha256": out_so_sha,
    "importStatus": {"imported": imp.returncode == 0,
                     "exports": len(exports)},
    "assertions": assertions,
}
body = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
pathlib.Path(receipt_path).write_text(body)
print(json.dumps({"status": status, "receipt": receipt_path}))
sys.exit(0 if status == "PASS" else 1)
PYEOF

# unit smoke on the freshly installed tested binary
"$PY" "$REPO/tools/population_candidate_unit.py" \
    --candidate "$CAND" --data-root "$REPO/data" \
    --receipt "$EVID/candidate-unit.json" --python "$PY"
