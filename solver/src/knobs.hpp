// knobs.hpp -- the WHOLE remaining config surface of the kernel.
// v5 simplification (2026-08-04): every refuted experiment knob was
// excised WITH its code path (history: docs/PIPELINE.md, git; the
// G004 candidates NFP2/NFPAUTO/BESTFIT were judged refuted on the
// 40-pair harness and excised in the generation-2 review pass).
// What remains is ADOPTED constants -- measured on the train_final
// 40-instance harness or the official-TL boards; provenance next to
// each value -- plus the one internal driver channel.
//
// The driver-side env surface: OGC_SEED / OGC_DIAG / OGC_SLICE
// (myalgorithm.py) + SMAX_DEBUG (kernel dump hook, diagnostics only).
#pragma once
#include <cstdlib>

namespace knobs {
// ---- adopted constants (semantics live at the call sites) ----------
// area screen slack x1.02 on the fill-ratio cap; heterogeneous per
// thread (densMode): tight cap won -1..-5% except z1-free boards.
inline constexpr double areaSlk() { return 1.02; }
// polygon-area accounting for the area screen/cap (vs bbox): adopted.
inline constexpr int    polyArea() { return 1; }
// energetic reservation: skip provably over-subscribed release days.
inline constexpr int    reserve() { return 1; }
// compact-orientation preference: 50% of draws sort orients by bbox
// area (boards are bbox-bound); the rest stay random for diversity.
inline constexpr int    oriPref() { return 50; }
// phase-3 sparse sweep stride (<=24-day windows scan every day).
inline constexpr int    stride() { return 6; }
// small-neighbourhood destroy cap: 3% of n (min 8).
inline constexpr int    qFrac() { return 3; }

} // namespace knobs
