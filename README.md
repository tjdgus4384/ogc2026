# OGC 2026 — Grand Shipyard Puzzle

Assign every block to a bay, place it (position + orientation), and choose its
ENTRY/EXIT days. Minimise `w1·Z1 + w2·Z2 + w3·Z3` (tardiness / workload
imbalance / preference regret). Full statement: `problem/problem_statement.md`.
Rigorous restatement with the facts we rely on: `docs/MODEL.md`.

## What matters about the scoring

The leaderboard is **rank-based per instance**: a feasible solution scores
`R − n_b` where `n_b` is how many teams beat it on *that* instance (§3.3). So
the currency is **how many instances you beat how many teams on**, not the size
of any single improvement. A change that gains 2% on twelve instances is worth
far more than one that gains 30% on one. Infeasible scores nothing, so the
never-crash / never-overrun hardening in `solver/myalgorithm.py` is load-bearing.

Final placement also weighs a technical report and a presentation, and the
committee explicitly rewards novel ideas even when they don't win benchmarks.

## Grading environment (what we must match)

| | |
|---|---|
| CPU | AMD Ryzen Threadripper PRO 9955WX, **at most 4 cores** (`cpulimit` 400%) |
| Memory | **16 GB** |
| OS / python | Ubuntu 24.04, python **3.12** |
| Network | blocked (`firejail`) |
| Time limit | per-instance, undisclosed, "a few minutes to half an hour", wall-clock |
| Submission | zip ≤ 15 MB, `myalgorithm.py` at root, `utils.py` overwritten by the server, **12 h cooldown** |

## Layout

```
solver/       THE engine. myalgorithm.py + sscore .so + src/ (C++ kernel).
              utils.py here is the competition's own -- never edit it.
tools/        Slurm evaluation harness (submit -> collect -> freeze).
docs/         MODEL.md (problem truth), KERNEL_SPEC.md (196-section research
              log from the offline campaign), KNOBS_AUDIT.md (which knobs are
              actually live -- read before touching any parameter).
patches/      8 measured-but-unshipped kernel patches + 4 analysis tools.
data/train/   the 40 training instances.
baseline/     competition-provided alg_tester + baseline + env yml.
results/      frozen eval snapshots. NEVER overwritten.
runs/         raw Slurm output (gitignored); collect.py promotes it to results/.
archive/      superseded June work: the python engine, its tools, its ledger.
```

## Rules of engagement

1. **Never run the solver on the gateway.** It is a shared login node. Every
   measurement goes through Slurm: `tools/submit_eval.sh`.
2. **At most 2 of our tasks run at once.** This is a shared cluster. Arrays are
   throttled with `%1`, `submit_eval.sh` refuses to submit when 2 are already
   running, and a full-40 @T300 therefore takes ~3.4 h. Budget experiments
   accordingly: prefer a targeted probe that answers the question in minutes
   over a full end-to-end run that answers it in hours.
3. **Never overwrite a frozen `results/<tag>.json`.** Comparisons read frozen
   files; re-running a prior version to compare it is how you end up comparing
   noise.
4. **Versions are git tags, not directories.** `v0` is the imported engine.
   The previous campaign produced 37 loose `smaxNN-vNN.zip` artifacts and all
   of them were lost; that is what this rule exists to prevent.
5. **`utils.py` is not ours.** The server overwrites it. Any behaviour we rely
   on must hold against the version in `solver/utils.py` verbatim.

## Environment

Local conda env (gitignored, rebuildable):

```bash
.miniforge/bin/conda create -y -n ogc2026 -c conda-forge \
    python=3.12 'shapely>=2.1.0' numpy pybind11
.miniforge/envs/ogc2026/bin/pip install ortools==9.15.6755
```

Python 3.12 is required: the shipped `sscore` extension is a cpython-312 build
and will not import on 3.10.

## Build and evaluate

```bash
solver/build.sh                              # rebuild sscore from solver/src/

tools/submit_eval.sh <tag> <timelimit_sec>   # full 40, 4 cores, 1 at a time
tools/submit_eval.sh probe 120 dept 1,2,22,28,38      # quick subset
squeue -u $USER
python3 tools/collect.py <tag> --vs <older_tag>
```

`dept` is 8×40 cores and `titan` 15×~48, both with `OverSubscribe=NO`, so a
`--cpus-per-task=4` task gets four dedicated cores — the grading envelope.
A full-40 array is 160 cores and fits on `dept` alone.

## State

`solver/` is **v1** (tag `v1`): the offline campaign's end point (`v0`,
ex-"smax78-v82") rewritten for full inspectability and hardened at the
checker gate. Distributional verdict vs a same-toolchain v0 rebuild:
quality parity (3-seed means −0.71%), same draw variance (~10%/instance),
and the v0 pipeline's collapse mode (prob_29 → `_trivial`, a ~4000× loss,
2 of 5 draws) eliminated. `solver/VERSION` carries the full evidence trail;
`docs/KERNEL_SPEC.md` the 196-section history behind v0.

Baselines frozen in `results/`: `v1e-t300` (seeds 7/8/9), `v1e-t900`,
`v0rb-*` controls. Judge engine changes distributionally (`SEED=` on
`tools/submit_eval.sh`); single draws differ by ~10% per instance on
unconverged boards.
