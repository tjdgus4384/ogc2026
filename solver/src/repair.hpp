// repair.hpp -- greedy earliest-tardiness insertion with occupancy prefilter.
// For each unplaced block: sweep entry e upward from R; at each e scan
// positions (bay, orient, x, y); occ prefilter rejects certain-overlap cells,
// full vpConservative confirms crane feasibility; first feasible at the
// earliest e is taken (minimises tardiness; ties broken by scan order which
// is shuffled per call for diversification).
#pragma once
#include "core.hpp"
#include "occ.hpp"
#include <random>
#include <algorithm>
#include <chrono>

namespace repair {

struct Prof {
    long calls=0, fails=0;
    double t1=0, t2=0, t3=0;
    long succR=0, succEvt=0, succFall=0, evtScans=0;
    double g=0, h=0, v=0;      // scanLocal: gather / hits / vp seconds
    long hN=0, vN=0;
};
inline Prof& prof() { thread_local Prof p; return p; }
inline double pclk() {
    return std::chrono::duration<double>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}
using core::Problem; using core::Sol;

struct Ctx {
    const Problem* pb;
    Sol* s;
    occ::OccIndex* oc;
    std::mt19937 rng;
    int horizon;
    bool eventSweep = true;           // sec 149: constant true in production
                                      // (no setter, not inherited by c2).
    const int* forcedBay = nullptr;   // optional: block -> bay (or -1)
    bool eventLocal = false;          // A/B: event-localized revival scan
    bool nestAnchors = false;         // A/B: layered-nest candidate anchors
    bool scoreLocal = false;          // A/B: best-contact pick in scanLocal
    int waveG = 0;                    // wave grid period (0 = off)
    const int* eMin = nullptr;        // per-block entry floor (plan tracking)
    bool prefStrong = false;          // v62 (sec 148): chase preferred bays
                                      // hard ONLY when the preference optimum
                                      // is nearly reachable, i.e. tardiness is
                                      // gone AND residual preference loss is
                                      // small (z3 < n, ~1 unit per block).
                                      // Measured z3/n: p1 0.02, p2 0.15 vs
                                      // p22 19, p28 18, p38 26 -- a two-order
                                      // gap, not a tuned constant. On the high
                                      // side the preference optimum is not
                                      // placeable at all (sec 142), so the
                                      // boost only wastes search (p22 722k ->
                                      // 753k). Set by the ALNS loop.
};

// candidate x/y sets from "corner points": bay walls and edges of present
// blocks (their union-bbox), plus the block's own previous position.
inline void cornerSets(const Ctx& c, int A, int oi, int j, int e, int f,
                       std::vector<int>& xs, std::vector<int>& ys) {
    const Problem& pb = *c.pb;
    const auto& g = pb.geo[A].orients[oi];
    double W = pb.bayW[j], H = pb.bayH[j];
    int x0 = (int)std::ceil(-g.bb.x0), x1 = (int)std::floor(W - g.bb.x1);
    int y0 = (int)std::ceil(-g.bb.y0), y1 = (int)std::floor(H - g.bb.y1);
    if (x1 < x0 || y1 < y0) { xs.clear(); ys.clear(); return; }
    xs.clear(); ys.clear();
    xs.push_back(x0); xs.push_back(x1);
    ys.push_back(y0); ys.push_back(y1);
    double wA = g.bb.x1 - g.bb.x0, hA = g.bb.y1 - g.bb.y0;
    for (int k : c.s->byBay[j]) {
        if (!c.s->placed[k]) continue;
        if (!(c.s->e[k] < f && e < c.s->f[k])) continue;    // time overlap
        const auto& gk = pb.geo[k].orients[c.s->ori[k]];
        double kx0 = gk.bb.x0 + c.s->x[k], kx1 = gk.bb.x1 + c.s->x[k];
        double ky0 = gk.bb.y0 + c.s->y[k], ky1 = gk.bb.y1 + c.s->y[k];
        int cand[4] = {(int)std::ceil (kx1 - g.bb.x0),          // right of k
                       (int)std::floor(kx0 - wA - g.bb.x0),     // left  of k
                       0, 0};
        for (int t = 0; t < 2; t++) {
            int v = cand[t];
            if (v >= x0 && v <= x1) xs.push_back(v);
        }
        int candy[2] = {(int)std::ceil (ky1 - g.bb.y0),         // above k
                        (int)std::floor(ky0 - hA - g.bb.y0)};   // below k
        for (int t = 0; t < 2; t++) {
            int v = candy[t];
            if (v >= y0 && v <= y1) ys.push_back(v);
        }
        // NEST anchors: legal layered overlap (checker j>=k sweep) requires
        // A's interval nested inside k's, and both multi-layer. Rare and
        // cheap: aligned-inside positions let the layer-exact prefilter+vp
        // discover cantilever placements the outside corners never propose.
        if (c.nestAnchors &&
            c.s->e[k] < e && f <= c.s->f[k] &&
            pb.geo[A].orients[oi].layers.size() >= 2 &&
            gk.layers.size() >= 2) {
            int nxL = (int)std::round(kx0 - g.bb.x0);   // left-align into k
            int nxR = (int)std::round(kx1 - g.bb.x1);   // right-align into k
            int nyB = (int)std::round(ky0 - g.bb.y0);   // bottom-align
            int nyT = (int)std::round(ky1 - g.bb.y1);   // top-align
            if (nxL >= x0 && nxL <= x1) xs.push_back(nxL);
            if (nxR >= x0 && nxR <= x1) xs.push_back(nxR);
            if (nyB >= y0 && nyB <= y1) ys.push_back(nyB);
            if (nyT >= y0 && nyT <= y1) ys.push_back(nyT);
        }
    }
    std::sort(xs.begin(), xs.end()); xs.erase(std::unique(xs.begin(), xs.end()), xs.end());
    std::sort(ys.begin(), ys.end()); ys.erase(std::unique(ys.begin(), ys.end()), ys.end());
}

// per-insertion neighbour index: per bay, 16 x-buckets of neighbour ids for
// O(overlap) gather instead of O(all neighbours) per event (large-n relief).
struct NbIndex {
    static const int NB = 16;
    std::vector<std::vector<int>> bucket;   // [bay*NB + b] -> ids
    std::vector<double> bayW;
    std::vector<int> seen;                  // visit stamps (dedupe)
    int epoch = 0;
    void build(const Problem& pb, const Sol& s) {
        if (bucket.size() != (size_t)pb.m * NB)
            bucket.resize((size_t)pb.m * NB);
        for (auto& b : bucket) b.clear();   // reuse capacity, no realloc
        bayW.assign(pb.m, 1.0);
        for (int j = 0; j < pb.m; j++) bayW[j] = pb.bayW[j];
        if ((int)seen.size() < pb.n) seen.assign(pb.n, 0);
        epoch++;
        for (int j = 0; j < pb.m; j++) {
            for (int k : s.byBay[j]) {
                if (!s.placed[k]) continue;
                const auto& gk = pb.geo[k].orients[s.ori[k]];
                double kx0 = gk.bb.x0 + s.x[k], kx1 = gk.bb.x1 + s.x[k];
                int b0 = std::max(0, std::min(NB-1, (int)(kx0 / bayW[j] * NB)));
                int b1 = std::max(0, std::min(NB-1, (int)(kx1 / bayW[j] * NB)));
                for (int b = b0; b <= b1; b++)
                    bucket[(size_t)j * NB + b].push_back(k);
            }
        }
    }
    template <class F>
    void forRange(int j, double xlo, double xhi, F&& fn) {
        int b0 = std::max(0, std::min(NB-1, (int)(xlo / bayW[j] * NB)));
        int b1 = std::max(0, std::min(NB-1, (int)(xhi / bayW[j] * NB)));
        int ep = ++epoch;
        for (int b = b0; b <= b1; b++)
            for (int k : bucket[(size_t)j * NB + b]) {
                if (seen[k] == ep) continue;    // dedupe across buckets
                seen[k] = ep;
                fn(k);
            }
    }
};
inline NbIndex& nbindex() { thread_local NbIndex x; return x; }

// localized scan at a revival event: only positions overlapping the departed
// block trig's bbox can newly become feasible (all crane/entry rules are
// footprint-overlap based, so a position's blockers all intersect it; the
// last blocker's exit is the revival moment and that blocker IS trig).
inline void tryWaveAlign(Ctx& c, int A);   // wave exit-alignment (def below)

inline bool scanLocal(Ctx& c, int A, int trig, int e, int eCap,
                      NbIndex* nbx = nullptr) {
    const Problem& pb = *c.pb;
    Sol& s = *c.s;
    int f = e + pb.P[A];
    int j = s.bay[trig];
    if (c.forcedBay && c.forcedBay[A] >= 0 && c.forcedBay[A] != j) return false;
    // area prefilter for this bay/window
    double cap = pb.bayW[j] * pb.bayH[j];
    const auto& au = c.oc->areaUsed[j];
    const auto& g0 = pb.geo[A].orients[0];
    double areaA = (g0.bb.x1 - g0.bb.x0) * (g0.bb.y1 - g0.bb.y0);
    for (int t = e; t < f && t < c.oc->T; t++)
        if (au[t] + areaA > cap * 1.02) return false;
    thread_local occ::OccIndex::Snap sn;
    c.oc->buildSnap(j, e, f, sn);
    const auto& gt = pb.geo[trig].orients[s.ori[trig]];
    double tx0 = gt.bb.x0 + s.x[trig], tx1 = gt.bb.x1 + s.x[trig];
    double ty0 = gt.bb.y0 + s.y[trig], ty1 = gt.bb.y1 + s.y[trig];
    thread_local std::vector<int> lxs, lys;
    // best-contact selection (tube-preservation proxy): snug placements
    // fragment the remaining free space least. Only vp-passing candidates
    // are scored -> a few neighbour sweeps per successful call.
    double bestSc = -1e18; int bOi = 0, bX = 0, bY = 0;
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
        auto consider = [&](int k) {
            if (!s.placed[k]) return;
            if (!(s.e[k] < f && e < s.f[k])) return;
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
            if (c.nestAnchors &&
                s.e[k] < e && f <= s.f[k] &&
                pb.geo[A].orients[oi].layers.size() >= 2 &&
                gk.layers.size() >= 2) {
                int nxL = (int)std::round(kx0 - g.bb.x0);
                int nxR = (int)std::round(kx1 - g.bb.x1);
                int nyB = (int)std::round(ky0 - g.bb.y0);
                int nyT = (int)std::round(ky1 - g.bb.y1);
                if (nxL >= axlo && nxL <= axhi) lxs.push_back(nxL);
                if (nxR >= axlo && nxR <= axhi) lxs.push_back(nxR);
                if (nyB >= aylo && nyB <= ayhi) lys.push_back(nyB);
                if (nyT >= aylo && nyT <= ayhi) lys.push_back(nyT);
            }
        };
        if (nbx) {
            // anchors can come from neighbours whose edges project into the
            // window even from one block-width away: widen the x query.
            nbx->forRange(j, std::max(0.0, tx0 - wA - wA),
                          std::min(pb.bayW[j], tx1 + wA + wA), consider);
        } else {
            for (int k : s.byBay[j]) consider(k);
        }
        std::sort(lxs.begin(), lxs.end());
        lxs.erase(std::unique(lxs.begin(), lxs.end()), lxs.end());
        std::sort(lys.begin(), lys.end());
        lys.erase(std::unique(lys.begin(), lys.end()), lys.end());
        for (int yv : lys) {
            for (int xv : lxs) {
                prof().hN++;
                if (c.oc->hitsSnap(sn, A, oi, xv, yv)) continue;
                s.bay[A]=j; s.ori[A]=oi; s.x[A]=xv; s.y[A]=yv;
                s.e[A]=e; s.f[A]=f; s.placed[A]=1;
                prof().vN++;
                if (core::vpConservative(pb, s, A)) {
                    if (!c.scoreLocal) {
                        s.addIdx(A);
                        c.oc->stamp(pb, s, A, true);
                        tryWaveAlign(c, A);
                        return true;
                    }
                    double cx0 = g.bb.x0 + xv, cx1 = g.bb.x1 + xv;
                    double cy0 = g.bb.y0 + yv, cy1 = g.bb.y1 + yv;
                    double ct = 0.0;
                    if (cx0 <= 0.01)      ct += hA;
                    if (cx1 >= W - 0.01)  ct += hA;
                    if (cy0 <= 0.01)      ct += wA;
                    if (cy1 >= H - 0.01)  ct += wA;
                    for (int k2 : s.byBay[j]) {
                        if (k2 == A || !s.placed[k2]) continue;
                        if (!(s.e[k2] < f && e < s.f[k2])) continue;
                        const auto& gk2 = pb.geo[k2].orients[s.ori[k2]];
                        double kx0 = gk2.bb.x0 + s.x[k2];
                        double kx1 = gk2.bb.x1 + s.x[k2];
                        double ky0 = gk2.bb.y0 + s.y[k2];
                        double ky1 = gk2.bb.y1 + s.y[k2];
                        double yol = std::min(cy1, ky1) -
                                     std::max(cy0, ky0);
                        double xol = std::min(cx1, kx1) -
                                     std::max(cx0, kx0);
                        if (yol > 0 && (std::abs(cx1 - kx0) <= 0.51 ||
                                        std::abs(kx1 - cx0) <= 0.51))
                            ct += yol;
                        if (xol > 0 && (std::abs(cy1 - ky0) <= 0.51 ||
                                        std::abs(ky1 - cy0) <= 0.51))
                            ct += xol;
                    }
                    double sc2 = ct - 1e-3 * (xv + yv);
                    if (sc2 > bestSc) {
                        bestSc = sc2; bOi = oi; bX = xv; bY = yv;
                    }
                    s.placed[A] = 0;
                    continue;
                }
                s.placed[A]=0;
            }
        }
    }
    if (c.scoreLocal && bestSc > -1e17) {
        s.bay[A] = j; s.ori[A] = bOi; s.x[A] = bX; s.y[A] = bY;
        s.e[A] = e; s.f[A] = e + pb.P[A]; s.placed[A] = 1;
        if (core::vpConservative(pb, s, A)) {
            s.addIdx(A);
            c.oc->stamp(pb, s, A, true);
            tryWaveAlign(c, A);
            return true;
        }
        s.placed[A] = 0;
    }
    return false;
}

