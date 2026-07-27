# v57 kernel changes vs recovered smax54 source

## Single change (surgical)
`repair.hpp` insertGreedy, bay-ordering probability:
- smax54: `< 0.7` (constant pref-first probability)
- v57:    `< (pb.n <= 120 ? 0.92 : 0.70)` (size-adaptive)

## Rationale (measured, secs 108-140 + this session)
- Small instances (n<=120) are Z3(bay-preference)-dominated: p2 Z1 hits 0
  fast, remaining objective is w2*Z2 + w3*Z3 with w3=150 large. Stronger
  pref-first (0.92) drives Z3 15 vs 29 -> p2 5440 -> 3690 (matches submitted v56).
- Large instances (n>120, e.g. p28 P4-proxy) are Z1(tardiness)-dominated and
  space-tight: strong pref-first costs packing flexibility. Keeping 0.70 leaves
  p28 identical to smax54 (A/B 4 seeds: v57 mean 3.265M vs smax54 3.243M, within
  0.7% noise; both min 3.2363M).
- Net: recovers v56's small-instance Z3 gain WITHOUT v56's P4 regression
  (submitted v56 p28 ~3.31M vs smax54/v57 ~3.24M). Both-sides win via adaptation.

## Validation
5/5 instances feasible. p2=3690, p22=780797 (< smax54 794401), p28/p38 nominal.
