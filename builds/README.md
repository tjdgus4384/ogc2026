# builds/ — probe kernels

Each directory is a full copy of `solver/` with one measured change, kept so
any ledger verdict can be re-run. `solver/` itself is the only shipped code.

## Alive (referenced by open threads, 2026-08-14)
- `ordf`   — probe kit: entryFloor/entryCap through reconstruct_ordered,
             set_entry_floor, feasible_positions(+bayOnly/eMax), set_dens_mode.
             The realizer-benchmark toolchain depends on it.
- `icmp`   — v6.5 + OGC_ICMP in-anneal spatialCompact+compactLeft (candidate
             under measurement). `icmpv` = same + SMAX_DEBUG kick marker
             (instrument validation only).
- `share`  — v6.5 + OGC_SLICESHARE slice-tail fraction knob (candidate under
             measurement).
- `spec6`  — v6.4.1 + OGC_LOCKSAT knob; produced the v6.5 adoption gates.
- `wit`, `ph3` — agent-built instrumented kernels from the 08-14 refutation
             round (insertGreedy decision-site counters; ph3 also carries
             OGC_PH3SNAP / OGC_NOSCREEN, both measured null).

## Historical
Everything else predates 08-14 and is documented where its verdict lives in
docs/CAMPAIGN_LEDGER.md. Do not delete without checking the ledger reference;
disk cost is small and re-creating a refuted build to re-check a verdict has
already been necessary more than once.
