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
//   6 semi-worst      7 least-recently-touched  8 preference ejection chain
//   9 tardy min-cut ejection
// Op 8 is a DIRECTED assignment move the plain removals cannot compose: pick
// a block whose preferred bay would pay (priced in w3), evict from that bay
// whoever values it least while the running eviction cost stays under the
// mover's gain, then reinsert the mover FORCED into the bay at an on-time
// entry. Measured -5.8~-7.6% on its habitat (KERNEL_SPEC secs 187-188); the
// old rejection came from wasted iterations on non-habitat boards, which the
// mask-level dominance gate in run() now prevents.
// A block's contribution to the objective: tardiness priced by w1
// plus preference regret priced by w3. Five of the ten destroy
// operators seeded from TARDINESS alone, so on a board that is
// already punctual (P3: z1 = 0, and the objective is pure regret)
// they lost their target and degenerated into random removal, while
// op 9 bailed out entirely. Targeting the priced contribution keeps
// every operator pointed at whatever actually costs on THIS instance,
// which is also what makes it safe on hidden ones.
inline double blockCost(const Problem& pb, const Sol& s, int i) {
    double t = (double)std::max(0, s.f[i] - pb.D[i]);
    double r = pb.Smax[i] - pb.pref[i][s.bay[i]];
    return pb.w1 * t + (knobs::costMix() ? pb.w3 * r : 0.0);
}

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
        size_t pk = idx[c.rng() % std::min<size_t>(knobs::op8Top(),
                                                   idx.size())];
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
        for (int j : (knobs::ejectAll()
                      ? [&]{ std::vector<int> v(pb.m);
                             for (int q2 = 0; q2 < pb.m; q2++) v[q2] = q2;
                             return v; }()
                      : std::vector<int>{s.bay[b]})) {
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
                    // The bbox test alone counts blockers that do not
                    // actually collide. These boards run 103-105% peak
                    // BBOX utilisation against 58-65% polygon: the
                    // blocks interlock deep inside each other's boxes,
                    // so bbox-overlap is a wild over-count. This is the
                    // one operator aimed straight at the tardiness root
                    // cause, and it was pricing eviction sets several
                    // times too large -- inflating cost past bestCost
                    // and abandoning positions that are in truth one
                    // displacement away (measured: for half of all
                    // tardy blocks the true minimum blocker set is a
                    // SINGLE resident). bbox stays as the prefilter it
                    // is sound for; the exact same-layer test decides.
                    if (knobs::exactCut()) {
                        const auto& gko = pb.geo[k].orients[s.ori[k]];
                        int LL = (int)std::min(g.layers.size(),
                                               gko.layers.size());
                        bool real = false;
                        for (int l2 = 0; l2 < LL && !real; l2++)
                            real = crane::overlapsLay(g, l2, x, y, gko,
                                                      l2, s.x[k], s.y[k]);
                        if (!real) continue;
                    }
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
    } else if (kind == 7 && knobs::bottleneck()) {
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
    int lahcLen = -2;            // -2: use the knob; 0: SA; -1: n-scaled
    int shiftPP = -1;            // -1: use the knob; else per-thread rate
    long maxIters = 0;           // 0 = wall-clock only; >0 makes runs exactly
                                 // reproducible (determinism tests, A/B)
};

inline int spatialCompact(repair::Ctx& c, int passes);

