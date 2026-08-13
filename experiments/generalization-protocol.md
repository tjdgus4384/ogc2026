# Train and hidden generalization protocol

## Authoritative local corpus

`data/train_final` is the robustness corpus: 40 instances, all byte-distinct
from the correspondingly numbered files in `data/train`.  The two directories
must not be pooled as repeated observations or substituted for one another.
The 40 final-train instances cover these size/bay cells:

| blocks × bays | instances |
|---:|---:|
| 150 × 2 | 6 |
| 150 × 3 | 4 |
| 200 × 3 | 4 |
| 200 × 4 | 6 |
| 250 × 3 | 4 |
| 250 × 4 | 6 |
| 300 × 2 | 5 |
| 300 × 5 | 5 |

Every candidate that survives its preliminary mechanism screen must run on all
40, not a favorable subset.  Problem names and IDs may be logged, but they may
not enter the solver or an adoption selector.  Predeclared structural splits
are rejection diagnostics only.

## Local adoption requirements

- Compare one immutable baseline snapshot with one immutable candidate
  snapshot; record source commit and binary hash.
- Use fixed iterations before wall-clock gates so node speed and draw noise do
  not masquerade as mechanism quality.
- Require every arm to pass the official checker.  Missing, timed-out, or
  failed outputs are losses, never dropped observations.
- Report paired wins/losses/ties, median and mean delta, worst regression, and
  the same metrics by `m<=3`/`m>=4`, saturation `<1`/`>=1`, and all eight
  block-count × bay-count cells above.
- Structural cells can veto a candidate but cannot dispatch hidden instances
  to different algorithms.  Adoption produces one universal solver.
- A Train survivor must also pass official-time P3-P6, no-ortools fallback,
  malformed-input and `_trivial` safety, package isolation, time/RSS, and
  checker-gate tests.

## Hidden limitation

The eight finals inputs are not present in this repository.  The only local
artifact is `data/finals_server.json`, containing outcomes of two historical
submissions on eight instances unrelated to preliminary P1-P6.  Those scores
cannot evaluate a new binary.

Therefore “hidden validated” is reserved for a new server response after a
user-authorized submission.  Before that point the honest claim is “hidden-
robust by universal mechanism and independent Train coverage,” not measured
hidden improvement.  No submission is triggered by an experiment harness.
