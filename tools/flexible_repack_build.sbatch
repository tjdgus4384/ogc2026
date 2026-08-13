#!/usr/bin/env bash
#SBATCH -p titan
#SBATCH -c 2
#SBATCH --mem=16G
#SBATCH -t 00:20:00
#SBATCH -J flex-repack-build
#SBATCH -o runs/flexible-repack-build-%j.log
set -euo pipefail

die() { echo "ERROR: $*" >&2; exit 2; }

persistent_path() {
  local label="$1" value="$2" resolved
  resolved="$(realpath -m -- "$value")" || die "cannot resolve $label: $value"
  case "$resolved" in
    /tmp|/tmp/*|/var/tmp|/var/tmp/*|/dev/shm|/dev/shm/*)
      die "$label must be persistent: $resolved" ;;
  esac
  printf '%s\n' "$resolved"
}

COMMIT="${1:?exact commit required}"
DEST="$(persistent_path destination "${2:?new destination required}")"
ROOT="$(persistent_path repository "${OGC_ROOT:-${SLURM_SUBMIT_DIR:-$PWD}}")"
[[ ! -e "$DEST" && ! -L "$DEST" ]] || die "destination already exists: $DEST"

format="$(git -C "$ROOT" rev-parse --show-object-format)"
case "$format" in sha1) length=40 ;; sha256) length=64 ;; *) die "bad git format" ;; esac
[[ ${#COMMIT} -eq $length && "$COMMIT" =~ ^[[:xdigit:]]+$ ]] ||
  die "commit must be a full $format object id"
RESOLVED="$(git -C "$ROOT" rev-parse --verify "${COMMIT}^{commit}")"
[[ "${COMMIT,,}" == "$RESOLVED" ]] || die "commit is not exact"

for member in solver/build.sh solver/src/api.cpp \
              solver/tests/exit_closure_test.cpp tools/flexible_repack.py; do
  git -C "$ROOT" cat-file -e "$RESOLVED:$member" || die "missing $member"
done

PY="${OGC_PY312:-$ROOT/.miniforge/envs/ogc2026/bin/python}"
if [[ ! -x "$PY" ]]; then
  common="$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir)"
  PY="$(dirname "$common")/.miniforge/envs/ogc2026/bin/python"
fi
PY="$(persistent_path Python "$PY")"
[[ -x "$PY" ]] || die "Python 3.12 not found"

mkdir -p "$DEST/solver" "$DEST/tools" "$DEST/runtime/tmp"
export TMPDIR="$DEST/runtime/tmp"
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1
unset OGC_PHASE2_ORIS OGC_EXIT_TWIN OGC_EXIT_TWIN_SHADOW OGC_PREFP

git -C "$ROOT" archive --format=tar "$RESOLVED" solver |
  tar --extract --file=- --directory="$DEST/solver" --strip-components=1
git -C "$ROOT" show "$RESOLVED:tools/flexible_repack.py" \
  >"$DEST/tools/flexible_repack.py"
printf '%s\n' "$RESOLVED" >"$DEST/SOURCE_COMMIT"
chmod 0444 "$DEST/SOURCE_COMMIT"

PY="$PY" "$DEST/solver/build.sh"
g++ -O2 -std=c++17 -fopenmp "$DEST/solver/tests/exit_closure_test.cpp" \
  -o "$DEST/solver/exit_closure_test"
"$DEST/solver/exit_closure_test"
"$PY" -m py_compile "$DEST/tools/flexible_repack.py"

"$PY" - "$DEST/solver" <<'PY'
import sys
from pathlib import Path
solver = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(solver))
import sscore
if Path(sscore.__file__).resolve().parent != solver:
    raise RuntimeError(f"wrong extension imported: {sscore.__file__}")
eng = sscore.Engine()
for name in ("event_corner_candidates", "pair_relations", "audit_state",
             "entry_ranks", "exit_ranks"):
    if not callable(getattr(eng, name, None)):
        raise RuntimeError(f"missing experimental API {name}")
print("flexible-repack build/API/unit PASS", flush=True)
PY

find "$DEST" -type f -print0 | sort -z | xargs -0 sha256sum >"$DEST/SHA256SUMS"
chmod 0444 "$DEST/SHA256SUMS"
echo "PASS $DEST $RESOLVED"
