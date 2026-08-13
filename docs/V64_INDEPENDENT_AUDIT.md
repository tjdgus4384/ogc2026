# Independent audit of the shared v6.4 candidate

Date: 2026-08-14  
Audited commit: `5137a2831a15bbc21ff9c49897ebacb9c47707e5`

## Decision

v6.4 is a promising, empirically active heuristic.  It is not an exact
energetic/correctness repair, and the surviving evidence does not establish
hidden-instance robustness.  Keep the prepared artifact as a candidate; do
not describe it as the current live server submission or as a proven bound.

## Recomputed evidence

All 405 stored A/B pair records in `runs/abgate/rsv4-*` have matching manifest
identity, two checker-feasible arms, the stated official time limits, and no
recorded overrun.  Recomputing the raw deltas gives:

| gate | pairs | W/L/T | paired median | interpretation |
|---|---:|---:|---:|---|
| `rsv4-tf` | 84 | 65/19/0 | -5.0913% | selected `train_final` active band |
| `rsv4-rep` | 84 | 65/19/0 | -3.9707% | same instances, disjoint seeds |
| `rsv4-tr` | 72 | 51/21/0 | -5.8387% | public `train` instance replication |
| `rsv4-off` | 69 | 33/30/6 | 0.0000% | no detected pooled bias, not equivalence |

The 24-seed official-TL preliminary readout is:

| board | candidate-arm median | paired median | sign-test reading |
|---|---:|---:|---|
| P3 | 80,140 | +0.0000% | neutral, mean slightly worse |
| P4 | 2,948,183 | -1.4898% | direction only (14W/10L, p=0.54) |
| P5 | 10,190,528.5 | -3.6033% | resolved (18W/6L, p=0.023) |
| P6 | 28,911,412 | +0.3922% | neutral/slightly worse |

The seed-7 candidate draws are `79,730 / 3,045,959 / 9,774,807 /
27,821,270`.  They are wall-time stochastic draws, not immutable submission
scores.  No v6.4 server receipt was found.

## Why the strength claim is held

The `1.60` dispatch boundary was not fixed before independent data.  An
ungated `train_final` read led to a `<1.5` band; after correcting the load
calculation, the same observations were re-binned and the boundary moved to
`<1.6`.  The seed replication therefore tests stochastic reproducibility on
the selected instances, not selection of the boundary.  `rsv4-tr` is useful
prospective evidence after the boundary was fixed, but `data/train` is a
repeatedly used public set rather than an untouched hidden proxy.  The active
class also contains large regressions, including one stored pair of +84.80%.

The new profile is a heuristic signal.  It spreads
`area * P / (D-R)` across every day in `[R,D]`; that is neither a compulsory
part nor proof that a block occupies any particular day.  The inclusive loop
also distributes more than `area * P` in a simple `R=0,D=10,P=2` example.
`packFill` is an empirical polygon/bounding-box ratio, not a capacity theorem.
Using these quantities to raise `eBot` deliberately deletes legal early-entry
choices.  Feasibility remains checker-gated, but comments such as “provably
over-subscribed” and “standard energetic profile” overstate the mechanism.

## Artifact authority

The shared repository's `solver/` and canonical zip are byte-identical to
commit `5137a28`; the zip SHA-256 is
`6c0f06ad63376443ca7a986fb66b02568a1193e977c8b715a4ac4b9ad3451ba7`.
The historical runs are not release-grade receipts: their manifests omit
source/binary/problem/checker hashes, solutions were not retained for replay,
and the old harness used shared `/tmp`.  Repository documentation also
disagrees about the live server version.  Therefore the prepared local v6.4
artifact and the last confirmed server submission must remain separate.
