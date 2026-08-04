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
    // Per-bay TIME-KEYED indexes. vp's five resident loops are the measured
    // 26-34% cost block (~22ns/iteration x pop x 5 passes); each rule is
    // really one of three query shapes, and each shape has a ZERO-DUPLICATE
    // bucket structure (a first stay-interval design lost 4-5% to bucket
    // duplication + dedup bookkeeping -- refuted by battery 07-30):
    //   stab(t)      "present at day t"      -> tbk[j][t/TB], one bucket
    //   enter(lo,hi) "e[k] in [lo,hi)"       -> ebk[j][e/TB], one home/block
    //   exit(lo,hi)  "f[k] in [lo,hi)"       -> fbk[j][(f-1)/TB], one home
    // Consumers' RESULTS are order-insensitive (booleans / obstacle sets fed
    // to pure conjunctions), so iteration-order changes do not change
    // trajectories. tbkN==0 (horizon not supplied) disables everything and
    // queries fall back to the full byBay scan.
    static constexpr int TB = 16;
    std::vector<std::vector<std::vector<int>>> tbk;   // stay homes  [bay][t/TB]
    std::vector<std::vector<std::vector<int>>> ebk;   // entry home  [bay][e/TB]
    std::vector<std::vector<std::vector<int>>> fbk;   // exit home   [bay][(f-1)/TB]
    int tbkN = 0;

    // bucket-only maintenance: e/f retimes keep the bay, so byBay must NOT
    // be touched there (its order is trajectory-relevant at legacy sites).
    void addTbk(int i) {
        if (!tbkN) return;
        int j = bay[i];
        for (int b = e[i] / TB, b1 = (f[i] - 1) / TB;
             b <= b1 && b < tbkN; b++)
            if (b >= 0) tbk[j][b].push_back(i);
        int be = e[i] / TB;      if (be >= 0 && be < tbkN) ebk[j][be].push_back(i);
        int bf = (f[i] - 1) / TB; if (bf >= 0 && bf < tbkN) fbk[j][bf].push_back(i);
    }
    static void swapRemove(std::vector<int>& w, int i) {
        for (size_t k = 0; k < w.size(); k++)
            if (w[k] == i) { w[k] = w.back(); w.pop_back(); break; }
    }
    void delTbk(int i) {
        if (!tbkN) return;
        int j = bay[i];
        for (int b = e[i] / TB, b1 = (f[i] - 1) / TB;
             b <= b1 && b < tbkN; b++)
            if (b >= 0) swapRemove(tbk[j][b], i);
        int be = e[i] / TB;      if (be >= 0 && be < tbkN) swapRemove(ebk[j][be], i);
        int bf = (f[i] - 1) / TB; if (bf >= 0 && bf < tbkN) swapRemove(fbk[j][bf], i);
    }
    void addIdx(int i) { byBay[bay[i]].push_back(i); addTbk(i); }
    void delIdx(int i) {
        swapRemove(byBay[bay[i]], i);
        delTbk(i);
    }
    void rebuildIdx(int m, int horizon = -1) {
        byBay.assign(m, {});
        if (horizon >= 0) {
            tbkN = horizon / TB + 2;
            tbk.assign(m, std::vector<std::vector<int>>(tbkN));
            ebk.assign(m, std::vector<std::vector<int>>(tbkN));
            fbk.assign(m, std::vector<std::vector<int>>(tbkN));
        } else if (tbkN) {
            for (auto* I : {&tbk, &ebk, &fbk})
                for (auto& B : *I) for (auto& w : B) w.clear();
        }
        for (int i = 0; i < (int)placed.size(); i++)
            if (placed[i]) addIdx(i);
    }
    // Query primitives. Callers keep their exact time filters (buckets are
    // supersets); fn returns true to stop, the primitive returns whether it
    // stopped. Zero duplicates by construction -- no dedup state anywhere.
    template <class F>
    bool forStab(int j, int t, F&& fn) const {          // candidates: present at t
        if (!tbkN) {
            for (int k : byBay[j]) if (fn(k)) return true;
            return false;
        }
        int b = t / TB;
        if (b < 0 || b >= tbkN) return false;
        for (int k : tbk[j][b]) if (fn(k)) return true;
        return false;
    }
    template <class F>
    bool forEnter(int j, int lo, int hi, F&& fn) const { // candidates: e[k] in [lo,hi)
        if (!tbkN) {
            for (int k : byBay[j]) if (fn(k)) return true;
            return false;
        }
        if (lo >= hi) return false;
        int b0 = std::max(0, lo / TB), b1 = std::min(tbkN - 1, (hi - 1) / TB);
        for (int b = b0; b <= b1; b++)
            for (int k : ebk[j][b]) if (fn(k)) return true;
        return false;
    }
    template <class F>
    bool forExit(int j, int lo, int hi, F&& fn) const {  // candidates: f[k] in [lo,hi)
        if (!tbkN) {
            for (int k : byBay[j]) if (fn(k)) return true;
            return false;
        }
        if (lo >= hi) return false;
        int b0 = std::max(0, (lo - 1) / TB), b1 = std::min(tbkN - 1, (hi - 2) / TB);
        for (int b = b0; b <= b1; b++)
            for (int k : fbk[j][b]) if (fn(k)) return true;
        return false;
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

    // rule 1 (residents intersecting A's stay), split disjointly:
    // present at eN (e<=eN<f)  +  entering later (eN < e < fN)
    if (s.forStab(jT, eN, [&](int k) {
            if (k == A || !s.placed[k]) return false;
            if (!(s.e[k] <= eN && eN < s.f[k])) return false;
            return restOverlap(pb, s, A, k);
        })) return false;
    if (s.forEnter(jT, eN + 1, fN, [&](int k) {
            if (k == A || !s.placed[k]) return false;
            if (!(eN < s.e[k] && s.e[k] < fN)) return false;
            return restOverlap(pb, s, A, k);
        })) return false;
    // pres is reused across calls: vpConservative runs millions of times per
    // anneal and entryOK/exitOK only read the buffer within the call.
    thread_local std::vector<Placement> pres;
    // rule 2: A's entry at eN
    {
        pres.clear();
        s.forStab(jT, eN, [&](int k) {
            if (k == A || !s.placed[k]) return false;
            if (s.e[k] <= eN && eN < s.f[k])
                pres.push_back({&pb.geo[k].orients[s.ori[k]],
                                (double)s.x[k], (double)s.y[k]});
            return false;
        });
        if (!crane::entryOK(W, H, pres, gA, s.x[A], s.y[A])) return false;
    }
    // rule 3: A's exit at fN
    {
        pres.clear();
        pres.push_back({&gA, (double)s.x[A], (double)s.y[A]});  // target at [0]
        s.forStab(jT, fN - 1, [&](int k) {
            if (k == A || !s.placed[k]) return false;
            if (s.e[k] < fN && (s.f[k] > fN || (s.f[k] == fN && s.e[k] < eN)))
                pres.push_back({&pb.geo[k].orients[s.ori[k]],
                                (double)s.x[k], (double)s.y[k]});
            return false;
        });
        if (!crane::exitOK(pres, 0)) return false;
    }
    // rule 4: A must not block entry of any k entering during [eN, fN)
    if (s.forEnter(jT, eN, fN, [&](int k) {
            if (k == A || !s.placed[k]) return false;
            int t = s.e[k];
            if (!(eN <= t && t < fN)) return false;
            if (!crane::bboxOverlapOff(gA.bb, s.x[A], s.y[A],
                                       pb.geo[k].orients[s.ori[k]].bb,
                                       s.x[k], s.y[k])) return false;
            pres.clear();
            pres.push_back({&gA, (double)s.x[A], (double)s.y[A]});
            s.forStab(jT, t, [&](int k2) {
                if (k2 == A || k2 == k || !s.placed[k2]) return false;
                if (s.e[k2] <= t && t < s.f[k2])
                    pres.push_back({&pb.geo[k2].orients[s.ori[k2]],
                                    (double)s.x[k2], (double)s.y[k2]});
                return false;
            });
            const OrientGeom& gk = pb.geo[k].orients[s.ori[k]];
            return !crane::entryOK(W, H, pres, gk, s.x[k], s.y[k]);
        })) return false;
    // rule 5: A must not block exit of any k exiting during (eN, fN]
    if (s.forExit(jT, eN + 1, fN + 1, [&](int k) {
            if (k == A || !s.placed[k]) return false;
            int t = s.f[k], ek = s.e[k];
            if (!(eN < t && t <= fN)) return false;
            if (!crane::bboxOverlapOff(gA.bb, s.x[A], s.y[A],
                                       pb.geo[k].orients[s.ori[k]].bb,
                                       s.x[k], s.y[k])) return false;
            pres.clear();
            pres.push_back({&pb.geo[k].orients[s.ori[k]],
                            (double)s.x[k], (double)s.y[k]});  // target [0]
            if (fN > t || (fN == t && eN < ek))
                pres.push_back({&gA, (double)s.x[A], (double)s.y[A]});
            s.forStab(jT, t - 1, [&](int k2) {
                if (k2 == A || k2 == k || !s.placed[k2]) return false;
                if (s.e[k2] < t &&
                    (s.f[k2] > t || (s.f[k2] == t && s.e[k2] < ek)))
                    pres.push_back({&pb.geo[k2].orients[s.ori[k2]],
                                    (double)s.x[k2], (double)s.y[k2]});
                return false;
            });
            return pres.size() > 1 && !crane::exitOK(pres, 0);
        })) return false;
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
