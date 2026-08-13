#!/usr/bin/env bash
#SBATCH -p titan
#SBATCH -c 1
#SBATCH -t 00:30:00
#SBATCH --array=0-47%7
#SBATCH -J jasg-archive-gate
#SBATCH -o runs/obstruction-exit-archive-gate-%A_%a.log
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
HARNESS_ROOT="$(persistent_path harness "${OGC_ARCHIVE_HARNESS_ROOT:-$ROOT}")"
CANDIDATE="$(persistent_path candidate "${1:?candidate build required}")"
REFERENCE="$(persistent_path reference "${2:?reference build required}")"
PLATEAUS="$(persistent_path plateaus "${3:?frozen plateau directory required}")"
OUT="$(persistent_path output "${4:?persistent result directory required}")"
PROFILE="${5:?profile required: heldout or dev}"
[[ -d "$ROOT" ]] || die "repository is not a directory: $ROOT"
[[ -d "$CANDIDATE" ]] || die "candidate build is not a directory: $CANDIDATE"
[[ -d "$REFERENCE" ]] || die "reference build is not a directory: $REFERENCE"
[[ -d "$PLATEAUS" ]] || die "plateau snapshot is not a directory: $PLATEAUS"

plateau_seeds=(7 11 23 41)
case "$PROFILE" in
  heldout) eval_seeds=(3 17 31 47) ;;
  dev) eval_seeds=(7 11 23 41) ;;
  *) die "profile must be heldout or dev, got: $PROFILE" ;;
esac

task="${SLURM_ARRAY_TASK_ID:?SLURM_ARRAY_TASK_ID is required}"
[[ "$task" =~ ^[0-9]+$ ]] || die "invalid array task: $task"
(( task >= 0 && task < 48 )) || die "array task out of range: $task"

modes=(reference control archive)
cell=$((task / 3))
mode="${modes[$((task % 3))]}"
instance=$((3 + cell / 4))
seed_index=$((cell % 4))
eval_seed="${eval_seeds[$seed_index]}"
plateau_seed="${plateau_seeds[$seed_index]}"
solver="$CANDIDATE"
if [[ "$mode" == reference ]]; then
  solver="$REFERENCE"
fi

plateau="$PLATEAUS/p${instance}-s${plateau_seed}.json"
[[ -f "$plateau" ]] || die "frozen plateau is missing: $plateau"
RESULT_DIR="$OUT/$PROFILE"
result="$RESULT_DIR/p${instance}-e${eval_seed}-ps${plateau_seed}-${mode}.json"
[[ ! -e "$result" && ! -L "$result" ]] ||
  die "refusing to overwrite gate result: $result"

arm="p${instance}-e${eval_seed}-ps${plateau_seed}-${mode}"
RUNTIME="$OUT/.runtime/$PROFILE/$arm"
mkdir -p "$RESULT_DIR" "$RUNTIME/home" "$RUNTIME/tmp"
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
[[ -f "$HARNESS_ROOT/tools/obstruction_exit_archive.py" ]] ||
  die "archive gate driver is missing"

"$PY" "$HARNESS_ROOT/tools/obstruction_exit_archive.py" run \
  "$solver" "$plateau" "$instance" "$eval_seed" "$plateau_seed" \
  "$mode" "$result" --iters 20000 --checkpoint 1000
