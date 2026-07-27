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
#include "occ.hpp"
#include <random>
#include <algorithm>

namespace repair {
using core::Problem; using core::Sol;

struct Ctx {
    const Problem* pb;
    Sol* s;
    occ::OccIndex* oc;
    std::mt19937 rng;
    int horizon;
    const int* forcedBay = nullptr;   // optional block->bay lock (or -1)
    bool prefStrong = false;          // z1==0 regime: chase preferred bays hard.
                                      // Predicate lives in alns::run (sec 148):
                                      // z1<=0 AND z3<n -- a measured two-order
                                      // regime gap, not a tuned constant.
};

// Smallest orientation bbox area: a true lower bound on the footprint
// whichever orientation gets chosen, so the area screen can never reject a
// bay that a slimmer orientation would fit. (v0 used orient 0's area, which
// is the LARGEST one for 8% of training blocks -> over-pruning.)
inline double minArea(const Problem& pb, int A) {
    double a = 1e18;
    for (const auto& g : pb.geo[A].orients)
        a = std::min(a, (g.bb.x1 - g.bb.x0) * (g.bb.y1 - g.bb.y0));
    return a;
}

// Free-area screen for bay j over [e,f). areaUsed is a bbox-area sum (itself
// an over-estimate of true occupancy), so the 1.02 slack keeps this a screen,
// not a verdict.
inline bool areaRoom(const Ctx& c, int j, double areaA, int e, int f) {
    double cap = c.pb->bayW[j] * c.pb->bayH[j] * 1.02;
    const auto& au = c.oc->areaUsed[j];
    for (int t = e; t < f && t < c.oc->T; t++)
        if (au[t] + areaA > cap) return false;
    return true;
}

// Candidate x/y sets from corner points: bay walls plus the four positions
// snugging A against each resident whose stay overlaps [e,f).
inline void cornerSets(const Ctx& c, int A, int oi, int j, int e, int f,
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
    for (int k : c.s->byBay[j]) {
        if (!c.s->placed[k]) continue;
        if (!(c.s->e[k] < f && e < c.s->f[k])) continue;    // time overlap
        const auto& gk = pb.geo[k].orients[c.s->ori[k]];
        double kx0 = gk.bb.x0 + c.s->x[k], kx1 = gk.bb.x1 + c.s->x[k];
        double ky0 = gk.bb.y0 + c.s->y[k], ky1 = gk.bb.y1 + c.s->y[k];
        int cx[2] = {(int)std::ceil (kx1 - g.bb.x0),          // right of k
                     (int)std::floor(kx0 - wA - g.bb.x0)};    // left  of k
        for (int v : cx) if (v >= x0 && v <= x1) xs.push_back(v);
        int cy[2] = {(int)std::ceil (ky1 - g.bb.y0),          // above k
                     (int)std::floor(ky0 - hA - g.bb.y0)};    // below k
        for (int v : cy) if (v >= y0 && v <= y1) ys.push_back(v);
    }
    std::sort(xs.begin(), xs.end()); xs.erase(std::unique(xs.begin(), xs.end()), xs.end());
    std::sort(ys.begin(), ys.end()); ys.erase(std::unique(ys.begin(), ys.end()), ys.end());
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
    for (int oi : oris) {
        cornerSets(c, A, oi, j, e, f, xs, ys);
        for (int y : ys) for (int x : xs) {
            if (c.oc->hitsSnap(sn, A, oi, x, y)) continue;
            s.bay[A]=j; s.ori[A]=oi; s.x[A]=x; s.y[A]=y;
            s.e[A]=e; s.f[A]=f; s.placed[A]=1;
            if (core::vpConservative(pb, s, A)) {
                s.addIdx(A);
                c.oc->stamp(pb, s, A, true);
                return true;
            }
            s.placed[A]=0;
        }
    }
    return false;
}

// Localised scan at a revival event: when trig exits at e, only positions
// overlapping trig's footprint can NEWLY become feasible (every blocking rule
// is footprint-overlap based, so a position's blockers all intersect it, and
// the last one to leave is trig -- sec 48). Scans the anchor window around
// trig only.
inline bool scanLocal(Ctx& c, int A, int trig, int e, double areaA) {
    const Problem& pb = *c.pb; Sol& s = *c.s;
    int f = e + pb.P[A];
    int j = s.bay[trig];
    if (c.forcedBay && c.forcedBay[A] >= 0 && c.forcedBay[A] != j) return false;
    if (!areaRoom(c, j, areaA, e, f)) return false;
    thread_local occ::OccIndex::Snap sn;
    c.oc->buildSnap(j, e, f, sn);
    const auto& gt = pb.geo[trig].orients[s.ori[trig]];
    double tx0 = gt.bb.x0 + s.x[trig], tx1 = gt.bb.x1 + s.x[trig];
    double ty0 = gt.bb.y0 + s.y[trig], ty1 = gt.bb.y1 + s.y[trig];
    thread_local std::vector<int> lxs, lys;
    for (size_t oo = 0; oo < pb.geo[A].orients.size(); oo++) {
        int oi = (int)oo;
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
        for (int k : s.byBay[j]) {
            if (!s.placed[k]) continue;
            if (!(s.e[k] < f && e < s.f[k])) continue;
            const auto& gk = pb.geo[k].orients[s.ori[k]];
            double kx0 = gk.bb.x0 + s.x[k], kx1 = gk.bb.x1 + s.x[k];
            double ky0 = gk.bb.y0 + s.y[k], ky1 = gk.bb.y1 + s.y[k];
            int rx = (int)std::ceil (kx1 - g.bb.x0);
            int lx = (int)std::floor(kx0 - wA - g.bb.x0);
            int ay = (int)std::ceil (ky1 - g.bb.y0);
            int by = (int)std::floor(ky0 - hA - g.bb.y0);
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
            if (core::vpConservative(pb, s, A)) {
                s.addIdx(A);
                c.oc->stamp(pb, s, A, true);
                return true;
            }
            s.placed[A]=0;
        }
    }
    return false;
}

// Insert block A at the earliest feasible entry <= eCap (eCap<=0: no cap).
inline bool insertGreedy(Ctx& c, int A, int eCap = -1) {
    const Problem& pb = *c.pb; Sol& s = *c.s;
    int eBot = pb.R[A];
    int eTop = (eCap > 0 ? eCap : c.horizon - pb.P[A] - 1);
    if (eTop < eBot) return false;

    std::vector<int> bays;
    if (c.forcedBay && c.forcedBay[A] >= 0) bays.assign(1, c.forcedBay[A]);
    else { bays.resize(pb.m); for (int j = 0; j < pb.m; j++) bays[j] = j; }
    std::vector<int> oris(pb.geo[A].orients.size());
    for (size_t o = 0; o < oris.size(); o++) oris[o] = (int)o;
    if (std::uniform_real_distribution<double>(0,1)(c.rng)
            < (c.prefStrong ? 0.92 : 0.70))
        std::sort(bays.begin(), bays.end(), [&](int a, int b){
            return pb.pref[A][a] > pb.pref[A][b]; });
    else
        std::shuffle(bays.begin(), bays.end(), c.rng);
    std::shuffle(oris.begin(), oris.end(), c.rng);

    double areaA = minArea(pb, A);
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
        if (scanLocal(c, A, pr.second, e, areaA)) return true;
    }
    // phase 3: sparse dense sweep -- the completeness net. Deliberately NO
    // area screen here: areaRoom compares a bbox-area SUM against bay area,
    // and interlocking non-convex shapes can exceed that sum while remaining
    // feasible, so the screen is a speed heuristic (phases 1-2), never a
    // verdict. Screening this phase collapsed dense boards (v1 candidate:
    // prob_16 +308%).
    int stride = (eTop - eBot <= 24) ? 1 : 6;
    for (int e = eBot; e <= eTop; e += stride) {
        for (int j : bays)
            if (scanBayAt(c, A, j, e, oris, xs, ys)) return true;
    }
    return false;
}

inline void removeBlock(Ctx& c, int A) {
    if (!c.s->placed[A]) return;
    c.oc->stamp(*c.pb, *c.s, A, false);
    c.s->delIdx(A);
    c.s->placed[A] = 0;
}

} // namespace repair
