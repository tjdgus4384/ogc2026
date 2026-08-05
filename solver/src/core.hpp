// core.hpp -- problem data, solution state, feasibility (the checker's five
// rules, evaluated for one block against its bay), objective.
#pragma once
#include "crane.hpp"
#include <vector>
#include <cmath>
#include <cstdint>
#include <chrono>
#include <algorithm>
#include <functional>
#include <unordered_set>

namespace core {
using crane::OrientGeom; using crane::BlockGeom; using crane::Placement;

struct Problem {
    int n = 0, m = 0;
    std::vector<BlockGeom> geo;            // [block].orients
    std::vector<int> R, D, P;              // integer days
    std::vector<double> workload;
    std::vector<std::vector<double>> pref; // [block][bay]
    std::vector<double> Smax;              // per block
    std::vector<double> bayW, bayH, u;     // u_j = avgArea / (W_j*H_j)
    double w1 = 0, w2 = 0, w3 = 0;
    // achievable polygon density: mean polygon/bbox fill of this
    // instance's pieces. With polygon-area accounting the screener
    // must compare against what polygons can ACTUALLY reach in a bay
    // (bbox packing tops out near 100%, so polygons top out near this
    // ratio) -- otherwise it never rejects and every insertion scans
    // every bay (measured: 65% of the iteration budget lost on P4).
    double fillRatio = 1.0;
    // per-day mandatory demand / total bay area: blocks whose
    // [release, punctual-entry] window covers a day MUST occupy space
    // somewhere that day. Days where this exceeds what polygons can
    // actually reach are provably contended -- a slack-rich block
    // entering there displaces someone who has no alternative.
    std::vector<double> dayLoad;
};

struct Sol {
    // per block; placed[i]==false => not in solution (mid-destroy)
    std::vector<uint8_t> placed;
    std::vector<int> bay, ori, x, y, e, f;
    std::vector<std::vector<int>> byBay;      // resident index (placed only)
    void init(int n, int m = 0) {
        placed.assign(n, 0);
        bay.assign(n, -1); ori.assign(n, 0);
        x.assign(n, 0); y.assign(n, 0); e.assign(n, 0); f.assign(n, 0);
        byBay.assign(m, {});
    }
    void addIdx(int i) { byBay[bay[i]].push_back(i); }
    void delIdx(int i) {
        auto& v = byBay[bay[i]];
        for (size_t k = 0; k < v.size(); k++)
            if (v[k] == i) { v[k] = v.back(); v.pop_back(); break; }
    }
    void rebuildIdx(int m) {
        byBay.assign(m, {});
        for (int i = 0; i < (int)placed.size(); i++)
            if (placed[i]) byBay[bay[i]].push_back(i);
    }
};
inline bool restOverlap(const Problem& pb, const Sol& s, int a, int b);
// Exact checker-order replay for one bay/day. Exits are monotone (removing a
// block cannot obstruct a later exit), while same-day entries require a small
// subset search because adding one block may obstruct another. The returned
// orders are directly usable by the solution emitter.
inline bool eventOrderAt(const Problem& pb, const Sol& s, int bay, int t,
                         std::vector<int>& exitOrder,
                         std::vector<int>& entryOrder) {
    exitOrder.clear();
    entryOrder.clear();
    std::vector<int> present, exits, entries;
    for (int i : s.byBay[bay]) {
        if (!s.placed[i]) continue;
        if (s.e[i] < t && t <= s.f[i]) present.push_back(i);
        if (s.f[i] == t) exits.push_back(i);
        if (s.e[i] == t) entries.push_back(i);
    }
    auto placements = [&](const std::vector<int>& ids) {
        std::vector<Placement> out;
        out.reserve(ids.size());
        for (int i : ids)
            out.push_back({&pb.geo[i].orients[s.ori[i]],
                           (double)s.x[i], (double)s.y[i]});
        return out;
    };

    std::sort(exits.begin(), exits.end());
    while (!exits.empty()) {
        bool progressed = false;
        for (size_t q = 0; q < exits.size(); q++) {
            const int id = exits[q];
            auto it = std::find(present.begin(), present.end(), id);
            if (it == present.end()) return false;
            std::vector<Placement> ps = placements(present);
            const size_t target = (size_t)(it - present.begin());
            if (!crane::exitOK(ps, (int)target)) continue;
            exitOrder.push_back(id);
            present.erase(it);
            exits.erase(exits.begin() + q);
            progressed = true;
            break;
        }
        if (!progressed) return false;
    }

    std::sort(entries.begin(), entries.end());
    if (entries.size() > 63) {
        // Pathological same-day batches avoid exponential state. Greedy is
        // fail-closed: it may reject an order but never admits an invalid one.
        while (!entries.empty()) {
            bool progressed = false;
            for (size_t q = 0; q < entries.size(); q++) {
                const int id = entries[q];
                std::vector<Placement> ps = placements(present);
                const auto& g = pb.geo[id].orients[s.ori[id]];
                if (!crane::entryOK(pb.bayW[bay], pb.bayH[bay], ps, g,
                                    s.x[id], s.y[id])) continue;
                entryOrder.push_back(id);
                present.push_back(id);
                entries.erase(entries.begin() + q);
                progressed = true;
                break;
            }
            if (!progressed) return false;
        }
        return true;
    }

    const std::vector<int> basePresent = present;
    std::unordered_set<uint64_t> failed;
    size_t searchNodes = 0;
    std::function<bool(uint64_t)> dfs = [&](uint64_t used) {
        if (++searchNodes > 512) return false;
        if (used == ((entries.size() == 64) ? ~0ull
                                             : ((1ull << entries.size()) - 1)))
            return true;
        if (failed.count(used)) return false;
        std::vector<int> ids = basePresent;
        for (size_t q = 0; q < entries.size(); q++)
            if (used & (1ull << q)) ids.push_back(entries[q]);
        std::vector<Placement> ps = placements(ids);
        for (size_t q = 0; q < entries.size(); q++) {
            if (used & (1ull << q)) continue;
            const int id = entries[q];
            const auto& g = pb.geo[id].orients[s.ori[id]];
            if (!crane::entryOK(pb.bayW[bay], pb.bayH[bay], ps, g,
                                s.x[id], s.y[id])) continue;
            entryOrder.push_back(id);
            if (dfs(used | (1ull << q))) return true;
            entryOrder.pop_back();
        }
        failed.insert(used);
        return false;
    };
    return dfs(0);
}

inline bool vpExactEvents(const Problem& pb, const Sol& s, int A) {
    const int bay = s.bay[A], e = s.e[A], f = s.f[A];
    for (int k : s.byBay[bay]) {
        if (k == A || !s.placed[k]) continue;
        if (s.e[k] < f && e < s.f[k] && restOverlap(pb, s, A, k))
            return false;
    }
    std::vector<int> times{e, f}, xo, en;
    for (int k : s.byBay[bay]) {
        if (k == A || !s.placed[k]) continue;
        if (e <= s.e[k] && s.e[k] < f) times.push_back(s.e[k]);
        if (e < s.f[k] && s.f[k] <= f) times.push_back(s.f[k]);
    }
    std::sort(times.begin(), times.end());
    times.erase(std::unique(times.begin(), times.end()), times.end());
    for (int t : times)
        if (!eventOrderAt(pb, s, bay, t, xo, en)) return false;
    return true;
}

// same-layer positive-area overlap between two placed blocks (rule 1 helper)
inline bool restOverlap(const Problem& pb, const Sol& s, int a, int b) {
    const OrientGeom& ga = pb.geo[a].orients[s.ori[a]];
    const OrientGeom& gb = pb.geo[b].orients[s.ori[b]];
    if (!crane::bboxOverlapOff(ga.bb, s.x[a], s.y[a], gb.bb, s.x[b], s.y[b]))
        return false;
    bool hit;
    if (crane::pairMemo(ga, s.x[a], s.y[a], gb, s.x[b], s.y[b], 0, hit))
        return hit;
    int L = std::min(ga.layers.size(), gb.layers.size());
    for (int l = 0; l < L; l++)
        if (crane::overlapsLay(ga, l, s.x[a], s.y[a],
                               gb, l, s.x[b], s.y[b])) return true;
    return false;
}
// Whole-state exact audit without repeating every event once per resident.
inline bool stateExactEvents(const Problem& pb, const Sol& s) {
    std::vector<int> exits, entries;
    for (int bay = 0; bay < pb.m; bay++) {
        const auto& ids = s.byBay[bay];
        for (size_t a = 0; a < ids.size(); a++) {
            const int i = ids[a];
            if (!s.placed[i]) continue;
            for (size_t b = a + 1; b < ids.size(); b++) {
                const int k = ids[b];
                if (!s.placed[k]) continue;
                if (s.e[i] < s.f[k] && s.e[k] < s.f[i] &&
                    restOverlap(pb, s, i, k))
                    return false;
            }
        }
        std::vector<int> times;
        for (int i : ids) if (s.placed[i]) {
            times.push_back(s.e[i]);
            times.push_back(s.f[i]);
        }
        std::sort(times.begin(), times.end());
        times.erase(std::unique(times.begin(), times.end()), times.end());
        for (int t : times)
            if (!eventOrderAt(pb, s, bay, t, exits, entries)) return false;
    }
    return true;
}

// Feasibility of block A at its current (bay, ori, x, y, e, f) against every
// resident of its bay -- the five checker rules from A's point of view:
//   1  resting collision: same-layer overlap with any time-overlapping resident
//   2  A's own entry at e: crane rule vs blocks present at e
//   3  A's own exit at f: crane rule vs blocks still present at f
//      (same-day LIFO: an earlier entrant leaving the same day stays "present")
//   4  A must not block the entry of any resident entering during [e, f)
//   5  A must not block the exit of any resident exiting during (e, f]
// Rules 4/5 skip residents whose bbox does not even touch A: A cannot be a
// blocker without positive-area overlap, and every prior insertion was
// verified here, so any remaining failure would be A-independent and thus
// pre-existing -- impossible. Exact, not a heuristic (sec 171). The early-out
// is only valid while every placement passes through this function; an
// operator writing placements directly must re-verify them (sec 176).

inline bool vpConservative(const Problem& pb, const Sol& s, int A) {
    int jT = s.bay[A], eN = s.e[A], fN = s.f[A];
    const OrientGeom& gA = pb.geo[A].orients[s.ori[A]];
    double W = pb.bayW[jT], H = pb.bayH[jT];

    const std::vector<int>& nb = s.byBay[jT];
    // rule 1
    for (int k : nb) {
        if (k == A || !s.placed[k]) continue;
        if (!(s.e[k] < fN && eN < s.f[k])) continue;
        if (restOverlap(pb, s, A, k)) return false;
    }
    // pres is reused across calls: vpConservative runs millions of times per
    // anneal and entryOK/exitOK only read the buffer within the call.
    thread_local std::vector<Placement> pres;
    // rule 2: A's entry at eN
    {
        pres.clear();
        for (int k : nb) {
            if (k == A || !s.placed[k]) continue;
            if (s.e[k] <= eN && eN < s.f[k])
                pres.push_back({&pb.geo[k].orients[s.ori[k]],
                                (double)s.x[k], (double)s.y[k]});
        }
        if (!crane::entryOK(W, H, pres, gA, s.x[A], s.y[A])) return false;
    }
    // rule 3: A's exit at fN
    {
        pres.clear();
        pres.push_back({&gA, (double)s.x[A], (double)s.y[A]});  // target at [0]
        for (int k : nb) {
            if (k == A || !s.placed[k]) continue;
            if (s.e[k] < fN && (s.f[k] > fN || (s.f[k] == fN && s.e[k] < eN)))
                pres.push_back({&pb.geo[k].orients[s.ori[k]],
                                (double)s.x[k], (double)s.y[k]});
        }
        if (!crane::exitOK(pres, 0)) return false;
    }
    // rule 4: A must not block entry of any k entering during [eN, fN)
    for (int k : nb) {
        if (k == A || !s.placed[k]) continue;
        int t = s.e[k];
        if (!(eN <= t && t < fN)) continue;
        if (!crane::bboxOverlapOff(gA.bb, s.x[A], s.y[A],
                                   pb.geo[k].orients[s.ori[k]].bb,
                                   s.x[k], s.y[k])) continue;
        pres.clear();
        pres.push_back({&gA, (double)s.x[A], (double)s.y[A]});
        for (int k2 : nb) {
            if (k2 == A || k2 == k || !s.placed[k2]) continue;
            if (s.e[k2] <= t && t < s.f[k2])
                pres.push_back({&pb.geo[k2].orients[s.ori[k2]],
                                (double)s.x[k2], (double)s.y[k2]});
        }
        const OrientGeom& gk = pb.geo[k].orients[s.ori[k]];
        if (!crane::entryOK(W, H, pres, gk, s.x[k], s.y[k])) return false;
    }
    // rule 5: A must not block exit of any k exiting during (eN, fN]
    for (int k : nb) {
        if (k == A || !s.placed[k]) continue;
        int t = s.f[k], ek = s.e[k];
        if (!(eN < t && t <= fN)) continue;
        if (!crane::bboxOverlapOff(gA.bb, s.x[A], s.y[A],
                                   pb.geo[k].orients[s.ori[k]].bb,
                                   s.x[k], s.y[k])) continue;
        pres.clear();
        pres.push_back({&pb.geo[k].orients[s.ori[k]],
                        (double)s.x[k], (double)s.y[k]});   // target k at [0]
        if (fN > t || (fN == t && eN < ek))
            pres.push_back({&gA, (double)s.x[A], (double)s.y[A]});
        for (int k2 : nb) {
            if (k2 == A || k2 == k || !s.placed[k2]) continue;
            if (s.e[k2] < t && (s.f[k2] > t || (s.f[k2] == t && s.e[k2] < ek)))
                pres.push_back({&pb.geo[k2].orients[s.ori[k2]],
                                (double)s.x[k2], (double)s.y[k2]});
        }
        if (pres.size() > 1 && !crane::exitOK(pres, 0)) return false;
    }
    return true;
}

struct Objective { double z1, z2, z3, total; };

inline Objective evaluate(const Problem& pb, const Sol& s);
// GLS penalties (guided local search): optional per-block tardiness
// multipliers. The anneal optimizes the PENALIZED objective while the
// caller keeps its incumbent by the TRUE one, so the search is pushed
// out of deep local optima without changing what "better" means.
// Null pointer = classic behaviour, bit-identical.
inline const double*& glsPen() {
    static thread_local const double* p = nullptr;
    return p;
}

// SLACK SURROGATE weight, always 0.0 since the 08-02 ablation refuted
// the term (the env knob was removed with the v5 pass); the machinery
// stays because evaluatePen reduces exactly to evaluate at w=0.
inline double& slackW() {
    static thread_local double w = 0.0;
    return w;
}

inline Objective evaluatePen(const Problem& pb, const Sol& s) {
    const double* pen = glsPen();
    const double sw = slackW();
    if (sw > 0.0) {
        double z1 = 0, z3 = 0, sl = 0;
        std::vector<double> load(pb.m, 0.0);
        for (int i = 0; i < pb.n; i++) {
            if (!s.placed[i]) continue;
            int d = s.f[i] - pb.D[i];
            z1 += std::max(0, d);
            if (d < 0) {
                double room = (double)(-d);
                double span = std::max(1.0, (double)(pb.D[i]
                                                     - pb.R[i]));
                sl += 1.0 - std::min(1.0, room / span);
            } else sl += 1.0;
            z3 += pb.Smax[i] - pb.pref[i][s.bay[i]];
            load[s.bay[i]] += pb.workload[i];
        }
        double z2 = 0;
        for (int a = 0; a < pb.m; a++)
            for (int b = a + 1; b < pb.m; b++)
                z2 = std::max(z2, std::fabs(pb.u[a]*load[a]
                                            - pb.u[b]*load[b]));
        z2 = std::floor(z2);
        double base = pb.w1*z1 + pb.w2*z2 + pb.w3*z3;
        return {z1, z2, z3, base + sw * pb.w1 * sl};
    }
    if (!pen) return evaluate(pb, s);
    double z1 = 0, z3 = 0, z1p = 0;
    std::vector<double> load(pb.m, 0.0);
    for (int i = 0; i < pb.n; i++) {
        if (!s.placed[i]) continue;
        double t = std::max(0, s.f[i] - pb.D[i]);
        z1 += t;
        z1p += t * (1.0 + pen[i]);
        z3 += pb.Smax[i] - pb.pref[i][s.bay[i]];
        load[s.bay[i]] += pb.workload[i];
    }
    double z2 = 0;
    for (int a = 0; a < pb.m; a++)
        for (int b = a + 1; b < pb.m; b++)
            z2 = std::max(z2, std::fabs(pb.u[a]*load[a] - pb.u[b]*load[b]));
    z2 = std::floor(z2);
    return {z1, z2, z3, pb.w1*z1p + pb.w2*z2 + pb.w3*z3};
}

inline Objective evaluate(const Problem& pb, const Sol& s) {
    double z1 = 0, z3 = 0;
    std::vector<double> load(pb.m, 0.0);
    for (int i = 0; i < pb.n; i++) {
        if (!s.placed[i]) continue;
        z1 += std::max(0, s.f[i] - pb.D[i]);
        z3 += pb.Smax[i] - pb.pref[i][s.bay[i]];
        load[s.bay[i]] += pb.workload[i];
    }
    double z2 = 0;
    for (int a = 0; a < pb.m; a++)
        for (int b = a + 1; b < pb.m; b++)
            z2 = std::max(z2, std::fabs(pb.u[a]*load[a] - pb.u[b]*load[b]));
    z2 = std::floor(z2);
    return {z1, z2, z3, pb.w1*z1 + pb.w2*z2 + pb.w3*z3};
}

// blockers of A entering at time tgt at its CURRENT (x,y,ori): placed k in
// same bay, present at tgt, with any (kLayer, j>=kLayer) positive overlap.
// Used by the crane-exact destroy operator.
inline void collectEntryBlockers(const Problem& pb, const Sol& s, int A,
                                 int tgt, std::vector<int>& out) {
    int jT = s.bay[A];
    const OrientGeom& gA = pb.geo[A].orients[s.ori[A]];
    for (int k : s.byBay[jT]) {
        if (k == A || !s.placed[k]) continue;
        if (!(s.e[k] <= tgt && tgt < s.f[k])) continue;
        const OrientGeom& gk = pb.geo[k].orients[s.ori[k]];
        if (!crane::bboxOverlapOff(gA.bb, s.x[A], s.y[A], gk.bb, s.x[k], s.y[k]))
            continue;
        bool hit = false;
        int nNew = (int)gA.layers.size(), nEx = (int)gk.layers.size();
        for (int kk = 0; kk < nNew && !hit; kk++)
            for (int j = kk; j < nEx && !hit; j++)
                if (crane::overlapsLay(gA, kk, s.x[A], s.y[A],
                                       gk, j, s.x[k], s.y[k])) hit = true;
        if (hit) out.push_back(k);
    }
}
} // namespace core
