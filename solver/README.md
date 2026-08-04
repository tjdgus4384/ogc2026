# solver/ — the engine (v5)

The only solver tree. Two layers: C++ kernel (`src/` → `sscore*.so`) +
python driver. One-page control-flow map: `docs/PIPELINE.md`; deep
history: `archive/docs-early/KERNEL_SPEC.md`, git log.

v5 = v4.1 semantics with every refuted experiment path excised (48%
less code, canary-verified byte-identical shipped trajectory). The
whole config surface is `src/knobs.hpp` (kernel) + three env hooks
(driver): OGC_SEED (portfolio seed), OGC_DIAG (stderr diagnostics),
OGC_SLICE=0 (slice-refine ablation).

```
myalgorithm.py   entry point. _seed_free (6 insertion orders, top-3
                 distinct layouts) -> run_portfolio (4 arms x anneal
                 legs, exploit/explore personas, iteration-governed
                 rounds) or _population_search (memetic, fires when
                 sec/block <= 1.6 and sec >= 150) -> _slice_refine tail
                 (30% of TL: anneal bursts race CP-SAT exact
                 re-placement of ~25-block tardy-seeded subsets;
                 anneal-only without ortools) -> checker gate + rescue
                 ladder (nudge -> retime -> move) -> _trivial safety
                 net. Never raises.
utils.py         the official checker (byte-authority; never modified).
src/
  geometry.hpp   positive-area polygon overlap, Shapely-equivalent (EPS 1e-9)
  crane.hpp      crane rule: moving layer k vs resident layers j>=k
  core.hpp       Problem, Sol, vpConservative (the 5 checker rules),
                 evaluate (Z1/Z2/Z3)
  occ.hpp        bit-grid prefilter (sound: admits only vp-checkable)
  repair.hpp     insertGreedy: earliest entry, first fit, 3 phases
                 (release day / exit events / sparse sweep)
  alns.hpp       the search: 10 destroy ops -> hardest-first reinsert ->
                 SA accept (scale-invariant schedule), shift/swap atomic
                 moves, compactLeft + spatialCompact, constructInitial
  knobs.hpp      THE config surface: adopted constants with provenance
                 + G004 candidate levers (OGC_NFP2/NFPAUTO/BESTFIT, off)
  api.cpp        Engine + pybind: portfolio, reconstruction, slice
                 support (feasible_positions/pair_conflicts), probes
```

Invariants (full numbered list with failure modes: docs/PIPELINE.md):
- every placement passed vpConservative at insertion;
- occ grid mirrors the placed set exactly;
- gate/checker (utils.py) is byte-authority; vp is a conservative mirror;
- simultaneous-exit LOCKSTEP ordering is assumed by vp rules 3/5.

Build: `./build.sh` (py3.12 conda env; grading server runs 3.12).
Never build/run on the gateway — `sbatch -p titan tools/battery.sbatch`.
