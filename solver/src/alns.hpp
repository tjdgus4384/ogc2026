// alns.hpp -- adaptive LNS: 10 destroy operators, adaptive weights, SA
// acceptance with reheating and a late hill-climb phase, plus the two
// deterministic helpers (left compaction, noisy-EDD construction).
//
// One iteration: pick a destroyer by adaptive weight -> remove q blocks ->
// reinsert them in due order with insertGreedy -> accept by SA on the exact
// objective; rejected or failed iterations roll back from the backup.
#pragma once
#include "repair.hpp"
#include "knobs.hpp"
#include <chrono>
#include <cmath>
#include <cstdio>

namespace alns {
using core::Problem; using core::Sol;

struct Backup { int A, bay, ori, x, y, e, f; };

struct RunStats { long iters=0, accepted=0, improved=0, failed=0; double best;
    // sampled phase timing (every 32nd iteration), nanoseconds
    long tRemove=0, tInsert=0, tEval=0, tSamples=0;
    long opUsed[10] = {0}, opImp[10] = {0};
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
    std::vector<long> totUsed, totImp;   // lifetime fires / new-best hits
                                         // (diagnostic only, [ops] dump)
    std::mt19937* rng;
    int K;
    explicit Destroyers(int k)
        : w(k,1.0), score(k,0.0), used(k,0), totUsed(k,0), totImp(k,0), K(k) {}
    int pick() {
        double sm = 0; for (double v : w) sm += v;
        double r = std::uniform_real_distribution<double>(0, sm)(*rng);
        for (int i = 0; i < K; i++) { r -= w[i]; if (r <= 0) return i; }
        return K - 1;
    }
    void update(double decay = 0.85) {
        for (int i = 0; i < K; i++) {
            totUsed[i] += used[i];
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
//   6 semi-worst      7 bottleneck window       8 preference ejection chain
//   9 tardy min-cut ejection
// Op 8 is a DIRECTED assignment move the plain removals cannot compose: pick
// a block whose preferred bay would pay (priced in w3), evict from that bay
// whoever values it least while the running eviction cost stays under the
// mover's gain, then reinsert the mover FORCED into the bay at an on-time
// entry. Measured -5.8~-7.6% on its habitat (KERNEL_SPEC secs 187-188); the
// old rejection came from wasted iterations on non-habitat boards, which the
// mask-level dominance gate in run() now prevents.
// A block's w1-priced tardiness: the seeding signal for the tardy-
// targeted destroy operators (1/3/4/6). (Pricing w3-regret in as well
// was the costMix screen -- measured wash, removed.)
inline double blockCost(const Problem& pb, const Sol& s, int i) {
    return pb.w1 * (double)std::max(0, s.f[i] - pb.D[i]);
}

inline std::vector<int> pickRemoval(repair::Ctx& c, int kind, int q) {
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
            double td = blockCost(pb, s, i);
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
        for (int i : placedIds)
            if (blockCost(pb, s, i) > 0) tardy.push_back(i);
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
        for (int i : placedIds)
            if (blockCost(pb, s, i) > 0) tardy.push_back(i);
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
            t.push_back({-blockCost(pb, s, i), i});
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
        int es = std::max(pb.R[b], pb.D[b] - pb.P[b]);
        int fs = es + pb.P[b];
        // ALL BAYS, not just the incumbent one. The old version searched
        // the block's own bay ("pure Z1 move"), but the census showed the
        // on-time window has no free position in ANY bay -- so the cheapest
        // eviction usually lives in a DIFFERENT bay, and restricting the
        // search made this operator produce almost nothing (measured
        // improvements: 3-4 per run vs 456-1071 for plain random removal).
        double bestCost = 1e18; std::vector<int> bestSet;
        int bestBay = s.bay[b];
        thread_local std::vector<int> cxs, cys;
        for (int j = 0; j < pb.m; j++) {
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
                    // bbox over-counts blockers on interlocked boards
                    // (the exact same-layer re-test measured no better:
                    // count+slack pricing already keeps sets small).
                    // eviction price: 1 + how tardy re-inserting k may get
                    // (slack-poor residents are expensive to displace).
                    // Tried pricing this in w1 units with an economic
                    // stop rule instead -- measured WORSE on 3 seeds
                    // (44.57M -> 45.00M): the count-based cost keeps
                    // eviction sets small, and small sets are what the
                    // greedy repair can actually re-seat.
                    int slack = pb.D[k] - s.f[k];
                    cost += 1.0 + (slack < 0 ? 2.0 : (slack < 3 ? 1.0 : 0.0));
                    setv.push_back(k);
                    if (cost >= bestCost) break;
                }
                if (cost < bestCost &&
                    (int)setv.size() <= std::max(q, 4)) {
                    bestCost = cost; bestSet = std::move(setv);
                    bestBay = j;
                }
            }
        }
        }
        if (bestCost >= 1e17) return {};
        out.push_back(b);
        for (int k : bestSet) out.push_back(k);
        c.tsBlk = b; c.tsBay = bestBay; c.tsECap = es;
    } else if (kind == 7) {
        // BOTTLENECK-DIRECTED DESTROY (the job-shop "critical block"
        // idea, transplanted): structural tardiness means some
        // (bay, day) windows are physically over-subscribed, and no
        // amount of local shuffling elsewhere can fix a block that
        // needs one of those windows. The engine already tracks the
        // area load per bay per day, so the contention peak inside a
        // waiting block's window is free to find. Remove the residents
        // holding that peak -- that is the only set whose departure
        // can actually open the window.
        // Only ACTUALLY TARDY blocks qualify: the operator exists to
        // break resource contention that costs objective. Keying on
        // "entered later than its punctual entry" also caught blocks
        // that finish on time anyway, so on a tardiness-free board it
        // tore up healthy structure (P3 +4.6%, 0/3).
        std::vector<int> late;
        for (int i : placedIds)
            if (s.f[i] > pb.D[i]) late.push_back(i);
        if (late.empty()) return {};
        int b = late[c.rng() % late.size()];
        int es = std::max(pb.R[b], pb.D[b] - pb.P[b]);
        int fs = es + pb.P[b];
        // peak-loaded (bay, day) inside the window it needs
        int bj = -1, bt = -1; double peak = -1.0;
        for (int j = 0; j < pb.m; j++) {
            double capj = pb.bayW[j] * pb.bayH[j];
            for (int t = es; t < fs && t < c.oc->T; t++) {
                double u = c.oc->areaUsed[j][t] / std::max(1e-9, capj);
                if (u > peak) { peak = u; bj = j; bt = t; }
            }
        }
        if (bj < 0) return {};
        std::vector<int> hold;
        for (int i : s.byBay[bj])
            if (s.placed[i] && i != b && s.e[i] <= bt && bt < s.f[i])
                hold.push_back(i);
        if (hold.empty()) return {};
        // evict the ones with the most room to move (cheapest to re-seat)
        std::sort(hold.begin(), hold.end(), [&](int x, int y){
            return (pb.D[x] - s.f[x]) > (pb.D[y] - s.f[y]); });
        if ((int)hold.size() > q) hold.resize(q);
        out.push_back(b);
        for (int k : hold) out.push_back(k);
        c.tsBlk = b; c.tsBay = bj; c.tsECap = es;
    }
    return out;
}

