# Kernel Source Recovery Note (2026-07-24)

## Status
Container reset wiped the live work tree. This is the most recent RECOVERABLE
kernel source, extracted from **smax54-lockbet.zip** (8 files: api.cpp,
alns.hpp, repair.hpp, core.hpp, occ.hpp, crane.hpp, geometry.hpp, skyline.hpp).

## Known gap
- smax54 source compiled + v56 python pipeline -> canary **p2 = 5440**.
- Submitted v56 binary -> canary **p2 = 3690**.
- The 1750 gap = kernel improvements made in the smax55~56 sessions whose
  SOURCE was never archived (only the compiled .so shipped, and that .so was
  lost in the reset — but it survives inside smax66-v56-current.zip).

## Implication for future kernel work
- The v56 `.so` (in smax66-v56-current.zip) still RUNS and is submittable.
- Any KERNEL-LEVEL change must start from this smax54 source and will regress
  p2 5440 unless the v55~56 improvement is re-derived. Diff-hunt candidates:
  reconstruct/seed path, tail gating, or nudge/move primitives touched after
  smax54 (see FAST_KERNEL_SPEC secs 108+).

## Build command
g++ -O3 -std=c++17 -funroll-loops -fopenmp -shared -fPIC \
  $(python3 -m pybind11 --includes) api.cpp -o sscore$(python3-config --extension-suffix)
