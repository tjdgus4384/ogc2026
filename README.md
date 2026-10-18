# OGC 2026: MSGA

Final submission for the **Optimization Grand Challenge 2026**:
*The Grand Shipyard Puzzle: Pack the Block, Beat the Clock*.

Competed solo under the team name MSGA.

**Finalist · 26th out of 958 teams from 55 countries**

[Technical report](report/technical-report.pdf) ·
[Source code](code/) ·
[Submission ZIP](submission/OGC2026_AlgorithmCode_MSGA.zip) ·
[Finalist certificate](certificate/OGC2026-0825.pdf)

## Overview

The challenge combines spatial packing and scheduling: assign shipyard blocks
to bays, orientations, positions, and entry dates while minimizing tardiness,
workload imbalance, and bay-preference penalties under geometric and crane-access
constraints.

The solver combines a C++17 search engine with a Python driver:

- A four-thread portfolio of adaptive large-neighborhood search (ALNS).
- Memetic population search for the instance sizes and time budgets where it was validated.
- Adaptive refinement with annealing and optional OR-Tools CP-SAT neighborhoods.
- Official-checker validation, repair, and cached feasible fallbacks.

The final code version is **v6.5.2**. The submitted code package and report are
preserved as originally prepared; the report predates the final robustness fixes.

## Repository

| Directory | Contents |
| --- | --- |
| [`code/`](code/) | Python entry point and official checker, C++ sources, compiled extension, and build helper |
| [`report/`](report/) | Submitted PDF and its LaTeX source |
| [`submission/`](submission/) | Original final submission ZIP |
| [`certificate/`](certificate/) | SeongHyeon Yun's finalist certificate, issued August 18, 2026 |

## Run

The included extension targets **CPython 3.12 on Linux x86-64**, with an
**x86-64-v3** CPU and the OpenMP runtime. Create a Python 3.12 environment:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r code/requirements.txt
```

OR-Tools is optional; without it, the CP-SAT refinement step is skipped:

```bash
python -m pip install ortools==9.15.6755
```

From the repository root, pass an instance in the competition's JSON format:

```python
import json
import sys

sys.path.insert(0, "code")
from myalgorithm import algorithm

with open("path/to/instance.json") as f:
    problem = json.load(f)

solution = algorithm(problem, timelimit=60)
```

To rebuild the extension, use a C++17 compiler with OpenMP support
(the original build used GCC 11.4):

```bash
PY=python bash code/build.sh
```

To rebuild the report with a LaTeX installation:

```bash
cd report
latexmk -pdf technical-report.tex
```

## Submission integrity

The final ZIP is **432,348 bytes**. Its SHA-256 is:

```text
5380a47a6e603c1397b73168b3987c1f7ecefecd7c0385f378b5b0b259c76955
```

All 12 files in the ZIP match their counterparts in `code/` byte for byte.
`build.sh` and `requirements.txt` are convenience files added for this repository.

## Development archive

The default branch contains the final deliverables. The full development tree,
evaluation data, and campaign notes are preserved on
[`archive/development-2026-10-18`](https://github.com/tjdgus4384/ogc2026/tree/archive/development-2026-10-18).
Unmerged experiments and work in progress are preserved on the other `archive/*`
and `codex/*` branches.

The [v6.5.2 release](https://github.com/tjdgus4384/ogc2026/releases/tag/v6.5.2)
also contains the submitted artifacts, historical campaign archive, and experiment
outputs with checksums.
