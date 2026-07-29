// alns.hpp -- adaptive LNS: 8 destroy operators, adaptive weights, SA
// acceptance with reheating and a late hill-climb phase, plus the two
// deterministic helpers (left compaction, noisy-EDD construction).
//
// One iteration: pick a destroyer by adaptive weight -> remove q blocks ->
// reinsert them in due order with insertGreedy -> accept by SA on the exact
// objective; rejected or failed iterations roll back from the backup.
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

// Full-state audit -- the development assertion tool. Run it (auditEvery) when
// a new operator misbehaves and it tells you WHERE the state broke:
// 0 ok; 1 byBay out of bounds; 2 stale member; 3 dup/missing; 4 vp violation.
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

// The 10 destroy operators. opMask selects a subset (portfolio: even threads
// run 0x1F = ops 0-4, odd threads 0x3FF = ops 0-9).
//   0 random          1 worst-tardy (noisy)     2 related (bay+time+space)
//   3 crane-bbox nbrs 4 crane-exact blocker BFS 5 bay x time-slice
//   6 semi-worst      7 least-recently-touched  8 preference ejection chain
//   9 tardy min-cut ejection
// Op 8 is a DIRECTED assignment move the plain removals cannot compose: pick
// a block whose preferred bay would pay (priced in w3), evict from that bay
// whoever values it least while the running eviction cost stays under the
// mover's gain, then reinsert the mover FORCED into the bay at an on-time
// entry. Measured -5.8~-7.6% on its habitat (KERNEL_SPEC secs 187-188); the
// old rejection came from wasted iterations on non-habitat boards, which the
// mask-level dominance gate in run() now prevents.
inline std::vector<int> pickRemoval(repair::Ctx& c, int kind, int q,
                                    const std::vector<long>& lastTouch) {
    const Problem& pb = *c.pb; Sol& s = *c.s;
    c.tsBlk = -1; c.tsBay = -1; c.tsECap = -1;      // op 8 handoff, see Ctx
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
    } else if (kind == 8) {                           // preference ejection chain
        // A single forced transfer cannot claim a preference upgrade: the
        // preferred bay is full in the mover's window (on-time transfers fail
        // outright, and a free entry costs 5-25x the gain in w1 -- sec 187).
        // So clear the space first. Evicting the temporally nearest residents
        // displaces as many winners as it creates in binary-preference
        // structures; evict whoever values the bay LEAST instead, and only
        // while the running eviction cost stays under the mover's gain.
        // Every quantity is priced in objective units -- no tuned constants.
        std::vector<std::pair<double,int>> t; std::vector<int> tgt;
        for (int i : placedIds) {
            int pj = s.bay[i];
            for (int j = 0; j < pb.m; j++)
                if (pb.pref[i][j] > pb.pref[i][pj]) pj = j;
            double g = pb.w3 * (pb.pref[i][pj] - pb.pref[i][s.bay[i]]);
            if (g > 0) { t.push_back({-g * (0.75 + 0.5 * U01()), i}); tgt.push_back(pj); }
        }
        if (t.empty()) return {};
        std::vector<size_t> idx(t.size());
        for (size_t k = 0; k < idx.size(); k++) idx[k] = k;
        std::sort(idx.begin(), idx.end(),
                  [&](size_t x, size_t y){ return t[x].first < t[y].first; });
        size_t pk = idx[c.rng() % std::min<size_t>(4, idx.size())];
        int b = t[pk].second, Y = tgt[pk];
        double gain = -t[pk].first;
        int eb = std::max(pb.R[b], pb.D[b] - pb.P[b]);
        int fb2 = eb + pb.P[b];
        out.push_back(b);
        std::vector<std::pair<double,int>> ev;
        for (int i : placedIds) {
            if (i == b || s.bay[i] != Y) continue;
            if (!(s.e[i] < fb2 && eb < s.f[i])) continue;
            double bo = -1e18;
            for (int j = 0; j < pb.m; j++)
                if (j != Y) bo = std::max(bo, (double)pb.pref[i][j]);
            ev.push_back({pb.w3 * (pb.pref[i][Y] - bo) * (0.75 + 0.5 * U01()), i});
        }
        if (ev.empty()) return {};
        std::sort(ev.begin(), ev.end());
        double spent = 0; int take = 0;
        int cap = std::min((int)ev.size(), std::max(q, 4));
        while (take < cap && spent + ev[take].first < gain) {
            spent += std::max(0.0, ev[take].first);
            out.push_back(ev[take].second); take++;
        }
        if (take == 0) return {};
        c.tsBlk = b; c.tsBay = Y; c.tsECap = eb;
    } else if (kind == 9) {                           // tardy min-cut ejection
        // The Z1 mirror of op 8: LNS theory says destroy proportional to the
        // relaxation value of the binding constraint. For a tardy block the
        // binding constraint is the occupancy of its ON-TIME window -- so
        // find, over all candidate positions in its bay, the hole whose
        // conflict set is CHEAPEST to evict (count, slack-weighted), remove
        // exactly that set, and reinsert the block forced at its on-time
        // entry. Ops 1/3/4 are noisy approximations of this certificate.
        std::vector<std::pair<double,int>> t;
        for (int i : placedIds) {
            int es = std::max(pb.R[i], pb.D[i] - pb.P[i]);
            double g = pb.w1 * std::max(0, s.f[i] - (es + pb.P[i]));
            if (g > 0) t.push_back({-g * (0.75 + 0.5 * U01()), i});
        }
        if (t.empty()) return {};
        std::sort(t.begin(), t.end());
        int b = t[c.rng() % std::min<size_t>(4, t.size())].second;
        int j = s.bay[b];                              // same bay: pure Z1 move
        int es = std::max(pb.R[b], pb.D[b] - pb.P[b]);
        int fs = es + pb.P[b];
        // candidate positions: corner set of the on-time window, per orient
        double bestCost = 1e18; std::vector<int> bestSet;
        thread_local std::vector<int> cxs, cys;
        for (size_t oo = 0; oo < pb.geo[b].orients.size(); oo++) {
            const auto& g = pb.geo[b].orients[(int)oo];
            repair::cornerSets(c, b, (int)oo, j, es, fs, cxs, cys);
            for (int y : cys) for (int x : cxs) {
                double cost = 0; std::vector<int> setv;
                for (int k : s.byBay[j]) {
                    if (k == b || !s.placed[k]) continue;
                    if (!(s.e[k] < fs && es < s.f[k])) continue;
                    const auto& gk = pb.geo[k].orients[s.ori[k]];
                    if (!crane::bboxOverlapOff(g.bb, x, y,
                                               gk.bb, s.x[k], s.y[k])) continue;
                    // eviction price: 1 + how tardy re-inserting k may get
                    // (slack-poor residents are expensive to displace)
                    int slack = pb.D[k] - s.f[k];
                    cost += 1.0 + (slack < 0 ? 2.0 : (slack < 3 ? 1.0 : 0.0));
                    setv.push_back(k);
                    if (cost >= bestCost) break;
                }
                if (cost < bestCost && (int)setv.size() <= std::max(q, 4)) {
                    bestCost = cost; bestSet = std::move(setv);
                }
            }
        }
        if (bestCost >= 1e17) return {};
        out.push_back(b);
        for (int k : bestSet) out.push_back(k);
        c.tsBlk = b; c.tsBay = j; c.tsECap = es;
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
    uint32_t opMask = 0x3FF;     // destroyer enable bits (10 ops)
};

