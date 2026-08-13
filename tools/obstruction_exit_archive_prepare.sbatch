#!/usr/bin/env bash
#SBATCH -p titan
#SBATCH -c 1
#SBATCH -t 00:10:00
#SBATCH -J jasg-archive-prep
#SBATCH -o runs/obstruction-exit-archive-prepare-%j.log
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
SOURCE="$(persistent_path source "${1:?source plateau directory required}")"
OUT="$(persistent_path snapshot "${2:?new persistent snapshot directory required}")"
[[ -d "$SOURCE" ]] || die "source plateau directory is missing: $SOURCE"
[[ ! -e "$OUT" && ! -L "$OUT" ]] || die "snapshot destination must be new: $OUT"

for instance in 3 4 5 6; do
  for seed in 7 11 23 41; do
    [[ -f "$SOURCE/p${instance}-s${seed}.json" ]] ||
      die "source plateau is missing: $SOURCE/p${instance}-s${seed}.json"
  done
done

PY="${OGC_PY312:-$ROOT/.miniforge/envs/ogc2026/bin/python}"
if [[ ! -x "$PY" ]]; then
  COMMON_DIR="$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir)"
  PY="$(dirname "$COMMON_DIR")/.miniforge/envs/ogc2026/bin/python"
fi
PY="$(persistent_path Python "$PY")"
[[ -x "$PY" ]] || die "Python 3.12 environment not found: $PY"

run_id="${SLURM_JOB_ID:-manual-$$}"
STAGE="${OUT}.incomplete-$run_id"
[[ ! -e "$STAGE" && ! -L "$STAGE" ]] ||
  die "staging directory already exists: $STAGE"
mkdir -p "$STAGE/.runtime/prepare/tmp"
chmod 0700 "$STAGE/.runtime/prepare/tmp"
export TMPDIR="$STAGE/.runtime/prepare/tmp"
unset OGC_PHASE2_ORIS OGC_EXIT_TWIN OGC_EXIT_TWIN_SHADOW OGC_PREFP
export OGC_CAUSAL_BAY_RNG=1
export OGC_KEEP_ASSIGNMENT_LOCK=1
export OMP_NUM_THREADS=1

# Validate the authoritative fields before taking the byte-for-byte snapshot.
"$PY" - "$SOURCE" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

source = Path(sys.argv[1])
for instance in range(3, 7):
    for seed in (7, 11, 23, 41):
        path = source / f"p{instance}-s{seed}.json"
        try:
            record = json.loads(path.read_text())
        except Exception as exc:
            raise RuntimeError(f"invalid plateau JSON {path}: {exc}") from exc
        if record.get("schema_version") != 1:
            raise RuntimeError(f"unexpected schema_version in {path}")
        if record.get("instance") != instance or record.get("seed") != seed:
            raise RuntimeError(f"instance/seed mismatch in {path}")
        flat = record.get("flat")
        if (not isinstance(flat, list) or not flat or len(flat) % 7 or
                any(not isinstance(value, int) for value in flat)):
            raise RuntimeError(f"invalid flat solution in {path}")
        flat_hash = hashlib.sha256(
            json.dumps(flat, separators=(",", ":")).encode()
        ).hexdigest()
        if record.get("flat_sha256") != flat_hash:
            raise RuntimeError(f"flat digest mismatch in {path}")
        audit = record.get("audit")
        raw = record.get("raw_checker")
        objective = record.get("objective")
        if (not isinstance(audit, list) or not audit or audit[0] != 0 or
                not isinstance(raw, dict) or raw.get("feasible") is not True or
                raw.get("flat_sha256") != flat_hash or
                not isinstance(objective, list) or not objective or
                raw.get("objective") != objective[-1]):
            raise RuntimeError(f"plateau authority mismatch in {path}")
print("validated 16 frozen plateaus", flush=True)
PY

for instance in 3 4 5 6; do
  for seed in 7 11 23 41; do
    name="p${instance}-s${seed}.json"
    install -m 0444 "$SOURCE/$name" "$STAGE/$name"
  done
done
(
  cd "$STAGE"
  for instance in 3 4 5 6; do
    for seed in 7 11 23 41; do
      sha256sum "p${instance}-s${seed}.json"
    done
  done
) >"$STAGE/SHA256SUMS"
chmod 0444 "$STAGE/SHA256SUMS"
chmod 0555 "$STAGE"

[[ ! -e "$OUT" && ! -L "$OUT" ]] || die "snapshot destination appeared: $OUT"
mv -T -- "$STAGE" "$OUT"
echo "persistent plateau snapshot ready: $OUT"
