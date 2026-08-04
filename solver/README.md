# solver/ — the engine (v4.1)

The only solver tree. Two layers: C++ kernel (`src/` → `sscore*.so`) +
python driver. The one-page control-flow/knob map lives in
`docs/PIPELINE.md`; deep kernel history in `archive/docs-early/KERNEL_SPEC.md`.

```
myalgorithm.py   entry point. _seed_free (6 insertion orders) ->
                 run_portfolio (4 arms x anneal legs, exploit/explore split)
                 -> _slice_refine tail (OGC_SLICE=1 default: 6%-slices race
                 anneal-burst vs CP-SAT exact re-placement via refine.py)
                 -> checker gate + rescue ladder (nudge -> retime -> move)
                 -> _trivial safety net. Never raises.
refine.py        shipped slice refinement: CP-SAT re-placement of ~25-block
                 tardy-seeded subsets (candidates from the engine, conflicts
                 from C++ pair_conflicts, vp-verified, reserve-protected).
                 Falls back to anneal-only slices without ortools.
boxlns.py        OFF by default (OGC_BOXLNS): CP-SAT exact-repair LNS probe.
baysplit.py      OFF by default (OGC_BAYSPLIT): per-bay decomposition probe.
utils.py         the official checker (byte-authority; never modified).
src/
  geometry.hpp   positive-area polygon overlap, Shapely-equivalent (EPS 1e-9)
  crane.hpp      crane rule: moving layer k vs resident layers j>=k
  core.hpp       Problem, Sol, vpConservative (the 5 checker rules),
                 evaluate (Z1/Z2/Z3)
  occ.hpp        bit-grid prefilter (sound: admits only vp-checkable)
  repair.hpp     insertGreedy: earliest entry, first fit, 3 phases
  alns.hpp       the search: 10 destroy ops -> due-order reinsert -> SA;
                 personas, compactLeft, constructInitial, audits
  knobs.hpp      every kernel constant, layer-grouped (see PIPELINE census)
  api.cpp        Engine + pybind: shipped path vs experiment probes
```

Invariants (the full numbered list with failure modes: docs/PIPELINE.md):
- every placement passed vpConservative at insertion;
- occ grid mirrors the placed set exactly;
- gate/checker (utils.py) is byte-authority; vp is a conservative mirror;
- simultaneous-exit LOCKSTEP ordering is assumed by vp rules 3/5.

Build: `./build.sh` (py3.12 conda env; grading server runs 3.12).
Never build/run on the gateway — `sbatch tools/battery.sbatch`.
