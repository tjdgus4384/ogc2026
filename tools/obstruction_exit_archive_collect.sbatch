#!/usr/bin/env bash
#SBATCH -p titan
#SBATCH -c 1
#SBATCH -t 00:05:00
#SBATCH -J jasg-archive-collect
#SBATCH -o runs/obstruction-exit-archive-collect-%j.log
set -euo pipefail

die() {
  echo "ERROR: $*" >&2
  exit 2
}

persistent_path() {
  local label="$1"
  local value="$2"
  local resolved
  resolved="$(realpath -m -- "$value")" || die "cannot resolve $label: $value"
  case "$resolved" in
    /tmp|/tmp/*|/var/tmp|/var/tmp/*|/dev/shm|/dev/shm/*)
      die "$label must be on persistent storage: $resolved"
      ;;
  esac
  printf '%s\n' "$resolved"
}

ROOT="$(persistent_path repository "${OGC_ROOT:-${SLURM_SUBMIT_DIR:-$PWD}}")"
OUT="$(persistent_path output "${1:?result directory required}")"
PROFILE="${2:?profile required: heldout or dev}"
case "$PROFILE" in
  heldout|dev) ;;
  *) die "profile must be heldout or dev, got: $PROFILE" ;;
esac
[[ -d "$OUT/$PROFILE" ]] ||
  die "profile result directory is missing: $OUT/$PROFILE"

run_id="${SLURM_JOB_ID:-manual-$$}"
RUNTIME="$OUT/.runtime/$PROFILE/collect-$run_id"
mkdir -p "$RUNTIME/home" "$RUNTIME/tmp"
chmod 0700 "$RUNTIME/home" "$RUNTIME/tmp"
export HOME="$RUNTIME/home"
export TMPDIR="$RUNTIME/tmp"
unset OGC_PHASE2_ORIS OGC_EXIT_TWIN OGC_EXIT_TWIN_SHADOW OGC_PREFP
export OGC_CAUSAL_BAY_RNG=1
export OGC_KEEP_ASSIGNMENT_LOCK=1
export OMP_NUM_THREADS=1

PY="${OGC_PY312:-$ROOT/.miniforge/envs/ogc2026/bin/python}"
if [[ ! -x "$PY" ]]; then
  COMMON_DIR="$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir)"
  PY="$(dirname "$COMMON_DIR")/.miniforge/envs/ogc2026/bin/python"
fi
PY="$(persistent_path Python "$PY")"
[[ -x "$PY" ]] || die "Python 3.12 environment not found: $PY"

"$PY" "$ROOT/tools/obstruction_exit_archive.py" collect "$OUT" "$PROFILE"
