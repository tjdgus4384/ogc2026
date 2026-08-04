# Boundary QA gen2 (delta-only) — base 53201b3 → head 1e1d545

sourceHash sha256:56701e9ed5368f9110509c63e98d7e3ad22ef5acb6d87391d3bfa2ead28fb9ff

## Overall status: FAIL (one novel, scoped blocker) — gen1's A2 finding is only
## partially fixed.

Gen1 flagged: tools/globalmip.py could report a gain and write a board on
vp_bad==0 alone, without gating on the official checker. The delta
(`git diff 53201b3..1e1d545 -- tools/globalmip.py`) patches exactly ONE of
the file's THREE structurally-identical result-emission blocks:

- `SOLVER=="cpsat"` path (the one that produced every frozen
  globalmip_p3_vocab.log line, `backend=cpsat`) — **FIXED**, proven under
  execution (real Slurm run + a verbatim-logic harness with forced
  infeasible/None checker results).
- `GM_CB=1` Gurobi lazy-callback path — **NOT fixed**: still computes and
  emits `gain_vs_best_input_pct` and calls `json.dump(sol, out_path)`
  unconditionally once `vp_bad==0`, with no `checker_feasible` gate.
- default Gurobi round-loop path (the file's tail block, used whenever
  `GM_SOLVER` is unset/`"gurobi"` and `GM_CB` is unset — i.e. the
  **default** invocation of this tool) — **NOT fixed**, same defect.

This is a genuinely novel, delta-scoped finding: gen1 could only observe the
cpsat path (that's what the frozen artifacts used), so it never exercised
the other two blocks. The fix that landed addresses the exact code the gen1
report cited, but the same defect pattern was copy-pasted (or pre-existed)
in the other two backends of the same tool and remains live -- including in
the tool's *default* configuration path.

Everything else in this delta (asgopt.py BENDERS/PUNCTUAL refusal + round/wall
caps, v5.5 bundle rebuild) verified PASS under live execution.

## Environment notes
- Confirmed `git rev-parse HEAD` == 1e1d545 (assigned head) throughout this
  session; no concurrent drift this time.
- All Slurm jobs: partition titan, `-w <idle node>`, never more than 3
  concurrent (checked via `squeue -u $USER` before each new batch).
- `solver/*.so` never rebuilt: all live-engine runs used the pre-built
  `builds/shipv55` / `builds/lockprobe` trees (unchanged `.so` mtimes,
  Aug 4 19:40 / Aug 4 for lockprobe, confirmed via `ls -la`).
- No edits to `solver/` or `tools/`; no commits; no formatters/lint run.

Raw evidence: results/qa-v55/gen2/*.json, *.log, *.out; harness scripts
results/qa-v55/gen2/adv_globalmip_gate_test.{py,out}.
