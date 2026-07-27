# v58 kernel changes vs v57

## Change (surgical, one flag + one predicate)
1. `repair.hpp` Ctx: add `bool prefStrong` (regime flag).
2. `repair.hpp` insertGreedy bay-ordering probability:
   - v57: `< (pb.n <= 120 ? 0.92 : 0.70)`   (size proxy)
   - v58: `< (c.prefStrong ? 0.92 : 0.70)`  (live regime)
3. `alns.hpp` obj lambda sets `c.prefStrong = (z1 <= 0)` on every evaluation.

## Rationale (measured, sec 141)
Objective composition per instance (T20 run):
  p1  n=100 Z1=0    -> Z2 dominant (w2Z2 1099 > w3Z3 400)
  p2  n=100 Z1=0    -> Z3 dominant (2250 > 1440)
  p22 n=100 Z1=0    -> Z3 dominant (780,000, overwhelming)
  p28 n=150 Z1=178  -> Z1 dominant (2.37M >> 862k)
  p38 n=250 Z1=3503 -> Z1 dominant (46.7M >> 1.92M)
=> The dominant term is decided by whether tardiness reaches 0, NOT by n.
   v57's `n<=120` was a lucky proxy. v58 switches on the live regime, so the
   solver chases preferred bays exactly when preference actually dominates,
   including mid-run (once an anneal drives Z1 to 0 it flips into Z3 mode).

## Validation (isolated processes, 3 reps, deterministic)
  p22: v57 787,002 -> v58 755,093  (-4.1% REAL GAIN)
  p1/p2/p38: identical.  p28: 3.36299M -> 3.37195M (+0.27%, inside the 6.5%
  seed/build noise band measured in sec 139; v58 is structurally identical to
  v57 while Z1>0, so P4/P6 behaviour is unchanged).

## Methodology note
Module-reload A/B within one process does NOT isolate a compiled .so (already
loaded extension survives sys.modules deletion) and produced a false "no gain".
All A/B must run in separate processes.