struct RunParams {
    double qloF = 0.05, qhiF = 0.15;
    double T0mul = 0.002, cooling = 0.9997;
    double hcFrac = 0.75;        // fraction of budget after which hill-climb
    double bigQProb = 0.04;      // probability of a large removal
    uint32_t opMask = 0x3FF;     // destroyer enable bits (10 ops)
    long maxIters = 0;           // 0 = wall-clock only; >0 makes runs exactly
                                 // reproducible (determinism tests, A/B)
};

inline int spatialCompact(repair::Ctx& c, int passes);

inline RunStats run(repair::Ctx& c, double seconds, const RunParams& rp,
                    uint64_t seedv, int auditEvery = 0) {

    const Problem& pb = *c.pb; Sol& s = *c.s;
    c.rng.seed(seedv);
    Destroyers ds(10); ds.rng = &c.rng;
    auto t0 = std::chrono::steady_clock::now();
    auto now = [&]{ return std::chrono::duration<double>(
        std::chrono::steady_clock::now() - t0).count(); };

    core::slackW() = 0.0;        // slack surrogate refuted (08-02 ablation)
    auto obj = [&]{
        core::Objective ob = core::evaluatePen(pb, s);
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
    // rare large jolt reach = nominal qhiF (widening refuted: the congested
    // 20-30 block neighbourhood is proven optimal, wider ruins wash)
    int qhi = std::max(qlo + 1, (int)(rp.qhiF * pb.n));
    qhi = std::min(qhi, std::max(qlo + 1, pb.n - 1));
    double T0 = std::max(1.0, cur * rp.T0mul), T = T0;
    // SCALE-INVARIANT SCHEDULE (adopted): cooling exp(-0.06/n), reheat
    // after 6n stalled iterations -- reduces exactly to the legacy
    // constants at n=200 and generalises to unseen sizes.
    double coolRate = std::exp(-0.06 / std::max(1, pb.n));
    long reheatAt = (long)6 * pb.n;
    RunStats st; st.best = best;
    std::uniform_real_distribution<double> U(0, 1);
    int sinceUpdate = 0;
    long sinceBest = 0;

    // mid-anneal lock release: with partial preference locks the deep
    // anneal can trap transiently-accepted tardiness (SA uphill moves
    // cannot unwind under assignment constraints -- the K-frontier cliff).
    // Releasing the lock at 0.6*budget keeps the early guidance and
    // frees the tail to repair. Fires only when a lock is active.
    while (now() < seconds &&
           (rp.maxIters <= 0 || st.iters < rp.maxIters)) {
        st.iters++;
        if (c.forcedBay &&
            (rp.maxIters > 0
                 ? st.iters >= (long)(0.6 * rp.maxIters)
                 : now() > 0.6 * seconds))
            c.forcedBay = nullptr;
        // late phase: hill climbing. Iteration-based when a budget is set
        // (machine-independent trajectory); wall-based only in legacy mode.
        bool hc = rp.maxIters > 0
                  ? st.iters > rp.hcFrac * rp.maxIters
                  : now() > rp.hcFrac * seconds;
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
            // Destroy size: the small neighbourhood was capped near 8
            // blocks (3% of n), which is far below the 10-40% that LNS
            // literature uses with a strong repair. Our repair got a
            // lot stronger this session (hardest-first insertion,
            // all-bay ejection), so the useful neighbourhood size very
            // likely moved with it. Expressed as a fraction of n so it
            // scales with unseen instances.
            int lo = 2;
            int hi = std::max(8, (int)(knobs::qFrac() * 0.01 * pb.n));
            q = lo + (int)(c.rng() % (uint32_t)(hi - lo + 1));
        }
        // SHIFT IN PLACE (env OGC_SHIFTP = per-mille): moving a block
        // today means remove + reinsert, which is expensive AND needs
        // free space. Sliding a resident to another (x,y) in its own
        // bay at its own entry keeps the schedule and costs one vp
        // check plus its bay neighbours -- the cheapest structural
        // move available, and it defragments around tardy blocks so
        // later insertions can land.
        // Fires at 10% of iterations (measured keep; the shift-on/off
        // persona split was refuted -- all threads run it).
        if ((int)(c.rng() % 1000) < 100) {
            thread_local std::vector<int> pl3;
            pl3.clear();
            for (int i2 = 0; i2 < pb.n; i2++)
                if (s.placed[i2]) pl3.push_back(i2);
            if (!pl3.empty()) {
                int A3 = pl3[c.rng() % pl3.size()];
                int j3 = s.bay[A3], o3 = s.ori[A3];
                const auto& bb3 = pb.geo[A3].orients[o3].bb;
                int xlo = (int)std::ceil(-bb3.x0);
                int xhi = (int)std::floor(pb.bayW[j3] - bb3.x1);
                int ylo = (int)std::ceil(-bb3.y0);
                int yhi = (int)std::floor(pb.bayH[j3] - bb3.y1);
                if (xhi >= xlo && yhi >= ylo) {
                    int oX = s.x[A3], oY = s.y[A3];
                    int nX, nY;
                        nX = xlo + (int)(c.rng() % (uint32_t)(xhi-xlo+1));
                        nY = ylo + (int)(c.rng() % (uint32_t)(yhi-ylo+1));
                    if (nX != oX || nY != oY) {
                        c.oc->stamp(pb, s, A3, false);
                        s.x[A3] = nX; s.y[A3] = nY;
                        bool ok4 = core::vpConservative(pb, s, A3);
                        if (ok4) {
                            c.oc->stamp(pb, s, A3, true);
                            for (int k3 : s.byBay[j3]) {
                                if (k3 == A3 || !s.placed[k3]) continue;
                                if (!core::vpConservative(pb, s, k3)) {
                                    ok4 = false; break; }
                            }
                            if (!ok4) c.oc->stamp(pb, s, A3, false);
                        }
                        double nv4 = ok4 ? obj() : 1e300;
                        if (ok4 && nv4 <= cur + 1e-9) {
                            cur = nv4;
                            if (nv4 < best - 1e-9) {
                                best = nv4; bestSol = s; st.improved++;
                                sinceBest = 0;
                            }
                        } else {
                            s.x[A3] = oX; s.y[A3] = oY;
                            c.oc->stamp(pb, s, A3, true);
                        }
                    }
                }
            }
            continue;
        }
        // SWAP MOVE (env OGC_SWAPP = per-mille probability): every
        // neighbourhood we have is remove-then-reinsert, which needs
        // free space -- but the boards are bbox-saturated (103%), so
        // that is exactly what is missing. Exchanging two blocks'
        // slots needs no free space at all and is the classic move for
        // saturated packings. Verified with the same vp oracle as any
        // placement, and priced by the same acceptance rule.
        if ((int)(c.rng() % 1000) < 20) {
            thread_local std::vector<int> pl;
            pl.clear();
            for (int i2 = 0; i2 < pb.n; i2++)
                if (s.placed[i2]) pl.push_back(i2);
            if (pl.size() >= 2) {
                thread_local std::vector<int> tard;
                tard.clear();
                for (int i2 : pl) if (s.f[i2] > pb.D[i2]) tard.push_back(i2);
                // the move exists to relieve tardiness pressure on a
                // saturated board; with nothing tardy it just churns a
                // healthy layout (measured: P3, the z1-free board,
                // +5.3% when swapping randomly)
                if (tard.empty()) goto no_swap;
                int A = tard[c.rng() % tard.size()];
                int B = pl[c.rng() % pl.size()];
                if (A != B) {
                    int aB=s.bay[A], aX=s.x[A], aY=s.y[A], aE=s.e[A];
                    int bB=s.bay[B], bX=s.x[B], bY=s.y[B], bE=s.e[B];
                    c.oc->stamp(pb, s, A, false);
                    c.oc->stamp(pb, s, B, false);
                    int nAe = std::max(pb.R[A], bE);
                    int nBe = std::max(pb.R[B], aE);
                    s.bay[A]=bB; s.x[A]=bX; s.y[A]=bY;
                    s.e[A]=nAe; s.f[A]=nAe+pb.P[A];
                    s.bay[B]=aB; s.x[B]=aX; s.y[B]=aY;
                    s.e[B]=nBe; s.f[B]=nBe+pb.P[B];
                    // full rebuild, not incremental: swapping to
                    // delIdx/addIdx measured WORSE (mean +1.8% on the
                    // same seeds) even though it is strictly less
                    // work. The rebuild also drops stale entries left
                    // by other paths, and the search evidently relies
                    // on that cleanup.
                    s.rebuildIdx(pb.m);
                    c.oc->stamp(pb, s, A, true);
                    c.oc->stamp(pb, s, B, true);
                    bool ok2 = core::vpConservative(pb, s, A)
                            && core::vpConservative(pb, s, B);
                    if (ok2)
                        for (int j2 : {aB, bB}) {
                            for (int k2 : s.byBay[j2]) {
                                if (k2 == A || k2 == B || !s.placed[k2])
                                    continue;
                                if (!core::vpConservative(pb, s, k2)) {
                                    ok2 = false; break; }
                            }
                            if (!ok2) break;
                        }
                    double nv2 = ok2 ? obj() : 1e300;
                    if (ok2 && nv2 <= cur + 1e-9) {
                        cur = nv2;
                        if (nv2 < best - 1e-9) {
                            best = nv2; bestSol = s; st.improved++;
                            sinceBest = 0;
                        }
                    } else {                       // revert
                        c.oc->stamp(pb, s, A, false);
                        c.oc->stamp(pb, s, B, false);
                        s.bay[A]=aB; s.x[A]=aX; s.y[A]=aY;
                        s.e[A]=aE; s.f[A]=aE+pb.P[A];
                        s.bay[B]=bB; s.x[B]=bX; s.y[B]=bY;
                        s.e[B]=bE; s.f[B]=bE+pb.P[B];
                        s.rebuildIdx(pb.m);
                        c.oc->stamp(pb, s, A, true);
                        c.oc->stamp(pb, s, B, true);
                    }
                }
            }
            continue;
        }
no_swap:
        const bool tmr = (st.iters & 31) == 0;
        auto tick = [] {
            struct timespec ts;
            clock_gettime(CLOCK_MONOTONIC, &ts);
            return (long)ts.tv_sec * 1000000000L + ts.tv_nsec;
        };
        long t0 = tmr ? tick() : 0;
        auto rem = pickRemoval(c, kind, q);
        if (rem.empty()) { ds.used[kind]++; continue; }   // decay, don't re-pick forever
        std::vector<Backup> bk;
        for (int A : rem) {
            bk.push_back({A, s.bay[A], s.ori[A], s.x[A], s.y[A], s.e[A], s.f[A]});
            repair::removeBlock(c, A);
        }
        long t1 = tmr ? tick() : 0;
        if (auditEvery > 0) {
            int bb = -1; int code = audit(pb, s, &bb);
            if (code) { st.badIter = st.iters; st.badKind = kind;
                st.badPhase = 10000 + code * 1000 + bb; break; }
        }
        // REPAIR ORDER (adopted): hardest-to-place first -- footprint x
        // stay length. Measured winner once repair got strong (all-bay
        // ejection); due-date, slack and layer-weighted orders lost or
        // washed on the official-TL boards.
        std::sort(rem.begin(), rem.end(), [&](int a, int b){
            const auto& ga = pb.geo[a].orients[s.ori[a]].bb;
            const auto& gb = pb.geo[b].orients[s.ori[b]].bb;
            double aa = (ga.x1-ga.x0)*(ga.y1-ga.y0)*pb.P[a];
            double ab = (gb.x1-gb.x0)*(gb.y1-gb.y0)*pb.P[b];
            return aa != ab ? aa > ab : pb.D[a] < pb.D[b]; });
        {
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
            for (int A : rem) {
                if (!repair::insertGreedy(c, A, -1)) { ok = false; break; }
            }
        }
        if (auditEvery > 0 && ok) {
            int bb = -1; int code = audit(pb, s, &bb);
            if (code) { st.badIter = st.iters; st.badKind = kind;
                st.badPhase = 20000 + code * 1000 + bb; break; }
        }
        long t2 = tmr ? tick() : 0;
        if (tmr) { st.tRemove += t1 - t0; st.tInsert += t2 - t1;
                   st.tSamples++; }
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
        }
        long t3 = tmr ? tick() : 0;
        double nv = obj();
        if (tmr) st.tEval += tick() - t3;
        double dlt = nv - cur;
        bool acc = dlt < -1e-9 ||
                   (!hc && U(c.rng) < std::exp(-dlt / std::max(1e-9, T)));
        ds.used[kind]++;
        if (acc) {
            cur = nv; st.accepted++;
            if (nv < best - 1e-9) {
                best = nv; bestSol = s; st.improved++; sinceBest = 0;
                ds.score[kind] += 3.0; ds.totImp[kind]++;
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
            T *= coolRate;
            if (sinceBest > reheatAt) { T = T0 * 0.3; sinceBest = 0; }
        }
        if (++sinceUpdate >= 60) { ds.update(); sinceUpdate = 0; }
        // RETURN TO BEST after a 20n-iteration stall: SA accepts worsening
        // moves, so the walk can strand in a worse basin; standard ALNS
        // intensification snaps back to the incumbent (n-scaled).
        if (sinceBest > 20L * pb.n) {
            s = bestSol;
            c.oc->clearBits();
            for (int i2 = 0; i2 < pb.n; i2++)
                if (s.placed[i2]) c.oc->stamp(pb, s, i2, true);
            s.rebuildIdx(pb.m);
            cur = obj();
            sinceBest = 0;
        }
        if (auditEvery > 0 && (st.iters % auditEvery) == 0) {
            int bb = -1; int code = audit(pb, s, &bb);
            if (code) { st.badIter = st.iters; st.badKind = kind;
                st.badPhase = code * 1000 + bb; break; }
        }
    }
    s = bestSol;
    for (int k = 0; k < 10; k++) {          // per-operator contribution
        st.opUsed[k] = ds.used[k];
        st.opImp[k] = ds.totImp[k];
    }
    st.best = best;
    if (getenv("SMAX_DEBUG")) {   // per-op economics: who fired, who earned
        fprintf(stderr, "[ops]");
        for (int i = 0; i < ds.K; i++)
            fprintf(stderr, " %d:%ld/%ld", i,
                    ds.totUsed[i] + ds.used[i], ds.totImp[i]);
        fprintf(stderr, " iters=%ld acc=%.2f fail=%ld\n",
                st.iters, st.accepted / (double)std::max(1L, st.iters),
                st.failed);

    }
    return st;
}

