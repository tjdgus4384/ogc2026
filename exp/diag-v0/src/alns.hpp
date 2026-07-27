// alns.hpp -- adaptive LNS v2: 8 destroy operators, fractional removal size
// (DMS 5-15% rule), SA -> hill-climb phase switch, reheating, adaptive weights.
#pragma once
#include "repair.hpp"
#include <chrono>
#include <cmath>
#include <cstdio>

namespace alns {
using core::Problem; using core::Sol;

struct Backup { int A, bay, ori, x, y, e, f; };

struct RunStats { long iters=0, accepted=0, improved=0, failed=0; double best;
                  long badIter=-1; int badKind=-1; int badPhase=-1; };

// full-state audit. 0 ok; 1 byBay bounds; 2 stale member; 3 dup/missing;
// 4 vp violation. badBlock gets the offending block id.
inline int audit(const core::Problem& pb, core::Sol& s, int* badBlock = nullptr) {
    std::vector<int> cnt(pb.n, 0);
    for (int j = 0; j < pb.m; j++)
        for (int i : s.byBay[j]) {
            if (i < 0 || i >= pb.n) return 1;
            cnt[i]++;
            if (!s.placed[i] || s.bay[i] != j) { if (badBlock) *badBlock = i; return 2; }
        }
    for (int i = 0; i < pb.n; i++) {
        if (s.placed[i] && cnt[i] != 1) { if (badBlock) *badBlock = i; return 3; }
        if (!s.placed[i] && cnt[i] != 0) { if (badBlock) *badBlock = i; return 3; }
    }
    for (int i = 0; i < pb.n; i++)
        if (s.placed[i] && !core::vpConservative(pb, s, i)) {
            if (badBlock) *badBlock = i; return 4;
        }
    return 0;
}

struct Destroyers {
    std::vector<double> w, score;
    std::vector<int> used;
    std::mt19937* rng;
    int K;
    explicit Destroyers(int k) : w(k,1.0), score(k,0.0), used(k,0), K(k) {}
    int pick() {
        double sm = 0; for (double v : w) sm += v;
        double r = std::uniform_real_distribution<double>(0, sm)(*rng);
        for (int i = 0; i < K; i++) { r -= w[i]; if (r <= 0) return i; }
        return K - 1;
    }
    void update(double decay = 0.85) {
        for (int i = 0; i < K; i++) {
            if (used[i]) w[i] = decay * w[i] + (1 - decay) * (score[i] / used[i]);
            w[i] = std::max(w[i], 0.05);
            score[i] = 0; used[i] = 0;
        }
    }
};

inline std::vector<int> pickRemoval(repair::Ctx& c, int kind, int q,
                                    const std::vector<long>& lastTouch) {
    const Problem& pb = *c.pb; Sol& s = *c.s;
    std::vector<int> placedIds;
    for (int i = 0; i < pb.n; i++) if (s.placed[i]) placedIds.push_back(i);
    if ((int)placedIds.size() <= q) return placedIds;
    std::vector<int> out;
    auto U01 = [&]{ return std::uniform_real_distribution<double>(0,1)(c.rng); };
    if (kind == 0) {                                  // random
        std::shuffle(placedIds.begin(), placedIds.end(), c.rng);
        out.assign(placedIds.begin(), placedIds.begin() + q);
    } else if (kind == 1) {                           // worst tardiness (noisy)
        std::vector<std::pair<double,int>> t;
        for (int i : placedIds) {
            double td = std::max(0, s.f[i] - pb.D[i]);
            td *= 0.75 + 0.5 * U01();
            t.push_back({-td, i});
        }
        std::sort(t.begin(), t.end());
        for (int k = 0; k < q; k++) out.push_back(t[k].second);
    } else if (kind == 2) {                           // related (bay+time+space)
        int seed = placedIds[c.rng() % placedIds.size()];
        std::vector<std::pair<double,int>> rel;
        for (int i : placedIds) {
            if (i == seed) continue;
            double d = 0;
            d += (s.bay[i] != s.bay[seed]) ? 50.0 : 0.0;
            d += std::abs(s.e[i] - s.e[seed]);
            d += 0.25 * (std::abs(s.x[i]-s.x[seed]) + std::abs(s.y[i]-s.y[seed]));
            rel.push_back({d, i});
        }
        std::sort(rel.begin(), rel.end());
        out.push_back(seed);
        for (int k = 0; k + 1 < q && k < (int)rel.size(); k++)
            out.push_back(rel[k].second);
    } else if (kind == 3) {                           // crane-bbox neighbours
        std::vector<int> tardy;
        for (int i : placedIds) if (s.f[i] > pb.D[i]) tardy.push_back(i);
        int seed = tardy.empty() ? placedIds[c.rng() % placedIds.size()]
                                 : tardy[c.rng() % tardy.size()];
        int tgt = std::max(pb.R[seed], pb.D[seed] - pb.P[seed]);
        out.push_back(seed);
        const auto& gs = pb.geo[seed].orients[s.ori[seed]];
        for (int i : s.byBay[s.bay[seed]]) {
            if ((int)out.size() >= q) break;
            if (i == seed || !s.placed[i]) continue;
            if (!(s.e[i] <= tgt && tgt < s.f[i]) &&
                !(s.e[i] < s.f[seed] && s.e[seed] < s.f[i])) continue;
            const auto& gi = pb.geo[i].orients[s.ori[i]];
            if (crane::bboxOverlapOff(gs.bb, s.x[seed], s.y[seed],
                                      gi.bb, s.x[i], s.y[i]))
                out.push_back(i);
        }
    } else if (kind == 4) {                           // crane-exact blocker BFS
        std::vector<int> tardy;
        for (int i : placedIds) if (s.f[i] > pb.D[i]) tardy.push_back(i);
        int seed = tardy.empty() ? placedIds[c.rng() % placedIds.size()]
                                 : tardy[c.rng() % tardy.size()];
        std::vector<int> comp{seed}, frontier{seed};
        std::vector<uint8_t> in(pb.n, 0); in[seed] = 1;
        while (!frontier.empty() && (int)comp.size() < q) {
            int A2 = frontier.back(); frontier.pop_back();
            int tgt = std::max(pb.R[A2], pb.D[A2] - pb.P[A2]);
            if (tgt >= s.e[A2]) continue;
            std::vector<int> bl;
            core::collectEntryBlockers(pb, s, A2, tgt, bl);
            for (int b : bl)
                if (!in[b] && (int)comp.size() < q) {
                    in[b] = 1; comp.push_back(b); frontier.push_back(b);
                }
        }
        out = comp;
    } else if (kind == 5) {                           // bay x time-slice
        int seed = placedIds[c.rng() % placedIds.size()];
        int j = s.bay[seed];
        int win = 3 + (int)(c.rng() % 10);
        int t0 = std::max(0, s.e[seed] - (int)(c.rng() % win));
        std::vector<int> cand;
        for (int i : s.byBay[j])
            if (s.placed[i] && s.e[i] < t0 + win && t0 < s.f[i])
                cand.push_back(i);
        std::shuffle(cand.begin(), cand.end(), c.rng);
        if ((int)cand.size() > q) cand.resize(q);
        out = cand;
    } else if (kind == 6) {                           // semi-worst (top-30% tardy pool)
        std::vector<std::pair<double,int>> t;
        for (int i : placedIds)
            t.push_back({-(double)std::max(0, s.f[i] - pb.D[i]), i});
        std::sort(t.begin(), t.end());
        int top = std::max(q, (int)(0.3 * t.size()));
        std::vector<int> pool;
        for (int k = 0; k < top && k < (int)t.size(); k++) pool.push_back(t[k].second);
        std::shuffle(pool.begin(), pool.end(), c.rng);
        pool.resize(std::min((size_t)q, pool.size()));
        out = pool;
    } else if (kind == 8) {                           // suffix reconstruction (FAST)
        // pick a pivot time biased to the tardy region of a random bay;
        // remove everything entering at/after it in that bay (capped).
        int seed = placedIds[c.rng() % placedIds.size()];
        std::vector<int> tardy;
        for (int i : placedIds) if (s.f[i] > pb.D[i]) tardy.push_back(i);
        if (!tardy.empty()) seed = tardy[c.rng() % tardy.size()];
        int j = s.bay[seed];
        int t0 = std::max(0, s.e[seed] - (int)(c.rng() % 5));
        std::vector<int> cand;
        for (int i : s.byBay[j])
            if (s.placed[i] && s.e[i] >= t0) cand.push_back(i);
        std::sort(cand.begin(), cand.end(),
                  [&](int a, int b){ return s.e[a] < s.e[b]; });
        int cap = std::max(q, 2 * q);
        if ((int)cand.size() > cap) cand.resize(cap);
        out = cand;
    } else if (kind == 9) {                           // overloaded-bay relief (Z2)
        std::vector<double> load(pb.m, 0.0);
        for (int i : placedIds) load[s.bay[i]] += pb.workload[i];
        int jmax = 0;
        for (int j = 1; j < pb.m; j++)
            if (pb.u[j] * load[j] > pb.u[jmax] * load[jmax]) jmax = j;
        std::vector<int> cand;
        for (int i : s.byBay[jmax]) if (s.placed[i]) cand.push_back(i);
        std::shuffle(cand.begin(), cand.end(), c.rng);
        if ((int)cand.size() > q) cand.resize(q);
        out = cand;
    } else if (kind == 10) {                          // worst preference-loss (Z3)
        std::vector<std::pair<double,int>> t;
        for (int i : placedIds)
            t.push_back({-(pb.Smax[i] - pb.pref[i][s.bay[i]])
                         * (0.75 + 0.5 * U01()), i});
        std::sort(t.begin(), t.end());
        for (int k = 0; k < q && k < (int)t.size(); k++) out.push_back(t[k].second);
    } else if (kind == 11) {                          // era defrag (complete)
        // Tardy-seeded window in the seed's bay; remove EVERYTHING that
        // intersects [t0, t0+win). Subset removals (kind 5) keep the jam's
        // skeleton -- measured era density stalls at ~0.42 with ~0.57 peaks
        // while the interval bound at that same density leaves ~1000
        // block-days on prob_27. Complete removal lets the repair re-lay
        // the era; reinsertion is largest-first (defrag semantics).
        std::vector<int> tardy;
        for (int i : placedIds) if (s.f[i] > pb.D[i]) tardy.push_back(i);
        int seed = tardy.empty() ? placedIds[c.rng() % placedIds.size()]
                                 : tardy[c.rng() % tardy.size()];
        int j = s.bay[seed];
        int win = 6 + (int)(c.rng() % 10);
        int t0 = std::max(0, s.e[seed] - (int)(c.rng() % win));
        std::vector<int> cand;
        for (int i : s.byBay[j])
            if (s.placed[i] && s.e[i] < t0 + win && t0 < s.f[i])
                cand.push_back(i);
        int cap = 3 * q + 8;                          // safety valve
        if ((int)cand.size() > cap) {
            std::sort(cand.begin(), cand.end(),
                      [&](int a, int b){ return std::abs(s.e[a] - t0)
                                              < std::abs(s.e[b] - t0); });
            cand.resize(cap);
        }
        out = cand;
    } else {                                          // 7: least-recently-touched
        std::vector<std::pair<double,int>> t;
        for (int i : placedIds)
            t.push_back({(double)lastTouch[i] + 3.0 * U01(), i});
        std::sort(t.begin(), t.end());
        for (int k = 0; k < q && k < (int)t.size(); k++) out.push_back(t[k].second);
    }
    return out;
}

struct RunParams {
    double qloF = 0.05, qhiF = 0.15;
    double T0mul = 0.002, cooling = 0.9997;
    double hcFrac = 0.75;        // fraction of budget after which hill-climb
    double bigQProb = 0.04;      // probability of a large removal
    uint32_t opMask = 0x7FF;     // destroyer enable bits (12 ops; bit 11 = era defrag)
};


// DIAGNOSTIC ONLY: effective destroy-kind mixture, split into first/second
// half of the run. [phase*16 + kind]; reset at every run() start.
inline long* kindHistFlat() { static thread_local long h[32]; return h; }
inline RunStats run(repair::Ctx& c, double seconds, const RunParams& rp,
                    uint64_t seedv, int auditEvery = 0) {
    const double qloF = rp.qloF, qhiF = rp.qhiF;
    const double T0mul = rp.T0mul, cooling = rp.cooling;
    const Problem& pb = *c.pb; Sol& s = *c.s;
    c.rng.seed(seedv);
    Destroyers ds(12); ds.rng = &c.rng;
    for (int z = 0; z < 32; z++) kindHistFlat()[z] = 0;
    auto t0 = std::chrono::steady_clock::now();
    auto now = [&]{ return std::chrono::duration<double>(
        std::chrono::steady_clock::now() - t0).count(); };

    auto obj = [&]{
        core::Objective ob = core::evaluate(pb, s);
        // v58: regime flag -- once tardiness is gone, the objective is
        // preference-dominated, so insertion should chase preferred bays.
        c.prefStrong = (ob.z1 <= 0.0 && ob.z3 < (double)pb.n);
        return ob.total;
    };
    double cur = obj(), best = cur;
    Sol bestSol = s;
    int qlo = std::max(2, (int)(qloF * pb.n));
    int qhi = std::max(qlo + 1, (int)(qhiF * pb.n));
    double T0 = std::max(1.0, cur * T0mul), T = T0;
    RunStats st; st.best = best;
    std::uniform_real_distribution<double> U(0, 1);
    int sinceUpdate = 0;
    long sinceBest = 0;
    std::vector<long> lastTouch(pb.n, 0);

    while (now() < seconds) {
        st.iters++;
        bool hc = now() > rp.hcFrac * seconds;        // late phase: hill climbing
        int kind = ds.pick();
        for (int tries = 0; tries < 8 && !((rp.opMask >> kind) & 1); tries++)
            kind = ds.pick();
        if (!((rp.opMask >> kind) & 1)) kind = 0;
        kindHistFlat()[(now() > 0.5 * seconds ? 16 : 0) + kind]++;
        int q;
        if (U(c.rng) < rp.bigQProb) {                 // rare large jolt
            q = qlo + (int)(c.rng() % (uint32_t)(qhi - qlo + 1));
        } else {                                      // small fast neighbourhood
            int lo = 2, hi = std::max(8, (int)(0.03 * pb.n));
            q = lo + (int)(c.rng() % (uint32_t)(hi - lo + 1));
        }
        auto rem = pickRemoval(c, kind, q, lastTouch);
        if (rem.empty()) continue;
        std::vector<Backup> bk;
        for (int A : rem) {
            bk.push_back({A, s.bay[A], s.ori[A], s.x[A], s.y[A], s.e[A], s.f[A]});
            repair::removeBlock(c, A);
        }
        if (auditEvery > 0) {
            int bb = -1; int code = audit(pb, s, &bb);
            if (code) { st.badIter = st.iters; st.badKind = kind;
                st.badPhase = 10000 + code * 1000 + bb; break; }
        }
        std::sort(rem.begin(), rem.end(),
                  [&](int a, int b){ return pb.D[a] < pb.D[b]; });
        if (kind == 11) {                              // defrag: big rocks first
            std::sort(rem.begin(), rem.end(), [&](int a, int b){
                const auto& ga = pb.geo[a].orients[0].bb;
                const auto& gb = pb.geo[b].orients[0].bb;
                double aa = (ga.x1 - ga.x0) * (ga.y1 - ga.y0);
                double ab = (gb.x1 - gb.x0) * (gb.y1 - gb.y0);
                if (aa != ab) return aa > ab;
                return pb.D[a] < pb.D[b];
            });
        }
        bool ok = true;
        for (int A : rem) if (!repair::insertGreedy(c, A, -1)) { ok = false; break; }
        if (auditEvery > 0 && ok) {
            int bb = -1; int code = audit(pb, s, &bb);
            if (code) { st.badIter = st.iters; st.badKind = kind;
                st.badPhase = 20000 + code * 1000 + bb; break; }
        }
        if (!ok) {
            st.failed++;
            for (int A : rem) if (s.placed[A]) repair::removeBlock(c, A);
            for (auto& b : bk) {
                s.bay[b.A]=b.bay; s.ori[b.A]=b.ori; s.x[b.A]=b.x; s.y[b.A]=b.y;
                s.e[b.A]=b.e; s.f[b.A]=b.f; s.placed[b.A]=1;
                s.addIdx(b.A);
                c.oc->stamp(pb, s, b.A, true);
            }
            ds.used[kind]++;
            continue;
        }
        double nv = obj();
        double dlt = nv - cur;
        bool acc = dlt < -1e-9 ||
                   (!hc && U(c.rng) < std::exp(-dlt / std::max(1e-9, T)));
        ds.used[kind]++;
        if (acc) {
            cur = nv; st.accepted++;
            for (int A : rem) lastTouch[A] = st.iters;
            if (nv < best - 1e-9) {
                best = nv; bestSol = s; st.improved++; sinceBest = 0;
                ds.score[kind] += 3.0;
            } else { ds.score[kind] += (dlt < -1e-9 ? 1.0 : 0.3); sinceBest++; }
        } else {
            sinceBest++;
            for (int A : rem) if (s.placed[A]) repair::removeBlock(c, A);
            for (auto& b : bk) {
                s.bay[b.A]=b.bay; s.ori[b.A]=b.ori; s.x[b.A]=b.x; s.y[b.A]=b.y;
                s.e[b.A]=b.e; s.f[b.A]=b.f; s.placed[b.A]=1;
                s.addIdx(b.A);
                c.oc->stamp(pb, s, b.A, true);
            }
        }
        if (!hc) {
            T *= cooling;
            if (sinceBest > 1200) { T = T0 * 0.3; sinceBest = 0; }   // reheat
        }
        if (++sinceUpdate >= 60) { ds.update(); sinceUpdate = 0; }
        if (auditEvery > 0 && (st.iters % auditEvery) == 0) {
            int bb = -1; int code = audit(pb, s, &bb);
            if (code) { st.badIter = st.iters; st.badKind = kind;
                st.badPhase = code * 1000 + bb; break; }
        }
    }
    s = bestSol;
    st.best = best;
    return st;
}

// left time-compaction on the current solution: for each placed block
// (ascending e), try earlier entries at the SAME (bay,x,y,ori); take the
// earliest vp-feasible. Strictly non-regressing on Z1; Z2 unchanged
// (assignment fixed); Z3 unchanged.
// compactLeft only re-times blocks at a FIXED (x,y,ori): in packed early
// eras the same spot is taken, so pure slides stall (measured: early-era
// mean density stays 0.42 while tardiness piles up late). This variant
// RE-PLACES: remove + insertGreedy capped at the current e, accepting any
// strictly-earlier placement (position/orientation free, same bay -> Z2/Z3
// invariant, Z1 monotone non-increasing).
inline int compactLeftReplace(repair::Ctx& c, int maxPasses) {
    const Problem& pb = *c.pb; Sol& s = *c.s;
    int total = 0;
    std::vector<int> asg(pb.n, -1);
    for (int pass = 0; pass < maxPasses; pass++) {
        int moved = 0;
        std::vector<int> ids;
        for (int i = 0; i < pb.n; i++) if (s.placed[i]) ids.push_back(i);
        std::sort(ids.begin(), ids.end(),
                  [&](int a, int b){ return s.e[a] < s.e[b]; });
        for (int i : ids) {
            if (s.e[i] <= pb.R[i]) continue;
            int oldBay=s.bay[i], oldOri=s.ori[i], oldX=s.x[i], oldY=s.y[i],
                oldE=s.e[i], oldF=s.f[i];
            repair::removeBlock(c, i);
            asg[i] = oldBay;
            c.forcedBay = asg.data();
            bool ok = repair::insertGreedy(c, i, oldE);
            c.forcedBay = nullptr;
            asg[i] = -1;
            if (!ok || s.e[i] >= oldE) {
                if (ok) repair::removeBlock(c, i);
                s.bay[i]=oldBay; s.ori[i]=oldOri; s.x[i]=oldX; s.y[i]=oldY;
                s.e[i]=oldE; s.f[i]=oldF; s.placed[i]=1;
                s.addIdx(i);
                c.oc->stamp(pb, s, i, true);
            } else {
                moved++;
            }
        }
        total += moved;
        if (!moved) break;
    }
    return total;
}

inline int compactLeft(repair::Ctx& c, int maxPasses) {
    const Problem& pb = *c.pb; Sol& s = *c.s;
    int totalMoved = 0;
    for (int pass = 0; pass < maxPasses; pass++) {
        int moved = 0;
        std::vector<int> ids;
        for (int i = 0; i < pb.n; i++) if (s.placed[i]) ids.push_back(i);
        std::sort(ids.begin(), ids.end(),
                  [&](int a, int b){ return s.e[a] < s.e[b]; });
        for (int i : ids) {
            if (s.e[i] <= pb.R[i]) continue;
            int oldE = s.e[i], oldF = s.f[i];
            std::vector<int> cand;
            cand.push_back(pb.R[i]);
            for (int k : s.byBay[s.bay[i]]) {
                if (k == i || !s.placed[k]) continue;
                if (s.f[k] >= pb.R[i] && s.f[k] < oldE) cand.push_back(s.f[k]);
            }
            std::sort(cand.begin(), cand.end());
            cand.erase(std::unique(cand.begin(), cand.end()), cand.end());
            for (int e2 : cand) {
                if (e2 >= oldE) break;
                c.oc->stamp(pb, s, i, false);
                s.e[i] = e2; s.f[i] = e2 + pb.P[i];
                if (core::vpConservative(pb, s, i)) {
                    c.oc->stamp(pb, s, i, true);
                    moved++; break;
                }
                s.e[i] = oldE; s.f[i] = oldF;
                c.oc->stamp(pb, s, i, true);
            }
        }
        totalMoved += moved;
        if (!moved) break;
    }
    return totalMoved;
}

// due-order (noisy) construction from an empty canvas. Returns false if any
// block cannot be placed; caller falls back to its previous solution.
inline bool constructInitial(repair::Ctx& c, uint64_t seedv) {
    const Problem& pb = *c.pb; Sol& s = *c.s;
    c.rng.seed(seedv);
    for (int i = 0; i < pb.n; i++) s.placed[i] = 0;
    s.rebuildIdx(pb.m);
    c.oc->clearBits();
    std::vector<int> order(pb.n);
    for (int i = 0; i < pb.n; i++) order[i] = i;
    std::normal_distribution<double> N(0.0, 3.0);
    std::vector<double> key(pb.n);
    for (int i = 0; i < pb.n; i++) key[i] = pb.D[i] + N(c.rng);
    std::sort(order.begin(), order.end(),
              [&](int a, int b){ return key[a] < key[b]; });
    for (int A : order)
        if (!repair::insertGreedy(c, A)) return false;
    return true;
}

} // namespace alns
