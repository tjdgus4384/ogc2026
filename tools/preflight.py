"""Pre-implementation prediction: what WILL happen if you make this change.

    python tools/preflight.py <change-class>

Run BEFORE writing code. Prints the law-derived prediction (docs/PIPELINE.md
L1-L6) and the exact validation chain the change must pass. This is the
executable form of the discipline that produced three predicted-then-
confirmed surgeries on 2026-07-30 (spec dedup, knobs.hpp, v31d6..d8 seals).
After implementing: tools/test_driver.py (driver gate) and
tools/battery.sbatch (kernel gate) judge the outcome.
"""
import sys

CLASSES = {
    "kernel-semantics": """\
CHANGE: anything altering search behaviour (ops, acceptance, vp filters,
        candidate order, RNG consumption, byBay order, float rounding).
PREDICT (L2): every trajectory reshuffles -- a NEW lottery, draw spread
        +/-5-8% per instance. Single-run A/Bs compare tickets, not code.
        The seed-dispatch menu goes STALE (config-specific).
VALIDATE: 2-seed screen -> 4-5 seed confirm at OFFICIAL TLs, +/-2% rule;
        battery audits + canaries; if adopted, RE-SWEEP the seed menu
        before rebuilding any submission zip.
LOCKSTEP: touching exit ordering or rule-3/5 tie logic? Invariant 7:
        _recs_to_sol EXIT sort and vp tie filters must change TOGETHER.""",
    "kernel-speed": """\
CHANGE: semantics-preserving speedup (caching, layout, SIMD, indexes).
PREDICT (L1+L2): fixed-iteration trajectories MUST stay byte-identical;
        quality moves ONLY via the governance rate table afterwards.
        Visit-reduction schemes are bounded ~0 (reject path is ~free at
        1-2ns/entry); only QUALIFYING-work reduction can pay, and
        memoizing it measured as cache-thrash. Two index designs died
        here -- read the PIPELINE speed ledger before trying a third.
VALIDATE: battery_speed byte-identity (p27 8000 it + real-P4 20000 it,
        JSON-equal) + T20 multipliers; adopt at >=x1.1 only; then rate
        bump + real-board adoption evals on 2 seeds.""",
    "driver": """\
CHANGE: myalgorithm.py orchestration (no RNG/order/float change).
PREDICT (L2): byte-identical everything -- canaries 1499/3690 exact,
        any known stable draw reproduces exactly.
VALIDATE: python tools/test_driver.py (dispatch/spec/env/exit-order),
        battery canaries, one fixed-seed official-TL reproduction
        against a known-stable draw (e.g. titan s8 P3 = 89865).""",
    "knob": """\
CHANGE: an env screener value (see src/knobs.hpp + myalgorithm config).
PREDICT: construction-layer knobs trade Z3 vs Z1; sign per board follows
        Z3-share (L3: >=23% helps, <=7% hurts; P3 81 / P4 23 / P5 6 /
        P6 3). Mix-layer knobs act through adaptive weights only (L4);
        P5 (the one 4-bay board) is the sensitive canary -- check first.
        Phase-3/STRIDE knobs sit on paths unreached at official TLs.
VALIDATE: 2-seed screen -> 4-5 seed confirm at official TLs, +/-2% rule
        vs same-seed baseline draws. One-seed signals are lottery
        tickets (dec95's P5 -7.4% did not reproduce).""",
    "zip": """\
CHANGE: packaging / dispatch table update (engine untouched).
PREDICT: boards equal the table's measured draws exactly (server
        reproduces governed trajectories; P4-class redraw variance only
        if chunk count shifts -- observed stable on titan).
VALIDATE: tools/test_driver.py (table plumbing), then shipcheck:
        eval_prelim.sbatch <tag> runs/zipstage-<ver> auto -- all six
        boards must match the predicted list BEFORE the zip is called
        canonical. Keep the previous zip as fallback.""",
}

if len(sys.argv) != 2 or sys.argv[1] not in CLASSES:
    print(__doc__)
    print("classes:", ", ".join(CLASSES))
    sys.exit(1)
print(CLASSES[sys.argv[1]])
