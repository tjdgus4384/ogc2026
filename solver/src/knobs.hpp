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
// Diagnostic assignment-rollout instrument.  A forced bay normally shrinks
// the bay vector before std::shuffle, which changes the RNG stream and makes a
// same-bay semantic no-op a different construction draw.  When enabled, the
// full vector consumes the baseline sort/shuffle first and is filtered only
// afterwards.  Default OFF preserves every shipped/production trajectory.
inline bool causalBayRng() {
    static const bool v = [] {
        const char* q = std::getenv("OGC_CAUSAL_BAY_RNG");
        return q && q[0] == '1' && q[1] == '\0';
    }();
    return v;
}
// Diagnostic-only persistent-lock audit.  Production deliberately releases
// assignment guidance after 60% of an anneal; a causal hard-lock experiment
// must be able to suppress that release without changing the shipped default.
inline bool keepAssignmentLock() {
    static const bool v = [] {
        const char* q = std::getenv("OGC_KEEP_ASSIGNMENT_LOCK");
        return q && q[0] == '1' && q[1] == '\0';
    }();
    return v;
}
// Diagnostic wiring audit.  insertGreedy draws one orientation order, but
// the ordinary phase-2 revival scan historically rebuilt ascending indices
// and discarded that order.  Default OFF preserves the shipped path.
inline bool phase2Oris() {
    static const bool v = [] {
        const char* q = std::getenv("OGC_PHASE2_ORIS");
        return q && q[0] == '1' && q[1] == '\0';
    }();
    return v;
}
// Diagnostic-only shadow: after the shipped oracle rejects an insertion
// candidate that shares both event days with a resident, remember the first
// official-semantic fallback but keep the shipped verdict.  Max-iteration
// probes therefore follow the identical stochastic trajectory while measuring
// how many otherwise-failed insertions can be rescued.
inline bool exitTwinShadow() {
    static const bool v = [] {
        const char* q = std::getenv("OGC_EXIT_TWIN_SHADOW");
        return q && q[0] == '1' && q[1] == '\0';
    }();
    return v;
}
inline bool exitTwin() {
    static const bool v = [] {
        const char* q = std::getenv("OGC_EXIT_TWIN");
        return q && q[0] == '1' && q[1] == '\0';
    }();
    return v;
}
} // namespace knobs
