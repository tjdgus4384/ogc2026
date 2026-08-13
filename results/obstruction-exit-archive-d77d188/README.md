# Control-preserving obstruction-exit archive evidence

This directory is the immutable evidence for the successor experiment in
`experiments/obstruction-exit-closure.md`.

- Candidate source: `1dcdef092c7813d2ee7229fe466acb2f1303477d`
- Candidate binary SHA-256:
  `a5c384dd1c7d797fbce2c9c7a01e1a30d8abbee0bb26f6fa839f71d919629e9f`
- Reference source: `9002c73b5831e3e334f93e9a2c41ba31ed8d54c4`
- Reference binary SHA-256:
  `177a09acb6da04ad7851e7381d306561f01d198b89e0adf16140676fb7d0d5a9`
- Frozen run harness: commit `d77d188`, driver SHA-256
  `f122e6d5e4b2acf94ea7253ca0815c3dbb15c0e3093b238a932112123c9d81a6`
- Frozen fail-closed collector: commit `23eb83c`
- Build/unit job: `75644`
- Development smoke array: `75642`
- Held-out 48-arm array: `75731`
- Collector job: `75785`
- Aggregate summary SHA-256:
  `fa238548454e9b73fb7e9f1a9fcedf37afece48c6d53b7a7c3f5b3ae01e9f764`

The held-out seed mapping was evaluation seeds `3,17,31,47` to pre-frozen
plateau seeds `7,11,23,41`.  Every arm ran 20,000 single-thread iterations
with an iteration-1,000 checkpoint.  All 48 reports were present and raw
checker-feasible.  Reference/candidate-control trajectories, archive internal
control trajectories, common counters, checkpoints, and full RNG states were
exact; the collector recorded no mismatch.

Final result: 3 wins, 0 losses, 13 ties; pooled median `0.000%`, mean
`-0.0116269%`, median slowdown `1.11836x`.  The preregistered efficacy gate
failed and the authoritative verdict is `reject_closure_archive`.  The raw
authority is `heldout/summary.json`; `plateaus/SHA256SUMS` binds all starts.
