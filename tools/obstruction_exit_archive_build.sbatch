#!/usr/bin/env bash
#SBATCH -p titan
#SBATCH -c 1
#SBATCH -t 00:15:00
#SBATCH -J jasg-archive-build
#SBATCH -o runs/obstruction-exit-archive-build-%j.log
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

# Only a full object ID is accepted.  In particular, HEAD, a branch, a tag,
# an abbreviated hash, and a working-tree source directory are all rejected.
COMMIT="${1:?exact git commit required}"
DEST_INPUT="${2:?new persistent destination required}"
ROOT="$(persistent_path repository "${OGC_ROOT:-${SLURM_SUBMIT_DIR:-$PWD}}")"
[[ -d "$ROOT" ]] || die "repository is not a directory: $ROOT"
git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
  die "not a git worktree: $ROOT"

object_format="$(git -C "$ROOT" rev-parse --show-object-format)"
case "$object_format" in
  sha1) object_length=40 ;;
  sha256) object_length=64 ;;
  *) die "unsupported git object format: $object_format" ;;
esac
if [[ ${#COMMIT} -ne $object_length || ! "$COMMIT" =~ ^[[:xdigit:]]+$ ]]; then
  die "commit must be a full $object_length-character $object_format object ID"
fi
if ! RESOLVED_COMMIT="$(git -C "$ROOT" rev-parse --verify "${COMMIT}^{commit}")"; then
  die "git object is not a commit: $COMMIT"
fi
[[ "${COMMIT,,}" == "$RESOLVED_COMMIT" ]] ||
  die "commit must be the exact, unabbreviated commit object ID"

DEST="$(persistent_path destination "$DEST_INPUT")"
[[ ! -e "$DEST" && ! -L "$DEST" ]] ||
  die "destination must be new: $DEST"
MUTABLE_SOLVER="$(realpath -e -- "$ROOT/solver")"
if [[ "$DEST" == "$MUTABLE_SOLVER" || "$DEST" == "$MUTABLE_SOLVER/"* ]]; then
  die "destination must not overlay the mutable working-tree solver"
fi

for member in solver/build.sh solver/src/api.cpp \
              solver/tests/exit_closure_test.cpp; do
  git -C "$ROOT" cat-file -e "$RESOLVED_COMMIT:$member" 2>/dev/null ||
    die "commit does not contain $member"
done

PY="${OGC_PY312:-$ROOT/.miniforge/envs/ogc2026/bin/python}"
if [[ ! -x "$PY" ]]; then
  COMMON_DIR="$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir)"
  PY="$(dirname "$COMMON_DIR")/.miniforge/envs/ogc2026/bin/python"
fi
PY="$(persistent_path Python "$PY")"
[[ -x "$PY" ]] || die "Python 3.12 environment not found: $PY"

mkdir -p "$DEST/.runtime/build/home" "$DEST/.runtime/build/tmp"
export HOME="$DEST/.runtime/build/home"
export TMPDIR="$DEST/.runtime/build/tmp"
unset OGC_PHASE2_ORIS OGC_EXIT_TWIN OGC_EXIT_TWIN_SHADOW OGC_PREFP
export OGC_CAUSAL_BAY_RNG=1
export OGC_KEEP_ASSIGNMENT_LOCK=1
export OMP_NUM_THREADS=1

# This stream is the only source of solver files.  Dirty working-tree files
# are never copied into the build destination.
git -C "$ROOT" archive --format=tar "$RESOLVED_COMMIT" solver |
  tar --extract --file=- --directory="$DEST" --strip-components=1
printf '%s\n' "$RESOLVED_COMMIT" >"$DEST/SOURCE_COMMIT"
chmod 0444 "$DEST/SOURCE_COMMIT"

PY="$PY" "$DEST/build.sh"
g++ -O2 -std=c++17 -fopenmp "$DEST/tests/exit_closure_test.cpp" \
  -o "$DEST/exit_closure_test"
"$DEST/exit_closure_test"

"$PY" - "$DEST" "$RESOLVED_COMMIT" <<'PY'
import sys
from pathlib import Path

build = Path(sys.argv[1]).resolve()
commit = sys.argv[2]
sys.path.insert(0, str(build))
import sscore

module_path = Path(sscore.__file__).resolve()
if module_path.parent != build:
    raise RuntimeError(f"sscore imported from {module_path}, not {build}")

engine = sscore.Engine()
expected = {
    "get_checkpoint_solution": list,
    "checkpoint_official": int,
    "get_archive_checkpoint_solution": list,
    "archive_checkpoint_official": int,
    "get_control_best_solution": list,
    "control_best_objective": float,
    "final_rng_state": str,
    "get_archive_solution": list,
    "archive_returned": int,
    "returned_official": int,
}
for name, value_type in expected.items():
    getter = getattr(engine, name, None)
    if getter is None or not callable(getter):
        raise RuntimeError(f"candidate API is missing callable getter {name}")
    value = getter()
    if not isinstance(value, value_type):
        raise RuntimeError(
            f"candidate getter {name} returned {type(value).__name__}, "
            f"expected {value_type.__name__}"
        )
if "exitClosureArchive" not in (sscore.Engine.run_alns.__doc__ or ""):
    raise RuntimeError("run_alns is missing the exitClosureArchive argument")
print(f"archive build {commit} import and API getters OK", flush=True)
PY

echo "archive build and exit_closure_test OK: $DEST ($RESOLVED_COMMIT)"
