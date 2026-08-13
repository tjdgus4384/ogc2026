# EXIT-LIFO v1 pre-registration

## Claim under test

For a same-bay, same-day EXIT batch, a checker-legal order is available without
search: descending ENTRY day, then reverse of the actual checker-legal ENTRY
topological order.  A later entrant cleared every earlier resident while
descending, and the official checker applies the identical `j >= k` geometry
while ascending.

This exposes a strict false-reject in the shipped equal-ENTRY/equal-EXIT id
tie-break.  The old `m15` experiment did not test the claim soundly: its
candidate was absent from the event index during validation.  Later
`exact-events` measurements bundled several changes and used underpowered,
mostly unpaired draws; they do not close this isolated mechanism.

## Treatment

The legacy insertion scan remains authoritative.  On a fixed-oracle reject,
the treatment remembers at most the first official-semantic candidate and
continues the complete legacy scan.  Any later legacy success wins unchanged.
The remembered candidate is committed only when the insertion would otherwise
fail.  Completed-board polishing is a separate switch and is OFF in this gate.

No public-instance selector is used.  Same-day ENTRY ordering is also made
size-unbounded; the previous 16-block fallback was not justified for hidden
instances.

## Deterministic gates

- Build: `builds/exitlifo1`, native SHA-256
  `75019dc282f57188fc9360eb61fe0489ad4a71f9e95098a5efe9b112611e91b2`.
- Build/smoke job `74742`: P1/P2/P3 checker-feasible.
- Checker audit jobs `74747` (OFF), `74748` (ON): synthetic false-reject,
  forced fallback, a 20-block ENTRY batch, and 20 historical boards.
- Fixed-iteration job `74753`: four preliminary boards x four seeds x
  OFF/SHADOW/ON, 20,000 single-thread iterations from common serialized starts.

For all 16 cells, OFF and SHADOW must have identical search-state hashes,
objective/trajectory counters, and production-gated output hashes.  All ON
outputs must pass the production checker gate.

The treatment advances to Train only if:

1. overall ON wins are at least overall losses;
2. no P3-P6 board has a four-seed median regression greater than 2%; and
3. the measured runtime ratio does not erase the mechanism at wall time.

A single large preliminary improvement cannot override a failed condition.
Passing this screen is not adoption: the next gate is broad `train_final`, with
`train` used as an independent distribution check before any submission change.
