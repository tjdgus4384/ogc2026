// repair.hpp -- THE insertion primitive: greedy earliest-entry, first-fit.
//
// insertGreedy(A) sweeps candidate entry days upward from R[A]. At each day it
// scans corner-point positions (bay walls + edges of time-overlapping
// residents); the bit-grid prefilter (certain same-layer overlap, occ.hpp)
// rejects most candidates cheaply and vpConservative confirms survivors
// against the checker's five rules. The FIRST feasible placement at the
// EARLIEST day wins -- minimising this block's tardiness. Position ties break
// by scan order, which is randomised per call: bay order follows preference
// with prob 0.70 (0.92 in the tardiness-free regime -- prefStrong, set live
// by the anneal), orientations are shuffled.
//
// Three phases per call:
//   1  full corner scan at e = R[A]        (most insertions succeed here)
//   2  ascending resident-exit events: space only opens when someone leaves,
//      so scan localised around the departing block; a bay that was
//      area-saturated gets one full re-scan when area first frees
//   3  sparse dense sweep over remaining days -- completeness net
//
// v1 note: v0 carried a second, older insertion algorithm that only the
// seeder reached (a missing flag copy -- an accident, not a design). Seed
// quality across all 40 instances was equal on average between the two paths
// (18W/11T/11L, mean -2.16% in favour of this one), so v1 keeps only this.
#pragma once
#include "core.hpp"
#include "knobs.hpp"
#include "occ.hpp"
#include <random>
#include <algorithm>
#include <chrono>

