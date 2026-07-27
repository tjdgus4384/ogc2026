// core.hpp -- problem data, solution state, conservative feasibility (exact
// port of move_fast._vp_conservative), objective evaluation.
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
    std::vector<std::vector<int>> byBay;      // neighbor index (placed only)
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
    for (int l = 0; l < L; l++) {
        if (crane::overlapsLay(ga, l, s.x[a], s.y[a],
                               gb, l, s.x[b], s.y[b])) return true;
    }
    return false;
}

// Exact port of move_fast._vp_conservative(prob, rx, A).
// Assumes s.placed[A]==1 with candidate values already written into s.
// DIAGNOSTIC ONLY: when true, skip crane entry/exit rules (rule 1 resting
// overlap stays). Never ship enabled; used to attribute the density ceiling
// (rules-bound vs search-bound). Guarded by env at engine build time.
inline bool& diagNoCrane() { static bool v = false; return v; }

inline bool vpConservative(const Problem& pb, const Sol& s, int A) {
    int jT = s.bay[A], eN = s.e[A], fN = s.f[A];
    const OrientGeom& gA = pb.geo[A].orients[s.ori[A]];
    double W = pb.bayW[jT], H = pb.bayH[jT];

    const std::vector<int>& nb = s.byBay[jT];
    // rule 1: time-overlapping same-bay neighbours must not overlap on the
    // SAME layer (resting collision)
    for (int k : nb) {
        if (k == A || !s.placed[k]) continue;
        if (!(s.e[k] < fN && eN < s.f[k])) continue;
        if (restOverlap(pb, s, A, k)) return false;
    }
    if (diagNoCrane()) return true;   // DIAGNOSTIC: rules 2/3 skipped
    // sec 166: pres buffers are thread_local -- vpConservative runs millions
    // of times per anneal and rules 2-5 used to heap-allocate a fresh vector
    // per call (rules 4/5: per neighbour event). Reuse is safe: entryOK /
    // exitOK only read the buffer within the call.
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
    // rule 3: A's exit at fN (same-day LIFO: earlier entrants remain)
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
    // sec 171: if A's footprint does not even bbox-overlap k, A cannot be a
    // blocker of k (the j>=kk rule needs positive-area overlap with A). Any
    // remaining entryOK failure would then be independent of A -- impossible,
    // because the state was vp-verified at every previous insertion. So the
    // whole O(nb) pres rebuild for this k is skippable. Exact, not heuristic.
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
    // sec 171: same exactness argument as rule 4 (no A-k overlap => A is not
    // a blocker => a failure here would be A-independent => pre-existing =>
    // impossible).
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
