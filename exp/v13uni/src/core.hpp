// core.hpp -- problem data, solution state, feasibility (the checker's five
// rules, evaluated for one block against its bay), objective.
#pragma once
#include "crane.hpp"
#include <vector>
#include <cmath>
#include <cstdint>

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

// same-layer positive-area overlap between two placed blocks (rule 1 helper)
inline bool restOverlap(const Problem& pb, const Sol& s, int a, int b) {
    const OrientGeom& ga = pb.geo[a].orients[s.ori[a]];
    const OrientGeom& gb = pb.geo[b].orients[s.ori[b]];
    if (!crane::bboxOverlapOff(ga.bb, s.x[a], s.y[a], gb.bb, s.x[b], s.y[b]))
        return false;
    int L = std::min(ga.layers.size(), gb.layers.size());
    for (int l = 0; l < L; l++)
        if (crane::overlapsLay(ga, l, s.x[a], s.y[a],
                               gb, l, s.x[b], s.y[b])) return true;
    return false;
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
