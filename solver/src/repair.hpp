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
    // live dominance gate for op 8: is the priced preference loss the dominant
    // objective term? Parameter-free (w3*z3 > w1*z1), set by the obj lambda.
    bool z3dom = false;
};

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
                if (core::vpConservative(pb, s, A)) {
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
    // resident bboxes are orientation-INDEPENDENT: collect the
    // time-overlapping residents once per event, not once per orientation
    // (the ori loop only shifts them by its own wA/hA offsets).
    thread_local std::vector<RB> rbs;
    rbCollect(c, j, e, f, rbs);
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
inline void removeBlock(Ctx& c, int A);

inline bool insertGreedy(Ctx& c, int A, int eCap = -1) {
    const Problem& pb = *c.pb; Sol& s = *c.s;
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

    std::vector<int> bays;
    if (c.forcedBay && c.forcedBay[A] >= 0) bays.assign(1, c.forcedBay[A]);
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
        if (scanLocal(c, A, pr.second, e, areaA)) return true;
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
            for (int oi : oris) {
                cornerSetsRB(c, A, oi, j, rbs, xs, ys);
                for (int y : ys) for (int x : xs) {
                    if (c.oc->hits(A, oi, j, x, y, e, f)) continue;
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
        }
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
