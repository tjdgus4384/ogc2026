#!/usr/bin/env bash
#SBATCH -p titan
#SBATCH --exclusive
#SBATCH -c 32
#SBATCH -t 00:10:00
#SBATCH -J p2-oris
#SBATCH -o runs/phase2-orientation-%j.log
set -uo pipefail

ROOT="${OGC_ROOT:-$PWD}"
if [[ -n "${OGC_PY312:-}" ]]; then
  PY="$OGC_PY312"
elif [[ -x "$ROOT/.miniforge/envs/ogc2026/bin/python" ]]; then
  PY="$ROOT/.miniforge/envs/ogc2026/bin/python"
else
  COMMON_DIR="$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir)"
  CANONICAL_ROOT="$(dirname "$COMMON_DIR")"
  PY="$CANONICAL_ROOT/.miniforge/envs/ogc2026/bin/python"
fi
[[ -x "$PY" ]] || { echo "Python 3.12 environment not found: $PY" >&2; exit 2; }

SOLVER="${1:-$ROOT/builds/phase2-oris}"
OUT="${2:-$ROOT/runs/phase2-orientation-${SLURM_JOB_ID}}"
mkdir -p "$OUT"
unset OGC_PHASE2_ORIS OGC_CAUSAL_BAY_RNG OGC_EXIT_TWIN \
  OGC_EXIT_TWIN_SHADOW OGC_EXIT_TWIN_POST

pids=()
slot=0
for instance in 3 4 5 6; do
  for mode in off on; do
    cpu=$((slot * 4))
    if [[ "$mode" == "on" ]]; then
      mode_env=(env OGC_PHASE2_ORIS=1)
    else
      mode_env=(env -u OGC_PHASE2_ORIS)
    fi
    OMP_NUM_THREADS=1 taskset -c "$cpu-$((cpu + 3))" "${mode_env[@]}" \
      "$PY" "$ROOT/tools/phase2_orientation_probe.py" run \
      "$SOLVER" "$instance" "$mode" "$OUT/p${instance}-${mode}.json" &
    pids+=("$!")
    slot=$((slot + 1))
  done
done

status=0
for pid in "${pids[@]}"; do
  if ! wait "$pid"; then status=1; fi
done
if (( status != 0 )); then exit "$status"; fi
"$PY" "$ROOT/tools/phase2_orientation_probe.py" collect "$OUT"
