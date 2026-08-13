# Obstruction exit-closure gate: `9002c73`

This directory is the frozen evidence for the first-witness obstruction
exit-closure delivery gate.

## Authority

- source commit: `9002c73b5831e3e334f93e9a2c41ba31ed8d54c4`
- persistent build: `builds/jasg-exit-closure`
- extension SHA-256:
  `177a09acb6da04ad7851e7381d306561f01d198b89e0adf16140676fb7d0d5a9`
- unit binary SHA-256:
  `771768cc0b148da5f8e2193b8e345f8fb6738a8fc5342b6e40c117b8e77a1be9`
- aggregate summary SHA-256:
  `6f880a7f4425567e6049b3ba6647e1e78e4363936d3aa008696d1706316333f8`

Build job 75426 completed and passed import plus
`tests/exit_closure_test.cpp`.  Gate jobs 75538--75540 and array 75548
completed every required arm; collector job 75617 completed with exit code 0.
The earlier mutable-source job 75406 is deliberately excluded from authority.

## Frozen result

`gate/summary.json` is the machine-readable authority.  Integrity contracts
all passed: checker feasibility, cross-arm identical starts, reference/OFF
exact replay, closure-counter-zero OFF, semantic null, fixed 20,000 iterations,
and the in-trajectory iteration-1,000 checkpoint.

The final result is 10 wins, 4 losses, 2 ties, pooled median `-1.9623787728%`,
and median slowdown `1.1320665423x`.  The preregistered pooled threshold was
`-2%`, so the authoritative verdict is
`reject_first_witness_exit_closure`.  No equal-wall or production adoption is
licensed by this result.

`plateaus/` contains the 16 checker-authoritative starting states.  `gate/`
contains all 48 raw reference/control/treatment reports and the aggregate
summary.  Actual gate arms never invoked the checker rescue ladder.
