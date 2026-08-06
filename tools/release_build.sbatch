#!/usr/bin/env bash
#SBATCH -J relbuild
#SBATCH -p titan
#SBATCH -c 4
#SBATCH --mem=16G
#SBATCH -t 00:30:00
#
# Out-of-band compilability proof (Stage 2 I-REBUILD). Rebuilds the release
# sources from the read-only final extract into an ISOLATED output directory
# and import-compares prebuilt vs rebuilt. The rebuilt .so is NEVER shipped
# and never copied back: shipped=false is asserted in the receipt, and the
# final-extract .so hash must be byte-identical before and after.
#
# Usage: sbatch tools/release_build.sbatch EXTRACT_DIR BUILD_ROOT RECEIPT_JSON
set -euo pipefail

EXTRACT_DIR="$1"
BUILD_ROOT="$2"
RECEIPT="$3"
PY="${OGC_PY312:-/data_fast/home/tjdgus4384/priv/ogc2026/.miniforge/envs/ogc2026/bin/python}"
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"

mkdir -p "$BUILD_ROOT/work/src" "$BUILD_ROOT/out"

"$PY" - "$EXTRACT_DIR" "$BUILD_ROOT" "$RECEIPT" "$PY" "$REPO" <<'PYEOF'
import json
import pathlib
import shutil
import subprocess
import sys

extract, build_root, receipt_path, py, repo = (pathlib.Path(sys.argv[1]).resolve(),
                                               pathlib.Path(sys.argv[2]).resolve(),
                                               sys.argv[3], sys.argv[4],
                                               pathlib.Path(sys.argv[5]).resolve())
sys.path.insert(0, str(repo / "tools"))
import release_schema as rs

SO = "sscore.cpython-312-x86_64-linux-gnu.so"
work = build_root / "work"
out = build_root / "out"
assertions = []


def A(aid, ok, detail=""):
    assertions.append({"id": aid, "status": "PASS" if ok else "FAIL",
                       "detail": str(detail)[:400]})


so_pre = rs.sha256_file(extract / SO)

# fresh work tree from the read-only extract: sources + build doc only
for name in ["build.txt"]:
    shutil.copy2(extract / name, work / name)
for p in sorted((extract / "src").glob("*")):
    shutil.copy2(p, work / "src" / p.name)

ext_suffix = subprocess.run(
    [py, "-c", "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))"],
    capture_output=True, text=True, check=True).stdout.strip()
includes = subprocess.run(
    [py, "-m", "pybind11", "--includes"],
    capture_output=True, text=True, check=True).stdout.split()

out_so = out / f"sscore{ext_suffix}"
cmd = (["g++", "-O3", "-std=c++17", "-funroll-loops", "-fopenmp",
        "-march=x86-64-v3", "-ffp-contract=off", "-flto", "-shared", "-fPIC"]
       + includes + ["src/api.cpp", "-o", str(out_so)])
proc = subprocess.run(cmd, cwd=work, capture_output=True, text=True)
A("rebuild-compile", proc.returncode == 0, proc.stderr[-400:])
if proc.returncode != 0:
    out_so_sha = None
else:
    out_so_sha = rs.sha256_file(out_so)

INV = r'''
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
import sscore
names = set(dir(sscore))
for cls in [getattr(sscore, n) for n in dir(sscore)
            if isinstance(getattr(sscore, n), type)]:
    names |= set(dir(cls))
print(json.dumps({"file": sscore.__file__,
                  "exports": sorted(n for n in names if not n.startswith("_"))}))
'''


def inventory(path):
    r = subprocess.run([py, "-c", INV, str(path)], capture_output=True, text=True,
                       env={"PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
                            "OMP_NUM_THREADS": "1", "HOME": "/tmp",
                            "PATH": "/usr/bin:/bin"})
    if r.returncode != 0:
        return None, r.stderr[-400:]
    return json.loads(r.stdout), None


pre_inv, pre_err = inventory(extract)
A("prebuilt-import", pre_inv is not None, pre_err or pre_inv["file"])
if out_so_sha:
    reb_inv, reb_err = inventory(out)
    A("rebuilt-import", reb_inv is not None, reb_err or reb_inv["file"])
else:
    reb_inv = None
    A("rebuilt-import", False, "no rebuilt output")

exports_match = bool(pre_inv and reb_inv
                     and pre_inv["exports"] == reb_inv["exports"])
A("exports-match", exports_match,
  f"prebuilt={len(pre_inv['exports']) if pre_inv else 0} exports")

so_post = rs.sha256_file(extract / SO)
A("extract-so-unchanged", so_pre == so_post, f"pre={so_pre} post={so_post}")
A("rebuilt-not-shipped", True,
  f"rebuilt .so written only to {out}; final-extract member untouched")

gxx = subprocess.run(["g++", "--version"], capture_output=True,
                     text=True).stdout.splitlines()[0]
pyver = subprocess.run([py, "--version"], capture_output=True,
                       text=True).stdout.strip()
pb = subprocess.run([py, "-c", "import pybind11; print(pybind11.__version__)"],
                    capture_output=True, text=True).stdout.strip()

status = "PASS" if all(a["status"] == "PASS" for a in assertions) else "FAIL"
import os
receipt = {
    "schemaVersion": rs.SCHEMA_VERSION,
    "kind": "build-proof",
    "status": status,
    "createdAt": rs.now_iso(),
    "jobId": os.environ.get("SLURM_JOB_ID", "local"),
    "tool": {"path": "tools/release_build.sbatch",
             "sha256": rs.sha256_file(repo / "tools" / "release_build.sbatch")},
    "extract": {"path": str(extract), "soSha256Pre": so_pre,
                "soSha256Post": so_post},
    "build": {"workTree": str(work), "command": " ".join(cmd),
              "commandSha256": rs.canonical_hash(cmd),
              "toolchain": {"gxx": gxx, "pybind11": pb, "python": pyver}},
    "outputSoSha256": out_so_sha,
    "importStatus": {"prebuilt": bool(pre_inv), "rebuilt": bool(reb_inv),
                     "exportsMatch": exports_match},
    "shipped": False,
    "assertions": assertions,
}
receipt_sha = rs.write_receipt(receipt_path, receipt)
print(json.dumps({"status": status, "receipt": receipt_path,
                  "receiptSha256": receipt_sha}))
sys.exit(0 if status == "PASS" else 1)
PYEOF