inline RunStats run(repair::Ctx& c, double seconds, const RunParams& rp,
                    uint64_t seedv, int auditEvery = 0) {

    const Problem& pb = *c.pb; Sol& s = *c.s;
    c.rng.seed(seedv);
    Destroyers ds(10); ds.rng = &c.rng;
    // Punctual rescue is a two-armed adaptive choice, not a fixed gate.
    // Measured across three independent builds the move is decisively
    // good on some boards (P4 -3.6/-2.8/-4.8%) and decisively bad on
    // others (P3, P6 +2-3%), so ANY constant firing rate is wrong for
    // half the instances -- and picking one per board would be fitting
    // the six we can see. The same roulette that already prices the
    // destroy operators prices this one: boards where it pays fire it,
    // boards where it does not starve it, with no new constant.
    Destroyers rsel(2); rsel.rng = &c.rng;
    // OVERLAP-TOLERANT ANNEALING (env OGC_OTA). Three separator probes
    // agreed: post-hoc separation of an all-punctual pile diverges; the
    // literature form is tolerance DURING search. v0 here: swap and
    // shift may land on vp-violating placements, accepted by SA on
    // (true-cost delta + MU * violation-count delta); MU ramps with
    // progress so early search tunnels through ridges both optimality
    // proofs say feasible search cannot cross, and late search is
    // effectively exact. Best is only ever recorded from verified
    // violation-free states, and stray violations are evicted and
    // greedily re-seated at the end (repair failure rate is 0%).
    const bool OTA = knobs::ota() != 0;
    long viol = 0;
    auto violOf = [&](int i2) -> long {
        return (s.placed[i2] && !core::vpConservative(pb, s, i2))
               ? (long)core::vpViolCount(pb, s, i2) : 0L;
    };
    // axis penetration depth of block M (sum over conflicts): smallest
    // axis translation that clears it, exponential probe capped at 16.
    // Boolean oracle + the qualifying memo keep each probe cheap.
    auto penOf = [&](int M) -> double {
        if (!s.placed[M] || core::vpConservative(pb, s, M)) return 0.0;
        int ox = s.x[M], oy = s.y[M];
        const auto& gb4 = pb.geo[M].orients[s.ori[M]].bb;
        int jj = s.bay[M];
        int xl = (int)std::ceil(-gb4.x0);
        int xh = (int)std::floor(pb.bayW[jj] - gb4.x1);
        int yl = (int)std::ceil(-gb4.y0);
        int yh = (int)std::floor(pb.bayH[jj] - gb4.y1);
        static const int dxs[4] = {1, -1, 0, 0};
        static const int dys[4] = {0, 0, 1, -1};
        double bestd = 16.0;
        for (int d2 = 0; d2 < 4; d2++) {
            for (int stp = 1; stp <= 16; stp <<= 1) {
                int nx4 = ox + dxs[d2] * stp, ny4 = oy + dys[d2] * stp;
                if (nx4 < xl || nx4 > xh || ny4 < yl || ny4 > yh) break;
                c.oc->stamp(pb, s, M, false);
                s.x[M] = nx4; s.y[M] = ny4;
                c.oc->stamp(pb, s, M, true);
                bool okc = core::vpConservative(pb, s, M);
                c.oc->stamp(pb, s, M, false);
                s.x[M] = ox; s.y[M] = oy;
                c.oc->stamp(pb, s, M, true);
                if (okc) { if (stp < bestd) bestd = stp; break; }
            }
        }
        return bestd;
    };
    double apen = 0.0;              // ambient penetration (OTA3)
    auto t0 = std::chrono::steady_clock::now();
    auto now = [&]{ return std::chrono::duration<double>(
        std::chrono::steady_clock::now() - t0).count(); };

    // GLS state (env OGC_GLS = penalty step, 0 = off): per-block
    // tardiness penalties that grow on blocks which stay tardy while
    // the search stalls, pushing the anneal to buy them space-time.
    // The incumbent is always kept by the TRUE objective.
    // SELF-SCALED slack surrogate: the tuned constant is replaced by
    // the problem's own scale. One unit of the slack term equals one
    // block sitting exactly at its due date; dividing by n makes the
    // whole term worth at most ONE tardy day, i.e. it can break ties
    // between equal-tardiness boards but can never outvote a real
    // improvement. No instance-specific number survives.
    core::slackW() = knobs::slackOn() ? (1.0 / (double)pb.n) : 0.0;
    const double GLSA = knobs::gls();
    std::vector<double> pen;
    if (GLSA > 0.0) {
        pen.assign(pb.n, 0.0);
        core::glsPen() = pen.data();
    }
    auto objTrue = [&]{ return core::evaluate(pb, s).total; };
    const bool useTrue = (GLSA > 0.0) || (core::slackW() > 0.0);
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
    // LATE ACCEPTANCE HILL CLIMBING (env OGC_LAHC = history length,
    // 0 = classic SA): accept a move if it beats the objective seen
    // LAHC iterations ago. One parameter, no temperature schedule,
    // and it keeps a long memory of quality instead of a decaying
    // threshold -- the standard alternative to SA on scheduling
    // problems and the one acceptance rule this engine never tried.
    // SELF-SCALED LAHC length: the literature sets L as a fraction of
    // the walk, not an absolute count. We know the iteration budget
    // only implicitly, so derive L from the instance size -- one
    // history slot per block gives the same L/iterations ratio on
    // every instance, which is what the measurements actually
    // supported (P3 n=200 wanted short, P6 n=250 with far more
    // iterations per block wanted long).
    int LAH = (rp.lahcLen != -2) ? rp.lahcLen : knobs::lahc();
    if (LAH == -1) LAH = std::max(32, pb.n * knobs::lahcMul());
    std::vector<double> hist;
    if (LAH > 0) hist.assign((size_t)LAH, cur);
    int qlo = std::max(2, (int)(rp.qloF * pb.n));
    // BIG-JOLT REACH (env OGC_QHIMUL, percent of the nominal qhi).
    // CP-SAT proved the incumbent boards exactly optimal over the most
    // congested 20-30 block neighbourhood (all bays, orientations,
    // entry days, corner AND arbitrary-grid positions). The big jolt
    // tops out at qhiF*n = 22 blocks on P4, so every ruin-and-recreate
    // this search performs lands inside a region measured to contain no
    // improvement -- it cannot escape by construction. Only the RARE
    // jolt is widened; the small neighbourhood (94-96% of iterations,
    // q in [2,8]) is untouched, which is what the old "uniform q x2/x3"
    // refutation actually broke.
    int qhi = std::max(qlo + 1,
                       (int)(rp.qhiF * pb.n * knobs::qhiMul() * 0.01));
    qhi = std::min(qhi, std::max(qlo + 1, pb.n - 1));
    double T0 = std::max(1.0, cur * rp.T0mul), T = T0;
    // SCALE-INVARIANT SCHEDULE (env OGC_SCALESCHED): the cooling rate
    // (0.9997/iteration) and the reheat trigger (1200 stalled
    // iterations) were absolute, so a 100-block instance and a
    // 300-block one got schedules that differ by 3x in "sweeps per
    // cooling period" -- the same code becomes a different algorithm
    // as the instance grows. Re-expressed per sweep: reheat after 6n
    // stalled iterations, cooling exp(-0.06/n) so one reheat period
    // always spans the same temperature decay. Both reduce EXACTLY to
    // today's numbers at n=200, and now generalise to unseen sizes.
    double coolRate = rp.cooling;
    long reheatAt = 1200;
    if (knobs::scaleSched()) {
        coolRate = std::exp(-0.06 / std::max(1, pb.n));
        reheatAt = (long)6 * pb.n;
    }
    RunStats st; st.best = best;
    std::uniform_real_distribution<double> U(0, 1);
    int sinceUpdate = 0;
    long sinceBest = 0;
    std::vector<long> lastTouch(pb.n, 0);
    std::vector<long> tabuUntil;
    if (knobs::tabu() > 0) tabuUntil.assign(pb.n, 0);
    // SQUEAKY WHEEL (env OGC_SQUEAK): blocks that keep ending up tardy
    // are the ones the construction order is failing. Classic squeaky-
    // wheel optimisation raises their priority so the NEXT rebuild
    // seats them before the easy blocks compete for the same space.
    // Cost is a multiplier in the existing sort key -- no extra work
    // per iteration, which is what every failed idea today violated.
    std::vector<double> squeak;
    if (knobs::squeak() > 0) squeak.assign(pb.n, 1.0);

    // mid-anneal lock release: with partial preference locks the deep
    // anneal can trap transiently-accepted tardiness (SA uphill moves
    // cannot unwind under assignment constraints -- the K-frontier cliff).
    // Releasing the lock at unlockF*budget keeps the early guidance and
    // frees the tail to repair. Inert unless OGC_UNLOCKF set AND a lock
    // is active.
    while (now() < seconds &&
           (rp.maxIters <= 0 || st.iters < rp.maxIters)) {
        st.iters++;
        if (c.forcedBay && knobs::unlockF() > 0 &&
            (rp.maxIters > 0
                 ? st.iters >= (long)(knobs::unlockF() * rp.maxIters)
                 : now() > knobs::unlockF() * seconds))
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
        // PATH RELINKING (env OGC_RELINK = per-mille): walk the
        // current solution toward the incumbent one block at a time,
        // scoring after each step and keeping the best point on the
        // path. The premise of the method is that good solutions have
        // good solutions BETWEEN them -- a region plain perturbation
        // never visits, because it would have to pass through worse
        // boards to get there. Bounded to a sampled set of differing
        // blocks so the step stays cheap.
        if (knobs::relink() > 0 && !bestSol.placed.empty() &&
            (int)(c.rng() % 1000) < knobs::relink()) {
            thread_local std::vector<int> diff;
            diff.clear();
            for (int i2 = 0; i2 < pb.n; i2++)
                if (s.placed[i2] && bestSol.placed[i2] &&
                    (s.bay[i2] != bestSol.bay[i2] ||
                     s.x[i2] != bestSol.x[i2] ||
                     s.y[i2] != bestSol.y[i2] ||
                     s.e[i2] != bestSol.e[i2]))
                    diff.push_back(i2);
            if (!diff.empty()) {
                std::shuffle(diff.begin(), diff.end(), c.rng);
                int steps = std::min<int>((int)diff.size(), 12);
                double bestPath = cur;
                Sol bestPathSol;
                bool have = false;
                for (int t3 = 0; t3 < steps; t3++) {
                    int i3 = diff[t3];
                    int oB=s.bay[i3], oO=s.ori[i3], oX=s.x[i3],
                        oY=s.y[i3], oE=s.e[i3], oF=s.f[i3];
                    c.oc->stamp(pb, s, i3, false);
                    s.delIdx(i3);
                    s.bay[i3]=bestSol.bay[i3]; s.ori[i3]=bestSol.ori[i3];
                    s.x[i3]=bestSol.x[i3];     s.y[i3]=bestSol.y[i3];
                    s.e[i3]=bestSol.e[i3];     s.f[i3]=bestSol.f[i3];
                    s.addIdx(i3);
                    c.oc->stamp(pb, s, i3, true);
                    bool okR = core::vpConservative(pb, s, i3);
                    if (okR)
                        for (int k5 : s.byBay[s.bay[i3]]) {
                            if (k5 == i3 || !s.placed[k5]) continue;
                            if (!core::vpConservative(pb, s, k5)) {
                                okR = false; break; }
                        }
                    if (!okR) {                      // step not legal
                        c.oc->stamp(pb, s, i3, false);
                        s.delIdx(i3);
                        s.bay[i3]=oB; s.ori[i3]=oO; s.x[i3]=oX;
                        s.y[i3]=oY; s.e[i3]=oE; s.f[i3]=oF;
                        s.addIdx(i3);
                        c.oc->stamp(pb, s, i3, true);
                        continue;
                    }
                    double v5 = obj();
                    if (v5 < bestPath - 1e-9) {
                        bestPath = v5; bestPathSol = s; have = true;
                    }
                }
                if (have) {
                    s = bestPathSol;
                    c.oc->clearBits();
                    for (int i4 = 0; i4 < pb.n; i4++)
                        if (s.placed[i4]) c.oc->stamp(pb, s, i4, true);
                    s.rebuildIdx(pb.m);
                    cur = obj();
                    if (cur < best - 1e-9) {
                        best = cur; bestSol = s; st.improved++;
                        sinceBest = 0;
                    }
                } else {
                    cur = obj();
                }
            }
            continue;
        }
        // TABU-STYLE STEEPEST MOVE (env OGC_TABU = candidates per
        // step, 0 = off). SA picks ONE random move and accepts it with
        // a probability; that is cheap but blind. Our atomic moves are
        // cheap enough to EVALUATE several and take the best, which is
        // the steepest-descent core of tabu search. The tabu list --
        // blocks touched in the last few steps may not move again --
        // is what stops the search from immediately undoing its own
        // best move, so it can keep descending instead of oscillating.
        // fires as an occasional INTENSIFICATION step, not as the
        // whole iteration: replacing every iteration with steepest
        // swap descent starved the destroy/repair engine completely
        // (measured +47% to +1359%, and K made no difference because
        // the search had collapsed to one deterministic descent).
        if (knobs::tabu() > 0 &&
            (int)(c.rng() % 1000) < knobs::tabuP()) {
            const int K = knobs::tabu();
            thread_local std::vector<int> pls;
            pls.clear();
            for (int i2 = 0; i2 < pb.n; i2++)
                if (s.placed[i2] && !tabuUntil.empty() &&
                    tabuUntil[i2] <= st.iters) pls.push_back(i2);
            if ((int)pls.size() >= 2) {
                int bA=-1,bB2=-1,bX2=0,bY2=0; double bDelta=0.0;
                for (int t2 = 0; t2 < K; t2++) {
                    int A4 = pls[c.rng() % pls.size()];
                    int B4 = pls[c.rng() % pls.size()];
                    if (A4 == B4) continue;
                    int aB4=s.bay[A4], aX4=s.x[A4], aY4=s.y[A4],
                        aE4=s.e[A4];
                    int bB4=s.bay[B4], bX4=s.x[B4], bY4=s.y[B4],
                        bE4=s.e[B4];
                    c.oc->stamp(pb, s, A4, false);
                    c.oc->stamp(pb, s, B4, false);
                    int nA4 = std::max(pb.R[A4], bE4);
                    int nB4 = std::max(pb.R[B4], aE4);
                    s.bay[A4]=bB4; s.x[A4]=bX4; s.y[A4]=bY4;
                    s.e[A4]=nA4; s.f[A4]=nA4+pb.P[A4];
                    s.bay[B4]=aB4; s.x[B4]=aX4; s.y[B4]=aY4;
                    s.e[B4]=nB4; s.f[B4]=nB4+pb.P[B4];
                    s.rebuildIdx(pb.m);
                    c.oc->stamp(pb, s, A4, true);
                    c.oc->stamp(pb, s, B4, true);
                    bool okT = core::vpConservative(pb, s, A4)
                            && core::vpConservative(pb, s, B4);
                    if (okT)
                        for (int j4 : {aB4, bB4}) {
                            for (int k4 : s.byBay[j4]) {
                                if (k4==A4 || k4==B4 || !s.placed[k4])
                                    continue;
                                if (!core::vpConservative(pb, s, k4)) {
                                    okT = false; break; }
                            }
                            if (!okT) break;
                        }
                    double d4 = okT ? (obj() - cur) : 1e300;
                    // undo; we only wanted the evaluation
                    c.oc->stamp(pb, s, A4, false);
                    c.oc->stamp(pb, s, B4, false);
                    s.bay[A4]=aB4; s.x[A4]=aX4; s.y[A4]=aY4;
                    s.e[A4]=aE4; s.f[A4]=aE4+pb.P[A4];
                    s.bay[B4]=bB4; s.x[B4]=bX4; s.y[B4]=bY4;
                    s.e[B4]=bE4; s.f[B4]=bE4+pb.P[B4];
                    s.rebuildIdx(pb.m);
                    c.oc->stamp(pb, s, A4, true);
                    c.oc->stamp(pb, s, B4, true);
                    if (d4 < bDelta - 1e-9) {
                        bDelta = d4; bA = A4; bB2 = B4;
                        bX2 = bX4; bY2 = bY4;
                    }
                }
                if (bA >= 0) {                    // commit the steepest
                    int aB4=s.bay[bA], aX4=s.x[bA], aY4=s.y[bA],
                        aE4=s.e[bA];
                    int bE4=s.e[bB2];
                    c.oc->stamp(pb, s, bA, false);
                    c.oc->stamp(pb, s, bB2, false);
                    int nA4 = std::max(pb.R[bA], bE4);
                    int nB4 = std::max(pb.R[bB2], aE4);
                    s.bay[bA]=s.bay[bB2]; s.x[bA]=bX2; s.y[bA]=bY2;
                    s.e[bA]=nA4; s.f[bA]=nA4+pb.P[bA];
                    s.bay[bB2]=aB4; s.x[bB2]=aX4; s.y[bB2]=aY4;
                    s.e[bB2]=nB4; s.f[bB2]=nB4+pb.P[bB2];
                    s.rebuildIdx(pb.m);
                    c.oc->stamp(pb, s, bA, true);
                    c.oc->stamp(pb, s, bB2, true);
                    cur = obj();
                    long hold = 2 + (long)(c.rng() % 6);
                    tabuUntil[bA] = st.iters + hold;
                    tabuUntil[bB2] = st.iters + hold;
                    if (cur < best - 1e-9) {
                        best = cur; bestSol = s; st.improved++;
                        sinceBest = 0;
                    }
                }
            }
            continue;
        }
        // SHIFT IN PLACE (env OGC_SHIFTP = per-mille): moving a block
        // today means remove + reinsert, which is expensive AND needs
        // free space. Sliding a resident to another (x,y) in its own
        // bay at its own entry keeps the schedule and costs one vp
        // check plus its bay neighbours -- the cheapest structural
        // move available, and it defragments around tardy blocks so
        // later insertions can land.
        // HETEROGENEOUS SHIFT-IN-PLACE. Ablated on 3 seeds against the
        // current base the move splits cleanly by board: P5 -5.2% and
        // P6 -1.2% when it is OFF (3/3 wins each), P4 +5.7% when it is
        // off. It overlaps the swap and compaction moves adopted after
        // it, so on some boards it now only costs iterations. No single
        // rate is right, and choosing one per instance would be fitting
        // the six boards we can see -- so half the portfolio runs with
        // it and half without, exactly as the acceptance rule already
        // splits, and the min over threads lets the instance decide.
        const int SHP = (rp.shiftPP >= 0 ? rp.shiftPP : knobs::shiftP());
        if (SHP > 0 && (int)(c.rng() % 1000) < SHP) {
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
                    if (knobs::shiftLoc() > 0 &&
                        (int)(c.rng() % 1000) < knobs::shiftLoc()) {
                        // local jitter: on a saturated board a uniform
                        // draw almost always collides, while a small
                        // step is usually legal -- same cost per try,
                        // far higher acceptance, and it closes the
                        // fragmentation gaps a block at a time.
                        int r = 1 + (int)(c.rng() % 3);
                        nX = oX + (int)(c.rng() % (uint32_t)(2*r+1)) - r;
                        nY = oY + (int)(c.rng() % (uint32_t)(2*r+1)) - r;
                        nX = std::max(xlo, std::min(xhi, nX));
                        nY = std::max(ylo, std::min(yhi, nY));
                    } else {
                        nX = xlo + (int)(c.rng() % (uint32_t)(xhi-xlo+1));
                        nY = ylo + (int)(c.rng() % (uint32_t)(yhi-ylo+1));
                    }
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
        // REORIENT IN PLACE (env OGC_REORP = per-mille): the engine
        // picks an orientation when a block is inserted and never
        // revisits it, yet orientations of the same block differ in
        // bounding-box area by a median factor of 1.38 (max 3.19) and
        // the boards are bbox-bound. Re-orienting a resident keeps its
        // position and schedule, so the check is even cheaper than a
        // swap: one block plus its bay neighbours.
        if (knobs::reorP() > 0 &&
            (int)(c.rng() % 1000) < knobs::reorP()) {
            thread_local std::vector<int> pl2;
            pl2.clear();
            for (int i2 = 0; i2 < pb.n; i2++)
                if (s.placed[i2] &&
                    pb.geo[i2].orients.size() > 1) pl2.push_back(i2);
            if (!pl2.empty()) {
                int A2 = pl2[c.rng() % pl2.size()];
                int oOld = s.ori[A2];
                int nOri = (int)(c.rng() % pb.geo[A2].orients.size());
                if (nOri != oOld) {
                    c.oc->stamp(pb, s, A2, false);
                    s.ori[A2] = nOri;
                    bool ok3 = core::vpConservative(pb, s, A2);
                    if (ok3) {
                        c.oc->stamp(pb, s, A2, true);
                        for (int k2 : s.byBay[s.bay[A2]]) {
                            if (k2 == A2 || !s.placed[k2]) continue;
                            if (!core::vpConservative(pb, s, k2)) {
                                ok3 = false; break; }
                        }
                        if (!ok3) c.oc->stamp(pb, s, A2, false);
                    }
                    double nv3 = ok3 ? obj() : 1e300;
                    if (ok3 && nv3 <= cur + 1e-9) {
                        cur = nv3;
                        if (nv3 < best - 1e-9) {
                            best = nv3; bestSol = s; st.improved++;
                            sinceBest = 0;
                        }
                    } else {
                        s.ori[A2] = oOld;
                        c.oc->stamp(pb, s, A2, true);
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
        // PUNCTUAL RESCUE (env OGC_RESCUE = per-mille gate). Measured
        // on the incumbent boards: the minimum number of residents that
        // must be displaced to open a punctual position for a tardy
        // block is 1 for about half of them, <=2 for ~90%, and 0 for 17
        // of P6's 147 tardy blocks. Punctuality is one displacement
        // away -- but a random-k destroy essentially never removes
        // exactly that blocker AND refills the hole it left with the
        // tardy block before some other block takes it. Repair also
        // always grabs the earliest legal day, so it re-lands the block
        // late for the same reason it was late before. This move is
        // that missing step, done directly: force a punctual entry cap,
        // and if nothing fits, evict one resident and let it re-seat
        // itself anywhere.
        if (knobs::rescueP() > 0 && rsel.pick() == 1) {
            // SAMPLE the tardy block, never scan for it: the first
            // build of this move listed all n blocks on every fire and
            // that O(n) toll ate the gain exactly where the move should
            // have paid most -- it lost 2.6% on P6 (largest n) and 2.0%
            // on P3, which has ZERO tardy blocks so the whole scan was
            // waste. Eight draws find a tardy block whenever they are
            // more than ~13% of the board, and cost nothing when there
            // are none.
            // FUTILITY COOLDOWN. A block is tardy precisely because no
            // punctual slot existed for it, so the forced-punctual
            // insert scans the whole board and fails -- the most
            // expensive path, taken most often, on exactly the boards
            // with the most tardy blocks. Measured: the move wins where
            // few blocks are tardy (P4 25 blocks -2.8%, P5 66 -1.0%)
            // and loses where many are (P6 147 +1.4%). A block that
            // just failed to be rescued is not retried for n
            // iterations, so repeated futile scans stop without
            // touching how often the move itself fires.
            int A = -1;
            for (int t2 = 0; t2 < 8; t2++) {
                int cand = (int)(c.rng() % (uint32_t)pb.n);
                if (s.placed[cand] && s.f[cand] > pb.D[cand]) {
                    A = cand; break;
                }
            }
            {
                if (A < 0) goto no_rescue;
                int cap = pb.D[A] - pb.P[A];   // latest punctual entry
                if (cap < pb.R[A]) goto no_rescue;  // never punctual
                {
                    int aB=s.bay[A], aO=s.ori[A], aX=s.x[A], aY=s.y[A];
                    int aE=s.e[A], aF=s.f[A];
                    repair::removeBlock(c, A);
                    bool ok2 = repair::insertGreedy(c, A, cap);
                    int V = -1, vB=0,vO=0,vX=0,vY=0,vE=0,vF=0;
                    if (!ok2) {
                        // evict one resident whose stay overlaps A's
                        // punctual window -- only those can be blocking.
                        // Only residents of ONE bay are candidates
                        // (env OGC_RESCUEB): drawing the evictee from
                        // every bay dilutes the 1-2 true blockers among
                        // all m bays' residents, so the move would
                        // almost always evict someone irrelevant. Pick
                        // A's preferred bay or the one it sits in.
                        int jt = -1;
                        if (knobs::rescueB()) {
                            jt = s.bay[A];
                            if (c.rng() & 1) {
                                int bp = 0;
                                for (int j2 = 1; j2 < pb.m; j2++)
                                    if (pb.pref[A][j2] > pb.pref[A][bp])
                                        bp = j2;
                                jt = bp;
                            }
                        }
                        thread_local std::vector<int> res;
                        res.clear();
                        if (jt >= 0) {          // walk the bay index,
                            for (int i2 : s.byBay[jt])   // not all n
                                if (i2 != A && s.placed[i2] &&
                                    s.e[i2] < cap + pb.P[A] &&
                                    s.f[i2] > pb.R[A])
                                    res.push_back(i2);
                        } else {
                            for (int i2 = 0; i2 < pb.n; i2++)
                                if (i2 != A && s.placed[i2] &&
                                    s.e[i2] < cap + pb.P[A] &&
                                    s.f[i2] > pb.R[A])
                                    res.push_back(i2);
                        }
                        if (!res.empty()) {
                            V = res[c.rng() % res.size()];
                            vB=s.bay[V]; vO=s.ori[V]; vX=s.x[V];
                            vY=s.y[V]; vE=s.e[V]; vF=s.f[V];
                            repair::removeBlock(c, V);
                            ok2 = repair::insertGreedy(c, A, cap);
                            if (ok2 && !repair::insertGreedy(c, V, -1)) {
                                repair::removeBlock(c, A);
                                ok2 = false;
                            }
                        }
                    }
                    double nv2 = ok2 ? obj() : 1e300;
                    rsel.used[1]++;
                    if (ok2 && nv2 <= cur + 1e-9) {
                        rsel.score[1] += (nv2 < best - 1e-9 ? 3.0 : 1.0);
                        cur = nv2;
                        if (nv2 < best - 1e-9 && viol == 0) {
                            best = nv2; bestSol = s; st.improved++;
                            sinceBest = 0;
                        }
                    } else {                       // revert both
                        rsel.score[1] += 0.3;
                        if (s.placed[A]) repair::removeBlock(c, A);
                        if (V >= 0 && s.placed[V])
                            repair::removeBlock(c, V);
                        s.bay[A]=aB; s.ori[A]=aO; s.x[A]=aX; s.y[A]=aY;
                        s.e[A]=aE; s.f[A]=aF; s.placed[A]=1;
                        s.addIdx(A); c.oc->stamp(pb, s, A, true);
                        if (V >= 0) {
                            s.bay[V]=vB; s.ori[V]=vO; s.x[V]=vX;
                            s.y[V]=vY; s.e[V]=vE; s.f[V]=vF;
                            s.placed[V]=1; s.addIdx(V);
                            c.oc->stamp(pb, s, V, true);
                        }
                    }
                }
            }
            continue;
        }
no_rescue:
        if (knobs::swapP() > 0 &&
            (int)(c.rng() % 1000) < knobs::swapP()) {
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
                int B;
                if (knobs::swapSel()) {
                    // Structured partner: the swap should hand A a slot
                    // it actually needs -- one occupied during the
                    // window where A is punctual -- and take it from
                    // someone who can absorb the move. Same logic as
                    // the ejection and bottleneck operators, applied to
                    // the partner choice instead of a uniform draw.
                    int esA = std::max(pb.R[A], pb.D[A] - pb.P[A]);
                    int fsA = esA + pb.P[A];
                    thread_local std::vector<std::pair<int,int>> cand;
                    cand.clear();
                    for (int i2 : pl) {
                        if (i2 == A) continue;
                        if (s.f[i2] <= esA || s.e[i2] >= fsA) continue;
                        cand.push_back({pb.D[i2] - s.f[i2], i2});
                    }
                    if (cand.empty()) goto no_swap;
                    std::sort(cand.begin(), cand.end(),
                              [](const std::pair<int,int>& x,
                                 const std::pair<int,int>& y){
                                  return x.first > y.first; });
                    int top = std::min<int>((int)cand.size(), 8);
                    B = cand[c.rng() % top].second;
                } else {
                    B = pl[c.rng() % pl.size()];
                }
                // 3-CYCLE (env OGC_SWAP3, per-mille of swap events):
                // some rearrangements need three slots to rotate
                // through -- A takes B's, B takes C's, C takes A's --
                // and are unreachable by any pairwise exchange. Random
                // partners, since the pairwise measurement showed the
                // value is in the diversity of the exchange.
                int C = -1;
                if (knobs::swap3() > 0 && pl.size() >= 3 &&
                    (int)(c.rng() % 1000) < knobs::swap3()) {
                    for (int tries = 0; tries < 4; tries++) {
                        int cc = pl[c.rng() % pl.size()];
                        if (cc != A && cc != B) { C = cc; break; }
                    }
                }
                if (A != B) {
                    int aB=s.bay[A], aX=s.x[A], aY=s.y[A], aE=s.e[A];
                    int bB=s.bay[B], bX=s.x[B], bY=s.y[B], bE=s.e[B];
                    int cB=0, cX=0, cY=0, cE=0;
                    if (C >= 0) { cB=s.bay[C]; cX=s.x[C]; cY=s.y[C];
                                  cE=s.e[C]; c.oc->stamp(pb, s, C, false); }
                    c.oc->stamp(pb, s, A, false);
                    c.oc->stamp(pb, s, B, false);
                    int nAe = std::max(pb.R[A], bE);
                    int nBe = std::max(pb.R[B], C >= 0 ? cE : aE);
                    s.bay[A]=bB; s.x[A]=bX; s.y[A]=bY;
                    s.e[A]=nAe; s.f[A]=nAe+pb.P[A];
                    if (C >= 0) {
                        s.bay[B]=cB; s.x[B]=cX; s.y[B]=cY;
                        s.e[B]=nBe; s.f[B]=nBe+pb.P[B];
                        int nCe = std::max(pb.R[C], aE);
                        s.bay[C]=aB; s.x[C]=aX; s.y[C]=aY;
                        s.e[C]=nCe; s.f[C]=nCe+pb.P[C];
                    } else {
                        s.bay[B]=aB; s.x[B]=aX; s.y[B]=aY;
                        s.e[B]=nBe; s.f[B]=nBe+pb.P[B];
                    }
                    // full rebuild, not incremental: swapping to
                    // delIdx/addIdx measured WORSE (mean +1.8% on the
                    // same seeds) even though it is strictly less
                    // work. The rebuild also drops stale entries left
                    // by other paths, and the search evidently relies
                    // on that cleanup.
                    s.rebuildIdx(pb.m);
                    c.oc->stamp(pb, s, A, true);
                    c.oc->stamp(pb, s, B, true);
                    if (C >= 0) c.oc->stamp(pb, s, C, true);
                    bool ok2 = core::vpConservative(pb, s, A)
                            && core::vpConservative(pb, s, B)
                            && (C < 0 || core::vpConservative(pb, s, C));
                    if (ok2)
                        for (int j2 : {aB, bB, C >= 0 ? cB : aB}) {
                            for (int k2 : s.byBay[j2]) {
                                if (k2 == A || k2 == B || k2 == C
                                    || !s.placed[k2])
                                    continue;
                                if (!core::vpConservative(pb, s, k2)) {
                                    ok2 = false; break; }
                            }
                            if (!ok2) break;
                        }
                    // RELOCATE VARIANT: A's old slot is often a bad
                    // home for B (that is why plain swaps fail). Let B
                    // find its own place instead -- still one atomic,
                    // priced move, just with the displaced block
                    // re-seated freely rather than forced into the
                    // vacated hole.
                    if (!ok2 && C < 0 && knobs::swapRel() > 0 &&
                        (int)(c.rng() % 1000) < knobs::swapRel()) {
                        removeBlock(c, B);
                        if (core::vpConservative(pb, s, A)) {
                            bool nb2 = true;
                            for (int k2 : s.byBay[bB]) {
                                if (k2 == A || !s.placed[k2]) continue;
                                if (!core::vpConservative(pb, s, k2)) {
                                    nb2 = false; break; }
                            }
                            if (nb2 && repair::insertGreedy(c, B, -1))
                                ok2 = true;
                        }
                        if (!ok2 && !s.placed[B]) {
                            s.bay[B]=bB; s.x[B]=bX; s.y[B]=bY;
                            s.e[B]=bE; s.f[B]=bE+pb.P[B];
                            s.placed[B]=1; s.addIdx(B);
                            c.oc->stamp(pb, s, B, true);
                        }
                    }
                    if (knobs::ota() >= 3 && !ok2) {
                        // FULL-FORM v1: ambient-infeasible search.
                        // Accept by SA on true-cost + mu * depth-delta;
                        // depth in axis units, mu ramps in units of one
                        // tardy day so a depth-5 ridge costs 0.1->25
                        // days across the run. Repairs are the global
                        // cluster-descent sweeps below, not eviction.
                        double dPen = penOf(A) + penOf(B)
                                    + (C >= 0 ? penOf(C) : 0.0);
                        double prog = rp.maxIters > 0
                            ? (double)st.iters / rp.maxIters
                            : now() / seconds;
                        double MU = pb.w1 * (0.02 + 5.0 * prog * prog);
                        double d4 = (obj() - cur) + MU * dPen;
                        if (prog < 0.9 &&
                            (d4 <= 0 || U(c.rng) < std::exp(-d4 / T))) {
                            apen += dPen;
                            cur = obj();
                            ds.used[kind]++;
                            continue;
                        }
                    }
                    if (OTA && !ok2) {
                        // SWAP + SEPARATION (Imamichi-style, minimal):
                        // the exchanged blocks rarely fit each other's
                        // slots exactly -- that is why plain swaps fail.
                        // Slide each still-violating mover along the
                        // four axes (binary ladder, memo-accelerated
                        // boolean oracle) until it clears; revert the
                        // whole move if any mover cannot be cleared.
                        // No infeasible state is ever kept, so the
                        // OTA v0/v0.1 collapse modes cannot occur.
                        auto slideClear = [&](int M) -> bool {
                            if (core::vpConservative(pb, s, M)) return true;
                            int ox = s.x[M], oy = s.y[M];
                            const auto& gb2 =
                                pb.geo[M].orients[s.ori[M]].bb;
                            int jj = s.bay[M];
                            int xl = (int)std::ceil(-gb2.x0);
                            int xh = (int)std::floor(pb.bayW[jj] - gb2.x1);
                            int yl = (int)std::ceil(-gb2.y0);
                            int yh = (int)std::floor(pb.bayH[jj] - gb2.y1);
                            static const int dxs[4] = {1, -1, 0, 0};
                            static const int dys[4] = {0, 0, 1, -1};
                            for (int step = 1; step <= 12; step += (step < 4 ? 1 : 3)) {
                                for (int d2 = 0; d2 < 4; d2++) {
                                    int nx2 = ox + dxs[d2] * step;
                                    int ny2 = oy + dys[d2] * step;
                                    if (nx2 < xl || nx2 > xh ||
                                        ny2 < yl || ny2 > yh) continue;
                                    c.oc->stamp(pb, s, M, false);
                                    s.x[M] = nx2; s.y[M] = ny2;
                                    c.oc->stamp(pb, s, M, true);
                                    if (core::vpConservative(pb, s, M))
                                        return true;
                                }
                            }
                            c.oc->stamp(pb, s, M, false);
                            s.x[M] = ox; s.y[M] = oy;
                            c.oc->stamp(pb, s, M, true);
                            return false;
                        };
                        bool rep = slideClear(A) && slideClear(B)
                                && (C < 0 || slideClear(C));
                        if (!rep && knobs::ota() >= 2) {
                            // CLUSTER COORDINATE DESCENT (rung 7): the
                            // movers alone cannot always clear -- the
                            // interlocked NEIGHBOURS must give way too.
                            // Gather every violating block in the two
                            // bays (cap 5), then slide cluster members
                            // in turn, keeping only strict reductions
                            // of the cluster's total violation count;
                            // success only at exactly zero.
                            thread_local std::vector<int> cl;
                            cl.clear();
                            for (int j2 : {aB, bB})
                                for (int k2 : s.byBay[j2]) {
                                    if (!s.placed[k2]) continue;
                                    if (!core::vpConservative(pb, s, k2))
                                        cl.push_back(k2);
                                }
                            std::sort(cl.begin(), cl.end());
                            cl.erase(std::unique(cl.begin(), cl.end()),
                                     cl.end());
                            if (!cl.empty() && cl.size() <= 5) {
                                thread_local std::vector<std::array<int,2>> bkp;
                                bkp.clear();
                                for (int M : cl)
                                    bkp.push_back({s.x[M], s.y[M]});
                                auto tot = [&]() {
                                    long v2 = 0;
                                    for (int M : cl)
                                        v2 += core::vpViolCount(pb, s, M);
                                    return v2;
                                };
                                long cv = tot();
                                static const int lad[5] = {1, 2, 4, 7, 11};
                                static const int dxs[4] = {1, -1, 0, 0};
                                static const int dys[4] = {0, 0, 1, -1};
                                for (int pass = 0; pass < 4 && cv > 0;
                                     pass++) {
                                    bool prog = false;
                                    for (int M : cl) {
                                        const auto& gb3 =
                                            pb.geo[M].orients[s.ori[M]].bb;
                                        int jj = s.bay[M];
                                        int xl = (int)std::ceil(-gb3.x0);
                                        int xh = (int)std::floor(
                                            pb.bayW[jj] - gb3.x1);
                                        int yl = (int)std::ceil(-gb3.y0);
                                        int yh = (int)std::floor(
                                            pb.bayH[jj] - gb3.y1);
                                        int bx2 = s.x[M], by2 = s.y[M];
                                        long bv = cv;
                                        int gx2 = bx2, gy2 = by2;
                                        for (int li = 0; li < 5; li++)
                                        for (int d3 = 0; d3 < 4; d3++) {
                                            int nx3 = bx2 + dxs[d3]*lad[li];
                                            int ny3 = by2 + dys[d3]*lad[li];
                                            if (nx3 < xl || nx3 > xh ||
                                                ny3 < yl || ny3 > yh)
                                                continue;
                                            c.oc->stamp(pb, s, M, false);
                                            s.x[M] = nx3; s.y[M] = ny3;
                                            c.oc->stamp(pb, s, M, true);
                                            long v3 = tot();
                                            if (v3 < bv) { bv = v3;
                                                gx2 = nx3; gy2 = ny3; }
                                        }
                                        c.oc->stamp(pb, s, M, false);
                                        s.x[M] = gx2; s.y[M] = gy2;
                                        c.oc->stamp(pb, s, M, true);
                                        if (bv < cv) { cv = bv;
                                                       prog = true; }
                                        if (cv == 0) break;
                                    }
                                    if (!prog) break;
                                }
                                if (cv == 0) rep = true;
                                else {
                                    for (size_t q2 = 0; q2 < cl.size();
                                         q2++) {
                                        int M = cl[q2];
                                        c.oc->stamp(pb, s, M, false);
                                        s.x[M] = bkp[q2][0];
                                        s.y[M] = bkp[q2][1];
                                        c.oc->stamp(pb, s, M, true);
                                    }
                                }
                            }
                        }
                        if (rep) {
                            // sliding may disturb neighbours: verify
                            bool nb2 = core::vpConservative(pb, s, A)
                                    && core::vpConservative(pb, s, B)
                                    && (C < 0 ||
                                        core::vpConservative(pb, s, C));
                            if (nb2)
                                for (int j2 : {aB, bB}) {
                                    for (int k2 : s.byBay[j2]) {
                                        if (k2 == A || k2 == B || k2 == C
                                            || !s.placed[k2]) continue;
                                        if (!core::vpConservative(pb, s,
                                                                  k2)) {
                                            nb2 = false; break; }
                                    }
                                    if (!nb2) break;
                                }
                            ok2 = nb2;
                        }
                    }
                    double nv2 = ok2 ? obj() : 1e300;
                    if (ok2 && nv2 <= cur + 1e-9) {
                        cur = nv2;
                        if (nv2 < best - 1e-9 && viol == 0) {
                            best = nv2; bestSol = s; st.improved++;
                            sinceBest = 0;
                        }
                    } else {                       // revert
                        c.oc->stamp(pb, s, A, false);
                        c.oc->stamp(pb, s, B, false);
                        if (C >= 0) c.oc->stamp(pb, s, C, false);
                        s.bay[A]=aB; s.x[A]=aX; s.y[A]=aY;
                        s.e[A]=aE; s.f[A]=aE+pb.P[A];
                        s.bay[B]=bB; s.x[B]=bX; s.y[B]=bY;
                        s.e[B]=bE; s.f[B]=bE+pb.P[B];
                        if (C >= 0) { s.bay[C]=cB; s.x[C]=cX; s.y[C]=cY;
                                      s.e[C]=cE; s.f[C]=cE+pb.P[C]; }
                        s.rebuildIdx(pb.m);
                        c.oc->stamp(pb, s, A, true);
                        c.oc->stamp(pb, s, B, true);
                        if (C >= 0) c.oc->stamp(pb, s, C, true);
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
        auto rem = pickRemoval(c, kind, q, lastTouch);
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
        // REPAIR ORDER (env OGC_RORDER): classic ALNS inserts by due
        // date, but the measured pathology is STRUCTURAL tardiness --
        // blocks that fit nowhere once others are down. The literature's
        // answer is regret-style ordering: place the most constrained
        // piece first. These keys are proxies computable for free (no
        // extra scans, so iteration count is untouched, which is what
        // killed best-of-R repair).
        switch (knobs::rOrder()) {
        case 1:   // slack ascending: least room to absorb a delay
            std::sort(rem.begin(), rem.end(), [&](int a, int b){
                int sa = pb.D[a] - pb.R[a] - pb.P[a];
                int sb = pb.D[b] - pb.R[b] - pb.P[b];
                return sa != sb ? sa < sb : pb.D[a] < pb.D[b]; });
            break;
        case 2:   // hardest to place first: footprint x stay length,
                  // scaled by the squeaky-wheel priority when enabled
            std::sort(rem.begin(), rem.end(), [&](int a, int b){
                const auto& ga = pb.geo[a].orients[s.ori[a]].bb;
                const auto& gb = pb.geo[b].orients[s.ori[b]].bb;
                double sa = squeak.empty() ? 1.0 : squeak[a];
                double sb2 = squeak.empty() ? 1.0 : squeak[b];
                double aa = (ga.x1-ga.x0)*(ga.y1-ga.y0)*pb.P[a]*sa;
                double ab = (gb.x1-gb.x0)*(gb.y1-gb.y0)*pb.P[b]*sb2;
                return aa != ab ? aa > ab : pb.D[a] < pb.D[b]; });
            break;
        case 4:   // same, times LAYER COUNT: the crane descends
                  // vertically, so a tall block is blocked by anything
                  // at or above each of its layers -- it is strictly
                  // harder to insert late, and it blocks more of the
                  // column for everyone else once it is down.
            std::sort(rem.begin(), rem.end(), [&](int a, int b){
                const auto& ga = pb.geo[a].orients[s.ori[a]];
                const auto& gb = pb.geo[b].orients[s.ori[b]];
                double aa = (ga.bb.x1-ga.bb.x0)*(ga.bb.y1-ga.bb.y0)
                            * pb.P[a] * (double)ga.layers.size();
                double ab = (gb.bb.x1-gb.bb.x0)*(gb.bb.y1-gb.bb.y0)
                            * pb.P[b] * (double)gb.layers.size();
                return aa != ab ? aa > ab : pb.D[a] < pb.D[b]; });
            break;
        case 3:   // due date, but hardest-first within the same day
            std::sort(rem.begin(), rem.end(), [&](int a, int b){
                if (pb.D[a] != pb.D[b]) return pb.D[a] < pb.D[b];
                const auto& ga = pb.geo[a].orients[s.ori[a]].bb;
                const auto& gb = pb.geo[b].orients[s.ori[b]].bb;
                return (ga.x1-ga.x0)*(ga.y1-ga.y0)
                     > (gb.x1-gb.x0)*(gb.y1-gb.y0); });
            break;
        default:
            std::sort(rem.begin(), rem.end(),
                      [&](int a, int b){ return pb.D[a] < pb.D[b]; });
        }
        // POLYGON-EXACT MULTI-REPAIR (env OGC_REPAIRR = attempts,
        // 1 = classic): insertGreedy is randomised (bay order,
        // orientation order), so re-running it on the same removal
        // samples genuinely different polygon-exact repairs. Taking
        // the best of R is a strictly stronger neighbourhood than the
        // single greedy pass, and unlike every contact-placement
        // policy tried so far it changes NOTHING about the geometry
        // rules -- it just searches the repair space properly.
        const int RR = knobs::repairR();
        if (RR > 1 && !(kind == 8 || kind == 9)) {
            double bestV = 1e300;
            std::vector<std::array<int,7>> bestPl;
            for (int att = 0; att < RR; att++) {
                bool ok2 = true;
                for (int A : rem)
                    if (!repair::insertGreedy(c, A, -1)) { ok2 = false;
                                                           break; }
                if (ok2) {
                    double v2 = core::evaluatePen(pb, s).total;
                    if (v2 < bestV) {
                        bestV = v2;
                        bestPl.clear();
                        for (int A : rem)
                            bestPl.push_back({s.bay[A], s.ori[A], s.x[A],
                                              s.y[A], s.e[A], s.f[A], 1});
                    }
                }
                for (int A : rem)              // always undo: the
                    if (s.placed[A])           // winner is replayed
                        repair::removeBlock(c, A);
            }
            if (bestV < 1e299) {
                // every attempt ends with `rem` removed, so restore
                // the winning attempt from scratch. Guard against
                // double-add: only stamp blocks that are currently
                // unplaced (the last attempt may have left some in).
                for (int A : rem)
                    if (s.placed[A]) repair::removeBlock(c, A);
                for (size_t q = 0; q < rem.size(); q++) {
                    int A = rem[q];
                    s.bay[A]=bestPl[q][0]; s.ori[A]=bestPl[q][1];
                    s.x[A]=bestPl[q][2];   s.y[A]=bestPl[q][3];
                    s.e[A]=bestPl[q][4];   s.f[A]=bestPl[q][5];
                    s.placed[A]=1; s.addIdx(A);
                    c.oc->stamp(pb, s, A, true);
                }
            }
            bool ok = (bestV < 1e299);
            if (!ok) {
                st.failed++;
                for (int A : rem) if (s.placed[A]) repair::removeBlock(c, A);
                for (auto& b : bk) {
                    s.bay[b.A]=b.bay; s.ori[b.A]=b.ori; s.x[b.A]=b.x;
                    s.y[b.A]=b.y; s.e[b.A]=b.e; s.f[b.A]=b.f;
                    s.placed[b.A]=1; s.addIdx(b.A);
                    c.oc->stamp(pb, s, b.A, true);
                }
                ds.used[kind]++;
                continue;
            }
            goto repaired;
        }
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
            // WIRE THE EJECTION CHAIN INTO THE ONE PATH THAT CAN USE IT.
            // The takeover sink was only ever attached to the plain
            // branch below, where every insert is uncapped -- and the
            // measured repair failure rate there is 0.0% over 1.07M and
            // 1.77M iterations, so the "last resort" it guards has
            // never once executed. Here the insert carries a PUNCTUAL
            // cap and therefore does fail, which is exactly when
            // displacing a slack-rich resident is the right move. The
            // census says punctuality is one displacement away for
            // about half of all tardy blocks; this is the wiring that
            // lets the solver take it.
            thread_local std::vector<int> evq2;
            thread_local std::vector<uint8_t> seen2;
            evq2.clear();
            seen2.assign(pb.n, 0);
            c.evicted = knobs::takeover() ? &evq2 : nullptr;
            c.chainSeen = knobs::takeover() ? &seen2 : nullptr;
            c.chainDepth = 0;
            ok = repair::insertGreedy(c, c.tsBlk, c.tsECap);
            c.forcedBay = save;
            if (ok)
                for (int A : rem) {
                    if (A == c.tsBlk) continue;
                    if (!repair::insertGreedy(c, A, -1)) { ok = false; break; }
                }
            for (size_t gi = 0; ok && gi < evq2.size(); gi++) {
                c.chainDepth = (int)gi + 1;
                if (!repair::insertGreedy(c, evq2[gi], -1)) { ok = false;
                                                             break; }
            }
            c.evicted = nullptr; c.chainSeen = nullptr; c.chainDepth = 0;
        } else {
            // takeover sink: a block that loses its slot to a harder
            // one comes back here and is re-placed like any other
            // EJECTION CHAIN: a displaced block may in turn take a
            // slot, so re-placing it can cascade. Depth is bounded and
            // a block can be displaced at most once per chain, which
            // is what keeps the cascade from cycling. On a saturated
            // board this is the neighbourhood that actually moves
            // structure -- plain reinsertion has no free space to use.
            thread_local std::vector<int> evq;
            thread_local std::vector<uint8_t> seen;
            evq.clear();
            seen.assign(pb.n, 0);
            c.evicted = knobs::takeover() ? &evq : nullptr;
            c.chainSeen = knobs::takeover() ? &seen : nullptr;
            c.chainDepth = 0;
            for (int A : rem) {
                if (!repair::insertGreedy(c, A, -1)) { ok = false; break; }
            }
            for (size_t gi = 0; ok && gi < evq.size(); gi++) {
                c.chainDepth = (int)gi + 1;
                if (!repair::insertGreedy(c, evq[gi], -1)) { ok = false;
                                                            break; }
            }
            c.evicted = nullptr; c.chainSeen = nullptr; c.chainDepth = 0;
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
repaired:
        long t3 = tmr ? tick() : 0;
        double nv = obj();
        if (tmr) st.tEval += tick() - t3;
        double dlt = nv - cur;
        double nvTrue = useTrue ? objTrue() : nv;
        bool acc;
        if (LAH > 0) {
            // canonical LAHC (Burke & Bykov): accept when the
            // candidate is no worse than the value L iterations ago
            // OR no worse than the current one; the history cell is
            // then overwritten with the POST-DECISION current value.
            // My first version updated it conditionally, which
            // freezes the history and stalls the walk.
            acc = (nv <= hist[(size_t)(st.iters % LAH)] + 1e-9)
                  || (nv <= cur + 1e-9);
        } else {
            acc = dlt < -1e-9 ||
                  (!hc && U(c.rng) < std::exp(-dlt / std::max(1e-9, T)));
        }
        ds.used[kind]++;
        if (acc) {
            cur = nv; st.accepted++;
            for (int A : rem) lastTouch[A] = st.iters;
            if (nvTrue < best - 1e-9) {
                best = nvTrue; bestSol = s; st.improved++; sinceBest = 0;
                ds.score[kind] += 3.0; ds.totImp[kind]++;
                rsel.used[0]++; rsel.score[0] += 3.0;
            } else { ds.score[kind] += (dlt < -1e-9 ? 1.0 : 0.3); sinceBest++;
                     rsel.used[0]++;
                     rsel.score[0] += (dlt < -1e-9 ? 1.0 : 0.3); }
            if (LAH > 0) hist[(size_t)(st.iters % LAH)] = cur;
        } else {
            sinceBest++;
            if (LAH > 0) hist[(size_t)(st.iters % LAH)] = cur;
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
        if (++sinceUpdate >= 60) { ds.update(knobs::decay());
                                   rsel.update(knobs::decay());
                                   sinceUpdate = 0; }
        if (!squeak.empty() && (st.iters % 500) == 0) {
            // reinforce the blocks that are still late, decay the rest
            for (int i2 = 0; i2 < pb.n; i2++) {
                if (s.placed[i2] && s.f[i2] > pb.D[i2])
                    squeak[i2] = std::min(8.0, squeak[i2] * 1.15);
                else
                    squeak[i2] = 1.0 + (squeak[i2] - 1.0) * 0.9;
            }
        }
        // RETURN TO BEST (env OGC_RETURN = stall length in units of n,
        // 0 = off): simulated annealing accepts worsening moves, so the
        // walk drifts away from the incumbent and can spend the rest of
        // the budget in a worse basin. Standard ALNS intensification is
        // to snap back to the best solution after a stall. The stall
        // length scales with n so the behaviour is identical on unseen
        // instance sizes.
        if (knobs::ota() >= 3 && (st.iters & 2047) == 0) {
            // GLOBAL DESCENT SWEEP (the full-form repairer): coordinate
            // descent over every violating block, monotone in summed
            // depth, progress KEPT (ambient infeasibility is allowed).
            thread_local std::vector<int> vio;
            vio.clear();
            for (int i2 = 0; i2 < pb.n; i2++)
                if (s.placed[i2] && !core::vpConservative(pb, s, i2))
                    vio.push_back(i2);
            static const int lad2[5] = {1, 2, 4, 7, 11};
            static const int dxs2[4] = {1, -1, 0, 0};
            static const int dys2[4] = {0, 0, 1, -1};
            for (int pass = 0; pass < 3 && !vio.empty(); pass++) {
                for (int M : vio) {
                    if (core::vpConservative(pb, s, M)) continue;
                    double pv = penOf(M);
                    const auto& gb5 = pb.geo[M].orients[s.ori[M]].bb;
                    int jj = s.bay[M];
                    int xl = (int)std::ceil(-gb5.x0);
                    int xh = (int)std::floor(pb.bayW[jj] - gb5.x1);
                    int yl = (int)std::ceil(-gb5.y0);
                    int yh = (int)std::floor(pb.bayH[jj] - gb5.y1);
                    int bx3 = s.x[M], by3 = s.y[M];
                    int gx3 = bx3, gy3 = by3;
                    for (int li = 0; li < 5; li++)
                    for (int d3 = 0; d3 < 4; d3++) {
                        int nx5 = bx3 + dxs2[d3] * lad2[li];
                        int ny5 = by3 + dys2[d3] * lad2[li];
                        if (nx5 < xl || nx5 > xh ||
                            ny5 < yl || ny5 > yh) continue;
                        c.oc->stamp(pb, s, M, false);
                        s.x[M] = nx5; s.y[M] = ny5;
                        c.oc->stamp(pb, s, M, true);
                        double pv2 = penOf(M);
                        if (pv2 < pv - 1e-9) { pv = pv2;
                            gx3 = nx5; gy3 = ny5; }
                        c.oc->stamp(pb, s, M, false);
                        s.x[M] = bx3; s.y[M] = by3;
                        c.oc->stamp(pb, s, M, true);
                    }
                    if (gx3 != bx3 || gy3 != by3) {
                        c.oc->stamp(pb, s, M, false);
                        s.x[M] = gx3; s.y[M] = gy3;
                        c.oc->stamp(pb, s, M, true);
                    }
                }
                vio.erase(std::remove_if(vio.begin(), vio.end(),
                    [&](int M){ return core::vpConservative(pb, s, M); }),
                    vio.end());
            }
            apen = 0.0;
            for (int M : vio) apen += penOf(M);
            double prog3 = rp.maxIters > 0
                ? (double)st.iters / rp.maxIters : now() / seconds;
            if (vio.empty()) {
                cur = obj();
                if (cur < best - 1e-9) { best = cur; bestSol = s;
                                         sinceBest = 0; st.improved++; }
            } else if (prog3 >= 0.9) {
                for (int M : vio) repair::removeBlock(c, M);
                for (int i2 = 0; i2 < pb.n; i2++)
                    if (!s.placed[i2]) repair::insertGreedy(c, i2, -1);
                apen = 0.0;
                cur = obj();
                if (cur < best - 1e-9) { best = cur; bestSol = s;
                                         sinceBest = 0; }
            }
        } else if (OTA && viol > 0 && (st.iters & 2047) == 0) {
            long v2 = 0;
            for (int i2 = 0; i2 < pb.n; i2++) v2 += violOf(i2);
            viol = v2;
            if (viol > 0) {
                for (int i2 = 0; i2 < pb.n; i2++)
                    if (s.placed[i2] && !core::vpConservative(pb, s, i2))
                        repair::removeBlock(c, i2);
                for (int i2 = 0; i2 < pb.n; i2++)
                    if (!s.placed[i2]) repair::insertGreedy(c, i2, -1);
                viol = 0;
                cur = obj();
                if (cur < best - 1e-9) { best = cur; bestSol = s;
                                         sinceBest = 0; }
            }
        }
        if (knobs::retN() > 0 &&
            sinceBest > (long)knobs::retN() * pb.n) {
            s = bestSol;
            c.oc->clearBits();
            for (int i2 = 0; i2 < pb.n; i2++)
                if (s.placed[i2]) c.oc->stamp(pb, s, i2, true);
            s.rebuildIdx(pb.m);
            if (knobs::iCompact() > 0) {
                // DEFRAGMENT AT THE STALL. Block positions do not enter
                // the objective at all (Z1 tardiness, Z2 imbalance, Z3
                // regret depend on bay/entry, never on x,y), so sliding
                // residents together is score-neutral: it only turns
                // scattered free area into contiguous space that the
                // next repairs can insert into. Measured: on a peak day
                // 35% of a bay is free yet no tardy block has a punctual
                // position in ANY bay -- the gaps bind, not the area.
                // Post-round compaction cannot help (the search is over
                // by then); this fires while there is still budget to
                // exploit the space it frees.
                spatialCompact(c, knobs::iCompact());
                s.rebuildIdx(pb.m);
                bestSol = s;   // same objective, less fragmented
            }
            cur = obj();
            sinceBest = 0;
        }
        if (GLSA > 0.0 && sinceBest > 0 && (sinceBest % 400) == 0) {
            // classic GLS utility cost/(1+penalty); cost = this
            // block's tardiness on the current board.
            int arg = -1; double bestUtil = 0.0;
            for (int i = 0; i < pb.n; i++) {
                if (!s.placed[i]) continue;
                double t = (double)std::max(0, s.f[i] - pb.D[i]);
                if (t <= 0) continue;
                double u = t / (1.0 + pen[i]);
                if (u > bestUtil) { bestUtil = u; arg = i; }
            }
            if (arg >= 0) { pen[arg] += GLSA; cur = obj(); }
        }
        if (auditEvery > 0 && (st.iters % auditEvery) == 0) {
            int bb = -1; int code = audit(pb, s, &bb);
            if (code) { st.badIter = st.iters; st.badKind = kind;
                st.badPhase = code * 1000 + bb; break; }
        }
    }
    s = bestSol;
    if (GLSA > 0.0) core::glsPen() = nullptr;
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
// SPATIAL COMPACTION (env OGC_SCOMPACT = passes, 0 = off): slide
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
            // also try sliding DOWN: fragmentation is two-dimensional,
            // so compacting on one axis only recovers half of it. The
            // free area on a peak day is 35% of the bay yet no tardy
            // block fits anywhere -- the gaps are scattered, and both
            // directions are needed to gather them.
            int ny = s.y[i];
            if (knobs::sCompact() > 0 && knobs::compactY()) {
                const auto& bbi = pb.geo[i].orients[oi].bb;
                int ylo = (int)std::ceil(-bbi.y0);
                for (int ty = ylo; ty < s.y[i]; ty++) {
                    int probe = c.oc->slideLeftX(sn, i, oi, ty,
                                                 s.x[i], s.x[i]);
                    if (probe == s.x[i]) { ny = ty; break; }
                }
            }
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
    std::normal_distribution<double> N(0.0, knobs::cnoise());
    std::vector<double> key(pb.n);
    for (int i = 0; i < pb.n; i++) key[i] = pb.D[i] + N(c.rng);
    std::sort(order.begin(), order.end(),
              [&](int a, int b){ return key[a] < key[b]; });
    for (int A : order)
        if (!repair::insertGreedy(c, A)) return false;
    return true;
}

} // namespace alns
