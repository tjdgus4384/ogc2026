#!/usr/bin/env bash
#SBATCH -J abgate
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -t 01:10:00
#SBATCH --output=/dev/null
#
# One cell of a matched AB/BA gate. A cell = one seed: its P3-P6 pairs run
# concurrently on disjoint 4-core cpusets of an EXCLUSIVE node, and within a
# pair the two variants run SEQUENTIALLY on the same cpuset. Pairing cancels
# node identity; --exclusive cancels co-tenants (the 08-09 screen lost 43/50
# axes to co-tenant confounding). Never submit directly: tools/abgate.py submit.
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="${OGC_PY312:-$REPO/.miniforge/envs/ogc2026/bin/python}"
GATE="${OGC_ABGATE_DIR:?abgate dir}"
IDX="${SLURM_ARRAY_TASK_ID:?array only}"
HARNESS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$PY" "$HARNESS/abgate_cell.py" "$GATE" "$IDX" "$PY"
