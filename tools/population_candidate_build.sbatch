#!/usr/bin/env bash
#SBATCH --job-name=popbuild
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=16G
#SBATCH --time=00:20:00
# Isolated one-shot build of the sealed population-candidate source.
#   sbatch -p titan tools/population_candidate_build.sbatch WORKTREE_ROOT
# Compiles ONLY runs/release/population-candidate/source (read-only seat),
# in a node-local temp dir, with a depfile proving the include closure.
# Emits evidence/candidate-build.json and seats the tested .so into
# runs/release/population-candidate/solver/.
set -euo pipefail

ROOT="$1"
WORK=$ROOT/runs/release/population-candidate
SRC=$WORK/source
EV=$WORK/evidence
PY=${OGC_PY:-/data_fast/home/tjdgus4384/priv/ogc2026/.miniforge/envs/ogc2026/bin/python}

BUILD=$(mktemp -d "${TMPDIR:-/tmp}/popcand-build-XXXXXX")
trap 'rm -rf "$BUILD"' EXIT
cp -r "$SRC" "$BUILD/tree"
chmod -R u+w "$BUILD/tree"

EXT=$("$PY" -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX"))')
INCLUDES=$("$PY" -m pybind11 --includes)
CMD="g++ -O3 -std=c++17 -funroll-loops -fopenmp -shared -fPIC -march=x86-64-v3 -ffp-contract=off -flto -MMD -MF sscore.d $INCLUDES src/api.cpp -o sscore$EXT"

cd "$BUILD/tree"
rm -f "sscore$EXT" sscore.d
set -x
eval "$CMD"
set +x

IMPORT_DIR=$(mktemp -d "${TMPDIR:-/tmp}/popcand-import-XXXXXX")
IMPORT_STATUS=FAIL
if (cd "$IMPORT_DIR" && "$PY" -c "
import sys; sys.path.insert(0, '$BUILD/tree')
import sscore
eng = sscore.Engine()
assert hasattr(eng, 'run_kernel_parallel_bandit')
assert hasattr(eng, 'set_pref_p')
assert not hasattr(eng, 'run_kernel_bandit')
assert not hasattr(eng, 'set_exact_events')
print('import OK')
"); then IMPORT_STATUS=PASS; fi
rm -rf "$IMPORT_DIR"

mkdir -p "$WORK/solver"
chmod -R u+w "$WORK/solver" || true
rm -rf "$WORK/solver"
cp -r "$SRC" "$WORK/solver"
chmod -R u+w "$WORK/solver"
cp "$BUILD/tree/sscore$EXT" "$WORK/solver/sscore$EXT"

"$PY" - "$ROOT" "$BUILD/tree" "$CMD" "$IMPORT_STATUS" "$EXT" <<'EOF'
import hashlib, json, pathlib, subprocess, sys, time
root, tree, cmd, import_status, ext = sys.argv[1:6]
work = pathlib.Path(root) / "runs/release/population-candidate"
src = work / "source"

def sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()

def tree_sha(d):
    items = [(str(p.relative_to(d)), sha(p))
             for p in sorted(d.rglob("*")) if p.is_file()]
    return hashlib.sha256(json.dumps(items).encode()).hexdigest()

dep = (pathlib.Path(tree) / "sscore.d").read_text()
deps = dep.replace("\\\n", " ").split(":", 1)[1].split()
local = sorted({d for d in deps if not pathlib.Path(d).is_absolute()})
external = sorted({d for d in deps if pathlib.Path(d).is_absolute()})
bad = [e for e in external if "/priv/ogc2026/solver" in e or "/worktrees/" in e]
so = pathlib.Path(tree) / ("sscore" + ext)
gxx = subprocess.run(["g++", "--version"], capture_output=True, text=True).stdout.splitlines()[0]
pyver = subprocess.run([sys.executable, "-c", "import sys;print(sys.version.split()[0])"],
                       capture_output=True, text=True).stdout.strip()
pb = subprocess.run([sys.executable, "-c", "import pybind11;print(pybind11.__version__)"],
                    capture_output=True, text=True).stdout.strip()
status = "PASS" if (import_status == "PASS" and not bad and so.exists()) else "FAIL"
receipt = {
    "schemaVersion": 1,
    "builtAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "status": status,
    "sourceTreeSha256": tree_sha(src),
    "commandSha256": hashlib.sha256(cmd.encode()).hexdigest(),
    "command": cmd,
    "toolchain": {"gxx": gxx, "python": pyver, "pybind11": pb,
                  "node": subprocess.run(["hostname"], capture_output=True,
                                         text=True).stdout.strip(),
                  "slurmJobId": __import__("os").environ.get("SLURM_JOB_ID")},
    "depfileLocal": local,
    "depfileExternalCount": len(external),
    "depfileEscapes": bad,
    "outputSoSha256": sha(so),
    "importStatus": import_status,
}
out = work / "evidence/candidate-build.json"
out.write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
print("build receipt:", receipt["status"], receipt["outputSoSha256"][:16])
EOF
echo done
