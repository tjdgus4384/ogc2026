#!/usr/bin/env bash
# Fan probes across DISTINCT idle nodes, one per node, round-robin.
#   probe_fan.sh <tag> <tool.py> "<argset1>" "<argset2>" ...
# Each argset is the full "<prob> <out> <sec> <workers> [extra...]" string.
set -uo pipefail
ROOT=/data_fast/home/tjdgus4384/priv/ogc2026
TAG="$1"; TOOL="$2"; shift 2
mapfile -t NODES < <(sinfo -h -p titan -t idle,mix -o "%n" | sort -u)
[ ${#NODES[@]} -eq 0 ] && { echo "no idle nodes"; exit 1; }
i=0
for ARGS in "$@"; do
  N=${NODES[$((i % ${#NODES[@]}))]}
  sbatch -p titan -w "$N" -o "$ROOT/runs/probe/${TAG}_$i.log" \
    "$ROOT/tools/probe1.sbatch" "$TOOL" $ARGS >/dev/null && echo "  $i -> $N"
  i=$((i+1))
done
