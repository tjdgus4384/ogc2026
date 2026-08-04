#!/usr/bin/env bash
# Build the sscore kernel extension from src/ into this directory.
#
# The grading server runs python 3.12, so the shipped .so must be a
# cpython-312 build. Use the project conda env (see README.md), not the system
# python -- a 3.10 .so will not import on the server.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-$HERE/../.miniforge/envs/ogc2026/bin/python}"

if [[ ! -x "$PY" ]]; then
    echo "no python at $PY -- create the env first (see README.md)" >&2
    exit 1
fi

VER=$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
if [[ "$VER" != "3.12" ]]; then
    echo "WARNING: building against python $VER, but the grading server is 3.12." >&2
    echo "         The resulting .so will NOT import there." >&2
fi

cd "$HERE"
EXT=$("$PY" -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX"))')

set -x
g++ -O3 -std=c++17 -funroll-loops -fopenmp -shared -fPIC \
    -march=x86-64-v3 -ffp-contract=off -flto \
    $("$PY" -m pybind11 --includes) \
    src/api.cpp -o "sscore${EXT}"
set +x

echo "built sscore${EXT}"
"$PY" -c "import sys; sys.path.insert(0,'$HERE'); import sscore; print('import OK')"
