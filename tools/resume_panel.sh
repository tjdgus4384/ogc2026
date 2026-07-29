#!/usr/bin/env bash
# Resume the paused knob-panel confirmations (cluster was yielded to a
# teammate on 2026-07-30; see PIPELINE.md census rows marked 판정 중).
# Run when dept nodes are free again.
set -euo pipefail
cd /data_fast/home/tjdgus4384/priv/ogc2026

# second seeds for the three live wave-1 signals
OGC_AREASLK=1.05 sbatch --partition=dept tools/eval_prelim.sbatch knob-slk105-s8 $PWD/solver 8
OGC_STRIDE=3     sbatch --partition=dept tools/eval_prelim.sbatch knob-str3-s8  $PWD/solver 8
OGC_DECAY=0.95   sbatch --partition=dept tools/eval_prelim.sbatch knob-dec95-s8 $PWD/solver 8
OGC_DECAY=0.95   sbatch --partition=dept tools/eval_prelim.sbatch knob-dec95-s9 $PWD/solver 9

# comment-only src edits since the last build: refresh + canary
sbatch --partition=dept tools/battery.sbatch

echo "panel resumed; judge with the ±2% rule against runs/prelim-v31e-* draws"
