#!/bin/bash
# Re-run bayscratch on the pc1 kernel.
#
# The first run of this tool returned INFEASIBLE on all four bays of prob_5.
# That looked like a bug in the tool, and it was not: pairConflicts forbids
# same-entry-day pairs unless BOTH descent orders clear, so freeing a whole bay
# and re-placing it exactly is unsatisfiable whenever two of its blocks want the
# same entry day -- which, in a single bay, is most of them. incumbent_conflict
# then confirmed the model rejects the SHIPPED board itself (P5 5 pairs, P4 3
# pairs, every one a same-entry-day pair, vp violations 0).
#
# pc1 fixes the tie rule (one clear order is enough; Engine::entryRanks emits
# it). With the model no longer over-constrained, the question the tool was
# built to ask is finally askable:
#
#   CP beats us on the bay   the greedy-insertion skeleton caps quality, and a
#                            decomposition solver is worth building;
#   CP matches us            our packing is near-optimal and the gap lives in
#                            the bay ASSIGNMENT, not the packing.
#
# Either answer redirects the work. Both arms run so the comparison is on one
# node, and the shipped arm doubles as a check that the INFEASIBLE reproduces.
#SBATCH -J bayscr1
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/bayscratch_pc1
i=0
for b in 0 1 2 3; do
  c=$((i*8)); OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u tools/bayscratch.py builds/pc1 prob_5 600 $b \
    > runs/bayscratch_pc1/bay$b.txt 2>&1 &
  i=$((i+1))
done
wait
for b in 0 1 2 3; do echo "########## pc1 bay $b"; cat runs/bayscratch_pc1/bay$b.txt; done
