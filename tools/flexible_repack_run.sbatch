#!/usr/bin/env bash
#SBATCH -p titan
#SBATCH -c 1
#SBATCH --mem=16G
#SBATCH -t 00:30:00
#SBATCH -J flex-repack-run
#SBATCH -o /data_fast/home/tjdgus4384/priv/ogc2026-worktrees/codex-fundamental/runs/flexible-repack-run-%j.out
#SBATCH -e /data_fast/home/tjdgus4384/priv/ogc2026-worktrees/codex-fundamental/runs/flexible-repack-run-%j.err
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
    /tmp|/tmp/*|/var/tmp|/var/tmp/*|/dev/shm|/dev/shm/*|/run|/run/*)
      die "$label must be on persistent storage: $resolved"
      ;;
  esac
  printf '%s\n' "$resolved"
}

BUILD="$(persistent_path build "${1:?immutable build required}")"
PROBLEM="$(persistent_path problem "${2:?problem JSON required}")"
PLATEAU="$(persistent_path plateau "${3:?plateau JSON required}")"
INSTANCE="${4:?instance number required}"
PLATEAU_SEED="${5:?plateau seed required}"
OUTPUT="$(persistent_path output "${6:?output JSON required}")"

[[ $# -eq 6 ]] ||
  die "usage: $0 BUILD PROBLEM PLATEAU INSTANCE PLATEAU_SEED OUTPUT"
[[ -d "$BUILD" && ! -L "$BUILD" ]] || die "build is not a real directory: $BUILD"
[[ -f "$PROBLEM" ]] || die "problem is not a regular file: $PROBLEM"
[[ -f "$PLATEAU" ]] || die "plateau is not a regular file: $PLATEAU"
case "$INSTANCE" in
  3|4|5|6) ;;
  *) die "instance must be one of 3, 4, 5, 6: $INSTANCE" ;;
esac
case "$PLATEAU_SEED" in
  7|11|23|41) ;;
  *) die "plateau seed must be one of 7, 11, 23, 41: $PLATEAU_SEED" ;;
esac
[[ ! -e "$OUTPUT" && ! -L "$OUTPUT" ]] ||
  die "refusing to overwrite output: $OUTPUT"
case "$OUTPUT" in
  "$BUILD"|"$BUILD"/*) die "output must be outside the immutable build" ;;
esac

SOURCE_COMMIT="$BUILD/SOURCE_COMMIT"
MANIFEST="$BUILD/SHA256SUMS"
HARNESS="$BUILD/tools/flexible_repack.py"
SOLVER="$BUILD/solver"
[[ -f "$SOURCE_COMMIT" && ! -L "$SOURCE_COMMIT" ]] ||
  die "build SOURCE_COMMIT is missing or not a regular file"
[[ -s "$MANIFEST" && ! -L "$MANIFEST" ]] ||
  die "build SHA256SUMS is missing, empty, or not a regular file"
[[ -f "$HARNESS" && ! -L "$HARNESS" ]] ||
  die "immutable flexible_repack.py is missing"
[[ -d "$SOLVER" && ! -L "$SOLVER" ]] ||
  die "immutable solver directory is missing"
[[ -f "$SOLVER/myalgorithm.py" && ! -L "$SOLVER/myalgorithm.py" ]] ||
  die "immutable solver/myalgorithm.py is missing"

mapfile -t source_lines <"$SOURCE_COMMIT"
(( ${#source_lines[@]} == 1 )) || die "SOURCE_COMMIT must contain exactly one line"
[[ "${source_lines[0]}" =~ ^[[:xdigit:]]{40}$|^[[:xdigit:]]{64}$ ]] ||
  die "SOURCE_COMMIT is not a full Git object id"

grep -Fq "  $SOURCE_COMMIT" "$MANIFEST" ||
  die "SOURCE_COMMIT is not covered by SHA256SUMS"
grep -Fq "  $HARNESS" "$MANIFEST" ||
  die "flexible_repack.py is not covered by SHA256SUMS"
grep -Fq "  $SOLVER/myalgorithm.py" "$MANIFEST" ||
  die "solver/myalgorithm.py is not covered by SHA256SUMS"
sha256sum --check --strict --quiet "$MANIFEST" ||
  die "immutable build checksum verification failed"
diff --brief "$MANIFEST" <(
  find "$BUILD" -type f ! -name SHA256SUMS -print0 |
    sort -z | xargs -0 -r sha256sum
) >/dev/null || die "immutable build file set or hashes differ from SHA256SUMS"

PY="${OGC_PY312:-/data_fast/home/tjdgus4384/priv/ogc2026/.miniforge/envs/ogc2026/bin/python}"
PY="$(persistent_path Python "$PY")"
[[ -x "$PY" ]] || die "Python 3.12 environment is unavailable: $PY"

OUTPUT_DIR="$(dirname "$OUTPUT")"
mkdir -p "$OUTPUT_DIR"
[[ ! -e "$OUTPUT" && ! -L "$OUTPUT" ]] ||
  die "output appeared before launch: $OUTPUT"

JOB_ID="${SLURM_JOB_ID:?SLURM_JOB_ID is required}"
RUNTIME="$(persistent_path runtime "${OUTPUT}.runtime-${JOB_ID}")"
[[ ! -e "$RUNTIME" && ! -L "$RUNTIME" ]] ||
  die "runtime directory already exists: $RUNTIME"
mkdir -m 0700 "$RUNTIME"
mkdir -m 0700 "$RUNTIME/tmp"

umask 077
export TMPDIR="$RUNTIME/tmp"
export PYTHONNOUSERSITE=1
export PYTHONDONTWRITEBYTECODE=1
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
unset OGC_PHASE2_ORIS OGC_EXIT_TWIN OGC_EXIT_TWIN_SHADOW OGC_PREFP

"$PY" "$HARNESS" "$PROBLEM" "$PLATEAU" "$OUTPUT" \
  --solver "$SOLVER" \
  --instance "$INSTANCE" \
  --plateau-seed "$PLATEAU_SEED" \
  --free-count 0 \
  --candidate-cap 0 \
  --seconds 60 \
  --seed 1
