// knobs.hpp -- the WHOLE remaining config surface of the kernel.
// v5 simplification (2026-08-04): every refuted experiment knob was
// excised with its code path (history: docs/PIPELINE.md census,
// docs/UNDERSTANDING.md, git). What remains is either an ADOPTED
// constant (measured on the 40-instance train_final harness or the
// official-TL boards; provenance next to each value) or one of the
// two G004 candidate levers still under judgment (env-gated, default
// OFF, inert on the grading server).
//
// The driver-side surface is equally small: OGC_SEED / OGC_DIAG /
// OGC_SLICE (myalgorithm.py) + the internal OGC_PREFP channel below.
#pragma once
#include <cstdlib>

namespace knobs {
inline int envi(const char* n, int d) {
    const char* v = std::getenv(n); return v ? atoi(v) : d;
}

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

// ---- driver channel ------------------------------------------------
// pref-first insertion probability. The DRIVER sets OGC_PREFP=0.85 for
// spec boards (m<=3 and under-saturated, _spec_rule); unset means the
// measured regime split 0.92 (prefStrong) / 0.70.
inline double prefP() {
    static const double v = [] {
        const char* q = std::getenv("OGC_PREFP");
        return q ? atof(q) : -1.0;
    }();
    return v;
}

// ---- G004 candidate levers (default OFF, judgment pending) ---------
// NFP2: candidates from the true two-axis contact set instead of the
// bbox-corner cross product. tf-nfp2 measured -4.3% vs v40 (32W/7L)
// at s7 but only -0.2% at s13 -- multi-seed adoption judgment open.
inline int nfp2()    { static const int v = envi("OGC_NFP2", 0); return v; }
// auto-gate NFP2 on dense boards (>=65 blocks/bay).
inline int nfpAuto() { static const int v = envi("OGC_NFPAUTO", 0); return v; }
// best-fit corner scoring (contact-hugging) instead of first-fit.
// tf-bfit measured -3.6% vs v40 -- multi-seed adoption judgment open.
inline int bestFit() { static const int v = envi("OGC_BESTFIT", 0); return v; }
} // namespace knobs
