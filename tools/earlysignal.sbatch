#!/bin/bash
#SBATCH -J earlysig
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
$PY -u tools/earlysignal.py solver prob_5 600 7,11,23,41,59,73,89,101,113,131,149,167