// try to insert block A; returns true and stamps grid on success.
// mode: corner-point candidates first; full-grid fallback for robustness.
// Accepts the FIRST vp-valid placement found (sec 150: the old bestOfK>1
// scoring path was provably dead -- runPortfolio pinned rp.bestOfK = 1 and
// every other caller used the default, so the Cand/found/pickbest machinery
// never ran; removed).
inline bool insertGreedy(Ctx& c, int A, int eCap = -1) {
    const Problem& pb = *c.pb;
    Sol& s = *c.s;
    int eTop = (eCap > 0 ? eCap : c.horizon - pb.P[A] - 1);
    int eBot = pb.R[A];
    if (c.eMin && c.eMin[A] > eBot) eBot = c.eMin[A];
    std::vector<int> bays, oris(pb.geo[A].orients.size());
    if (c.forcedBay && c.forcedBay[A] >= 0) bays.assign(1, c.forcedBay[A]);
    else { bays.resize(pb.m); for (int j = 0; j < pb.m; j++) bays[j] = j; }
    for (size_t o = 0; o < oris.size(); o++) oris[o] = (int)o;
    // v58: regime-adaptive pref-first. sec 141 measured that the dominant
    // objective term is decided by whether tardiness is fully eliminated,
    // not by instance size (p22: n=100 yet w3*Z3=780k; p28/p38: Z1 dominates).
    // prefStrong is set live by the ALNS loop when Z1 hits 0.
    if (std::uniform_real_distribution<double>(0,1)(c.rng)
            < (c.prefStrong ? 0.92 : 0.70))
        std::sort(bays.begin(), bays.end(), [&](int a, int b){
            return pb.pref[A][a] > pb.pref[A][b]; });
    else
        std::shuffle(bays.begin(), bays.end(), c.rng);
    std::shuffle(oris.begin(), oris.end(), c.rng);

    double areaA = 0;                        // footprint (layer-0 bbox proxy)
    {
        const auto& g0 = pb.geo[A].orients[0];
        areaA = (g0.bb.x1 - g0.bb.x0) * (g0.bb.y1 - g0.bb.y0);
    }
    thread_local std::vector<int> xs, ys;
    // event-driven entry candidates: R[A], every neighbour exit f (a slot
    // opens), and e[k]-P[A] (finish just before a future entrant). Dense
    // fallback below covers anything exotic when we are already very late.
    thread_local std::vector<int> ev;
    ev.clear();
    if (c.eventLocal && c.eventSweep) {
        // Phase 1: full scan at e = R (existing corner machinery, single e)
        // Phase 2: ascending f-events, localized to the departing block.
        thread_local std::vector<std::pair<int,int>> fev;   // (f, k)
        fev.clear();
        for (int j = 0; j < pb.m; j++) {
            if (c.forcedBay && c.forcedBay[A] >= 0 && c.forcedBay[A] != j)
                continue;
            for (int k : s.byBay[j]) {
                if (!s.placed[k]) continue;
                if (s.f[k] > eBot && s.f[k] <= eTop)
                    fev.push_back({s.f[k], k});
            }
        }
        std::sort(fev.begin(), fev.end());
        // NbIndex REJECTED (§53): n900 A/B showed no gain (1125 vs 1062
        // without), and the x-bucket filter drops far-x neighbours' y-anchors
        // (candidate reduction — repeatedly proven harmful). Kept dormant.
        NbIndex* nbxp = nullptr;
        ev.assign(1, eBot);
        prof().calls++;
        thread_local std::vector<char> arejR;
        arejR.assign(pb.m, 0);
        // fall through to the generic loop for e=R only, then run phase 2
        for (int e : ev) {
            int f = e + pb.P[A];
            for (int j : bays) {
                double cap = pb.bayW[j] * pb.bayH[j];
                bool room = true;
                const auto& au = c.oc->areaUsed[j];
                for (int t = e; t < f && t < c.oc->T; t++)
                    if (au[t] + areaA > cap * 1.02) { room = false; break; }
                if (!room) { arejR[j] = 1; continue; }
                thread_local occ::OccIndex::Snap snR;
                c.oc->buildSnap(j, e, f, snR);
                for (int oi : oris) {
                    cornerSets(c, A, oi, j, e, f, xs, ys);
                    for (int y : ys) for (int x : xs) {
                        if (c.oc->hitsSnap(snR, A, oi, x, y)) continue;
                        s.bay[A]=j; s.ori[A]=oi; s.x[A]=x; s.y[A]=y;
                        s.e[A]=e; s.f[A]=f; s.placed[A]=1;
                        if (core::vpConservative(pb, s, A)) {
                            s.addIdx(A); c.oc->stamp(pb, s, A, true); tryWaveAlign(c, A);
                            prof().succR++;
                            return true;
                        }
                        s.placed[A]=0;
                    }
                }
            }
        }
        for (auto& pr : fev) {
            prof().evtScans++;
            int jt = s.bay[pr.second];
            if (arejR[jt]) {
                // bay was area-saturated at R and skipped: once area fits at
                // this event, restore completeness with a FULL bay scan here.
                int e2 = pr.first, f2 = e2 + pb.P[A];
                double cap2 = pb.bayW[jt] * pb.bayH[jt];
                bool room2 = true;
                const auto& au2 = c.oc->areaUsed[jt];
                for (int t = e2; t < f2 && t < c.oc->T; t++)
                    if (au2[t] + areaA > cap2 * 1.02) { room2 = false; break; }
                if (room2) {
                    arejR[jt] = 0;
                    thread_local occ::OccIndex::Snap snE;
                    c.oc->buildSnap(jt, e2, f2, snE);
                    for (int oi : oris) {
                        cornerSets(c, A, oi, jt, e2, f2, xs, ys);
                        for (int y : ys) for (int x : xs) {
                            if (c.oc->hitsSnap(snE, A, oi, x, y)) continue;
                            s.bay[A]=jt; s.ori[A]=oi; s.x[A]=x; s.y[A]=y;
                            s.e[A]=e2; s.f[A]=f2; s.placed[A]=1;
                            if (core::vpConservative(pb, s, A)) {
                                s.addIdx(A); c.oc->stamp(pb, s, A, true); tryWaveAlign(c, A);
                                prof().succEvt++;
                                return true;
                            }
                            s.placed[A]=0;
                        }
                    }
                    continue;   // full scan supersedes the local scan
                }
            }
            if (scanLocal(c, A, pr.second, pr.first, eCap, nbxp)) {
                prof().succEvt++;
                return true;
            }
        }
        // Phase 3: dense fallback (unchanged safety net)
        int stride = (eTop - pb.R[A] <= 24) ? 1 : 6;
        for (int e = eBot; e <= eTop; e += stride) {
            int f = e + pb.P[A];
            for (int j : bays) {
                for (int oi : oris) {
                    cornerSets(c, A, oi, j, e, f, xs, ys);
                    for (int y : ys) for (int x : xs) {
                        if (c.oc->hits(A, oi, j, x, y, e, f)) continue;
                        s.bay[A]=j; s.ori[A]=oi; s.x[A]=x; s.y[A]=y;
                        s.e[A]=e; s.f[A]=f; s.placed[A]=1;
                        if (core::vpConservative(pb, s, A)) {
                            s.addIdx(A); c.oc->stamp(pb, s, A, true); tryWaveAlign(c, A);
                            prof().succFall++;
                            return true;
                        }
                        s.placed[A]=0;
                    }
                }
            }
        }
        prof().fails++;
        return false;
    }
    if (c.eventSweep) {
        ev.push_back(pb.R[A]);
        for (int j = 0; j < pb.m; j++)
            for (int k : s.byBay[j]) {
                if (!s.placed[k]) continue;
                if (s.f[k] >= pb.R[A] && s.f[k] <= eTop) ev.push_back(s.f[k]);
                int cand = s.e[k] - pb.P[A];
                if (cand >= pb.R[A] && cand <= eTop) ev.push_back(cand);
            }
        std::sort(ev.begin(), ev.end());
        ev.erase(std::unique(ev.begin(), ev.end()), ev.end());
    } else {
        for (int e = eBot; e <= eTop; e++) ev.push_back(e);
    }
    for (int e : ev) {
        int f = e + pb.P[A];
        for (int j : bays) {
            // free-area prefilter over the window (incremental accounting)
            double cap = pb.bayW[j] * pb.bayH[j];
            bool room = true;
            const auto& au = c.oc->areaUsed[j];
            for (int t = e; t < f && t < c.oc->T; t++)
                if (au[t] + areaA > cap * 1.02) { room = false; break; }
            if (!room) continue;
            for (int oi : oris) {
                cornerSets(c, A, oi, j, e, f, xs, ys);
                for (int y : ys) {
                    for (int x : xs) {
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
    }
    // sparse dense-grid fallback (completeness net). Tight windows (small
    // eTop-R, e.g. assignment-fixed zero-tardiness rebuilds) need stride 1.
    int stride = (eTop - pb.R[A] <= 24) ? 1 : 6;
    for (int e = eBot; e <= eTop; e += stride) {
        int f = e + pb.P[A];
        {
            for (int j : bays) {
                double W = pb.bayW[j], H = pb.bayH[j];
                for (int oi : oris) {
                    const auto& g = pb.geo[A].orients[oi];
                    int gx0 = (int)std::ceil(-g.bb.x0), gx1 = (int)std::floor(W - g.bb.x1);
                    int gy0 = (int)std::ceil(-g.bb.y0), gy1 = (int)std::floor(H - g.bb.y1);
                    for (int y = gy0; y <= gy1; y++)
                        for (int x = gx0; x <= gx1; x++) {
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
    }
    return false;
}

inline void removeBlock(Ctx& c, int A) {
    if (!c.s->placed[A]) return;
    c.oc->stamp(*c.pb, *c.s, A, false);
    c.s->delIdx(A);
    c.s->placed[A] = 0;
}


// wave alignment: within slack, slide a fresh placement RIGHT so its exit
// lands on the wave grid (period ctx.waveG). Measured wall (prob_27): tube
// occupancy over a 24d window peaks at 0.78 while instantaneous density is
// 0.56 -- misaligned exits never open long contiguous tubes, so big/long
// blocks queue ~30d. Z1-safe (never past D); vp-gated for A and neighbours.
inline void tryWaveAlign(Ctx& c, int A) {
    if (c.waveG <= 0) return;
    const core::Problem& pb = *c.pb; core::Sol& s = *c.s;
    int G = c.waveG, f = s.f[A];
    int f2 = ((f + G - 1) / G) * G;
    if (f2 == f || f2 > pb.D[A]) return;
    int e2 = f2 - pb.P[A];
    if (e2 <= s.e[A]) return;
    int oldE = s.e[A], oldF = s.f[A];
    c.oc->stamp(pb, s, A, false);
    s.e[A] = e2; s.f[A] = f2;
    bool ok = core::vpConservative(pb, s, A);
    if (ok) {
        for (int k : s.byBay[s.bay[A]]) {
            if (k == A || !s.placed[k]) continue;
            if (!(s.e[k] < f2 && e2 < s.f[k])) continue;
            if (!core::vpConservative(pb, s, k)) { ok = false; break; }
        }
    }
    if (ok) { c.oc->stamp(pb, s, A, true); return; }
    s.e[A] = oldE; s.f[A] = oldF;
    c.oc->stamp(pb, s, A, true);
}

} // namespace repair