// Left time-compaction: for each placed block (ascending e), try earlier
// entries at the SAME (bay,x,y,ori); take the earliest vp-feasible one.
// Strictly non-regressing on Z1; Z2/Z3 unchanged (assignment fixed).
// SPATIAL COMPACTION (6 passes post-round, adopted): slide
// every resident to the leftmost legal x at its own row, keeping its
// schedule untouched. compactLeft only ever compacted TIME; nothing
// in the engine ever defragmented SPACE, and the measured pathology
// is exactly that -- 35-42% of bay area is free but scattered inside
// bounding boxes, so arriving blocks find no contiguous room (peak
// polygon utilisation 58-65% vs bbox utilisation 103-105%).
// Schedule-safe by construction: only (x) changes, never (e,f).
inline int spatialCompact(repair::Ctx& c, int passes) {
    const Problem& pb = *c.pb; Sol& s = *c.s;
    int moved = 0;
    for (int pass = 0; pass < passes; pass++) {
        int mv = 0;
        for (int i = 0; i < pb.n; i++) {
            if (!s.placed[i]) continue;
            int j = s.bay[i], oi = s.ori[i];
            const auto& bb = pb.geo[i].orients[oi].bb;
            int xlo = (int)std::ceil(-bb.x0);
            if (s.x[i] <= xlo) continue;
            // snapshot of the bay WITHOUT this block over its stay
            c.oc->stamp(pb, s, i, false);
            thread_local occ::OccIndex::Snap sn;
            c.oc->buildSnap(j, s.e[i], s.f[i], sn);
            int nx = c.oc->slideLeftX(sn, i, oi, s.y[i], xlo,
                                      s.x[i] - 1);
            int ny = s.y[i];
            if ((nx == INT_MIN || nx >= s.x[i]) && ny == s.y[i]) {
                c.oc->stamp(pb, s, i, true);
                continue;
            }
            int oldx = s.x[i], oldy = s.y[i];
            if (nx != INT_MIN && nx < s.x[i]) s.x[i] = nx;
            s.y[i] = ny;
            bool ok = core::vpConservative(pb, s, i);
            if (ok)                       // neighbours' crane paths
                for (int k : s.byBay[j]) {
                    if (k == i || !s.placed[k]) continue;
                    if (s.f[k] <= s.e[i] || s.e[k] >= s.f[i]) continue;
                    if (!core::vpConservative(pb, s, k)) { ok = false;
                                                           break; }
                }
            if (!ok) { s.x[i] = oldx; s.y[i] = oldy; }
            else mv++;
            c.oc->stamp(pb, s, i, true);
        }
        moved += mv;
        if (!mv) break;
    }
    return moved;
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
