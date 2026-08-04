> ADDENDUM (frozen after the fact): the checker_feasible gap reported below as an OPEN finding
> was closed before this file was committed -- commit d0a07f1 gated the cpsat path, and efc1977
> lifted the gate into `_report_gain` so all three backends share it. Read the status line as
> "PASS, finding closed in d0a07f1/efc1977".

# Boundary QA — campaign-3 change set (base 95201bb → head 53201b3)

sourceHash sha256:993208275a308affe1859ef5e9125ccc9769f3390f6c6d1139709d390f1c0e1b

All checks below independently recompute/reproduce, they do not merely re-read the
frozen logs. Slurm jobs used partition titan, `-w <idle node>`, ≤3 concurrent.
Note: repo HEAD advanced to 5590af8 partway through this QA session (peer commit,
comment/citation-only diff to tools/asgopt.py, tools/asgvalue.py, solver/myalgorithm.py
— verified via `git diff 53201b3 5590af8` to touch no executable semantics). All
findings below are valid against the frozen sourceHash; solver runs used isolated
build trees (builds/lockprobe, builds/shipv55) untouched by that commit.

Raw evidence: results/qa-v55/*.json (solver outputs, engine solutions),
results/qa-v55/slurm-*.out (job logs), results/qa-v55/adv_asgopt_layer_test.*
(adversarial unit test + output).

## Overall status: PASS WITH ONE FINDING

results/campaign3/asgfrontier_p3.log and results/campaign3/globalmip_p3_vocab.log
numbers all independently reproduced exactly. The CLI bundle (builds/shipv55 /
submissions/2026-08-05_v55-finals.zip) is byte-identical and checker-feasible
end-to-end, canaries reproduce exactly. tools/asgopt.py's orientation/layer guard
is sound under adversarial synthetic input. tools/globalmip.py's gain report is
gated on vp_bad==0 but is NOT gated on checker_feasible in code — a real (if so
far unobserved-in-practice) soundness gap versus the stated invariant.
