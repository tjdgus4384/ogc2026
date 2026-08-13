#!/usr/bin/env bash
#SBATCH -p titan
#SBATCH -c 1
#SBATCH -t 00:10:00
#SBATCH -J jasg-closure-build
#SBATCH -o runs/obstruction-exit-closure-build-%j.log
set -euo pipefail

ROOT="${OGC_ROOT:-$PWD}"
BUILD="${1:?persistent build directory required}"
PY="${OGC_PY312:-$ROOT/.miniforge/envs/ogc2026/bin/python}"
if [[ ! -x "$PY" ]]; then
  COMMON_DIR="$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir)"
  PY="$(dirname "$COMMON_DIR")/.miniforge/envs/ogc2026/bin/python"
fi
[[ -x "$PY" ]] || { echo "Python 3.12 environment not found" >&2; exit 2; }

PY="$PY" "$BUILD/build.sh"
# geometry_test currently exercises a population-tail-only helper that is not
# part of the frozen solver source.  The closure gate owns and runs the exact
# regression test below; the production import is checked separately.
g++ -O2 -std=c++17 "$BUILD/tests/exit_closure_test.cpp" \
  -o "$BUILD/exit_closure_test"
"$BUILD/exit_closure_test"

"$PY" - "$BUILD" <<'PY'
import sys
from pathlib import Path

build = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(build))
import sscore

required = ("get_checkpoint_solution", "checkpoint_official")
engine = sscore.Engine()
for name in required:
    if not hasattr(engine, name):
        raise RuntimeError(f"candidate API is missing {name}")
print("closure build and unit tests OK", flush=True)
PY
