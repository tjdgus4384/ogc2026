# Fast-exact per-bay decoder — research plan

## Why
brkga2 (the engine for large/congested instances) decodes each chromosome with `placer.c` = bottom-left
first-fit extreme-point greedy. It UNDER-packs vs CP-SAT, so the Z1 it reports is inflated → brkga2
optimizes a weak/noisy signal on congested instances (prob_27/38 stuck +5–8% vs overnight best). The
per-bay packing oracle is the structural ceiling for the congested frontier (established repeatedly this
session). Need: FAST (1000s of decodes per brkga2 run) AND ACCURATE (near-CP-SAT Z1).

## Decoder tradeoff (measured)
- greedy C (placer.c): ~ms/bay, under-packs. Used for the whole population.
- beam (beam_polish): slower, better; used only on the ELITE chromosome (beam_final).
- CP-SAT (cpsat_packer): exact, ~1–5s/bay (≤~50 blocks), too slow to decode a population. Useful as polish
  on small bays only; on 75+ block bays (prob_27/38) it's too slow even for polish.

## Current placement rule (placer.c place_one)
Candidate positions = bay base corner + (right/top edges of time-overlapping present blocks), clamped.
Selection = BOTTOM-LEFT first-fit (lowest y, then lowest x, first non-overlapping). Time = earliest
feasible entry (event times). Entry-aware overlap (fixed this session: exit>e AND entry<e+proc).

## Hypothesis
Bottom-left first-fit leaves fragmented gaps. A BEST-FIT / max-CONTACT selection (snuggest nestle,
minimizing fragmented free space) packs denser → lower Z1 in the decoder → brkga2 evaluates congested
assignments more accurately → better congested results. (Python `_best_pos` has a contact mode;
`packer.PLACEMENT="contact"` was A/B'd net-slightly-worse GENERALLY, but never tested as the brkga2
decoder on CONGESTED-specific instances, where tighter packing directly cuts the dominant Z1.)

## Experiments (run AFTER the full-40 freeze finishes — they are CPU-heavy and would corrupt it)
- **E1 (gap quantification)**: on prob_27/38 congested bays (75+ blocks), measure Z1 of greedy-C vs beam
  vs CP-SAT (capped tl) + wall time. Confirms how much Z1 the greedy leaves on the table and the speed budget.
- **E2 (contact-max in C)**: add a `contact` selection to place_one (evaluate all feasible candidate
  positions, pick max boundary-contact, tie-break bottom-left). Benchmark Z1 vs bottom-left on congested
  bays (same block-set + order). Cheap change, isolates the placement-rule lever.
- **E3 (integrate)**: if E2 packs tighter at acceptable cost, gate it into the brkga2 decoder (flag) and
  run brkga2 on prob_27/38 — does the congested obj drop? best-of so no regression.
- **E4 (maximal-rectangles)** — only if E2 insufficient: a maximal-free-rectangles decoder (track free
  rects, place into best-fit free rect, split). Tighter than skyline/extreme-point; bigger rewrite.
- **E5 (hybrid budget)**: periodic CP-SAT re-decode of brkga2's elite on small-enough bays (prob_24-type,
  ≤~50 blocks) — already partly covered by the z10-route + cpsat_polish; quantify the remaining gain.

## Guardrails
- Every decoder change is best-of in the engine → cannot regress (validate on the balanced spread + prob_3
  stability before committing).
- Measure on the FROZEN full-40 baseline (`results/2026-06-21_entryaware-route.json`), not single runs.
- No parameter overfitting: prefer a structurally better placement rule over tuned constants.