namespace repair {
using core::Problem; using core::Sol;

struct Ctx {
    const Problem* pb;
    Sol* s;
    occ::OccIndex* oc;
    std::mt19937 rng;
    int horizon;
    const int* forcedBay = nullptr;   // optional block->bay lock (or -1)
    int densMode = -1;                // -1: knob; 0: bay-area cap
                                      // (never falsely rejects);
                                      // 1: instance fill cap (filters)
    const int* entryCap  = nullptr;   // optional block->latest-entry lock
                                      // (or -1): the Z1 twin of forcedBay.
                                      // Held for the WHOLE anneal -- the
                                      // pref-lock lesson (07-30) is that
                                      // transient starts wash out and only
                                      // anneal-long constraints move the
                                      // final basin.
    bool prefStrong = false;          // z1==0 regime: chase preferred bays hard.
                                      // Predicate lives in alns::run (sec 148):
                                      // z1<=0 AND z3<n -- a measured two-order
                                      // regime gap, not a tuned constant.
    // preference ejection chain (op 8) handoff: pickRemoval chose an upgrading
    // block and its target bay; the reinsert step must know to force that bay
    // with an on-time entry cap. Reset at every pickRemoval call.
    int tsBlk = -1, tsBay = -1, tsECap = -1;
    // live habitat flags for the two ejection ops, set by the obj lambda.
    // z3dom compared the two TOTALS, which is the wrong question for a
    // marginal move: one tardy day buys 44-119 preference points on these
    // boards, so a preference transfer that costs no tardiness pays whether or
    // not w3*z3 leads the sum. Gating on it switched op 8 off on P4/P5/P6,
    // where w3*z3 is still 0.61-0.97M and 38-59% of the gap to the all-team
    // minimum. The mask now only asks whether the op has anything to work on.
    bool z3dom = false;
    bool z3live = false;              // some block prefers another bay
    bool z1live = false;              // some block is tardy
    long exitTwinTests = 0;           // diagnostic shadow counters
    long exitTwinPass = 0;
    long exitTwinCompared = 0;
    long exitTwinDominant = 0;
    long exitTwinRescued = 0;
    long phase2Calls = 0;              // diagnostic orientation wiring audit
    long phase2Success = 0;
    struct ExitFallback {
        bool valid = false;
        int bay = -1, ori = 0, x = 0, y = 0, e = 0, f = 0;
    } exitFallback;
    // Default-off C5' experiment.  The normal insertion scan remains the
    // control, but may remember the first candidate whose one-way crane
    // obstruction has a feasible minimal exit closure.  Observation consumes
    // no RNG and never mutates the live solution.
    struct ExitExtension { int block = -1, from = 0, to = 0; };
    struct ExitClosureWitness {
        bool valid = false;
        int block = -1;
        ExitFallback candidate;
        uint64_t prefixSignature = 0;
        std::mt19937 rngAfterScan;
        std::vector<ExitExtension> extensions;
        int extendedBlocks = 0, extendedDays = 0, paidDays = 0;
        bool phase3Policy = false;
    } exitClosure;
    bool exitClosureProbe = false;
    // Archive observation is causally read-only: exact geometry calls bypass
    // the thread-local direct-mapped memos so their history remains control's.
    bool exitClosureMemoNeutral = false;
    bool exitClosureSeedSpent = false; // one structural attempt/neighbourhood
    int exitClosureCandidateTests = 0;
    int exitClosurePhase3WindowsNow = 0;
    bool exitClosureCandidateCapReported = false;
    bool exitClosurePhase3CapReported = false;
    long exitClosureCandidates = 0;
    long exitClosureSeeds = 0, exitClosureAttempts = 0;
    long exitClosureBuilt = 0, exitClosureHardRejects = 0;
    long exitClosureCycleRejects = 0, exitClosurePhase3Seeds = 0;
    long exitClosurePhase3Windows = 0, exitClosureBudgetHits = 0;
    long exitClosureDetectorNs = 0;
};

inline Ctx::ExitFallback currentPlacement(const Sol& s, int A) {
    return {true, s.bay[A], s.ori[A], s.x[A], s.y[A], s.e[A], s.f[A]};
}

inline void loadPlacement(Sol& s, int A, const Ctx::ExitFallback& q) {
    s.bay[A]=q.bay; s.ori[A]=q.ori; s.x[A]=q.x; s.y[A]=q.y;
    s.e[A]=q.e; s.f[A]=q.f; s.placed[A]=1;
}

inline void resetExitClosureProbe(Ctx& c, bool enabled) {
    c.exitClosureProbe = enabled;
    c.exitClosureSeedSpent = false;
    c.exitClosureCandidateTests = 0;
    c.exitClosurePhase3WindowsNow = 0;
    c.exitClosureCandidateCapReported = false;
    c.exitClosurePhase3CapReported = false;
    c.exitClosure = {};
}

inline bool exitClosureProbeOpen(const Ctx& c) {
    return c.exitClosureProbe && !c.exitClosure.valid &&
           !c.exitClosureSeedSpent;
}

inline uint64_t exitClosurePrefixSignature(const Sol& s, int candidate) {
    uint64_t h = 1469598103934665603ULL;
    auto mix = [&](uint64_t value) {
        h ^= value;
        h *= 1099511628211ULL;
    };
    for (int i = 0; i < (int)s.placed.size(); i++) {
        const bool placed = i != candidate && s.placed[i];
        mix((uint64_t)i);
        mix(placed ? 1ULL : 0ULL);
        if (!placed) continue;
        mix((uint32_t)s.bay[i]); mix((uint32_t)s.ori[i]);
        mix((uint32_t)s.x[i]);   mix((uint32_t)s.y[i]);
        mix((uint32_t)s.e[i]);   mix((uint32_t)s.f[i]);
    }
    for (int bay = 0; bay < (int)s.byBay.size(); bay++) {
        mix(0x9e3779b97f4a7c15ULL ^ (uint64_t)bay);
        for (int i : s.byBay[bay]) if (i != candidate) mix((uint32_t)i);
    }
    return h;
}

// Given fixed entries and geometry, compute the componentwise-minimal exits
// reachable by extension only.  If resident `inner` obstructs moving `outer`,
// coexistence requires inner's interval to be nested in outer's, hence
// f[outer] >= f[inner].  Newly extended intervals can cross more entry events,
// so scan to a monotone fixed point.  Same-entry directed cycles have no legal
// operation order and are rejected explicitly.
inline bool minimumExitClosure(const Problem& pb, Sol& trial, int bay,
                               int horizon,
                               std::vector<Ctx::ExitExtension>& extensions,
                               bool& cycleReject, bool& hardReject) {
    // Closure is bounded speculation at a causal/official audit boundary.  Use
    // the exact predicates here so even a 64-bit memo fingerprint collision
    // cannot turn a pure directed relation into a false mutual obstruction,
    // and do not warm/evict the live trajectory's process-lifetime cache.
    crane::ScopedMemoBypass memoBypass(true);
    const std::vector<int>& ids = trial.byBay[bay];
    std::vector<int> before = trial.f;
    extensions.clear();
    cycleReject = false;
    hardReject = false;

    for (int i : ids) {
        if (!trial.placed[i] || trial.bay[i] != bay ||
            trial.e[i] < pb.R[i] || trial.f[i] <= trial.e[i] ||
            trial.f[i] - trial.e[i] < pb.P[i] || trial.f[i] > horizon) {
            hardReject = true;
            return false;
        }
    }

    bool changed = true;
    while (changed) {
        changed = false;
        for (size_t a = 0; a < ids.size(); a++) {
            const int i = ids[a];
            if (!trial.placed[i]) continue;
            for (size_t b = a + 1; b < ids.size(); b++) {
                const int k = ids[b];
                if (!trial.placed[k]) continue;
                if (!(trial.e[i] < trial.f[k] &&
                      trial.e[k] < trial.f[i])) continue;
                if (core::restOverlap(pb, trial, i, k)) {
                    hardReject = true;
                    return false;
                }
                const bool kBlocksI = core::entryBlocked(pb, trial, k, i);
                const bool iBlocksK = core::entryBlocked(pb, trial, i, k);
                if (kBlocksI && iBlocksK) {
                    hardReject = true;
                    return false;
                }
                if (kBlocksI) {
                    // k is the inner obstructer; i must be its outer wrapper.
                    if (trial.e[i] > trial.e[k]) {
                        hardReject = true;
                        return false;
                    }
                    if (trial.f[i] < trial.f[k]) {
                        trial.f[i] = trial.f[k];
                        if (trial.f[i] > horizon) {
                            hardReject = true;
                            return false;
                        }
                        changed = true;
                    }
                } else if (iBlocksK) {
                    // i is the inner obstructer; k must be its outer wrapper.
                    if (trial.e[k] > trial.e[i]) {
                        hardReject = true;
                        return false;
                    }
                    if (trial.f[k] < trial.f[i]) {
                        trial.f[k] = trial.f[i];
                        if (trial.f[k] > horizon) {
                            hardReject = true;
                            return false;
                        }
                        changed = true;
                    }
                }
            }
        }
    }

    for (int i : ids) {
        if (!trial.placed[i] || trial.f[i] <= before[i]) continue;
        extensions.push_back({i, before[i], trial.f[i]});
    }
    if (extensions.empty()) return false;

    // A bool placement oracle is not enough when several operations share a
    // day: the returned schedule must admit concrete checker event orders.
    // Check ENTRY order before the aggregate placement oracle so a directed
    // same-day cycle is classified as such instead of being folded into the
    // generic hard-reject bucket by vpFlexibleExits.
    std::vector<int> days, order;
    for (int i : ids) if (trial.placed[i]) days.push_back(trial.e[i]);
    std::sort(days.begin(), days.end());
    days.erase(std::unique(days.begin(), days.end()), days.end());
    for (int day : days)
        if (!core::entryOrderAt(pb, trial, bay, day, order)) {
            cycleReject = true;
            return false;
        }

    for (int i : ids)
        if (trial.placed[i] && !core::vpFlexibleExits(pb, trial, i)) {
            hardReject = true;
            return false;
        }

    days.clear();
    for (int i : ids) if (trial.placed[i]) days.push_back(trial.f[i]);
    std::sort(days.begin(), days.end());
    days.erase(std::unique(days.begin(), days.end()), days.end());
    for (int day : days)
        if (!core::exitOrderAt(pb, trial, bay, day, order)) {
            hardReject = true;
            return false;
        }
    return true;
}

// Shadow the first structurally relevant rejected candidate.  A cheap
// pair-direction screen runs before the full Sol copy/fixed point.  The first
// seed owns the neighbourhood's single attempt whether or not closure works;
// this is a scan-order rule and a hard cost bound, not a fitted frequency.
inline void observeExitClosure(Ctx& c, int A, bool phase3Policy = false) {
    if (!exitClosureProbeOpen(c) || !c.s->placed[A]) return;
    crane::ScopedMemoBypass memoBypass(c.exitClosureMemoNeutral);
    const auto detectorStart = std::chrono::steady_clock::now();
    struct DetectorTimer {
        Ctx& c;
        std::chrono::steady_clock::time_point start;
        ~DetectorTimer() {
            c.exitClosureDetectorNs +=
                std::chrono::duration_cast<std::chrono::nanoseconds>(
                    std::chrono::steady_clock::now() - start).count();
        }
    } detectorTimer{c, detectorStart};
    const Problem& pb = *c.pb;
    const Sol& s = *c.s;
    const int bay = s.bay[A];
    const auto& gA = pb.geo[A].orients[s.ori[A]];
    bool bboxCandidate = false;
    bool seed = false;
    for (int k : s.byBay[bay]) {
        if (!s.placed[k] ||
            !(s.e[k] < s.f[A] && s.e[A] < s.f[k])) continue;
        const auto& gk = pb.geo[k].orients[s.ori[k]];
        if (!crane::bboxOverlapOff(gA.bb, s.x[A], s.y[A],
                                   gk.bb, s.x[k], s.y[k])) continue;
        bboxCandidate = true;
    }
    if (!bboxCandidate) return;
    if (c.exitClosureCandidateTests >= 8) {
        if (!c.exitClosureCandidateCapReported) {
            c.exitClosureCandidateCapReported = true;
            c.exitClosureBudgetHits++;
        }
        return;
    }
    c.exitClosureCandidateTests++;
    c.exitClosureCandidates++;

    for (int k : s.byBay[bay]) {
        if (!s.placed[k] ||
            !(s.e[k] < s.f[A] && s.e[A] < s.f[k])) continue;
        const auto& gk = pb.geo[k].orients[s.ori[k]];
        if (!crane::bboxOverlapOff(gA.bb, s.x[A], s.y[A],
                                   gk.bb, s.x[k], s.y[k])) continue;
        if (core::restOverlap(pb, s, A, k)) continue;
        const bool kBlocksA = core::entryBlocked(pb, s, k, A);
        const bool aBlocksK = core::entryBlocked(pb, s, A, k);
        if (kBlocksA == aBlocksK) continue;
        if (kBlocksA && s.e[A] <= s.e[k] && s.f[A] < s.f[k]) seed = true;
        if (aBlocksK && s.e[k] <= s.e[A] && s.f[k] < s.f[A]) seed = true;
        if (seed) break;
    }
    if (!seed) return;
    // The first raw structural seed owns this neighbourhood even if the
    // defensive official-oracle check below says it needs no duration change.
    c.exitClosureSeedSpent = true;
    c.exitClosureSeeds++;
    c.exitClosurePhase3Seeds += phase3Policy;
    c.exitClosureAttempts++;
    // Do not mix the separate same-day EXIT-order relaxation into this probe.
    // A minimum-duration candidate that is already official-feasible needs no
    // duration closure and belongs to the exit-twin experiment instead.
    if (core::vpFlexibleExits(pb, s, A)) return;
    Sol trial = s;
    trial.addIdx(A);
    std::vector<Ctx::ExitExtension> extensions;
    bool cycleReject = false, hardReject = false;
    if (!minimumExitClosure(pb, trial, bay, c.horizon, extensions,
                            cycleReject, hardReject)) {
        c.exitClosureCycleRejects += cycleReject;
        c.exitClosureHardRejects += hardReject;
        return;
    }

    Ctx::ExitClosureWitness w;
    w.valid = true;
    w.block = A;
    w.candidate = currentPlacement(s, A); // minimum-duration scan candidate
    w.prefixSignature = exitClosurePrefixSignature(s, A);
    w.rngAfterScan = c.rng;
    w.extensions = std::move(extensions);
    w.phase3Policy = phase3Policy;
    for (const auto& q : w.extensions) {
        w.extendedBlocks++;
        w.extendedDays += q.to - q.from;
        w.paidDays += std::max(0, q.to - pb.D[q.block]) -
                      std::max(0, q.from - pb.D[q.block]);
    }
    c.exitClosure = std::move(w);
    c.exitClosureBuilt++;
}

// Commit a previously certified witness to an identical replayed prefix.
// A is currently absent (the normal replay placement was removed solely to
// consume the control RNG).  Validate the complete new exit vector before
// touching occupancy.  On success, existing residents stamp only their new
// [oldF,newF) tails; this preserves the exact floating occupancy cache and
// avoids the non-reference-counted bit-grid hazards of unstamp/restamp.
inline bool applyExitClosure(Ctx& c, int A,
                             const Ctx::ExitClosureWitness& w) {
    if (!w.valid || w.block != A || A < 0 || A >= c.pb->n ||
        c.s->placed[A] || w.extensions.empty()) return false;
    const Problem& pb = *c.pb;
    Sol& s = *c.s;
    if (!w.candidate.valid || w.candidate.bay < 0 ||
        w.candidate.bay >= pb.m || w.candidate.e < pb.R[A] ||
        w.candidate.f - w.candidate.e != pb.P[A] ||
        w.candidate.f > c.horizon ||
        exitClosurePrefixSignature(s, A) != w.prefixSignature)
        return false;
    const Sol before = s;
    std::vector<uint8_t> seen((size_t)pb.n, 0);
    for (const auto& q : w.extensions) {
        if (q.block < 0 || q.block >= pb.n || seen[q.block] ||
            q.to <= q.from || q.to > c.horizon)
            return false;
        seen[q.block] = 1;
        if (q.block == A) {
            if (w.candidate.f != q.from) return false;
        } else if (!s.placed[q.block] ||
                   s.bay[q.block] != w.candidate.bay ||
                   s.f[q.block] != q.from) {
            return false;
        }
    }
    loadPlacement(s, A, w.candidate);
    s.addIdx(A);
    for (const auto& q : w.extensions) {
        if ((q.block != A && s.f[q.block] != q.from) ||
            (q.block == A && s.f[A] != q.from)) {
            s = before;
            return false;
        }
        s.f[q.block] = q.to;
    }
    bool ok = true;
    for (int k : s.byBay[s.bay[A]])
        if (s.placed[k] && !core::vpFlexibleExits(pb, s, k)) {
            ok = false;
            break;
        }
    std::vector<int> days, order;
    const int bay = s.bay[A];
    for (int k : s.byBay[bay]) if (s.placed[k]) days.push_back(s.e[k]);
    std::sort(days.begin(), days.end());
    days.erase(std::unique(days.begin(), days.end()), days.end());
    for (int day : days)
        if (!core::entryOrderAt(pb, s, bay, day, order)) ok = false;
    days.clear();
    for (int k : s.byBay[bay]) if (s.placed[k]) days.push_back(s.f[k]);
    std::sort(days.begin(), days.end());
    days.erase(std::unique(days.begin(), days.end()), days.end());
    for (int day : days)
        if (!core::exitOrderAt(pb, s, bay, day, order)) ok = false;
    if (!ok) { s = before; return false; }

    for (const auto& q : w.extensions) {
        if (q.block == A) continue;
        const int oldE = s.e[q.block];
        s.e[q.block] = q.from;
        c.oc->stamp(pb, s, q.block, true);
        s.e[q.block] = oldE;
    }
    c.oc->stamp(pb, s, A, true);
    return true;
}

// Official-feasible state predicate for operators that move an already
// constructed board.  The fixed oracle remains the cheap first path.
inline bool stateFeasible(Ctx& c, int A) {
    if (core::vpConservative(*c.pb, *c.s, A)) return true;
    return knobs::exitTwin() &&
           core::vpFlexibleExits(*c.pb, *c.s, A);
}

// Preserve the legacy first-fit path unless an exact-only candidate is
// objectively dominant on the SAME partial board.  We remember the first
// checker-only candidate and continue the original scan.  At the first legacy
// success, compare the two true weighted objectives without drawing RNG: a
// strict improvement may replace the legacy choice, while a tie or regression
// restores it byte-for-byte.  If the legacy scan exhausts, the saved candidate
// remains a rescue fallback.  SHADOW performs the comparison but never changes
// the selected placement.
inline bool insertionCandidate(Ctx& c, int A) {
    Sol& s = *c.s;
    if (core::vpConservative(*c.pb, s, A)) {
        if (!c.exitFallback.valid ||
            !(knobs::exitTwinShadow() || knobs::exitTwin()))
            return true;

        const auto legacy = currentPlacement(s, A);
        const double legacyObj = core::evaluate(*c.pb, s).total;
        loadPlacement(s, A, c.exitFallback);
        const bool exactStillFeasible = core::vpFlexibleExits(*c.pb, s, A);
        if (!exactStillFeasible) {
            // Residents cannot change during one insertion scan, so this is a
            // defensive invariant guard rather than an expected branch.
            loadPlacement(s, A, legacy);
            c.exitFallback.valid = false;
            return true;
        }
        const double exactObj = core::evaluate(*c.pb, s).total;
        c.exitTwinCompared++;
        const bool dominant = exactObj < legacyObj - 1e-9;
        c.exitTwinDominant += dominant;
        if (!knobs::exitTwin() || !dominant)
            loadPlacement(s, A, legacy);
        c.exitFallback.valid = false;
        return true;
    }
    observeExitClosure(c, A);
    if (!c.exitFallback.valid &&
        (knobs::exitTwinShadow() || knobs::exitTwin()) &&
        core::hasExitTwin(s, A)) {
        c.exitTwinTests++;
        const bool flexible = core::vpFlexibleExits(*c.pb, s, A);
        c.exitTwinPass += flexible;
        if (flexible) {
            c.exitFallback = {true, s.bay[A], s.ori[A], s.x[A], s.y[A],
                              s.e[A], s.f[A]};
        }
    }
    return false;
}

inline bool commitExitFallback(Ctx& c, int A) {
    if (!knobs::exitTwin() || !c.exitFallback.valid) return false;
    Sol& s = *c.s;
    const Problem& pb = *c.pb;
    const auto q = c.exitFallback;
    loadPlacement(s, A, q);
    if (!core::vpFlexibleExits(pb, s, A)) {
        s.placed[A]=0;
        c.exitFallback.valid=false;
        return false;
    }
    s.addIdx(A);
    c.oc->stamp(pb, s, A, true);
    c.exitTwinRescued++;
    return true;
}

// Area screen for bay j over [e,f) -- a SPEED HEURISTIC, never a verdict.
// It compares bbox-area sums (areaUsed) against bay area with 2% slack, and
// interlocking non-convex shapes can legally exceed that sum, so it may
// reject a feasible (bay, day). That is accepted by design: the screen's job
// is to skip crowded bays cheaply, and only a heuristic can be tight here --
// the exact per-layer area condition never binds at real densities (measured
// peaks 0.45-0.53, KERNEL_SPEC sec 191), so an exact screen would prune
// nothing. Two roles, two rules:
//   phases 1-2: screen aggressively (speed; misses are re-caught below)
//   phase 3:    NO screen -- the completeness net must stay unconditional.
// Screening phase 3 collapsed dense boards (v1 candidate: prob_16 +308%).
inline bool areaRoom(const Ctx& c, int j, double areaA, int e, int f) {
    // in polygon currency the ceiling is the bay area times what
    // polygons of THIS instance can actually reach (their mean
    // polygon/bbox fill); in bbox currency it is the bay area itself.
    // Two regimes, told apart by a flag the engine already maintains:
    // while tardiness is live the screener should FILTER (tight cap
    // from the instance's own polygon/bbox fill, which restores the
    // iteration budget); once z1 hits zero the board is packing for
    // preference and any false rejection costs real placements, so
    // fall back to the only cap that can never be wrong -- the bay
    // area itself. Measured 08-02: tight cap gives P4 -4.8%, P5
    // -1.2%, P6 -3.8% but P3 (z1-free) +9.0%.
    // HETEROGENEOUS SCREENING: a single cap cannot fit both regimes --
    // the tight fill cap is worth -1..-5% on roomy boards but +9% on
    // one that already runs at its own fill ratio (P3). Hidden
    // instances could be either, so threads split: some filter, some
    // never falsely reject, and the portfolio keeps whichever wins.
    double dens = 1.0;
    if (knobs::polyArea()) {
        int dm = (c.densMode >= 0) ? c.densMode : 1;
        dens = dm ? c.pb->fillRatio : 1.0;
    }
    double cap = c.pb->bayW[j] * c.pb->bayH[j] * dens * knobs::areaSlk();
    const auto& au = c.oc->areaUsed[j];
    for (int t = e; t < f && t < c.oc->T; t++)
        if (au[t] + areaA > cap) return false;
    return true;
}

// Candidate x/y sets from corner points: bay walls plus the four positions
// snugging A against each resident whose stay overlaps [e,f).
// resident bbox, orientation-independent -- collected once per (bay, e, f)
// and reused by every orientation's corner derivation (the resident scan
// was the dominant cost when repeated per ori; measured +11-16% removing
// the repeat in scanLocal alone).
struct RB { double x0, x1, y0, y1; };
inline void rbCollect(const Ctx& c, int j, int e, int f,
                      std::vector<RB>& rbs) {
    const Problem& pb = *c.pb;
    rbs.clear();
    for (int k : c.s->byBay[j]) {
        if (!c.s->placed[k]) continue;
        if (!(c.s->e[k] < f && e < c.s->f[k])) continue;    // time overlap
        const auto& gk = pb.geo[k].orients[c.s->ori[k]];
        rbs.push_back({gk.bb.x0 + c.s->x[k], gk.bb.x1 + c.s->x[k],
                       gk.bb.y0 + c.s->y[k], gk.bb.y1 + c.s->y[k]});
    }
}
inline void cornerSetsRB(const Ctx& c, int A, int oi, int j,
                         const std::vector<RB>& rbs,
                         std::vector<int>& xs, std::vector<int>& ys) {
    const Problem& pb = *c.pb;
    const auto& g = pb.geo[A].orients[oi];
    double W = pb.bayW[j], H = pb.bayH[j];
    int x0 = (int)std::ceil(-g.bb.x0), x1 = (int)std::floor(W - g.bb.x1);
    int y0 = (int)std::ceil(-g.bb.y0), y1 = (int)std::floor(H - g.bb.y1);
    xs.clear(); ys.clear();
    if (x1 < x0 || y1 < y0) return;
    xs.push_back(x0); xs.push_back(x1);
    ys.push_back(y0); ys.push_back(y1);
    double wA = g.bb.x1 - g.bb.x0, hA = g.bb.y1 - g.bb.y0;
    for (const RB& rb : rbs) {
        int cx[2] = {(int)std::ceil (rb.x1 - g.bb.x0),        // right of k
                     (int)std::floor(rb.x0 - wA - g.bb.x0)};  // left  of k
        for (int v : cx) if (v >= x0 && v <= x1) xs.push_back(v);
        int cy[2] = {(int)std::ceil (rb.y1 - g.bb.y0),        // above k
                     (int)std::floor(rb.y0 - hA - g.bb.y0)};  // below k
        for (int v : cy) if (v >= y0 && v <= y1) ys.push_back(v);
    }
    std::sort(xs.begin(), xs.end()); xs.erase(std::unique(xs.begin(), xs.end()), xs.end());
    std::sort(ys.begin(), ys.end()); ys.erase(std::unique(ys.begin(), ys.end()), ys.end());
}
inline void cornerSets(const Ctx& c, int A, int oi, int j, int e, int f,
                       std::vector<int>& xs, std::vector<int>& ys) {
    thread_local std::vector<RB> rbs;
    rbCollect(c, j, e, f, rbs);
    cornerSetsRB(c, A, oi, j, rbs, xs, ys);
}

// Try to place A at (bay j, entry e): every corner candidate of every
// orientation, snapshot-prefiltered, vp-confirmed. First fit wins; on success
// the placement is committed (index + occupancy stamp).
inline bool scanBayAt(Ctx& c, int A, int j, int e, const std::vector<int>& oris,
                      std::vector<int>& xs, std::vector<int>& ys) {
    const Problem& pb = *c.pb; Sol& s = *c.s;
    int f = e + pb.P[A];
    thread_local occ::OccIndex::Snap sn;
    c.oc->buildSnap(j, e, f, sn);
    thread_local std::vector<RB> rbs;
    rbCollect(c, j, e, f, rbs);
    for (int oi : oris) {
        cornerSetsRB(c, A, oi, j, rbs, xs, ys);
        for (int y : ys) {
            for (int x : xs) {
                if (c.oc->hitsSnap(sn, A, oi, x, y)) continue;
                s.bay[A]=j; s.ori[A]=oi; s.x[A]=x; s.y[A]=y;
                s.e[A]=e; s.f[A]=f; s.placed[A]=1;
                if (insertionCandidate(c, A)) {
                    s.addIdx(A);
                    c.oc->stamp(pb, s, A, true);
                    return true;
                }
                s.placed[A]=0;
            }
        }
    }
    return false;
}

// Localised scan at a revival event: when trig exits at e, only positions
// overlapping trig's footprint can NEWLY become feasible (every blocking rule
// is footprint-overlap based, so a position's blockers all intersect it, and
// the last one to leave is trig -- sec 48). Scans the anchor window around
// trig only.
inline bool scanLocal(Ctx& c, int A, int trig, int e, double areaA,
                      const std::vector<int>& oris) {
    const Problem& pb = *c.pb; Sol& s = *c.s;
    int f = e + pb.P[A];
    int j = s.bay[trig];
    if (c.forcedBay && c.forcedBay[A] >= 0 && c.forcedBay[A] != j) return false;
    if (!areaRoom(c, j, areaA, e, f)) return false;
    c.phase2Calls++;
    thread_local occ::OccIndex::Snap sn;
    c.oc->buildSnap(j, e, f, sn);
    const auto& gt = pb.geo[trig].orients[s.ori[trig]];
    double tx0 = gt.bb.x0 + s.x[trig], tx1 = gt.bb.x1 + s.x[trig];
    double ty0 = gt.bb.y0 + s.y[trig], ty1 = gt.bb.y1 + s.y[trig];
    thread_local std::vector<int> lxs, lys;
    // resident bboxes are orientation-INDEPENDENT: collect the
    // time-overlapping residents once per event, not once per orientation
    // (the ori loop only shifts them by its own wA/hA offsets).
    thread_local std::vector<RB> rbs;
    rbCollect(c, j, e, f, rbs);
    const bool wiredOrder = knobs::phase2Oris();
    for (size_t oo = 0; oo < pb.geo[A].orients.size(); oo++) {
        int oi = wiredOrder ? oris[oo] : (int)oo;
        const auto& g = pb.geo[A].orients[oi];
        double W = pb.bayW[j], H = pb.bayH[j];
        int x0 = (int)std::ceil(-g.bb.x0), x1 = (int)std::floor(W - g.bb.x1);
        int y0 = (int)std::ceil(-g.bb.y0), y1 = (int)std::floor(H - g.bb.y1);
        if (x1 < x0 || y1 < y0) continue;
        double wA = g.bb.x1 - g.bb.x0, hA = g.bb.y1 - g.bb.y0;
        // anchor window: positions whose span intersects trig's bbox
        int axlo = std::max(x0, (int)std::floor(tx0 - wA - g.bb.x0));
        int axhi = std::min(x1, (int)std::ceil (tx1 - g.bb.x0));
        int aylo = std::max(y0, (int)std::floor(ty0 - hA - g.bb.y0));
        int ayhi = std::min(y1, (int)std::ceil (ty1 - g.bb.y0));
        if (axhi < axlo || ayhi < aylo) continue;
        lxs.clear(); lys.clear();
        lxs.push_back(axlo); if (axhi != axlo) lxs.push_back(axhi);
        lys.push_back(aylo); if (ayhi != aylo) lys.push_back(ayhi);
        for (const RB& rb : rbs) {
            int rx = (int)std::ceil (rb.x1 - g.bb.x0);
            int lx = (int)std::floor(rb.x0 - wA - g.bb.x0);
            int ay = (int)std::ceil (rb.y1 - g.bb.y0);
            int by = (int)std::floor(rb.y0 - hA - g.bb.y0);
            if (rx >= axlo && rx <= axhi) lxs.push_back(rx);
            if (lx >= axlo && lx <= axhi) lxs.push_back(lx);
            if (ay >= aylo && ay <= ayhi) lys.push_back(ay);
            if (by >= aylo && by <= ayhi) lys.push_back(by);
        }
        std::sort(lxs.begin(), lxs.end());
        lxs.erase(std::unique(lxs.begin(), lxs.end()), lxs.end());
        std::sort(lys.begin(), lys.end());
        lys.erase(std::unique(lys.begin(), lys.end()), lys.end());
        for (int yv : lys) for (int xv : lxs) {
            if (c.oc->hitsSnap(sn, A, oi, xv, yv)) continue;
            s.bay[A]=j; s.ori[A]=oi; s.x[A]=xv; s.y[A]=yv;
            s.e[A]=e; s.f[A]=f; s.placed[A]=1;
            if (insertionCandidate(c, A)) {
                s.addIdx(A);
                c.oc->stamp(pb, s, A, true);
                c.phase2Success++;
                return true;
            }
            s.placed[A]=0;
        }
    }
    return false;
}

// Insert block A at the earliest feasible entry <= eCap (eCap<=0: no cap).
inline void removeBlock(Ctx& c, int A);

inline bool insertGreedy(Ctx& c, int A, int eCap = -1) {
    const Problem& pb = *c.pb; Sol& s = *c.s;
    c.exitFallback.valid = false;
    if (c.entryCap && c.entryCap[A] >= 0 &&
        (eCap < 0 || c.entryCap[A] < eCap))
        eCap = c.entryCap[A];
    int eBot = pb.R[A];
    int eTop = (eCap > 0 ? eCap : c.horizon - pb.P[A] - 1);
    // SLACK-AWARE ENTRY (env OGC_SLACKDEF = fraction of slack, 0 =
    // off): repair always grabs the earliest legal day, so slack-rich
    // blocks occupy prime early space and tight blocks find none --
    // the measured pathology (peak utilisation 63% yet tardy blocks
    // wait 13-19 days past release). Starting slack-rich blocks later
    // reserves that space, inside the anneal, vp-exact.
    // ENERGETIC RESERVATION (env OGC_RESERVE): skip release days that
    // are PROVABLY over-subscribed -- days where the blocks that must
    // be present already demand more area than polygons can pack. A
    // block with slack gains nothing by entering there and denies the
    // space to someone with no choice. Unlike the refuted blanket
    // slack-deferral, this waits only where contention is proven, and
    // never past the block's own punctual entry.
    if (knobs::reserve() && !c.pb->dayLoad.empty()) {
        int punct = pb.D[A] - pb.P[A];
        int lim = std::min(punct, (int)c.pb->dayLoad.size() - 1);
        double thr = c.pb->fillRatio;
        while (eBot < lim && eBot < (int)c.pb->dayLoad.size() &&
               c.pb->dayLoad[eBot] > thr)
            eBot++;
    }
    if (eTop < eBot) return false;

    const int forced = (c.forcedBay ? c.forcedBay[A] : -1);
    const bool causalBayRng = forced >= 0 && knobs::causalBayRng();
    std::vector<int> bays;
    if (forced >= 0 && !causalBayRng) bays.assign(1, forced);
    else { bays.resize(pb.m); for (int j = 0; j < pb.m; j++) bays[j] = j; }
    std::vector<int> oris(pb.geo[A].orients.size());
    for (size_t o = 0; o < oris.size(); o++) oris[o] = (int)o;
    const double PREFP = knobs::prefP();
    if (std::uniform_real_distribution<double>(0,1)(c.rng)
            < (PREFP >= 0 ? PREFP : (c.prefStrong ? 0.92 : 0.70)))
        std::sort(bays.begin(), bays.end(), [&](int a, int b){
            return pb.pref[A][a] > pb.pref[A][b]; });
    else
        std::shuffle(bays.begin(), bays.end(), c.rng);
    if (causalBayRng) {
        // Filter only after the same full-vector operation as the free arm.
        // Candidate scans draw no RNG, so this also preserves the stream for
        // orientation choice and every later block.
        bays.erase(std::remove_if(bays.begin(), bays.end(),
                                  [&](int j){ return j != forced; }),
                   bays.end());
    }
    // COMPACT-ORIENTATION PREFERENCE (env OGC_ORIPREF = percent):
    // orientations of the same block differ in bounding-box area by a
    // median factor of 1.38 (max 3.19), and the boards are bbox-bound
    // (bbox utilisation 103% while polygon utilisation is 58-65%), so
    // drawing an orientation uniformly at random throws away real
    // capacity. Prefer the compact ones most of the time, keep some
    // random draws for diversity.
    const int OP = knobs::oriPref();
    if (OP > 0 && (int)(c.rng() % 100) < OP) {
        std::sort(oris.begin(), oris.end(), [&](int a, int b){
            const auto& ga = pb.geo[A].orients[a].bb;
            const auto& gb = pb.geo[A].orients[b].bb;
            return (ga.x1-ga.x0)*(ga.y1-ga.y0)
                 < (gb.x1-gb.x0)*(gb.y1-gb.y0); });
    } else {
        std::shuffle(oris.begin(), oris.end(), c.rng);
    }

    // orient-0 bbox area: the same currency areaUsed accumulates (occ::stamp
    // stamps the chosen orientation's bbox). Heuristic either way -- see
    // areaRoom.
    // orient-0 area, deliberately NOT the admissible min over
    // orientations. Making it admissible was tried 08-04 and REFUTED
    // (p36 3 seeds: P4 +4.7%, all-board mean +1.4% -- measured together
    // with the per-arm prefP change, so the isolated cost of this one
    // was never separated): the screen is a
    // SPEED device -- its false rejections are the pruning, and they
    // are what keeps the insertion from scanning every bay (the
    // comment on areaRoom measured 65% of P4's iteration budget lost
    // without it). Correctness never depended on it: the phase-3 sweep
    // is unconditional and re-examines what the screen skipped.
    const auto& g0 = pb.geo[A].orients[0];
    double areaA = knobs::polyArea() && g0.parea > 0.0
                   ? g0.parea
                   : (g0.bb.x1 - g0.bb.x0) * (g0.bb.y1 - g0.bb.y0);
    thread_local std::vector<int> xs, ys;

    // phase 1: full scan at the release day
    thread_local std::vector<char> areaRej;
    areaRej.assign(pb.m, 0);
    {
        int e = eBot, f = e + pb.P[A];
        for (int j : bays) {
            if (!areaRoom(c, j, areaA, e, f)) { areaRej[j] = 1; continue; }
            if (scanBayAt(c, A, j, e, oris, xs, ys)) return true;
        }
    }
    // phase 2: resident exits open space, in time order
    thread_local std::vector<std::pair<int,int>> fev;   // (exit day, resident)
    fev.clear();
    for (int j : bays)
        for (int k : s.byBay[j])
            if (s.placed[k] && s.f[k] > eBot && s.f[k] <= eTop)
                fev.push_back({s.f[k], k});
    std::sort(fev.begin(), fev.end());
    for (auto& pr : fev) {
        int e = pr.first, jt = s.bay[pr.second], f = e + pb.P[A];
        if (areaRej[jt]) {
            // bay skipped while area-saturated: once area fits again, restore
            // completeness with one full scan of the bay at this event.
            if (!areaRoom(c, jt, areaA, e, f)) continue;
            areaRej[jt] = 0;
            if (scanBayAt(c, A, jt, e, oris, xs, ys)) return true;
            continue;
        }
        if (scanLocal(c, A, pr.second, e, areaA, oris)) return true;
    }
    // phase 3: sparse dense sweep -- the completeness net. Unconditional by
    // design: the area screen is a heuristic (see areaRoom) and must not gate
    // the last resort. The candidate test here is the crane-aware occ::hits
    // (a placement policy: avoid sticky interlocks at crowded moments -- see
    // its comment), NOT the same-layer snapshot the fast phases use.
    int stride = (eTop - eBot <= 24) ? 1 : knobs::stride();
    for (int e = eBot; e <= eTop; e += stride) {
        int f = e + pb.P[A];
        for (int j : bays) {
            thread_local std::vector<RB> rbs;
            rbCollect(c, j, e, f, rbs);
            bool closureSnapReady = false;
            thread_local occ::OccIndex::Snap closureSameLayer;
            for (int oi : oris) {
                cornerSetsRB(c, A, oi, j, rbs, xs, ys);
                for (int y : ys) for (int x : xs) {
                    const bool policyHit = c.oc->hits(A, oi, j, x, y, e, f);
                    if (policyHit) {
                        // The dense policy deliberately excludes crane-
                        // interlocked positions.  Observe only candidates that
                        // remain clear under the same-layer exact-underfilter;
                        // the default path retains its original immediate
                        // continue and pays no snapshot build.
                        if (exitClosureProbeOpen(c) &&
                            c.exitClosureCandidateTests < 8 &&
                            (closureSnapReady ||
                             c.exitClosurePhase3WindowsNow < 4)) {
                            const auto detectorStart =
                                std::chrono::steady_clock::now();
                            if (!closureSnapReady) {
                                c.oc->buildSnap(j, e, f, closureSameLayer);
                                closureSnapReady = true;
                                c.exitClosurePhase3WindowsNow++;
                                c.exitClosurePhase3Windows++;
                            }
                            const bool sameLayerHit = c.oc->hitsSnap(
                                closureSameLayer, A, oi, x, y);
                            c.exitClosureDetectorNs +=
                                std::chrono::duration_cast<
                                    std::chrono::nanoseconds>(
                                    std::chrono::steady_clock::now() -
                                    detectorStart).count();
                            if (!sameLayerHit) {
                                s.bay[A]=j; s.ori[A]=oi; s.x[A]=x; s.y[A]=y;
                                s.e[A]=e; s.f[A]=f; s.placed[A]=1;
                                observeExitClosure(c, A, true);
                                s.placed[A]=0;
                            }
                        } else if (exitClosureProbeOpen(c)) {
                            bool& reported =
                                c.exitClosureCandidateTests >= 8
                                    ? c.exitClosureCandidateCapReported
                                    : c.exitClosurePhase3CapReported;
                            if (!reported) {
                                reported = true;
                                c.exitClosureBudgetHits++;
                            }
                        }
                        continue;
                    }
                    s.bay[A]=j; s.ori[A]=oi; s.x[A]=x; s.y[A]=y;
                    s.e[A]=e; s.f[A]=f; s.placed[A]=1;
                    if (insertionCandidate(c, A)) {
                        s.addIdx(A);
                        c.oc->stamp(pb, s, A, true);
                        return true;
                    }
                    s.placed[A]=0;
                }
            }
        }
    }
    return commitExitFallback(c, A);
}

inline void removeBlock(Ctx& c, int A) {
    if (!c.s->placed[A]) return;
    c.oc->stamp(*c.pb, *c.s, A, false);
    c.s->delIdx(A);
    c.s->placed[A] = 0;
}

} // namespace repair
