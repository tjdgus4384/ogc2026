# Boundary QA gen3 (delta-only) — base 1e1d545 → head ae01b93

sourceHash sha256:cc2a1e52b155c26c063e023eb53b3f96c7023713d16cd26d4468302055221c6b

## Overall status: PASS — G-BLOCKER-1 (gen2) is resolved and verified under execution.

git diff 1e1d545..ae01b93 -- tools/globalmip.py introduces `_report_gain(res,
sol, best_obj, final_obj, chk, out_path)` (lines 88-103) and routes all
three previously-divergent emission sites through it:
  - cpsat block            (line 271)
  - GM_CB Gurobi callback  (line 406)
  - default Gurobi tail    (line 461)

Exhaustive source search confirms exactly ONE `json.dump(` call and exactly
ONE `gain_vs_best_input_pct = ` assignment site in the whole file, both
inside `_report_gain`, with no alternate-named bypass (`_pct`, `.write(`,
`open(out_path` all resolve to that same single site). No emission site was
missed.

The helper explicitly treats a `None` checker verdict as failure, not
permission (`checker_feasible = None if chk is None else bool(...)`, then
gated on truthiness) -- this closes the specific edge the gen2 report also
worried about (checker call itself failing).

## Execution evidence
- `adv_globalmip_gate_test.py` copies `_report_gain` verbatim and drives all
  three call-site *shapes* (cpsat/`res`, GM_CB/`out`, default/`res`) through
  3 sub-cases each: checker_feasible=False, chk=None, checker_feasible=True.
  9/9 pass (`ALL_PASS: true`): infeasible and None cases -> gain=null, no
  board written; feasible case -> numeric gain + board written, for all
  three call-site shapes including the two gen2 found broken.
- Real Slurm regression run (job 63488, node alan, `GM_SOLVER=cpsat`) on the
  cpsat path: `checker_feasible: true, gain_vs_best_input_pct: 0.0`, board
  written (26675 B). Independently re-verified with a fresh, separate
  `solver/utils.check_feasibility` call: `feasible: true, objective: 80140.0`
  -- matches the tool's self-report exactly. No regression from the fix.

## Environment / compliance notes
- `git rev-parse HEAD` == ae01b93 (assigned head) throughout this session.
- Slurm: partition titan, `-w <idle node>`, at most 1 concurrent job needed
  this generation (single regression run); `squeue -u $USER` checked clear
  before and after.
- `solver/*.so` never touched or rebuilt this generation (no engine-build
  interaction beyond loading the existing `.so` via `import myalgorithm`,
  which the shipped tool already does).
- No edits to `solver/` or `tools/`; no commits; no formatters/lint run.
- `git status --short` shows only the new `results/qa-v55/gen3/` artifacts
  as untracked; no tracked file was modified by this QA pass.

Raw evidence: results/qa-v55/gen3/adv_globalmip_gate_test.{py,out},
results/qa-v55/gen3/globalmip_repro.json, results/qa-v55/gen3/slurm-63488.out.
