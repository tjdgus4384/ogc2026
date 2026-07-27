# v59 changes vs v58

## Change (python layer only — kernel identical to v58)
`myalgorithm.py` `_seed_free` best-of-N seed orders: add `(D[i], -P[i])`
(due order, long-processing tie-break).

## Rationale (measured, sec 143 — first deep dive on the P6-class instance)
p38 (n=250, P6-proxy) diagnosis:
- Z1 is **96% of the objective** (w1*Z1 = 4.7e7 of 4.89e7). Z2/Z3 are noise.
- On-time area demand peaks at **1.58x total bay capacity**; 37 of 90 timesteps
  exceed capacity. Integrated excess implies **forced tardiness >= ~1211 days**
  -> p38 is structurally over-saturated (unlike p28, which has slack).
- Tardiness concentrates on LONG blocks: avg tardy 24.0 days for P>=22 vs
  2.5 days for P<22 (10x). Classic over-saturation signature.
- Seed-order probe (pure reconstruct, no anneal):
    due (current)     Z1=4810
    due then P-desc   Z1=4231   <-- -12%
    slack (D-P-R)     Z1=5563
    P-desc first      Z1=10960  <-- catastrophic: due order MUST lead
    critical-ratio    Z1=5251
  The best-of-N order list had an area-based tie-break (-A) but no
  processing-time tie-break; that was the missing piece.

## Validation (isolated processes, 3 reps)
  p38: v58 mean 4.83824e7 -> v59 mean 4.68796e7  (**-3.1% REAL GAIN**;
       v59 range 4.685-4.689e7 vs v58 4.812-4.851e7 — disjoint, tighter)
  p28: identical (2.87302e6 both, 3 reps deterministic)
  p1/p2/p22: identical
Seed improvements normally get absorbed by the anneal (sec 136), but on an
over-saturated board the seed matters and the gain survives.
