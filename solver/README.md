# solver/ — the engine (v5)

The only solver tree. Two layers: C++ kernel (`src/` → `sscore*.so`) +
python driver. One-page control-flow map: `docs/PIPELINE.md`; deep
history: `archive/docs-early/KERNEL_SPEC.md`, git log.

v5 = v4.1 semantics with refuted production paths removed. Canonical
production controls remain `src/knobs.hpp` plus `OGC_SEED`, `OGC_DIAG`,
and `OGC_SLICE`. Experimental `OGC_*` mechanisms are inert unless
explicitly enabled and every Slurm treatment records their values in
`experiment_env`; they are not adopted without frozen checker evidence.

```
myalgorithm.py   entry point. _seed_free (6 insertion orders, top-3
                 distinct layouts) -> run_portfolio (4 wall-budgeted arms
                 with exploit/explore personas) or _population_search
                 (memetic, opt-in habitat) -> _slice_refine tail
                 (anneal bursts race a restricted generated-column CP-SAT
                 neighborhood). CP exactness covers only its declared finite
                 candidate domain; whole-state audit precedes the official
                 checker gate. The checker is final authority. Rescue ladder
                 (nudge -> retime -> move) -> cached checker-certified safety
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
