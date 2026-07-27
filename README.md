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
2. **Never overwrite a frozen `results/<tag>.json`.** Comparisons read frozen
   files; re-running a prior version to compare it is how you end up comparing
   noise.
3. **Versions are git tags, not directories.** `v0` is the imported engine.
   The previous campaign produced 37 loose `smaxNN-vNN.zip` artifacts and all
   of them were lost; that is what this rule exists to prevent.
4. **`utils.py` is not ours.** The server overwrites it. Any behaviour we rely
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

tools/submit_eval.sh <tag> <timelimit_sec>   # full 40 on dept, 4 cores each
tools/submit_eval.sh probe 120 dept 1,2,22,28,38      # quick subset
squeue -u $USER
python tools/collect.py <tag> --vs <older_tag>
```

`dept` is 8×40 cores and `titan` 15×~48, both with `OverSubscribe=NO`, so a
`--cpus-per-task=4` task gets four dedicated cores — the grading envelope.
A full-40 array is 160 cores and fits on `dept` alone.

## State

`solver/` is **v0**, imported from the offline campaign's end point (formerly
"smax78-v82"). Canary reproduced here: `prob_1 @T20 → 1499`, `prob_2 @T20 → 3690`.

That campaign's own conclusion (`docs/KERNEL_SPEC.md` §196) is that its three
objective layers are closed under the knobs it explored: scheduling is provably
optimal for its layouts (CP-SAT), assignment is capacity-saturated and the
capacity is assignment-invariant, and placement matches an exhaustive nester on
the same block sets. It reached roughly 20th–30th on the board and attributed
the residual −18%/−35%/−36% gaps to an engine-class difference.

Two caveats on inheriting that conclusion, both structural rather than
disagreements with the measurements:

- **It was measured on a 1-core sandbox.** `KERNEL_SPEC` §160 records
  `nproc = 1`, with the 4-thread portfolio time-slicing a single core, and
  names this as a root cause of local→server transfer failures. Every
  portfolio-, thread-, and budget-shaped judgement was made under that
  distortion. Here, a 4-core Slurm task is the real thing.
- **It was measured on 1–2 instances at a time.** Adoption gates used a
  20-instance synthetic suite that did not survive; the full 40 training
  instances were never run per change. `tools/` now makes full-40 the default
  unit of measurement.

So the exhaustion claims are trustworthy *as statements about that setup*, and
the ones that depend on parallelism or on breadth deserve one re-test each
before being treated as settled.