inline RunStats run(repair::Ctx& c, double seconds, const RunParams& rp,
                    uint64_t seedv, int auditEvery = 0) {
    const Problem& pb = *c.pb; Sol& s = *c.s;
    c.rng.seed(seedv);
    Destroyers ds(10); ds.rng = &c.rng;
    auto t0 = std::chrono::steady_clock::now();
    auto now = [&]{ return std::chrono::duration<double>(
        std::chrono::steady_clock::now() - t0).count(); };

    auto obj = [&]{
        core::Objective ob = core::evaluate(pb, s);
        // regime flag (sec 148): once tardiness is gone the objective is
        // preference-dominated, so insertion should chase preferred bays.
        c.prefStrong = (ob.z1 <= 0.0 && ob.z3 < (double)pb.n);
        // op 8's live habitat gate: parameter-free comparison of the two
        // priced terms (measured separation: p22 fires, p28/p38 silent).
        c.z3dom = (pb.w3 * ob.z3 > pb.w1 * ob.z1);
        return ob.total;
    };
    double cur = obj(), best = cur;
    Sol bestSol = s;
    int qlo = std::max(2, (int)(rp.qloF * pb.n));
    int qhi = std::max(qlo + 1, (int)(rp.qhiF * pb.n));
    double T0 = std::max(1.0, cur * rp.T0mul), T = T0;
    RunStats st; st.best = best;
    std::uniform_real_distribution<double> U(0, 1);
    int sinceUpdate = 0;
    long sinceBest = 0;
    std::vector<long> lastTouch(pb.n, 0);

    while (now() < seconds) {
        st.iters++;
        bool hc = now() > rp.hcFrac * seconds;        // late phase: hill climbing
        // Mask-level habitat gate for op 8: when the preference term is not
        // dominant the op is simply not selectable. The old design let a
        // gated op return an empty removal, wasting the iteration while its
        // adaptive weight stayed at 1.0 -- ~10% of iterations burned on
        // exactly the boards that could least afford it (the real cause of
        // the sec 193-195 Set B losses).
        uint32_t em = rp.opMask;
        if (!c.z3dom) em &= ~(1u << 8);   // op 8 lives where w3*Z3 dominates
        if (c.z3dom)  em &= ~(1u << 9);   // op 9 lives on the complement
        int kind = ds.pick();
        for (int tries = 0; tries < 8 && !((em >> kind) & 1); tries++)
            kind = ds.pick();
        if (!((em >> kind) & 1)) kind = 0;
        int q;
        if (U(c.rng) < rp.bigQProb) {                 // rare large jolt
            q = qlo + (int)(c.rng() % (uint32_t)(qhi - qlo + 1));
        } else {                                      // small fast neighbourhood
            int lo = 2, hi = std::max(8, (int)(0.03 * pb.n));
            q = lo + (int)(c.rng() % (uint32_t)(hi - lo + 1));
        }
        auto rem = pickRemoval(c, kind, q, lastTouch);
        if (rem.empty()) { ds.used[kind]++; continue; }   // decay, don't re-pick forever
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
        bool ok = true;
        if ((kind == 8 || kind == 9) && c.tsBlk >= 0) {
            // the upgrader claims the freed space first: forced into its
            // preferred bay at an on-time entry, then the evicted go free.
            thread_local std::vector<int> fbv;
            fbv.assign(pb.n, -1);
            fbv[c.tsBlk] = c.tsBay;
            const int* save = c.forcedBay;
            c.forcedBay = fbv.data();
            ok = repair::insertGreedy(c, c.tsBlk, c.tsECap);
            c.forcedBay = save;
            if (ok)
                for (int A : rem) {
                    if (A == c.tsBlk) continue;
                    if (!repair::insertGreedy(c, A, -1)) { ok = false; break; }
                }
        } else {
            for (int A : rem)
                if (!repair::insertGreedy(c, A, -1)) { ok = false; break; }
        }
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
            T *= rp.cooling;
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

// Left time-compaction: for each placed block (ascending e), try earlier
// entries at the SAME (bay,x,y,ori); take the earliest vp-feasible one.
// Strictly non-regressing on Z1; Z2/Z3 unchanged (assignment fixed).
// Preference compaction -- the Z3 sibling of compactLeft. In descending
// regret order, try each block in every HIGHER-preference bay at the same
// (or earlier) entry: Z1 cannot worsen by construction, Z3 strictly gains,
// Z2 shifts, so a move is kept only when the EXACT objective improves.
// Passes repeat until none moves. Motive: P3 anneals to a near-fixed-point
// (95 new-bests in 212k iters) while a fifth of its objective is Z3 the
// stochastic op8 grinds down glacially; a deterministic sweep finishes the
// job at ~one insertGreedy per attempt.
inline int compactPref(repair::Ctx& c, int maxPasses) {
    const Problem& pb = *c.pb; Sol& s = *c.s;
    int total = 0;
    for (int pass = 0; pass < maxPasses; pass++) {
        int moved = 0;
        std::vector<std::pair<double,int>> worst;
        for (int i = 0; i < pb.n; i++) {
            if (!s.placed[i]) continue;
            double r = pb.Smax[i] - pb.pref[i][s.bay[i]];
            if (r > 1e-9) worst.push_back({-r, i});
        }
        std::sort(worst.begin(), worst.end());
        for (auto& pr : worst) {
            int i = pr.second;
            double ob0 = core::evaluate(pb, s).total;
            Backup bk{i, s.bay[i], s.ori[i], s.x[i], s.y[i], s.e[i], s.f[i]};
            std::vector<std::pair<double,int>> better;
            for (int j = 0; j < pb.m; j++)
                if (pb.pref[i][j] > pb.pref[i][bk.bay] + 1e-9)
                    better.push_back({-pb.pref[i][j], j});
            std::sort(better.begin(), better.end());
            for (auto& bj : better) {
                repair::removeBlock(c, i);
                thread_local std::vector<int> fbv;
                fbv.assign(pb.n, -1);
                fbv[i] = bj.second;
                const int* save = c.forcedBay;
                c.forcedBay = fbv.data();
                bool ok = repair::insertGreedy(c, i, bk.e);
                c.forcedBay = save;
                if (ok && core::evaluate(pb, s).total < ob0 - 1e-9) {
                    moved++; break;
                }
                if (s.placed[i]) repair::removeBlock(c, i);
                s.bay[i]=bk.bay; s.ori[i]=bk.ori; s.x[i]=bk.x; s.y[i]=bk.y;
                s.e[i]=bk.e; s.f[i]=bk.f; s.placed[i]=1;
                s.addIdx(i);
                c.oc->stamp(pb, s, i, true);
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

// Noisy due-order construction from an empty canvas. Returns false if any
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
