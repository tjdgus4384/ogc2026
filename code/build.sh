#!/usr/bin/env bash
# Rebuild the final submission's CPython 3.12 extension.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-python3}"

"$PY" -c 'import sys; assert sys.version_info[:2] == (3, 12), "Use Python 3.12 to match the submission ABI."'
EXT="$("$PY" -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX"))')"
read -r -a PYBIND_INCLUDES <<< "$("$PY" -m pybind11 --includes)"

cd "$HERE"
g++ -O3 -std=c++17 -funroll-loops -fopenmp -march=x86-64-v3 \
    -ffp-contract=off -flto -shared -fPIC \
    "${PYBIND_INCLUDES[@]}" src/api.cpp -o "sscore${EXT}"

"$PY" -c 'import sscore; print("sscore import OK")'
